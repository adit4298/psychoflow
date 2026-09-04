"""Shared vocabulary for the Incident & Priority agent (Part 4a).

The BOTTOM layer of `agents/incident_priority.py`: the priority policy
itself, the four event classes, every tunable constant, the immutable data
shapes, the bias-shaping functions, and arbitration. Split out of the agent
module purely so each file stays inside the 800-line maintainability ceiling
(CLAUDE.md's coding-style rule prefers many small cohesive files); this is a
layering split, not a behavioural one.

Nothing here holds state or touches I/O, and — like `backend/control_api.py`
— it imports no SUMO, torch or numpy, so it stays importable in a voice-only
or offline context.

Read `agents/incident_priority.py`'s docstring for the honest boundaries
(§17 class) that govern the whole agent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# control_api is deliberately SUMO-free (its own docstring says so), so
# importing the bounds here costs nothing and is what makes it STRUCTURALLY
# impossible for this agent to emit a directive control_api would reject on
# range. Not a layering inversion: this is the contract, not the backend.
from backend.control_api import (
    LANE_BIAS_DURATION_RANGE_S,
    LANE_BIAS_WEIGHT_RANGE,
)

# ---------------------------------------------------------------------------
# The priority policy — constraint 2, in one greppable place
# ---------------------------------------------------------------------------
EVENT_EMERGENCY = "emergency"
EVENT_ACCIDENT = "accident"
EVENT_MAJOR_CONGESTION = "major_congestion"
EVENT_FAIRNESS = "fairness"
EVENT_CLASSES = (
    EVENT_EMERGENCY, EVENT_ACCIDENT, EVENT_MAJOR_CONGESTION, EVENT_FAIRNESS,
)
CLASS_RANK = {cls: i for i, cls in enumerate(EVENT_CLASSES)}
assert set(CLASS_RANK) == set(EVENT_CLASSES), "priority policy drifted"

# Where an event's evidence came from. `Event.source` is provenance only —
# it gates nothing, exactly as §11.2's `trigger_source` describes what
# caused a clearance without changing how it is handled.
SOURCE_SENSOR = "lane_sensor"        # §7.1 type_composition
SOURCE_INTAKE = "incident_intake"    # §7.3 reported incident
SOURCE_OPERATOR = "operator"         # §13.1 trigger_emergency
SOURCE_VISION = "vision"             # Track A / §7.2

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_PREEMPTED = "preempted"

# ---------------------------------------------------------------------------
# DUPLICATED LITERALS — see the NO SUMO note in the module docstring. The
# self-test's drift guard compares each against its home module when SUMO is
# importable.
# ---------------------------------------------------------------------------
FAIRNESS_WAIT_S = 90.0    # == perception.lane_sensor.DEFAULT_STARVATION_THRESHOLD_S
CEILING_WAIT_S = 120.0    # == safety.validator.STARVATION_CEILING_S
MIN_GREEN_S = 10.0        # == env.psychoflow_env.MIN_GREEN_S
CORRIDOR_ORDER = ("J1", "J2", "J3")   # §0.1; == control_api._CORRIDOR_JUNCTIONS
_JUNCTION_INDEX = {jid: i for i, jid in enumerate(CORRIDOR_ORDER)}

# ---------------------------------------------------------------------------
# Classification thresholds. Every one is an existing repo constant or
# arithmetic on two of them — see the design rationale in docs/BUILD_LOG.md.
# ---------------------------------------------------------------------------
# Two lanes starved at once is BY CONSTRUCTION beyond what the per-lane
# fairness mechanism can fix: §10's ceiling and §9.1's starvation bonus each
# act on ONE lane. 2 is the smallest integer meaning "more than one", not a
# fitted value — and it is what makes congestion (a throughput problem) and
# fairness (an equity problem) genuinely disjoint rather than a soft/hard
# split of one signal.
CONGESTION_MIN_STARVED_LANES = 2
# == backend.sim_runner._SPILLOVER_MIN_DELTA. Reusing the materiality bar
# §13.2 already applies before showing a forecast to a human means the agent
# acts on exactly the forecasts the dashboard displays — no class of
# forecast can move the agent invisibly.
CONGESTION_SPILLOVER_DELTA_VEH = 1.0
# == prediction.spillover.CONFIDENCE_COLD_START, and STRICT `>`. With
# today's constants (0.85 - 0.20 = 0.65 > 0.5) that value uniquely means
# "no previous snapshot to compute a rate from", so the rule reads "ignore a
# cold-start forecast" while still admitting the incident-penalised 0.65
# case — which is exactly when you most want to act.
# STATED COUPLING: this equality breaks if spillover's incident confidence
# penalty ever exceeds 0.35. Recorded in NOTES-FOR-INTEGRATION.md.
CONGESTION_MIN_CONFIDENCE = 0.5
# == perception.vision_mock.CONFIDENCE_RANGE[0]. Every output the existing
# mock produces clears this, and a real detector must clear the same bar the
# mock already sets.
VISION_MIN_CONFIDENCE = 0.85

# ---------------------------------------------------------------------------
# Bias shaping. Ranges are IMPORTED from control_api, so a directive can
# never be out of range by construction (see boost_weight/suppress_weight).
# ---------------------------------------------------------------------------
BIAS_NEUTRAL_WEIGHT = 1.0
BIAS_MIN_WEIGHT, BIAS_MAX_WEIGHT = LANE_BIAS_WEIGHT_RANGE        # (0.1, 10.0)
DURATION_MIN_S, DURATION_MAX_S = LANE_BIAS_DURATION_RANGE_S      # (10.0, 900.0)
# 10.0 is both control_api's minimum duration AND MIN_GREEN_S, so a
# neutralising re-issue covers exactly one green decision and then ages out
# of sim_runner._expire_windows() on its own.
NEUTRALISE_DURATION_S = DURATION_MIN_S
assert NEUTRALISE_DURATION_S == MIN_GREEN_S, "neutralise window != one green"
# The width of the soft band §10 deliberately left open — a fairness
# response lasts exactly as long as the band it corrects.
FAIRNESS_DURATION_S = CEILING_WAIT_S - FAIRNESS_WAIT_S           # 30.0
CONGESTION_DURATION_S = 60.0   # == prediction.spillover.DEFAULT_HORIZON_S
# Re-issue this early so no gap opens between an expiring response and its
# replacement.
REISSUE_MARGIN_S = MIN_GREEN_S
WEIGHT_ROUND_DP = 3            # matches prediction/spillover.py's rounding

# HARDENING: `TickResult.dropped_inputs` is diagnostic text built from
# CALLER-SUPPLIED records, and from Part 4c it rides the §13.2 WebSocket
# frame. An oversized or hostile vision/forecast feed would otherwise let an
# upstream producer inflate every frame without bound. The list is capped and
# the overflow reported as a single count, so the diagnostic survives but the
# wire exposure does not scale with the input. Same spirit as
# control_api's dynamic `affected_lanes` cap.
MAX_DROPPED_REPORTED = 32


class IncidentPriorityError(RuntimeError):
    """Raised on a backwards `sim_time` — see `IncidentPriorityAgent.tick`."""


@dataclass(frozen=True)
class PriorityConfig:
    """Classification thresholds, defaulting to the module constants."""

    min_starved_lanes: int = CONGESTION_MIN_STARVED_LANES
    spillover_delta_veh: float = CONGESTION_SPILLOVER_DELTA_VEH
    min_spillover_confidence: float = CONGESTION_MIN_CONFIDENCE
    vision_min_confidence: float = VISION_MIN_CONFIDENCE
    # OFF by default: it writes into the twin's §7.3 registry and would
    # double-count against an operator's own inject_incident.
    report_vision_incidents: bool = False


DEFAULT_CONFIG = PriorityConfig()


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    """One classified situation. `event_id` is CONTENT-STABLE across ticks,
    which is what lets the response registry match without fuzzy logic."""

    event_id: str
    event_class: str
    junction_id: str
    lane_id: str
    severity: str
    severity_value: float
    urgency: float
    source: str
    detected_at_sim_time: float
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "event_class": self.event_class,
            "junction_id": self.junction_id, "lane_id": self.lane_id,
            "severity": self.severity, "severity_value": self.severity_value,
            "urgency": round(self.urgency, 4), "source": self.source,
            "detected_at_sim_time": self.detected_at_sim_time,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class Directive:
    """A PROPOSED control-API call. `function` is always on the allowlist."""

    function: str
    args: dict
    event_id: str
    event_class: str
    rank: int
    rationale: str

    def to_dict(self) -> dict:
        return {"function": self.function, "args": dict(self.args),
                "event_id": self.event_id, "event_class": self.event_class,
                "rank": self.rank, "rationale": self.rationale}


@dataclass(frozen=True)
class ActiveResponse:
    event_id: str
    event_class: str
    lane_id: str
    junction_id: str
    directive: Directive
    severity_value: float
    issued_at_sim_time: float
    expires_at_sim_time: float
    status: str
    preempted_by: str | None = None

    def to_dict(self) -> dict:
        return {"event_id": self.event_id, "event_class": self.event_class,
                "lane_id": self.lane_id, "junction_id": self.junction_id,
                "status": self.status, "preempted_by": self.preempted_by,
                "issued_at_sim_time": self.issued_at_sim_time,
                "expires_at_sim_time": self.expires_at_sim_time}


@dataclass(frozen=True)
class TickResult:
    sim_time: float
    events: tuple[Event, ...]
    directives: tuple[Directive, ...]
    preempted: tuple[str, ...]
    suppressed: tuple[str, ...]
    dropped_inputs: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"sim_time": self.sim_time,
                "events": [e.to_dict() for e in self.events],
                "directives": [d.to_dict() for d in self.directives],
                "preempted": list(self.preempted),
                "suppressed": list(self.suppressed),
                "dropped_inputs": list(self.dropped_inputs)}


# ---------------------------------------------------------------------------
# Bias shaping — provably in range for severity_value in [0, 1]
# ---------------------------------------------------------------------------
def boost_weight(severity_value: float) -> float:
    """Neutral -> BIAS_MAX_WEIGHT as severity goes 0 -> 1."""
    span = BIAS_MAX_WEIGHT - BIAS_NEUTRAL_WEIGHT
    return _clamp_weight(round(BIAS_NEUTRAL_WEIGHT + severity_value * span,
                               WEIGHT_ROUND_DP))


def suppress_weight(severity_value: float) -> float:
    """Neutral -> BIAS_MIN_WEIGHT as severity goes 0 -> 1. Floors at the
    minimum, never zero: the lane keeps minimum service and §10 still
    protects it if it backs up (honest boundary 3)."""
    span = BIAS_NEUTRAL_WEIGHT - BIAS_MIN_WEIGHT
    return _clamp_weight(round(BIAS_NEUTRAL_WEIGHT - severity_value * span,
                               WEIGHT_ROUND_DP))


def _clamp_weight(w: float) -> float:
    return min(BIAS_MAX_WEIGHT, max(BIAS_MIN_WEIGHT, w))


def _clamp_duration(d: float) -> float:
    if not (isinstance(d, (int, float)) and math.isfinite(d)):
        return DURATION_MIN_S
    return min(DURATION_MAX_S, max(DURATION_MIN_S, float(d)))


def _urgency(wait_max_s: float) -> float:
    """The ONE urgency signal, uniform across all four classes: how far the
    worst wait on this event's own lanes has travelled toward §10's ceiling.

    Grounded in an existing per-lane field (`wait_time_max_single_vehicle`,
    §7.1) normalised by an existing constant (`CEILING_WAIT_S`). Deliberately
    NOT a per-class invented scalar — a class-specific formula would make
    `urgency` incomparable between classes while sitting in a shared sort
    key. Left unclamped above 1.0: a wait CAN exceed the ceiling (§10.1
    measures 124-141s), and the ratio stays monotone in the real signal.
    """
    try:
        value = float(wait_max_s)
    except (TypeError, ValueError):
        return 0.0
    return (max(0.0, value) / CEILING_WAIT_S) if math.isfinite(value) else 0.0


# ---------------------------------------------------------------------------
# Arbitration
# ---------------------------------------------------------------------------
def _priority_key(event: Event) -> tuple:
    return (
        CLASS_RANK[event.event_class],          # 1. the policy
        -event.severity_value,                  # 2. severity desc
        -event.urgency,                         # 3. urgency desc
        _JUNCTION_INDEX.get(event.junction_id, len(CORRIDOR_ORDER)),
        event.lane_id,                          # 5. TOTAL order
    )


def arbitrate(events: Iterable[Event]) -> tuple[Event, ...]:
    """Order events by the priority policy. Pure; returns a new tuple.

    The trailing `lane_id` term is load-bearing: without it, sort stability
    would leak the classifier's iteration order into the result, and an
    ordering assertion could pass by accident rather than by policy.
    """
    return tuple(sorted(events, key=_priority_key))
