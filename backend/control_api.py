"""§13.1 Control API — the one surface both dashboard buttons and §14 voice call.

Plain module-level functions taking a `ControlState` plus arguments and returning
JSON-able dicts. `backend/main.py` wraps each in an ``APIRouter`` POST; Phase 11's
Ollama/Gemma intent agent (§14) will import these same functions directly, since
§14 is explicit that voice drives "the same control API the dashboard buttons
use". Keeping this module free of any SUMO / torch / numpy import is deliberate —
it must stay importable in a voice-only context.

Nothing here calls TraCI or mutates the running simulation. Each function either
answers from `ControlState`'s lock-protected caches (`get_stats`) or pushes a
`Command` onto `ControlState.pending`, which the single sim thread
(`backend/sim_runner.py`) drains and applies between decision steps. That is what
keeps every TraCI call on one thread.

STANDING RULE (CLAUDE.md §8): `enable_safety_validator` is NOT referenced
anywhere in this module or anywhere else under `backend/`. There is no
operator-facing reason to switch off §10, and its guarantee ("nothing reaches
the road without passing through here") only holds if the off-switch is
unreachable from anything that drives a real sim. `set_mode`, `set_baseline_mode`,
`trigger_emergency`, `set_topology`, `set_lane_bias` and `inject_incident`
cannot reach it.

HONEST BOUNDARY (§17): the intervention surface here is signal-phase control
(§9, via `set_mode` / `set_lane_bias` / the running policy) plus emergency-
corridor clearance (§10/§11, via `trigger_emergency`). The problem statement's
parenthetical also names "lane closures" — those are OUT OF SCOPE as a system
output. `inject_incident` REPORTS a blockage into perception (§7.3) so the
system can predict its impact (§8.2) and re-time around it; there is
deliberately no `close_lane` — commanding a closure is a physical / authority
action, not a control signal PsychoFlow emits.
"""

from __future__ import annotations

import copy
import queue
import threading
from dataclasses import dataclass, field

# perception.incident_intake is pure dataclasses — no SUMO / torch import,
# so this stays importable in a voice-only context (§14).
from perception.incident_intake import INCIDENT_TYPES, SEVERITIES

# §13.1 `trigger_emergency` has no natural release event (it is operator-forced,
# not a real vehicle that leaves the approach), so the sim thread auto-clears it
# after this many simulated seconds. Approved as the Phase 9 default; revisit
# once Phase 10 exists and an operator can see/clear it on screen.
EMERGENCY_HOLD_S = 20.0

VALID_MODES = ("manual", "auto")
VALID_BASELINES = ("psychoflow", "greedy")

# CLAUDE.md §8 / generate_corridor.VALID_LANE_COUNTS. Duplicated as a literal
# rather than imported so this module pulls in no SUMO dependency; the sim
# thread does the authoritative check when it builds the network.
_VALID_LANE_COUNTS = (2, 3, 4)

# §0.1's locked 3-junction corridor. Literal, not imported from
# twin.digital_twin (which imports sumolib).
_CORRIDOR_JUNCTIONS = ("J1", "J2", "J3")

# §7.3 default incident duration when the caller does not give one.
DEFAULT_INCIDENT_DURATION_S = 600.0


@dataclass(frozen=True)
class Command:
    """One control mutation queued for the sim thread to apply."""

    kind: str
    args: dict


@dataclass
class ControlState:
    """Shared state between the API handlers and the sim thread.

    The API side only ever: reads `mode` / `baseline_mode` (plain reads of a
    str are atomic enough for a status echo), calls `snapshot_stats()`, and
    `pending.put(...)`. The sim thread owns every write to `mode` /
    `baseline_mode` / the stats cache.
    """

    mode: str = "manual"
    baseline_mode: str = "psychoflow"
    has_checkpoint: bool = False

    pending: "queue.Queue[Command]" = field(default_factory=queue.Queue)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stats: dict = field(default_factory=dict)

    def publish_stats(self, stats: dict) -> None:
        with self._lock:
            self._stats = stats

    def snapshot_stats(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._stats)


def _parse_topology(topology_id) -> tuple[int, int, int] | None:
    """Accept '432', '4,3,2', '4 3 2', (4,3,2) or [4,3,2] -> (4, 3, 2)."""
    if isinstance(topology_id, (list, tuple)):
        digits = list(topology_id)
    else:
        s = str(topology_id).strip()
        if "," in s or " " in s:
            parts = [p for p in s.replace(",", " ").split() if p]
        else:
            parts = list(s)
        try:
            digits = [int(p) for p in parts]
        except ValueError:
            return None
    if len(digits) != 3:
        return None
    try:
        combo = tuple(int(d) for d in digits)
    except (TypeError, ValueError):
        return None
    if any(d not in _VALID_LANE_COUNTS for d in combo):
        return None
    return combo  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# §13.1 functions
# ---------------------------------------------------------------------------
def set_mode(state: ControlState, mode: str) -> dict:
    """manual -> Tier 0 (§9.1) takes over, RL paused; auto -> RL resumes."""
    if mode not in VALID_MODES:
        return {"applied": False, "mode": state.mode,
                "reason": f"mode must be one of {VALID_MODES}, got {mode!r}"}
    if mode == "auto" and not state.has_checkpoint:
        return {"applied": False, "mode": state.mode,
                "reason": "no trained checkpoint loaded — auto mode unavailable"}
    state.pending.put(Command("set_mode", {"mode": mode}))
    return {"applied": True, "mode": mode}


def set_lane_bias(state: ControlState, lane_id: str, weight, duration_s) -> dict:
    """Multiply `lane_id`'s §9.1 score by `weight` for `duration_s`, auto-revert.

    Applied by the Tier 0 scorer (§13.1 approved design: additive `lane_weights`
    param on `Tier0Controller.act`). Under `mode=auto` the RL policy has no
    per-lane score, so the bias is recorded and echoed but has no effect until
    the operator switches back to manual.
    """
    stats = state.snapshot_stats()
    known = stats.get("lanes", {})
    if known and lane_id not in known:
        return {"applied": False,
                "reason": f"unknown lane_id {lane_id!r} (not in the current network)"}
    try:
        weight = float(weight)
        duration_s = float(duration_s)
    except (TypeError, ValueError):
        return {"applied": False, "reason": "weight and duration_s must be numeric"}
    if weight <= 0.0 or duration_s <= 0.0:
        return {"applied": False, "reason": "weight and duration_s must be positive"}

    state.pending.put(Command("set_lane_bias", {
        "lane_id": lane_id, "weight": weight, "duration_s": duration_s,
    }))
    out = {"applied": True, "lane_id": lane_id, "weight": weight,
           "duration_s": duration_s}
    if state.mode != "manual":
        out["note"] = ("recorded, but mode=auto — the bias applies only to the "
                       "Tier 0 scorer (switch to manual for it to take effect)")
    return out


def get_stats(state: ControlState) -> dict:
    """Current per-lane wait times / counts / starvation, plus the §13.2 metrics."""
    stats = state.snapshot_stats()
    if not stats:
        return {"ready": False,
                "reason": "the simulation has not produced a snapshot yet"}
    return {"ready": True, **stats}


def trigger_emergency(state: ControlState, lane_id: str) -> dict:
    """Manually force the same override §10 raises automatically for `lane_id`."""
    stats = state.snapshot_stats()
    known = stats.get("lanes", {})
    if known and lane_id not in known:
        return {"applied": False,
                "reason": f"unknown lane_id {lane_id!r} (not in the current network)"}
    state.pending.put(Command("trigger_emergency", {"lane_id": lane_id}))
    return {"applied": True, "lane_id": lane_id, "hold_s": EMERGENCY_HOLD_S}


def set_topology(state: ControlState, topology_id) -> dict:
    """Swap the live SUMO network, restart the sim with the same agent."""
    combo = _parse_topology(topology_id)
    if combo is None:
        return {"applied": False,
                "reason": "topology_id must give 3 lane counts, each in "
                          f"{_VALID_LANE_COUNTS} — e.g. '432'"}
    state.pending.put(Command("set_topology", {"lane_counts": list(combo)}))
    return {"applied": True, "topology_id": "".join(str(d) for d in combo),
            "lane_counts": list(combo)}


def inject_incident(
    state: ControlState,
    junction_id: str,
    affected_lanes: list[str],
    *,
    incident_type: str = "lane_blocked",
    severity: str = "high",
    lane_id: str | None = None,
    estimated_duration_s: float = DEFAULT_INCIDENT_DURATION_S,
) -> dict:
    """Report a §7.3 incident into perception so the system can react to it.

    This is the LIVE trigger the demo otherwise lacks — without it
    `digital_twin.active_incidents` is always empty and "detects incidents"
    has nothing to detect. The sim thread calls
    `env.twin.incidents.report(...)` between decision steps; from the next
    step the incident shows up in the twin snapshot, in §8.2's
    incident-impact prediction, and (via §8.1's confidence penalty) in the
    spillover forecast.

    §17 boundary: this REPORTS a blockage, it does not command a lane
    closure — there is no `close_lane`. The system re-times signals around
    the reported incident; it never actuates a physical closure.
    """
    if junction_id not in _CORRIDOR_JUNCTIONS:
        return {"applied": False,
                "reason": f"junction_id must be one of {_CORRIDOR_JUNCTIONS}, "
                          f"got {junction_id!r}"}
    if incident_type not in INCIDENT_TYPES:
        return {"applied": False,
                "reason": f"type must be one of {INCIDENT_TYPES}, got {incident_type!r}"}
    if severity not in SEVERITIES:
        return {"applied": False,
                "reason": f"severity must be one of {SEVERITIES}, got {severity!r}"}
    if not isinstance(affected_lanes, (list, tuple)) or not affected_lanes:
        return {"applied": False,
                "reason": "affected_lanes must be a non-empty list of lane ids"}
    affected_lanes = [str(l) for l in affected_lanes]
    try:
        estimated_duration_s = float(estimated_duration_s)
    except (TypeError, ValueError):
        return {"applied": False, "reason": "estimated_duration_s must be numeric"}
    if estimated_duration_s <= 0.0:
        return {"applied": False, "reason": "estimated_duration_s must be positive"}

    if lane_id is None:
        lane_id = affected_lanes[0]
    lane_id = str(lane_id)

    # If the sim has published a lane set, sanity-check the lane ids against
    # it (same courtesy as set_lane_bias / trigger_emergency). The sim
    # thread's registry does no such check, so this is the only guard.
    known = state.snapshot_stats().get("lanes", {})
    if known:
        unknown = sorted({lane_id, *affected_lanes} - set(known))
        if unknown:
            return {"applied": False,
                    "reason": f"unknown lane id(s) {unknown} (not in the current network)"}
        misplaced = sorted(
            l for l in affected_lanes
            if known.get(l, {}).get("junction_id") not in (None, junction_id)
        )
        if misplaced:
            return {"applied": False,
                    "reason": f"lane(s) {misplaced} are not at junction {junction_id}"}

    state.pending.put(Command("inject_incident", {
        "incident_type": incident_type,
        "junction_id": junction_id,
        "lane_id": lane_id,
        "severity": severity,
        "affected_lanes": affected_lanes,
        "estimated_duration_s": estimated_duration_s,
    }))
    # incident_id is assigned by the registry on the sim thread; it will
    # appear in digital_twin.active_incidents on the stream from the next
    # step. The echo reports the request, not the assigned id.
    return {"applied": True, "incident": {
        "type": incident_type, "location": {"junction_id": junction_id, "lane_id": lane_id},
        "severity": severity, "affected_lanes": affected_lanes,
        "estimated_duration_s": estimated_duration_s,
    }}


def set_baseline_mode(state: ControlState, baseline: str) -> dict:
    """Swap controller live, no restart (§15.1). psychoflow <-> greedy.

    The switch is fully plumbed here, but the Greedy controller itself is a
    Phase 12 deliverable (§18) and CLAUDE.md §3 forbids building ahead — so
    `greedy` currently reports that it is not yet available rather than
    switching. §19 names the Greedy-vs-PsychoFlow side-by-side as the strongest
    demo beat, so Phase 12 needs real rehearsal runway before the event.
    """
    if baseline not in VALID_BASELINES:
        return {"applied": False, "baseline_mode": state.baseline_mode,
                "reason": f"baseline must be one of {VALID_BASELINES}, got {baseline!r}"}
    if baseline == "greedy":
        return {"applied": False, "baseline_mode": state.baseline_mode,
                "reason": "Greedy baseline lands in Phase 12 (§18); the switch is "
                          "plumbed but no Greedy controller exists yet"}
    state.pending.put(Command("set_baseline_mode", {"baseline": baseline}))
    return {"applied": True, "baseline_mode": baseline}
