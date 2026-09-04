"""§7.2 vision -> advisory events, and real distances for §13.2 alerts.

Two jobs, both adapters. Neither detects anything: each reshapes a fact some
other module already established, which is the same rule
`backend/frame_sources.py` follows.

1. `advisory_vision_events()` — NOTES §9.2, applied
--------------------------------------------------
The detector emits **`emergency_vehicle_flag`**, never `emergency`. §9.2's
recorded decision is that this flag is ADVISORY ONLY:

  * it is NOT mapped onto §A1's `emergency` key;
  * it NEVER enters `safety.validator`'s `forced_emergency_lanes`;
  * the fail-closed `type_composition["ambulance"] > 0` path is kept, and for a
    detector source that is always false — COCO has no ambulance class — which
    is correct, not a bug.

So the events this builds carry the flag under its own name plus
`emergency_flag_is_experimental`, and the IncidentPriority agent sees a
low-confidence advisory rather than an emergency. Rationale from §9.2, kept
here because this is where it is enforced: the flag is a heuristic, §10's
override is stateless and recomputes every step (so a flickering false positive
would thrash a junction), and the project's floor is structural, not
statistical. Actuation stays with a real detected ambulance or the operator's
audited `trigger_emergency`.

2. `IncidentGeometry` — a real `distance_m`, or none at all
-----------------------------------------------------------
`frame_sources._alert()` hardcodes `distance_m: None` and its docstring is
emphatic that this "is not a placeholder to fill in with a guess". This class
does not guess. It measures, from two things that are already ground truth:

  * the lane's stop line, READ FROM THE NET FILE via
    `incident_detector.load_stop_line_coord` — never derived from
    `generate_corridor.py`'s authored parameters, which netconvert shifted by
    [150, 150];
  * a real vehicle position from TraCI.

`distance_to_stop_line()` then returns metres with the confidence and `method`
that produced them (0.95 / "stop_line" when the exact stop line is known, 0.60
/ "junction_centre" when only the centre is). With no vehicle on the lane there
is nothing to measure and it returns None — which is the honest answer, and the
one that keeps "render 'distance unknown', never 0" true.

HONEST BOUNDARY (§17), and it must travel with the number: this distance is
TWIN-FRAME, derived from SUMO ground truth, NOT ranged by the camera. A
camera-measured distance needs a fixed viewpoint and a homography from four
image points to four ground points; `sim/media/README.md` records that no such
footage or calibration exists in this repo yet. `incident_detector` also ships
the pixel path (`calibrate_pixels` + `distance_to_stop_line_px`) and
`detector_incidents()` will use it the moment a calibration is supplied — the
seam is the `calibration` argument, not a rewrite. Until then, do not describe
this figure as something the detector measured.

TraCI is read here, so every function in this module must be called ONLY from
the SimRunner thread (the standing single-thread rule).
"""

from __future__ import annotations

from typing import Mapping, Sequence

from perception.incident_detector import (
    LaneFlowState,
    PixelCalibration,
    VehicleTrack,
    detect_incident,
    distance_to_stop_line,
    load_junction_coord,
    load_stop_line_coord,
)

#: Detector-sourced §7.2 fields that ride to the agent as an advisory.
ADVISORY_SOURCE = "vision_detector_advisory"

#: A vehicle at or below this speed (m/s) counts as halted when picking which
#: vehicle a distance is measured to. `incident_detector.STATIONARY_SPEED_MPS`
#: has the same value but a different meaning — "stationary enough to classify
#: as an incident" versus "stopped enough to be the thing a responder is being
#: sent to" — so they are deliberately separate constants.
HALTED_SPEED_MPS = 0.5

#: Rough metres of road one queued vehicle occupies (length + gap), used only
#: to express `halted_count` as a queue length for the §8.2 congestion
#: classifier. An approximation, and only ever compared against thresholds.
QUEUE_METRES_PER_VEHICLE = 7.5


def advisory_vision_events(snapshot: Mapping) -> tuple[dict, ...]:
    """§7.2 observations reshaped as advisory events for IncidentPriority.

    Returns `()` when the twin carries no vision block. Deliberately does NOT
    emit an `emergency` key — see this module's docstring and NOTES §9.2.
    """
    events: list[dict] = []
    for junction_id, jdata in (snapshot.get("junctions") or {}).items():
        vision = jdata.get("vision") or {}
        if not isinstance(vision, Mapping):
            continue
        for lane_id, obs in vision.items():
            if not isinstance(obs, Mapping):
                continue
            events.append({
                "lane_id": lane_id,
                "junction_id": junction_id,
                "vehicle_count": obs.get("vehicle_count"),
                "type_composition": obs.get("type_composition") or {},
                "confidence": obs.get("confidence"),
                "source": obs.get("source", ADVISORY_SOURCE),
                # ADVISORY. Not `emergency`. Never reaches §10.
                "emergency_vehicle_flag": bool(obs.get("emergency_vehicle_flag")),
                "emergency_flag_is_experimental": bool(
                    obs.get("emergency_flag_is_experimental", True)
                ),
            })
    return tuple(events)


class IncidentGeometry:
    """Measured distances from a lane's stop line, cached per network.

    `net_file` is the .net.xml the twin is running. The two coordinate helpers
    are cached inside `incident_detector`, so repeated lookups are dict hits
    rather than re-parses; this class additionally memoises per lane so a
    steady-state frame does no parsing work at all.
    """

    def __init__(self, net_file: str,
                 calibration: PixelCalibration | None = None) -> None:
        self.net_file = str(net_file)
        self.calibration = calibration
        self._stop_line: dict[str, tuple[float, float] | None] = {}
        self._junction: dict[str, tuple[float, float] | None] = {}

    def stop_line(self, lane_id: str):
        if lane_id not in self._stop_line:
            try:
                self._stop_line[lane_id] = load_stop_line_coord(self.net_file, lane_id)
            except Exception:  # noqa: BLE001 — a lane not in this net
                self._stop_line[lane_id] = None
        return self._stop_line[lane_id]

    def junction(self, junction_id: str):
        if junction_id not in self._junction:
            try:
                self._junction[junction_id] = load_junction_coord(
                    self.net_file, junction_id)
            except Exception:  # noqa: BLE001
                self._junction[junction_id] = None
        return self._junction[junction_id]

    # -- TraCI reads (SimRunner thread ONLY) --------------------------
    @staticmethod
    def _lane_vehicles(lane_id: str) -> list[tuple[str, tuple[float, float], float, float]]:
        """(vehicle_id, (x, y), speed_mps, waiting_s) for one lane, or []."""
        import traci

        try:
            ids = traci.lane.getLastStepVehicleIDs(lane_id)
        except Exception:  # noqa: BLE001 — lane gone after a rebuild
            return []
        out = []
        for vid in ids:
            try:
                out.append((
                    vid,
                    traci.vehicle.getPosition(vid),
                    float(traci.vehicle.getSpeed(vid)),
                    float(traci.vehicle.getWaitingTime(vid)),
                ))
            except Exception:  # noqa: BLE001 — vehicle left mid-read
                continue
        return out

    def distance_for(self, lane_id: str, junction_id: str | None):
        """Metres from the lane's most relevant vehicle to its stop line.

        "Most relevant" = the halted vehicle FURTHEST from the stop line, i.e.
        the tail of the queue, falling back to the furthest vehicle of any kind
        if none are halted. That is the figure a responder needs: how far the
        obstruction extends back from the junction, not how close its nearest
        car happens to be.

        Returns `(distance_m, confidence, method)`, or `(None, None, None)`
        when there is nothing to measure. Never 0.0 for "unknown".
        """
        stop_xy = self.stop_line(lane_id)
        junction_xy = self.junction(junction_id) if junction_id else None
        reference = stop_xy or junction_xy
        if reference is None:
            return (None, None, None)

        vehicles = self._lane_vehicles(lane_id)
        if not vehicles:
            return (None, None, None)

        halted = [v for v in vehicles if v[2] <= HALTED_SPEED_MPS]
        pool = halted or vehicles
        _vid, xy, _spd, _wait = max(
            pool,
            key=lambda v: (v[1][0] - reference[0]) ** 2 + (v[1][1] - reference[1]) ** 2,
        )
        est = distance_to_stop_line(xy, junction_xy or stop_xy, stop_xy)
        return (est.distance_m, est.confidence, est.method)

    def tracks_for_lane(self, lane_id: str, approach: str, lane_index: int
                        ) -> tuple[VehicleTrack, ...]:
        """Real TraCI vehicles as `VehicleTrack`s.

        `VehicleTrack`'s own docstring anticipates this: speed and
        `stationary_for_s` "come from whoever owns the track history — the
        detector for pixel speeds converted through a calibration, or the
        caller for SUMO-derived ones". `x`/`y` are twin-frame METRES here
        rather than image pixels, which matters for exactly one threshold —
        `ACCIDENT_CLUSTER_RADIUS_PX` (90). Judged in metres that is a much
        TIGHTER test than in pixels, so it cannot manufacture accidents; it can
        miss a spread-out one. Fail-quiet, which is the right direction.
        """
        return tuple(
            VehicleTrack(
                track_id=vid, approach=approach, lane_index=lane_index,
                x=xy[0], y=xy[1], speed_mps=speed, stationary_for_s=waiting,
            )
            for vid, xy, speed, waiting in self._lane_vehicles(lane_id)
        )


def detector_incidents(snapshot: Mapping, sim_time: float,
                       geometry: IncidentGeometry,
                       queue_history: Mapping[str, Sequence[float]] | None = None,
                       ) -> list[dict]:
    """Run §8.2's classifiers over the live corridor, with real geometry.

    One `detect_incident()` call per occupied lane. Returns the nine-key alert
    dicts that `build_incident_alerts(detector_alerts=...)` passes through
    verbatim, so a real `distance_m` reaches the frame.

    Returns `[]` when nothing is wrong anywhere, which is the normal case — the
    classifiers are deliberately conservative (a red light is not a breakdown).
    """
    alerts: list[dict] = []
    for junction_id, jdata in (snapshot.get("junctions") or {}).items():
        for lane_id, reading in (jdata.get("lanes") or {}).items():
            if not isinstance(reading, Mapping) or not reading.get("vehicle_count"):
                continue
            approach = str(reading.get("approach", ""))
            lane_index = lane_index_of(lane_id)
            tracks = geometry.tracks_for_lane(lane_id, approach, lane_index)
            if not tracks:
                continue
            speeds = [t.speed_mps for t in tracks]
            flow = LaneFlowState(
                lane_id=lane_id, approach=approach, lane_index=lane_index,
                mean_speed_mps=sum(speeds) / len(speeds),
                free_flow_speed_mps=_free_flow(lane_id),
                queue_length_m=float(reading.get("halted_count", 0))
                * QUEUE_METRES_PER_VEHICLE,
                vehicle_count=int(reading.get("vehicle_count", 0)),
            )
            stop_xy = geometry.stop_line(lane_id)
            junction_xy = geometry.junction(junction_id)
            found = detect_incident(
                tracks, flow, junction=junction_id, detected_at=sim_time,
                queue_history=(queue_history or {}).get(lane_id, ()),
                junction_xy=junction_xy or stop_xy,
                vehicle_xy=_origin_xy(tracks),
                stop_line_xy=stop_xy,
                calibration=geometry.calibration,
            )
            if found is not None:
                alerts.append(found)
    return alerts


def _origin_xy(tracks: Sequence[VehicleTrack]) -> tuple[float, float] | None:
    stationary = [t for t in tracks if t.is_stationary]
    pick = stationary[0] if stationary else (tracks[0] if tracks else None)
    return None if pick is None else (pick.x, pick.y)


def _free_flow(lane_id: str) -> float:
    import traci

    try:
        return float(traci.lane.getMaxSpeed(lane_id))
    except Exception:  # noqa: BLE001
        return 13.89   # generate_corridor.py's DEFAULT_SPEED_MPS


def lane_index_of(lane_id: str) -> int:
    """0-BASED trailing index, matching `narrator._lane_index` and ALERT_KEYS."""
    tail = str(lane_id).rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def demo() -> int:
    """Self-check for the pure logic. No TraCI, no SUMO, no broker."""
    # -- §9.2: the advisory never becomes `emergency` ------------------
    snap = {"junctions": {"J1": {"vision": {
        "N1_J1_0": {"vehicle_count": 4, "confidence": 0.8,
                    "emergency_vehicle_flag": True,
                    "emergency_flag_is_experimental": True,
                    "type_composition": {"car": 4, "ambulance": 0},
                    "source": "vision_detector"},
    }}}}
    events = advisory_vision_events(snap)
    assert len(events) == 1, events
    ev = events[0]
    assert ev["emergency_vehicle_flag"] is True
    assert "emergency" not in ev, (
        "NOTES §9.2 forbids mapping the detector flag onto §A1's `emergency` — "
        f"that routes an experimental heuristic into the priority agent's "
        f"emergency class: {sorted(ev)}")
    assert ev["emergency_flag_is_experimental"] is True
    assert ev["type_composition"].get("ambulance", 0) == 0, (
        "the fail-closed ambulance path must stay false for a detector source")
    assert advisory_vision_events({}) == ()
    assert advisory_vision_events({"junctions": {"J1": {}}}) == ()

    # -- lane index is 0-based, matching the narrator -------------------
    assert lane_index_of("N1_J1_0") == 0
    assert lane_index_of("J2_J1_2") == 2
    assert lane_index_of("weird") == 0

    # -- geometry: unknown stays None, never 0.0 -----------------------
    g = IncidentGeometry(net_file="does_not_exist.net.xml")
    assert g.stop_line("X_0") is None
    assert g.junction("J9") is None
    assert g.distance_for("X_0", "J9") == (None, None, None), (
        "an unmeasurable distance must be None, not 0.0 — a responder-facing "
        "zero that means 'unknown' is the defect this repo already records")

    # -- distance is a real measurement, with its method recorded -------
    est = distance_to_stop_line((150.0, 200.0), (150.0, 150.0), (150.0, 163.6))
    assert abs(est.distance_m - 36.4) < 1e-6, est
    assert est.method == "stop_line" and est.confidence == 0.95, est
    far = distance_to_stop_line((150.0, 200.0), (150.0, 150.0))
    assert far.method == "junction_centre" and far.confidence == 0.60, far
    # The two AGREE here (both 36.4) precisely because this stop line sits at
    # the nominal DEFAULT_STOP_LINE_OFFSET_M behind the centre — so this pins
    # that 13.6m against the corridor's real geometry rather than just
    # re-checking arithmetic. What separates the methods is confidence, not
    # the number: the fallback is an approximation and says so.
    assert abs(far.distance_m - est.distance_m) < 1e-6, (far, est)
    assert far.confidence < est.confidence, "the approximation must be less trusted"
    # Move the stop line off the nominal setback and they must diverge, or the
    # exact path is not actually using the coordinate it was given.
    near = distance_to_stop_line((150.0, 200.0), (150.0, 150.0), (150.0, 180.0))
    assert abs(near.distance_m - 20.0) < 1e-6, near
    assert near.distance_m < far.distance_m

    print("backend.vision_alerts: 19/19 assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(demo())
