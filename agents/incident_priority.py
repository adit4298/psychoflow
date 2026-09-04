"""Incident & Priority agent (Part 4a) — aggregate, classify, arbitrate, propose.

It reads what other modules already produced — §7.1 lane readings, §7.3
reported incidents, §8.1 spillover forecasts, §8.2 incident impacts, an
optional vision feed, and the operator's own `trigger_emergency` set — turns
each into a classified `Event`, orders them under ONE explicit priority
policy, and emits `Directive`s.

    PRIORITY POLICY (constraint 2, and the whole point of the module):
        emergency > accident > major_congestion > fairness

===========================================================================
HONEST BOUNDARIES (§17 class) — read before quoting this agent to anyone
===========================================================================

1.  **`set_lane_bias` is INERT under `mode="auto"`.** The RL policy has no
    per-lane score, so three of the four classes (accident, congestion,
    fairness) are recorded and echoed but have NO effect while the trained
    policy is driving — `control_api.set_lane_bias` says exactly this in its
    own return `note`, and this agent mirrors that behaviour rather than
    engineering around it. `trigger_emergency` works in BOTH modes, so the
    emergency class is fully functional either way. The incident-priority
    beats are demonstrated in manual / Tier 0 mode. There is deliberately NO
    `force_phase` auto-mode fallback.
2.  **It proposes; it never actuates.** Every directive names a function on
    `control_api.CONTROL_FUNCTIONS` and is applied by the caller through
    `control_api.dispatch()`, after which §10's validator is still the sole
    gate to the road. This module makes no TraCI call, never touches
    `env.step()`, and contains no reference to `enable_safety_validator`.
    Returning a *description* of an intervention rather than performing one
    is what makes that structural rather than a convention (see `apply`).
3.  **An accident SUPPRESSES the blocked lane rather than boosting it.**
    §9.1 scores `0.6*halted_count + 0.4*wait_time_current`; a blocked lane
    accumulates BOTH terms while being physically unable to discharge, so
    the Tier 0 scorer would over-serve it and burn green time on a queue
    that cannot move. De-prioritising corrects a real failure mode of the
    actual scoring formula. The weight FLOORS at `BIAS_MIN_WEIGHT` (0.1),
    never zero — the lane keeps minimum service, and if it does back up,
    §10's starvation ceiling still protects it exactly as it protects any
    other lane. Boosting an ALTERNATE lane to route around the blockage is
    a separate policy call and is deliberately not built.
4.  **It cannot cancel a bias — there is no `clear_lane_bias`.** Preemption
    is a neutralising re-issue at `BIAS_NEUTRAL_WEIGHT` plus a registry
    mark. That works ONLY because `sim_runner._apply_command` does
    `self._bias[lane_id] = (weight, expiry)` — LAST WRITE WINS, keyed by
    lane. If `_bias` ever becomes additive, preemption silently stops
    working. Stated here because it is a non-obvious cross-module coupling.
5.  **The priority order is a fixed ordinal policy** — not learned, not
    tuned against measured outcomes. The claim is that it is explicit,
    total, deterministic and testable; not that it is optimal.
6.  **The congestion/fairness thresholds are re-derived from constants that
    already exist in this repo, not calibrated against a distribution** the
    way `Tier0Config.starvation_bonus_scale` was. Do not present them as
    tuned numbers.
7.  **It detects nothing.** Every event is classified from data another
    module produced. "Incident agent" means it *reasons about* incidents.
8.  **No lane closures** (§17's standing boundary). `inject_incident`
    REPORTS a blockage; nothing here closes one.
9.  **Single corridor, 3 junctions, linear.** The junction tiebreak assumes
    J1 -> J2 -> J3 (§0.1), the same assumption `CORRIDOR_ADJACENCY` makes.
10. **Not thread-safe.** Plain dicts; tick from one thread — the same
    discipline `DecisionLog` follows.

===========================================================================
NO SUMO / TORCH / NUMPY IMPORT — deliberate
===========================================================================
This module stays importable in a voice-only or offline context, exactly as
`backend/control_api.py` does. Consequence: four constants are duplicated as
literals rather than imported, because their home modules pull in sumolib or
traci (`perception.lane_sensor`, `safety.validator`, `env.psychoflow_env`).
The self-test carries a drift guard that imports and compares them WHEN SUMO
happens to be importable, and skips silently when it is not — so the
duplication is checked wherever it can be, and never blocks an offline run.
`prediction.spillover` / `prediction.incident_impact` are not SUMO-free
either, which is why forecasts and impacts arrive as ARGUMENTS rather than
being computed here.

Done-bar:  python -m agents.incident_priority
Unit tests: python -m tests.test_incident_priority
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

# control_api is deliberately SUMO-free (its own docstring says so), so
# importing the bounds here costs nothing and is what makes it STRUCTURALLY
# impossible for this agent to emit a directive control_api would reject on
# range. Not a layering inversion: this is the contract, not the backend.
from backend.control_api import (
    CONTROL_FUNCTIONS,
    EMERGENCY_HOLD_S,
    LANE_BIAS_DURATION_RANGE_S,
    LANE_BIAS_WEIGHT_RANGE,
)
from perception.incident_intake import SEVERITIES, SEVERITY_VALUE

# The shared vocabulary lives one layer down (see agents/incident_types.py) so
# both files stay inside the 800-line ceiling. Re-exported here so
# `agents.incident_priority` remains the single public import surface — no
# caller needs to know the split exists.
from agents.incident_types import (  # noqa: F401
    BIAS_MAX_WEIGHT, BIAS_MIN_WEIGHT, BIAS_NEUTRAL_WEIGHT, CEILING_WAIT_S,
    CLASS_RANK, CONGESTION_DURATION_S, CONGESTION_MIN_CONFIDENCE,
    CONGESTION_MIN_STARVED_LANES, CONGESTION_SPILLOVER_DELTA_VEH,
    CORRIDOR_ORDER, DEFAULT_CONFIG, DURATION_MAX_S, DURATION_MIN_S,
    EVENT_ACCIDENT, EVENT_CLASSES, EVENT_EMERGENCY, EVENT_FAIRNESS,
    EVENT_MAJOR_CONGESTION, FAIRNESS_DURATION_S, FAIRNESS_WAIT_S,
    MAX_DROPPED_REPORTED, MIN_GREEN_S, NEUTRALISE_DURATION_S,
    REISSUE_MARGIN_S, SOURCE_INTAKE, SOURCE_OPERATOR, SOURCE_SENSOR,
    SOURCE_VISION, STATUS_ACTIVE, STATUS_PENDING, STATUS_PREEMPTED,
    VISION_MIN_CONFIDENCE, WEIGHT_ROUND_DP, _JUNCTION_INDEX, _clamp_duration,
    _clamp_weight, _priority_key, _urgency, ActiveResponse, Directive, Event,
    IncidentPriorityError, PriorityConfig, TickResult, arbitrate,
    boost_weight, suppress_weight,
)



# ---------------------------------------------------------------------------
# Snapshot helpers — all read-only, all tolerant of a missing key
# ---------------------------------------------------------------------------
def _junctions(snapshot: Mapping) -> dict[str, dict]:
    junctions = snapshot.get("junctions") or {}
    return junctions if isinstance(junctions, Mapping) else {}


def _lanes_of(snapshot: Mapping, junction_id: str) -> dict[str, dict]:
    lanes = (_junctions(snapshot).get(junction_id) or {}).get("lanes") or {}
    return lanes if isinstance(lanes, Mapping) else {}


def _all_lane_ids(snapshot: Mapping) -> dict[str, str]:
    """lane_id -> junction_id, across the whole corridor."""
    return {lane_id: jid
            for jid in _junctions(snapshot)
            for lane_id in _lanes_of(snapshot, jid)}


def _wait_max(reading: Mapping) -> float:
    try:
        value = float(reading.get("wait_time_max_single_vehicle", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _halted(reading: Mapping) -> float:
    try:
        value = float(reading.get("halted_count", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _starved_lanes(snapshot: Mapping, junction_id: str) -> list[str]:
    return sorted(
        lane_id for lane_id, r in _lanes_of(snapshot, junction_id).items()
        if isinstance(r, Mapping) and bool(r.get("starvation_flag"))
    )


def _busiest_lane(snapshot: Mapping, junction_id: str) -> str | None:
    """The junction's max-`halted_count` lane — the one a boost should target.

    `lane_id` breaks a tie so the choice is deterministic rather than
    dict-order dependent, for the same reason `_priority_key` carries one.
    """
    lanes = _lanes_of(snapshot, junction_id)
    if not lanes:
        return None
    return max(lanes, key=lambda lid: (_halted(lanes[lid]), lid))


def _worst_wait_lane(snapshot: Mapping, lane_ids: Sequence[str]) -> float:
    known = _all_lane_ids(snapshot)
    waits = [
        _wait_max(_lanes_of(snapshot, known[lid])[lid])
        for lid in lane_ids if lid in known
    ]
    return max(waits) if waits else 0.0


# ---------------------------------------------------------------------------
# Classifiers — one per class, each independently testable
# ---------------------------------------------------------------------------
def _emergency_lanes(snapshot: Mapping, forced: frozenset[str],
                     vision: dict[str, str]) -> dict[str, str]:
    """lane_id -> source. DETECTION WINS over an operator force, matching
    §11.2's `trigger_source` provenance rule."""
    found: dict[str, str] = {}
    for jid in _junctions(snapshot):
        for lane_id, r in _lanes_of(snapshot, jid).items():
            if not isinstance(r, Mapping):
                continue
            composition = r.get("type_composition") or {}
            if isinstance(composition, Mapping) and composition.get("ambulance"):
                found[lane_id] = SOURCE_SENSOR
    known = _all_lane_ids(snapshot)
    for lane_id, source in vision.items():
        found.setdefault(lane_id, source)
    for lane_id in forced:
        if lane_id in known:
            found.setdefault(lane_id, SOURCE_OPERATOR)
    return found


def _classify_emergency(snapshot: Mapping, sim_time: float,
                        forced: frozenset[str],
                        vision: dict[str, str]) -> list[Event]:
    known = _all_lane_ids(snapshot)
    events = []
    for lane_id, source in sorted(_emergency_lanes(snapshot, forced, vision).items()):
        jid = known.get(lane_id)
        if jid is None:
            continue
        wait = _wait_max(_lanes_of(snapshot, jid)[lane_id])
        events.append(Event(
            event_id=f"{EVENT_EMERGENCY}:{lane_id}",
            event_class=EVENT_EMERGENCY, junction_id=jid, lane_id=lane_id,
            # An ambulance is not partly urgent. §10 treats the override as
            # unconditional ("cannot be delayed/blocked/deprioritized"),
            # pinned by validator unit scenario 7.
            severity="high", severity_value=SEVERITY_VALUE["high"],
            urgency=_urgency(wait), source=source,
            detected_at_sim_time=sim_time,
            evidence={"wait_time_max_single_vehicle": wait,
                      "already_forced": lane_id in forced},
        ))
    return events


def _classify_accident(snapshot: Mapping, sim_time: float,
                       impacts: dict[str, Mapping]) -> list[Event]:
    """Every active §7.3 incident, whatever its `type`.

    §7.3's `type` enum describes the CAUSE; `severity` is the urgency
    channel and is already the field `SEVERITY_VALUE` maps. Demoting
    `roadworks` to a lower class would invent a policy the plan does not
    have, so all three INCIDENT_TYPES classify here and are separated by
    their own recorded severity.
    """
    events = []
    for incident in snapshot.get("active_incidents") or []:
        if not isinstance(incident, Mapping):
            continue
        severity = incident.get("severity")
        location = incident.get("location") or {}
        jid = location.get("junction_id")
        lane_id = location.get("lane_id")
        if severity not in SEVERITIES or jid is None or lane_id is None:
            continue
        affected = [str(l) for l in (incident.get("affected_lanes") or [])]
        incident_id = str(incident.get("incident_id", lane_id))
        impact = impacts.get(incident_id)
        events.append(Event(
            event_id=f"{EVENT_ACCIDENT}:{incident_id}",
            event_class=EVENT_ACCIDENT, junction_id=str(jid),
            lane_id=str(lane_id), severity=severity,
            severity_value=SEVERITY_VALUE[severity],
            urgency=_urgency(_worst_wait_lane(snapshot, affected or [lane_id])),
            source=SOURCE_INTAKE, detected_at_sim_time=sim_time,
            evidence={"incident_id": incident_id,
                      "type": incident.get("type"),
                      "affected_lanes": affected,
                      "estimated_duration_s": incident.get("estimated_duration_s"),
                      "impact": dict(impact) if impact else None},
        ))
    return events


def _congestion_severity(starved: Sequence[str], worst_wait: float,
                         config: PriorityConfig) -> str:
    """A 3-band ladder: predicted < observed multi-lane < ceiling crossed.
    Makes a FORECAST strictly lower priority than a thing already happening."""
    if worst_wait >= CEILING_WAIT_S:
        return "high"
    if len(starved) >= config.min_starved_lanes:
        return "medium"
    return "low"


def _classify_congestion(snapshot: Mapping, sim_time: float,
                         forecasts: dict[str, Mapping],
                         config: PriorityConfig) -> list[Event]:
    events = []
    for jid in sorted(_junctions(snapshot)):
        lanes = _lanes_of(snapshot, jid)
        if not lanes:
            continue
        starved = _starved_lanes(snapshot, jid)
        forecast = forecasts.get(jid)
        multi_lane = len(starved) >= config.min_starved_lanes
        if not (multi_lane or forecast):
            continue
        target = _busiest_lane(snapshot, jid)
        if target is None:
            continue
        worst = max((_wait_max(r) for r in lanes.values()
                     if isinstance(r, Mapping)), default=0.0)
        severity = _congestion_severity(starved, worst, config)
        events.append(Event(
            event_id=f"{EVENT_MAJOR_CONGESTION}:{jid}",
            event_class=EVENT_MAJOR_CONGESTION, junction_id=jid,
            lane_id=target, severity=severity,
            severity_value=SEVERITY_VALUE[severity], urgency=_urgency(worst),
            source=SOURCE_SENSOR, detected_at_sim_time=sim_time,
            evidence={"starved_lanes": starved, "worst_wait_s": worst,
                      "forecast": dict(forecast) if forecast else None},
        ))
    return events


def _classify_fairness(snapshot: Mapping, sim_time: float,
                       config: PriorityConfig) -> list[Event]:
    """ONE lane held too long, inside the band §10 deliberately left open.

    The lower edge is read from the published `starvation_flag` rather than
    compared against a literal 90.0, so this can never drift from the
    sensor's own threshold. The upper edge is `CEILING_WAIT_S`: above it §10
    has taken the junction, and biasing there would be this agent competing
    with the sole gate.
    """
    events = []
    for jid in sorted(_junctions(snapshot)):
        starved = _starved_lanes(snapshot, jid)
        # >= min_starved_lanes is major_congestion's territory, not this one.
        if not starved or len(starved) >= config.min_starved_lanes:
            continue
        lanes = _lanes_of(snapshot, jid)
        lane_id = max(starved, key=lambda lid: (_wait_max(lanes[lid]), lid))
        wait = _wait_max(lanes[lane_id])
        if wait >= CEILING_WAIT_S:
            continue
        events.append(Event(
            event_id=f"{EVENT_FAIRNESS}:{lane_id}",
            event_class=EVENT_FAIRNESS, junction_id=jid, lane_id=lane_id,
            severity="low", severity_value=SEVERITY_VALUE["low"],
            urgency=_urgency(wait), source=SOURCE_SENSOR,
            detected_at_sim_time=sim_time,
            evidence={"wait_time_max_single_vehicle": wait,
                      "band": [FAIRNESS_WAIT_S, CEILING_WAIT_S]},
        ))
    return events


# ---------------------------------------------------------------------------
# Boundary validation — bad input is DROPPED with a reason, never raised on
# ---------------------------------------------------------------------------
def _validate_forecasts(spillover, snapshot: Mapping, config: PriorityConfig,
                        dropped: list[str]) -> dict[str, Mapping]:
    """§8.1 forecasts, keyed by `to_junction`, filtered to the material ones."""
    out: dict[str, Mapping] = {}
    known = set(_junctions(snapshot))
    for raw in spillover or []:
        if not isinstance(raw, Mapping):
            dropped.append(f"forecast: not a mapping ({type(raw).__name__})")
            continue
        jid = raw.get("to_junction")
        if jid not in known:
            dropped.append(f"forecast: unknown to_junction {jid!r}")
            continue
        try:
            delta = float(raw.get("predicted_queue_delta"))
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError):
            dropped.append(f"forecast {jid}: non-numeric delta/confidence")
            continue
        if not (math.isfinite(delta) and math.isfinite(confidence)):
            dropped.append(f"forecast {jid}: non-finite delta/confidence")
            continue
        if delta < config.spillover_delta_veh:
            continue    # immaterial, not malformed — no `dropped` entry
        if confidence <= config.min_spillover_confidence:
            continue    # cold start; see CONGESTION_MIN_CONFIDENCE
        out[str(jid)] = raw
    return out


def _validate_vision(vision_events, snapshot: Mapping, config: PriorityConfig,
                     dropped: list[str]) -> dict[str, str]:
    """Track A's feed -> {lane_id: SOURCE_VISION} for emergency claims only.

    The agent NEVER imports Track A; it takes a list of dicts. With
    `vision_events=None` it behaves exactly as if Track A does not exist,
    and the emergency class stays fully functional from §7.1's
    `type_composition` plus `forced_emergency_lanes` alone.
    """
    out: dict[str, str] = {}
    known = _all_lane_ids(snapshot)
    for raw in vision_events or []:
        if not isinstance(raw, Mapping):
            dropped.append(f"vision: not a mapping ({type(raw).__name__})")
            continue
        lane_id = raw.get("lane_id")
        if lane_id not in known:
            dropped.append(f"vision: unknown lane_id {lane_id!r}")
            continue
        composition = raw.get("type_composition") or {}
        claims = bool(raw.get("emergency")) or bool(
            isinstance(composition, Mapping) and composition.get("ambulance")
        )
        if not claims:
            continue    # a well-formed non-emergency record; nothing to do
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError):
            dropped.append(f"vision {lane_id}: emergency claim without a "
                           f"usable confidence")
            continue
        if not math.isfinite(confidence) or confidence < config.vision_min_confidence:
            dropped.append(f"vision {lane_id}: confidence {confidence} below "
                           f"{config.vision_min_confidence}")
            continue
        out[str(lane_id)] = SOURCE_VISION
    return out


def _cap_dropped(dropped: Sequence[str]) -> tuple[str, ...]:
    """Bound the diagnostic list — see MAX_DROPPED_REPORTED."""
    if len(dropped) <= MAX_DROPPED_REPORTED:
        return tuple(dropped)
    hidden = len(dropped) - MAX_DROPPED_REPORTED
    return (*dropped[:MAX_DROPPED_REPORTED],
            f"... and {hidden} further dropped input(s) not reported")


def _validate_impacts(impacts, dropped: list[str]) -> dict[str, Mapping]:
    out: dict[str, Mapping] = {}
    for raw in impacts or []:
        if not isinstance(raw, Mapping) or "incident_id" not in raw:
            dropped.append("impact: not a mapping / no incident_id")
            continue
        out[str(raw["incident_id"])] = raw
    return out


# ---------------------------------------------------------------------------
# Directives
# ---------------------------------------------------------------------------
def _bias(lane_id: str, weight: float, duration_s: float, event: Event,
          rationale: str) -> Directive:
    return Directive(
        function="set_lane_bias",
        args={"lane_id": lane_id, "weight": _clamp_weight(weight),
              "duration_s": _clamp_duration(duration_s)},
        event_id=event.event_id, event_class=event.event_class,
        rank=CLASS_RANK[event.event_class], rationale=rationale,
    )


def _accident_duration(event: Event) -> float:
    impact = event.evidence.get("impact")
    if isinstance(impact, Mapping) and impact.get("horizon_s") is not None:
        return _clamp_duration(impact["horizon_s"])
    return _clamp_duration(event.evidence.get("estimated_duration_s")
                           or CONGESTION_DURATION_S)


def _congestion_duration(event: Event) -> float:
    forecast = event.evidence.get("forecast")
    if isinstance(forecast, Mapping) and forecast.get("horizon_s") is not None:
        return _clamp_duration(forecast["horizon_s"])
    return CONGESTION_DURATION_S


def _directive_for(event: Event, forced: frozenset[str]) -> Directive | None:
    """The one control-API call this event asks for, or None."""
    if event.event_class == EVENT_EMERGENCY:
        if event.lane_id in forced:
            return None     # already forced; a redundant re-force is noise
        return Directive(
            function="trigger_emergency", args={"lane_id": event.lane_id},
            event_id=event.event_id, event_class=event.event_class,
            rank=CLASS_RANK[event.event_class],
            rationale=(f"Emergency vehicle on {event.lane_id} "
                       f"({event.source}) — forcing §10's override."),
        )
    if event.event_class == EVENT_ACCIDENT:
        return _bias(
            event.lane_id, suppress_weight(event.severity_value),
            _accident_duration(event), event,
            rationale=(f"{event.evidence.get('type')} at {event.junction_id} "
                       f"(severity {event.severity}) — de-prioritising the "
                       f"blocked lane, which cannot discharge if served."),
        )
    if event.event_class == EVENT_MAJOR_CONGESTION:
        return _bias(
            event.lane_id, boost_weight(event.severity_value),
            _congestion_duration(event), event,
            rationale=(f"{event.junction_id} oversubscribed "
                       f"({len(event.evidence.get('starved_lanes') or [])} "
                       f"starved lanes, worst wait "
                       f"{event.evidence.get('worst_wait_s', 0.0):.1f}s) — "
                       f"boosting its busiest lane."),
        )
    if event.event_class == EVENT_FAIRNESS:
        return _bias(
            event.lane_id, boost_weight(event.severity_value),
            FAIRNESS_DURATION_S, event,
            rationale=(f"{event.lane_id} starved at "
                       f"{event.evidence.get('wait_time_max_single_vehicle', 0.0):.1f}s, "
                       f"inside the [{FAIRNESS_WAIT_S:.0f}, "
                       f"{CEILING_WAIT_S:.0f}) soft band — boosting before "
                       f"§10's ceiling has to."),
        )
    return None


def _neutraliser(response: ActiveResponse, by_event_id: str) -> Directive:
    """Revoke an in-flight bias by re-issuing it at neutral weight.

    Depends on `sim_runner._bias[lane_id] = (weight, expiry)` being LAST
    WRITE WINS — see honest boundary 4.
    """
    return Directive(
        function="set_lane_bias",
        args={"lane_id": response.lane_id, "weight": BIAS_NEUTRAL_WEIGHT,
              "duration_s": NEUTRALISE_DURATION_S},
        event_id=response.event_id, event_class=response.event_class,
        rank=CLASS_RANK[response.event_class],
        rationale=(f"Neutralising the {response.event_class} bias on "
                   f"{response.lane_id} — preempted by {by_event_id}."),
    )


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
class IncidentPriorityAgent:
    """Stateful across ticks; ONE instance per EPISODE.

    EPISODE-BOUNDARY RULE — replace the instance, never carry it across a
    reset. `env.reset()` sends sim_time back to ~0, so a carried-over
    registry holds expiry deadlines that are ALL in the future relative to a
    t~0 clock: every response reads as permanently active and the agent
    emits NOTHING for an entire episode, silently, while passing every
    smoke test. That is exactly CLAUDE.md's "a verification run that passes
    while proving nothing" failure class, so `tick()` RAISES on a backwards
    sim_time rather than absorbing it — the same refusal `DecisionLog` makes
    and for the same reason. `backend/sim_runner.py` replaces this instance
    wherever `_reset_counters()` replaces `self._log`.
    """

    def __init__(self, config: PriorityConfig = DEFAULT_CONFIG) -> None:
        self.config = config
        self._active: dict[str, ActiveResponse] = {}
        self._last_sim_time: float | None = None

    def reset(self) -> None:
        """Rebind the registry for a new episode (SpilloverPredictor's
        precedent). Prefer replacing the whole instance in the backend."""
        self._active = {}
        self._last_sim_time = None

    @property
    def active_responses(self) -> tuple[ActiveResponse, ...]:
        return tuple(sorted(self._active.values(), key=lambda r: r.event_id))

    # -- classification -------------------------------------------------
    def classify(self, snapshot: Mapping, *, sim_time: float,
                 spillover=None, incident_impacts=None, vision_events=None,
                 forced_emergency_lanes: frozenset[str] = frozenset(),
                 dropped: list[str] | None = None) -> tuple[Event, ...]:
        """All four classifiers, arbitrated. Pure — no registry access."""
        drops = dropped if dropped is not None else []
        forecasts = _validate_forecasts(spillover, snapshot, self.config, drops)
        vision = _validate_vision(vision_events, snapshot, self.config, drops)
        impacts = _validate_impacts(incident_impacts, drops)
        events = (
            _classify_emergency(snapshot, sim_time,
                                frozenset(forced_emergency_lanes), vision)
            + _classify_accident(snapshot, sim_time, impacts)
            + _classify_congestion(snapshot, sim_time, forecasts, self.config)
            + _classify_fairness(snapshot, sim_time, self.config)
        )
        return arbitrate(events)

    # -- one tick -------------------------------------------------------
    def tick(self, snapshot: Mapping, *, sim_time: float | None = None,
             spillover=None, incident_impacts=None, vision_events=None,
             forced_emergency_lanes: frozenset[str] = frozenset()) -> TickResult:
        now = self._check_clock(snapshot, sim_time)
        forced = frozenset(forced_emergency_lanes)
        dropped: list[str] = []
        events = self.classify(
            snapshot, sim_time=now, spillover=spillover,
            incident_impacts=incident_impacts, vision_events=vision_events,
            forced_emergency_lanes=forced, dropped=dropped,
        )
        registry = {eid: r for eid, r in self._active.items()
                    if r.expires_at_sim_time > now}
        directives, suppressed, registry = self._emit(events, forced, now,
                                                      registry)
        neutralisers, preempted, registry = self._preempt(events, now, registry)
        self._active = registry
        self._last_sim_time = now
        return TickResult(
            sim_time=now, events=events,
            directives=tuple(directives) + tuple(neutralisers),
            preempted=tuple(preempted), suppressed=tuple(suppressed),
            dropped_inputs=_cap_dropped(dropped),
        )

    def _check_clock(self, snapshot: Mapping, sim_time: float | None) -> float:
        now = snapshot.get("sim_time", 0.0) if sim_time is None else sim_time
        try:
            now = float(now)
        except (TypeError, ValueError):
            raise IncidentPriorityError(f"sim_time {now!r} is not numeric")
        if not math.isfinite(now):
            raise IncidentPriorityError("sim_time must be finite")
        if self._last_sim_time is not None and now < self._last_sim_time:
            raise IncidentPriorityError(
                f"sim_time went backwards ({self._last_sim_time} -> {now}). "
                "An IncidentPriorityAgent covers exactly ONE episode — "
                "replace it at the boundary, as sim_runner replaces its "
                "DecisionLog."
            )
        return now

    def _emit(self, events, forced, now, registry):
        """Class directives, in arbitration order, with the shadowing rule.

        An emergency claims its WHOLE junction (§10 gives that lane green and
        every conflicting movement red, so any other bias there is fighting
        the shield). Every other event claims only its target lane.
        """
        directives, suppressed = [], []
        claimed_lanes: set[str] = set()
        claimed_junctions: set[str] = set()
        for event in events:
            if (event.lane_id in claimed_lanes
                    or event.junction_id in claimed_junctions):
                suppressed.append(event.event_id)
                continue
            claimed_lanes.add(event.lane_id)
            if event.event_class == EVENT_EMERGENCY:
                claimed_junctions.add(event.junction_id)
            if self._is_suppressed_reissue(event, now, registry):
                suppressed.append(event.event_id)
                continue
            directive = _directive_for(event, forced)
            if directive is None:
                continue
            directives.append(directive)
            registry = {**registry,
                        event.event_id: self._as_response(event, directive, now)}
        return directives, suppressed, registry

    def _is_suppressed_reissue(self, event: Event, now: float,
                               registry: Mapping[str, ActiveResponse]) -> bool:
        """Don't re-issue a live response every step — but DO re-issue when
        severity escalated, or a low->high accident would sit on a weak bias
        for the rest of its window."""
        existing = registry.get(event.event_id)
        if existing is None or existing.status != STATUS_ACTIVE:
            return False
        if event.severity_value > existing.severity_value:
            return False
        return now < existing.expires_at_sim_time - REISSUE_MARGIN_S

    @staticmethod
    def _as_response(event: Event, directive: Directive,
                     now: float) -> ActiveResponse:
        hold = (EMERGENCY_HOLD_S if event.event_class == EVENT_EMERGENCY
                else float(directive.args.get("duration_s", DURATION_MIN_S)))
        return ActiveResponse(
            event_id=event.event_id, event_class=event.event_class,
            lane_id=event.lane_id, junction_id=event.junction_id,
            directive=directive, severity_value=event.severity_value,
            issued_at_sim_time=now, expires_at_sim_time=now + hold,
            status=STATUS_PENDING,
        )

    def _preempt(self, events, now, registry):
        """Mark and neutralise in-flight responses an emergency has taken over.

        Preemption is OBSERVABLE by design — the registry entry carries
        `status`/`preempted_by` and the event id rides `TickResult.preempted`
        — so the done-bar can assert the MECHANISM fired, not merely that the
        outcome looked right (CLAUDE.md's named failure mode).
        """
        emergencies = [e for e in events if e.event_class == EVENT_EMERGENCY]
        if not emergencies:
            return [], [], registry
        claimed = {e.junction_id for e in emergencies}
        by_event_id = emergencies[0].event_id
        neutralisers, preempted = [], []
        for eid, response in sorted(registry.items()):
            if (response.event_class == EVENT_EMERGENCY
                    or response.status == STATUS_PREEMPTED
                    or response.junction_id not in claimed
                    or response.directive.function != "set_lane_bias"):
                continue
            neutralisers.append(_neutraliser(response, by_event_id))
            preempted.append(eid)
            registry = {**registry, eid: replace(
                response, status=STATUS_PREEMPTED, preempted_by=by_event_id,
                expires_at_sim_time=now + NEUTRALISE_DURATION_S)}
        return neutralisers, preempted, registry

    # -- feedback -------------------------------------------------------
    def confirm(self, directives: Sequence[Directive], results: Sequence[Mapping],
                sim_time: float) -> None:
        """Promote PENDING -> ACTIVE, but only where dispatch actually applied.

        Without this, a REJECTED bias would record as active and then be
        suppressed as a live response — the agent would never retry it. That
        is a real, silent failure mode and cheap to design out, so calling
        `confirm` is part of the caller contract, not optional politeness.
        """
        # A mismatch means the caller lost or reordered results. zip() would
        # silently truncate and leave the tail stuck PENDING forever — a
        # response that is never active and never retried. Refuse instead.
        if len(directives) != len(results):
            raise IncidentPriorityError(
                f"confirm() needs one result per directive — got "
                f"{len(directives)} directives, {len(results)} results"
            )
        registry = dict(self._active)
        for directive, result in zip(directives, results):
            response = registry.get(directive.event_id)
            if response is None or response.status == STATUS_PREEMPTED:
                continue
            if isinstance(result, Mapping) and result.get("applied") is True:
                registry[directive.event_id] = replace(response,
                                                       status=STATUS_ACTIVE)
            else:
                registry.pop(directive.event_id, None)
        self._active = registry


def apply(state: Any, directives: Sequence[Directive], *,
          dispatch: Callable[[Any, str, dict], dict] | None = None
          ) -> tuple[dict, ...]:
    """Dispatch `directives` IN ORDER through the §13.1 control API.

    The ONLY function in this module that takes a `ControlState`, and it
    contains ZERO policy — it is a loop. `dispatch` is injectable purely so
    tests never construct backend threading state; the default is
    `control_api.dispatch`, which refuses any name outside
    `CONTROL_FUNCTIONS` before argument binding.
    """
    if dispatch is None:
        from backend.control_api import dispatch as dispatch  # noqa: PLC0415
    return tuple(dispatch(state, d.function, dict(d.args)) for d in directives)

if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # The done-bar command stays `python -m agents.incident_priority`; the
    # scenarios themselves live in the sibling module (see the split note
    # above).
    from agents.incident_priority_scenarios import (
        test_incident_priority_scenarios,
    )

    test_incident_priority_scenarios()
