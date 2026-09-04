"""Shared vocabulary for the orchestrator blackboard (Part 4b).

The six named agents, the entry shape that rides the §13.2 WebSocket frame as
`agent_activity`, and the immutable context every wrapper reads from.

WHAT THIS PACKAGE IS: six named VIEWS over modules that already ran. It
observes and records "who said what" for the demo panel. It computes nothing,
decides nothing, and has no path to the road — see `orchestrator/bus.py` for
the full boundary statement.

No SUMO / torch / numpy / random import anywhere in this package, asserted by
the self-test (W7). That is what lets the whole blackboard be exercised
against a synthetic §7.6 snapshot with no simulator running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, NamedTuple

# ---------------------------------------------------------------------------
# The roster. Pinned as a tuple so a wrapper silently dropped from the
# registry fails a check (W1) rather than quietly shrinking the panel.
# ---------------------------------------------------------------------------
AGENT_DETECTION = "Detection"
AGENT_VISION = "Vision"
AGENT_PREDICTION = "Prediction"
AGENT_INCIDENT_PRIORITY = "IncidentPriority"
AGENT_CONTROL = "Control"
AGENT_SUPERVISOR = "Supervisor"
AGENT_NAMES = (
    AGENT_DETECTION, AGENT_VISION, AGENT_PREDICTION,
    AGENT_INCIDENT_PRIORITY, AGENT_CONTROL, AGENT_SUPERVISOR,
)

# `kind` is load-bearing, not decoration: it is what makes the veto assertion
# in the self-test machine-checkable. KIND_IDLE exists so every agent emits at
# least one line every round — "each agent ticked and published" becomes a
# per-round invariant instead of something you hope for, and the panel always
# shows six live rows.
KIND_OBSERVATION = "observation"
KIND_FORECAST = "forecast"
KIND_ARBITRATION = "arbitration"
KIND_ACTION = "action"
KIND_VETO = "veto"
KIND_IDLE = "idle"
KINDS = (KIND_OBSERVATION, KIND_FORECAST, KIND_ARBITRATION, KIND_ACTION,
         KIND_VETO, KIND_IDLE)

# The wire shape, pinned as a frozenset the way sim/run_shadow_advisor_check.py
# pins SHADOW_KEYS — a silent field rename then fails a check rather than a
# frontend. Part 3's documented minimum {agent, said, at} is a strict subset,
# so a naive consumer keeps working.
ENTRY_KEYS = frozenset({
    "agent", "role", "wraps", "kind", "said", "at", "step", "detail",
})

# ---------------------------------------------------------------------------
# SIZE BOUNDS. `agent_activity` rides every frame, and one of its producers
# (IncidentPriority) consumes a caller-supplied feed, so the bound has to be
# structural rather than expected. Same hardening as
# agents/incident_types.py's MAX_DROPPED_REPORTED.
# ---------------------------------------------------------------------------
SAID_MAX_CHARS = 240
ROLE_MAX_CHARS = 160
DETAIL_MAX_KEYS = 8
DETAIL_VALUE_MAX_CHARS = 120
# >= 3: one OverrideRecord per junction is the most one step can produce on
# the locked 3-junction corridor (§0.1).
MAX_ENTRIES_PER_TICK = 4
# 6 agents x ~1.4KB worst case ~= 9KB, against a digital_twin field already in
# the tens of KB. The budget is asserted, not assumed (W3).
PER_FRAME_BYTE_BUDGET = 16_384

# One episode is at most episode_horizon_s / DECISION_INTERVAL_S = 3600/5 =
# 720 rounds x 6 agents = 4,320 entries, plus up to 3 extra Supervisor vetoes
# per round -> ~6,480 worst realistic. 12,000 is ~2x headroom. Same framing as
# sim_runner._LOG_MAXLEN: a memory bound for a demo left running, not a policy.
BLACKBOARD_MAXLEN = 12_000

# Formatting/selection constants. Named here rather than written as literals
# in wrappers.py, because W7 AST-scans that file for numeric literals as the
# tripwire for "a wrapper started computing something".
TOP_N_REPORTED = 2
WAIT_DECIMALS = 1
CONFIDENCE_DECIMALS = 2
DELTA_DECIMALS = 1


class LaneRow(NamedTuple):
    """One sensed lane, with its junction. Named rather than a bare tuple so
    `wrappers.py` never needs a positional index — the self-test (W7) treats
    any numeric literal in that file as a threshold until proven otherwise."""

    junction_id: str
    lane_id: str
    reading: Mapping


class BlackboardError(RuntimeError):
    """Raised on a malformed publish or a backwards `at`."""


def _clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _coerce_detail(detail: Mapping | None) -> dict:
    """Flat JSON scalars only, bounded in keys and value length.

    No nested containers — that restriction is what makes the per-frame size
    bound provable rather than typical.
    """
    if not isinstance(detail, Mapping):
        return {}
    out: dict = {}
    for key in list(detail)[:DETAIL_MAX_KEYS]:
        value = detail[key]
        if isinstance(value, str):
            out[str(key)] = _clip(value, DETAIL_VALUE_MAX_CHARS)
        elif isinstance(value, bool) or value is None:
            out[str(key)] = value
        elif isinstance(value, (int, float)):
            out[str(key)] = value
        else:
            out[str(key)] = _clip(repr(value), DETAIL_VALUE_MAX_CHARS)
    return out


@dataclass(frozen=True)
class AgentEntry:
    """One line on the blackboard — and one `agent_activity` element."""

    agent: str
    role: str
    wraps: str
    kind: str
    said: str
    at: float
    step: int
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Frozen dataclass: coerce at the boundary via object.__setattr__ so
        # the bounds hold by construction rather than by every caller
        # remembering to clip.
        object.__setattr__(self, "said", _clip(self.said, SAID_MAX_CHARS))
        object.__setattr__(self, "role", _clip(self.role, ROLE_MAX_CHARS))
        object.__setattr__(self, "detail", _coerce_detail(self.detail))

    def to_dict(self) -> dict:
        return {"agent": self.agent, "role": self.role, "wraps": self.wraps,
                "kind": self.kind, "said": self.said, "at": self.at,
                "step": self.step, "detail": dict(self.detail)}


@dataclass(frozen=True)
class AgentContext:
    """Everything a wrapper may read, for ONE round.

    This is NOT a handle to the system — it is a bundle of artifacts the sim
    thread already had in hand at the call site. Every field references an
    object some other module already produced, which is what lets one uniform
    interface serve six different appetites while no wrapper computes
    anything: a wrapper SELECTS, it never fetches or derives.

    `pre_snapshot` is the PRE-step §7.6 snapshot — the state the decision was
    made from and the state §10's validator judged it against. Handing a
    wrapper the POST-step snapshot instead would fail SILENTLY (lane ids exist
    in both, every field still populates) and Detection would be reporting the
    consequence of the very decision Control claims, one line below, to have
    made from it. See backend/sim_runner.py's two-snapshots docstring.
    """

    step: int
    sim_time: float
    pre_snapshot: Mapping
    info: Mapping
    decisions: Mapping
    predictions: Mapping
    forced_emergency_lanes: frozenset
    mode: str
    #: §7.2 observations reshaped as ADVISORY events (NOTES §9.2). Defaulted so
    #: every existing construction site keeps working; empty when the vision
    #: source produced nothing. Carries `emergency_vehicle_flag`, never
    #: `emergency` — see backend/vision_alerts.py.
    vision_events: tuple = ()

    def readonly(self) -> "AgentContext":
        """A copy whose two big mappings reject a top-level write.

        HONEST LIMIT: MappingProxyType is SHALLOW. It stops
        `ctx.info["safety_overrides"] = []`; it does not stop
        `ctx.info["safety_overrides"].append(...)`. The self-test's
        deepcopy-equality check (W5) is what covers the rest.
        """
        return AgentContext(
            step=self.step, sim_time=self.sim_time,
            pre_snapshot=MappingProxyType(dict(self.pre_snapshot)),
            info=MappingProxyType(dict(self.info)),
            decisions=self.decisions, predictions=self.predictions,
            forced_emergency_lanes=self.forced_emergency_lanes, mode=self.mode,
            vision_events=self.vision_events,
        )
