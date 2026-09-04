"""Adapters for the §13.2 frame's `incident_alerts` and `iot_sensors` keys.

Both are ADDITIVE and emitted ONLY when non-empty, exactly like
`responder_messages` / `predictions` / `shadow_advisor` / `agent_activity`, so
the frozen five-key core is unchanged and no consumer ever handles an empty
container.

=======================================================================
THESE ARE ADAPTERS. THEY DETECT NOTHING.
=======================================================================
Every alert is a RESHAPE of a fact some other module already established —
a §7.3 incident someone reported, or an ambulance §7.1 already counted in a
lane's `type_composition`. Nothing here inspects an image, and nothing here
decides anything. The wire shape is Track A's (`perception.incident_detector`,
NOTES-FOR-INTEGRATION §A1); this module fills it from the sources that exist
today so the frontend has a stable contract to build against before Track A
lands, and Track A's own alerts pass through unchanged when it does.

HONEST BOUNDARY (§17 class) — `distance_m` / `distance_confidence` are
`None` for every alert this module produces, and that is not a placeholder
to be filled in later with a guess. Distance requires a fixed camera and a
homography (see `sim/media/README.md`); the twin has no such thing, and the
lane occupancy it does have is TraCI ground truth, not a ranged detection.
Emitting a number there would be inventing one. A consumer must render
"distance unknown", never 0.
"""

from __future__ import annotations

import math
from typing import Mapping

from perception.incident_intake import SEVERITIES

# The §13.2 wire shapes, pinned as frozensets the way
# sim/run_shadow_advisor_check.py pins SHADOW_KEYS and orchestrator/types.py
# pins ENTRY_KEYS — a silent field rename then fails a check, not a frontend.
ALERT_KEYS = frozenset({
    "type", "junction", "approach", "lane_index", "distance_m",
    "distance_confidence", "severity", "detected_at", "source",
})
IOT_KEYS = frozenset({"source", "fresh_s"})

# `type` values this module can raise. Track A may add its own; the frontend
# should treat an unknown type as a generic alert rather than dropping it.
ALERT_EMERGENCY_VEHICLE = "emergency_vehicle"

SOURCE_INTAKE = "incident_intake"    # §7.3 — reported, incl. operator inject
SOURCE_LANE_SENSOR = "lane_sensor"   # §7.1 — an ambulance already counted
SOURCE_OPERATOR = "operator"         # §13.1 trigger_emergency

# An operator-forced clearance has no reported severity of its own; §10 treats
# the override as unconditional, so it is reported at the top of §7.3's scale.
EMERGENCY_SEVERITY = "high"
assert EMERGENCY_SEVERITY in SEVERITIES, "severity drifted from §7.3's enum"

# Bound the list: a hostile or buggy upstream detector must not be able to
# inflate every frame. Same hardening as agents/incident_types.py's
# MAX_DROPPED_REPORTED and orchestrator/types.py's per-round caps.
MAX_ALERTS = 32


def _lane_index(lane_id: str) -> int | None:
    """Trailing integer of a SUMO lane id ('N1_J1_0' -> 0).

    0-BASED, matching `explainability/narrator.py`'s existing `_lane_index`.
    CLAUDE.md's APPROVED VOICE DESIGN flags the 0-vs-1-based mismatch as a
    real reconciliation item for Phase 11; this field stays 0-based so it
    agrees with the narration the same frame carries, and the frontend owns
    the presentation choice.
    """
    tail = str(lane_id).rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _lane_lookup(snapshot: Mapping) -> dict[str, tuple[str, Mapping]]:
    """lane_id -> (junction_id, §7.1 reading)."""
    return {
        lane_id: (jid, reading)
        for jid, block in (snapshot.get("junctions") or {}).items()
        for lane_id, reading in (block.get("lanes") or {}).items()
        if isinstance(reading, Mapping)
    }


def _alert(alert_type: str, junction: str | None, lane_id: str | None,
           reading: Mapping | None, severity: str, detected_at: float,
           source: str, distance_for=None) -> dict:
    # `distance_for` is a MEASUREMENT hook, not a default. With it absent —
    # every caller before Part 5c, the recorded fixture, and any consumer that
    # has no corridor geometry — distance stays None, which the module
    # docstring is emphatic about: unknown, deliberately, not zero.
    #
    # When supplied it returns (metres, confidence, method) computed by
    # `backend/vision_alerts.py` from the net file's real stop line and a real
    # TraCI vehicle position. That is a measurement, which is what the "not a
    # placeholder to fill in with a guess" rule actually forbids replacing.
    # It is TWIN-FRAME, not camera-ranged — carry that wherever it is shown.
    distance_m = distance_confidence = None
    if distance_for is not None and lane_id is not None:
        try:
            distance_m, distance_confidence, _method = distance_for(lane_id, junction)
        except Exception:  # noqa: BLE001 — a frame field is never worth a crash
            distance_m = distance_confidence = None
    return {
        "type": alert_type,
        "junction": junction,
        "approach": (reading or {}).get("approach"),
        "lane_index": _lane_index(lane_id) if lane_id is not None else None,
        "distance_m": distance_m,
        "distance_confidence": distance_confidence,
        "severity": severity,
        "detected_at": detected_at,
        "source": source,
    }


def build_incident_alerts(snapshot: Mapping, sim_time: float, *,
                          forced_emergency_lanes: frozenset[str] = frozenset(),
                          detector_alerts=None, distance_for=None) -> list[dict]:
    """Everything worth alerting a human about on this frame.

    Three sources, all of them already-established facts:
      * §7.3 `active_incidents` — reported incidents, including every
        operator `inject_incident`.
      * §7.1 `type_composition["ambulance"]` — the same channel
        `safety/validator.py` reads to raise its emergency override.
      * §13.1 `trigger_emergency` — the operator's own forced lanes.
      * Track A's detector, passed through VERBATIM when present.

    Deterministically ordered and bounded. Returns `[]` when there is nothing
    to say, and the caller then omits the frame key entirely.
    """
    lanes = _lane_lookup(snapshot)
    alerts: list[dict] = []

    for incident in snapshot.get("active_incidents") or []:
        if not isinstance(incident, Mapping):
            continue
        location = incident.get("location") or {}
        lane_id = location.get("lane_id")
        junction, reading = lanes.get(str(lane_id), (location.get("junction_id"), None))
        severity = incident.get("severity")
        alerts.append(_alert(
            str(incident.get("type")), junction, lane_id, reading,
            severity if severity in SEVERITIES else EMERGENCY_SEVERITY,
            float(incident.get("reported_at_sim_time", sim_time)),
            SOURCE_INTAKE, distance_for,
        ))

    for lane_id, (junction, reading) in lanes.items():
        composition = reading.get("type_composition") or {}
        detected = isinstance(composition, Mapping) and composition.get("ambulance")
        forced = lane_id in forced_emergency_lanes
        if not (detected or forced):
            continue
        # DETECTION WINS over an operator force on overlap — the same
        # provenance rule §11.2's `trigger_source` follows.
        alerts.append(_alert(
            ALERT_EMERGENCY_VEHICLE, junction, lane_id, reading,
            EMERGENCY_SEVERITY, sim_time,
            SOURCE_LANE_SENSOR if detected else SOURCE_OPERATOR, distance_for,
        ))

    for raw in detector_alerts or []:
        # Track A's own alerts ride through unchanged apart from the key
        # filter, so a field it adds cannot silently break the pinned shape.
        if isinstance(raw, Mapping):
            alerts.append({k: raw.get(k) for k in ALERT_KEYS})

    alerts.sort(key=lambda a: (str(a["junction"]), str(a["type"]),
                               a["lane_index"] if a["lane_index"] is not None else -1))
    return alerts[:MAX_ALERTS]


def build_iot_sensors(snapshot: Mapping, sim_time: float,
                      telemetry=None) -> dict:
    """Per-lane IoT freshness — `{lane_id: {source, fresh_s}}`.

    NO TRACK A, NO KEY. With `telemetry=None` this returns `{}` and the
    caller omits the frame key entirely. That is deliberate and is the honest
    behaviour: the twin's lane occupancy is TraCI ground truth read fresh
    every step, NOT an IoT feed, and reporting it as `{"source": "mqtt",
    "fresh_s": 0.0}` would be fabricating a sensor network that does not
    exist. See NOTES-FOR-INTEGRATION §A3 for the shape Track A must publish.

    `telemetry` is `{lane_id: {"source": str, "last_seen_s": float}}`;
    `fresh_s` is derived as `sim_time - last_seen_s`, floored at 0, so a
    producer only has to report WHEN it last heard from a lane.
    """
    if not telemetry:
        return {}
    lanes = _lane_lookup(snapshot)
    out: dict[str, dict] = {}
    for lane_id, record in telemetry.items():
        if str(lane_id) not in lanes or not isinstance(record, Mapping):
            continue    # never report a lane that is not in the live network
        try:
            last_seen = float(record.get("last_seen_s", sim_time))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(last_seen):
            continue
        out[str(lane_id)] = {
            "source": str(record.get("source", "unknown")),
            "fresh_s": round(max(0.0, float(sim_time) - last_seen), 3),
        }
    return out
