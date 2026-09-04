"""Incident classification from vision tracks + externally-supplied flow state.

    python -m perception.incident_detector    # hand-scored assertions, no SUMO

Pure functions, deliberately
----------------------------
Nothing in this module reads a simulator, a camera, or a clock. Tracks
come from `perception/vision_detector.py`; lane speed and queue state come
from the caller (§7.1 shaped, whether that is TraCI-derived or measured);
`detected_at` is a caller-supplied simulation time. A `time.time()` in
here would make every result irreproducible and would also be *wrong* -
everything else in this repo timestamps against sim time.

`sumolib` is imported INSIDE `_read_net` (which both `load_junction_coord`
and `load_stop_line_coord` go through) and nowhere else, so importing this
module needs no `SUMO_HOME`. Those two
functions exist because of `CLAUDE.md`'s standing rule: **never hardcode
junction coordinates from `generate_corridor.py`'s parameters.** netconvert
normalises to non-negative coordinates and shifted this corridor by
[150, 150] - J2 authored at (300, 0) actually sits at (450, 150). Every
absolute-position consumer must read the net file. Callers that already
have a coordinate pass it in and never touch these.

The three classifiers, and why the negatives matter more than the positives
---------------------------------------------------------------------------
`breakdown`     one vehicle stationary > 15s in a lane that is otherwise
                FLOWING. The flow condition is the whole rule: at a red
                light every vehicle is stationary and nothing is broken.
`accident`      2+ stationary vehicles CLUSTERED together AND the lane's
                speed has COLLAPSED. Either alone is ordinary - two parked
                cars in a flowing lane, or a long red-light queue.
`major_congestion`
                a queue GROWING past a threshold with NO single stationary
                origin. If there is a stationary origin, the queue is a
                breakdown's consequence and reporting it as congestion
                would dispatch the wrong crew.

Precedence is accident > breakdown > major_congestion, matching severity.

Distance, and refusing to invent it
-----------------------------------
`distance_m` is `None` when the caller supplied no geometry, with
`distance_confidence` 0.0. It is NOT 0.0. A responder-facing zero that
actually means "unknown" is the same defect as §11.2's `served_on_arrival`
reporting a lane as already clear - a plausible number in front of a human
who has no way to tell it is fabricated. Every estimate carries the
`method` that produced it and a confidence that reflects it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

SOURCE_TAG = "vision_incident_detector"

INCIDENT_KINDS = ("breakdown", "accident", "major_congestion")

#: §7.3's severities. Restated (not imported) only for the rank map below;
#: `to_intake_kwargs` and tests.test_incident_detector F5 check against the
#: real `perception.incident_intake.SEVERITIES`, which has no SUMO import.
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

SEVERITY_FOR_KIND = {
    "accident": "high",
    "breakdown": "medium",
    "major_congestion": "low",
}

#: §7.3's `type` enum has no `breakdown` or `major_congestion` member, so
#: the detector's kinds are mapped onto it explicitly rather than being
#: assumed to line up. See `to_intake_kwargs`.
INTAKE_TYPE_FOR_KIND = {
    "accident": "accident",
    "breakdown": "lane_blocked",
    "major_congestion": "lane_blocked",
}

# -- thresholds --------------------------------------------------------
#: A vehicle stationary longer than this is not simply waiting.
STATIONARY_MIN_S = 15.0
#: At or below this, a vehicle counts as stationary this instant.
STATIONARY_SPEED_MPS = 0.5
#: Two stationary vehicles closer than this (image pixels) are clustered.
ACCIDENT_CLUSTER_RADIUS_PX = 90.0
ACCIDENT_MIN_VEHICLES = 2
#: Lane mean speed below this fraction of free-flow is a speed collapse.
SPEED_COLLAPSE_RATIO = 0.25
#: A lane is "flowing" (the breakdown precondition) at or above this.
FLOWING_RATIO = 0.35
#: Congestion needs a queue this long before it is worth reporting...
CONGESTION_QUEUE_MIN_M = 60.0
#: ...growing by at least this much across the supplied history...
CONGESTION_GROWTH_MIN_M = 20.0
#: ...over at least this many samples.
CONGESTION_MIN_SAMPLES = 3

#: Stop-line setback used when only a junction centre is known. Measured
#: on sim/networks/generated/corridor_432.net.xml: an approach lane's
#: shape ends 13.6m short of the junction centre.
DEFAULT_STOP_LINE_OFFSET_M = 13.6

#: A two-point calibration spanning this many pixels or more is treated as
#: fully trusted; below it, confidence falls off linearly.
CALIBRATION_FULL_CONFIDENCE_PX = 200.0

CONFIDENCE_STOP_LINE = 0.95
CONFIDENCE_JUNCTION_CENTRE = 0.60


class CalibrationError(ValueError):
    """A pixel->metre calibration was rejected."""


# ----------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class VehicleTrack:
    """One tracked vehicle at one instant.

    `x`/`y` are image-space pixels (the detector's frame). `speed_mps` and
    `stationary_for_s` come from whoever owns the track history - the
    detector for pixel speeds converted through a calibration, or the
    caller for SUMO-derived ones. This module only compares them against
    thresholds; it never differentiates positions itself.
    """

    track_id: str
    approach: str
    lane_index: int
    x: float
    y: float
    speed_mps: float
    stationary_for_s: float = 0.0

    @property
    def is_stationary(self) -> bool:
        return self.speed_mps <= STATIONARY_SPEED_MPS and self.stationary_for_s >= STATIONARY_MIN_S

    def distance_px_to(self, other: VehicleTrack) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class LaneFlowState:
    """SUMO-style speed/flow state for one lane, supplied by the caller."""

    lane_id: str
    approach: str
    lane_index: int
    mean_speed_mps: float
    free_flow_speed_mps: float
    queue_length_m: float = 0.0
    vehicle_count: int = 0

    @property
    def speed_ratio(self) -> float:
        if self.free_flow_speed_mps <= 0:
            return 0.0
        return self.mean_speed_mps / self.free_flow_speed_mps

    @property
    def is_flowing(self) -> bool:
        return self.speed_ratio >= FLOWING_RATIO

    @property
    def has_speed_collapse(self) -> bool:
        return self.speed_ratio < SPEED_COLLAPSE_RATIO


@dataclass(frozen=True)
class IncidentCandidate:
    """What a classifier found, before geometry and formatting."""

    type: str
    approach: str
    lane_index: int
    severity: str
    origin_track_id: str | None = None
    member_track_ids: tuple[str, ...] = ()
    detail: str = ""


# ----------------------------------------------------------------------
# Distance helper (a): pixel -> metre, from a 2-point calibration
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class PixelCalibration:
    """Scale from image pixels to ground metres along one known segment.

    This is a SCALE, not a homography: it is only valid near the segment
    it was measured on, and on a perspective view the same pixel distance
    is more metres further from the camera. For a fixed overhead camera
    over one approach it is a reasonable approximation; for an oblique
    street-level view it is not, and `confidence` is the only thing
    carrying that caveat downstream.
    """

    p1: tuple[float, float]
    p2: tuple[float, float]
    real_distance_m: float
    pixel_distance: float
    metres_per_pixel: float
    confidence: float

    def pixels_to_metres(self, pixel_distance: float) -> float:
        return float(pixel_distance) * self.metres_per_pixel


def calibrate_pixels(
    p1: Sequence[float], p2: Sequence[float], real_distance_m: float
) -> PixelCalibration:
    """Build a calibration from two image points a known distance apart.

    Pick the two points as far apart as the frame allows: the pixel
    measurement error is roughly constant, so a short baseline multiplies
    it into the scale. That is what `confidence` reports.
    """
    try:
        x1, y1 = float(p1[0]), float(p1[1])
        x2, y2 = float(p2[0]), float(p2[1])
        real = float(real_distance_m)
    except (TypeError, ValueError, IndexError) as exc:
        raise CalibrationError(f"calibration points must be numeric (x, y) pairs: {p1!r}, {p2!r}") from exc

    if not all(math.isfinite(v) for v in (x1, y1, x2, y2, real)):
        raise CalibrationError("calibration inputs must be finite (NaN/inf rejected)")
    if real <= 0.0:
        raise CalibrationError(f"real_distance_m={real} must be > 0")

    pixel_distance = math.hypot(x2 - x1, y2 - y1)
    if pixel_distance <= 0.0:
        raise CalibrationError("calibration points are identical - no pixel baseline to scale from")

    return PixelCalibration(
        p1=(x1, y1),
        p2=(x2, y2),
        real_distance_m=real,
        pixel_distance=pixel_distance,
        metres_per_pixel=real / pixel_distance,
        confidence=round(min(1.0, pixel_distance / CALIBRATION_FULL_CONFIDENCE_PX), 4),
    )


# ----------------------------------------------------------------------
# Distance helper (b): twin-frame distance to a stop line
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class DistanceEstimate:
    """A distance, how it was obtained, and how much to trust it."""

    distance_m: float
    confidence: float
    method: str


@lru_cache(maxsize=8)
def _read_net(net_file: str):
    """Parse a .net.xml once per path, not once per lookup.

    Both coordinate helpers below used to call `sumolib.net.readNet` on every
    invocation, which is fine for a one-shot script and far too slow for a
    per-step consumer: the live backend resolves a stop line for every alerting
    lane on every frame, so an uncached read re-parses the whole corridor many
    times a second. Cached, a corridor is parsed once and every later lookup is
    a dict hit. Keyed by path string, so a `set_topology` rebuild onto a
    different .net.xml gets its own entry rather than a stale one.
    """
    import sumolib  # deferred: keeps this module importable with no SUMO_HOME

    return sumolib.net.readNet(net_file)


def load_junction_coord(net_file: str | Path, junction_id: str) -> tuple[float, float]:
    """Junction centre in the twin frame, READ FROM THE NET FILE.

    Never derive this from `generate_corridor.py`'s parameters: netconvert
    normalises to non-negative coordinates and shifted this corridor by
    [150, 150], so J1 authored at (0, 0) is really (150, 150). It is a
    rigid translation, so distances are unaffected - but absolute
    positions, which is exactly what this function returns, are not.
    """
    x, y = _read_net(str(net_file)).getNode(junction_id).getCoord()
    return (float(x), float(y))


def load_stop_line_coord(net_file: str | Path, lane_id: str) -> tuple[float, float]:
    """Where an approach lane actually ends - its stop line.

    The lane's shape terminates at the stop line, which sits short of the
    junction centre by the junction's radius (~13.6m on the generated
    corridor). Using the centre instead overstates every distance by that
    much, which matters at the scale a responder cares about.
    """
    x, y = _read_net(str(net_file)).getLane(lane_id).getShape()[-1]
    return (float(x), float(y))


def distance_to_stop_line(
    vehicle_xy: Sequence[float],
    junction_xy: Sequence[float],
    stop_line_xy: Sequence[float] | None = None,
    *,
    stop_line_offset_m: float = DEFAULT_STOP_LINE_OFFSET_M,
) -> DistanceEstimate:
    """Metres from a vehicle to the stop line, in the twin's frame.

    With `stop_line_xy` this is exact. Without it, the junction centre
    distance less a nominal setback - which is an approximation, and says
    so through a lower confidence and a different `method`.
    """
    vx, vy = float(vehicle_xy[0]), float(vehicle_xy[1])
    if stop_line_xy is not None:
        distance = math.hypot(vx - float(stop_line_xy[0]), vy - float(stop_line_xy[1]))
        return DistanceEstimate(round(distance, 3), CONFIDENCE_STOP_LINE, "stop_line")

    jx, jy = float(junction_xy[0]), float(junction_xy[1])
    distance = math.hypot(vx - jx, vy - jy) - float(stop_line_offset_m)
    # A vehicle inside the junction is at the line, not behind it.
    return DistanceEstimate(round(max(0.0, distance), 3), CONFIDENCE_JUNCTION_CENTRE, "junction_centre")


def distance_to_stop_line_px(
    vehicle_px: Sequence[float],
    stop_line_px: Sequence[float],
    calibration: PixelCalibration,
) -> DistanceEstimate:
    """Image-space distance converted to metres through a calibration.

    Confidence is the calibration's own - the conversion adds no
    information, so it cannot add certainty either.
    """
    pixel_distance = math.hypot(
        float(vehicle_px[0]) - float(stop_line_px[0]),
        float(vehicle_px[1]) - float(stop_line_px[1]),
    )
    return DistanceEstimate(
        round(calibration.pixels_to_metres(pixel_distance), 3),
        calibration.confidence,
        "pixel_calibration",
    )


# ----------------------------------------------------------------------
# Classifiers
# ----------------------------------------------------------------------
def _stationary(tracks: Sequence[VehicleTrack]) -> list[VehicleTrack]:
    return [t for t in tracks if t.is_stationary]


def classify_breakdown(
    tracks: Sequence[VehicleTrack], flow: LaneFlowState
) -> IncidentCandidate | None:
    """Exactly one stationary vehicle in a lane that is otherwise flowing.

    Both halves are necessary. Drop "exactly one" and a red-light queue
    reports as a breakdown; drop "otherwise flowing" and so does every
    stopped lane in the corridor.
    """
    stuck = _stationary(tracks)
    if len(stuck) != 1:
        return None
    if not flow.is_flowing:
        return None

    origin = stuck[0]
    return IncidentCandidate(
        type="breakdown",
        approach=origin.approach,
        lane_index=origin.lane_index,
        severity=SEVERITY_FOR_KIND["breakdown"],
        origin_track_id=origin.track_id,
        member_track_ids=(origin.track_id,),
        detail=f"vehicle {origin.track_id} stationary {origin.stationary_for_s:.0f}s "
               f"while the lane runs at {flow.speed_ratio:.0%} of free flow",
    )


def classify_accident(
    tracks: Sequence[VehicleTrack], flow: LaneFlowState
) -> IncidentCandidate | None:
    """Two or more stationary vehicles clustered together, plus a speed
    collapse in the lane.

    The cluster alone is not enough (two vehicles parked side by side in a
    flowing lane) and the collapse alone is not enough (an ordinary long
    queue). Requiring both is what separates a collision from traffic.
    """
    stuck = _stationary(tracks)
    if len(stuck) < ACCIDENT_MIN_VEHICLES:
        return None
    if not flow.has_speed_collapse:
        return None

    cluster = _largest_cluster(stuck, ACCIDENT_CLUSTER_RADIUS_PX)
    if len(cluster) < ACCIDENT_MIN_VEHICLES:
        return None

    anchor = cluster[0]
    return IncidentCandidate(
        type="accident",
        approach=anchor.approach,
        lane_index=anchor.lane_index,
        severity=SEVERITY_FOR_KIND["accident"],
        origin_track_id=anchor.track_id,
        member_track_ids=tuple(t.track_id for t in cluster),
        detail=f"{len(cluster)} stationary vehicles within {ACCIDENT_CLUSTER_RADIUS_PX:.0f}px "
               f"and lane speed collapsed to {flow.speed_ratio:.0%} of free flow",
    )


def _largest_cluster(
    stuck: Sequence[VehicleTrack], radius_px: float
) -> list[VehicleTrack]:
    """Biggest set of stationary vehicles within `radius_px` OF ONE ANCHOR.

    Each vehicle is tried as the anchor and the largest group wins. Note
    this is NOT a mutual-distance clique: members can be up to *twice*
    `radius_px` from each other, so a 0 / 80 / 160 px chain is one group at
    radius 90 even though its ends are 160 apart. That is deliberate - a
    collision is vehicles piled around a point, and requiring every pair to
    be mutually close would miss a three-car shunt strung along a lane.
    It does mean `ACCIDENT_CLUSTER_RADIUS_PX` behaves like a radius, not a
    diameter, when it is retuned against real footage.

    Quadratic, which is irrelevant at the handful of stationary tracks one
    approach ever holds, and worth more than a spatial index in clarity.
    """
    best: list[VehicleTrack] = []
    for seed in stuck:
        group = [t for t in stuck if t.distance_px_to(seed) <= radius_px]
        if len(group) > len(best):
            best = group
    return best


def classify_major_congestion(
    tracks: Sequence[VehicleTrack],
    flow: LaneFlowState,
    queue_history: Sequence[float] = (),
) -> IncidentCandidate | None:
    """A queue growing past a threshold, with no single stationary origin.

    `queue_history` is oldest-first queue lengths in metres, including the
    current one. Growth is measured across the whole window rather than
    the last step, so one noisy sample cannot trigger a report.

    The "no stationary origin" clause is the important one: a queue
    growing behind a broken-down vehicle IS a breakdown, and reporting it
    as congestion would send a traffic crew where a recovery truck is
    needed.
    """
    history = [float(q) for q in queue_history]
    if len(history) < CONGESTION_MIN_SAMPLES:
        return None
    if flow.queue_length_m < CONGESTION_QUEUE_MIN_M:
        return None
    if history[-1] - history[0] < CONGESTION_GROWTH_MIN_M:
        return None
    if _stationary(tracks):
        return None

    return IncidentCandidate(
        type="major_congestion",
        approach=flow.approach,
        lane_index=flow.lane_index,
        severity=SEVERITY_FOR_KIND["major_congestion"],
        origin_track_id=None,  # by definition - no single origin
        member_track_ids=tuple(t.track_id for t in tracks),
        detail=f"queue grew {history[0]:.0f}m -> {history[-1]:.0f}m over "
               f"{len(history)} samples with no stationary origin",
    )


#: Evaluated in order; the first match wins. Matches severity order, so a
#: collision is never reported as the congestion it also causes.
CLASSIFIER_PRECEDENCE = ("accident", "breakdown", "major_congestion")


# ----------------------------------------------------------------------
# Composition
# ----------------------------------------------------------------------
def detect_incident(
    tracks: Sequence[VehicleTrack],
    flow: LaneFlowState,
    *,
    junction: str,
    detected_at: float,
    queue_history: Sequence[float] = (),
    junction_xy: Sequence[float] | None = None,
    vehicle_xy: Sequence[float] | None = None,
    stop_line_xy: Sequence[float] | None = None,
    calibration: PixelCalibration | None = None,
    stop_line_px: Sequence[float] | None = None,
) -> dict[str, Any] | None:
    """Classify one lane's state, or return None if nothing is wrong.

    Geometry is entirely optional and entirely explicit. Supply either
    twin-frame coordinates (`junction_xy` + `vehicle_xy`, ideally with
    `stop_line_xy`) or image-space ones (`calibration` + `stop_line_px`,
    using the origin track's own pixel position). With neither,
    `distance_m` is None and `distance_confidence` is 0.0 - see the module
    docstring on why that is not 0.0 metres.
    """
    candidate = None
    for kind in CLASSIFIER_PRECEDENCE:
        if kind == "accident":
            candidate = classify_accident(tracks, flow)
        elif kind == "breakdown":
            candidate = classify_breakdown(tracks, flow)
        else:
            candidate = classify_major_congestion(tracks, flow, queue_history)
        if candidate is not None:
            break

    if candidate is None:
        return None

    estimate = _estimate_distance(
        candidate, tracks,
        junction_xy=junction_xy, vehicle_xy=vehicle_xy, stop_line_xy=stop_line_xy,
        calibration=calibration, stop_line_px=stop_line_px,
    )

    return {
        "type": candidate.type,
        "junction": junction,
        "approach": candidate.approach,
        "lane_index": candidate.lane_index,
        "distance_m": None if estimate is None else estimate.distance_m,
        "distance_confidence": 0.0 if estimate is None else estimate.confidence,
        "severity": candidate.severity,
        "detected_at": float(detected_at),
        "source": SOURCE_TAG,
    }


def _estimate_distance(
    candidate: IncidentCandidate,
    tracks: Sequence[VehicleTrack],
    *,
    junction_xy: Sequence[float] | None,
    vehicle_xy: Sequence[float] | None,
    stop_line_xy: Sequence[float] | None,
    calibration: PixelCalibration | None,
    stop_line_px: Sequence[float] | None,
) -> DistanceEstimate | None:
    """Best available distance for the incident's origin, or None.

    Preference order is by how much the method actually knows:
    twin-frame stop line > twin-frame junction centre > pixel calibration.
    """
    if vehicle_xy is not None and (stop_line_xy is not None or junction_xy is not None):
        return distance_to_stop_line(
            vehicle_xy, junction_xy or stop_line_xy, stop_line_xy=stop_line_xy
        )

    if calibration is not None and stop_line_px is not None:
        origin = _origin_track(candidate, tracks)
        if origin is not None:
            return distance_to_stop_line_px((origin.x, origin.y), stop_line_px, calibration)

    return None


def _origin_track(
    candidate: IncidentCandidate, tracks: Sequence[VehicleTrack]
) -> VehicleTrack | None:
    if candidate.origin_track_id is None:
        return None
    for track in tracks:
        if track.track_id == candidate.origin_track_id:
            return track
    return None


def to_intake_kwargs(
    incident: dict[str, Any],
    *,
    lane_id: str,
    estimated_duration_s: float,
    affected_lanes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Map a detector output onto `IncidentIntake.report()`'s signature.

    The mapping is DECLARED rather than assumed, because the two schemas
    genuinely do not line up:

    - §7.3's `type` enum is `lane_blocked / accident / roadworks` and has
      no `breakdown` or `major_congestion`. `INTAKE_TYPE_FOR_KIND` folds
      both onto `lane_blocked`, which is what a responder acts on.
    - `distance_m`, `distance_confidence` and `lane_index` have NO home in
      `Incident` and are dropped here. If they should survive into the
      twin, `Incident` needs new optional fields - an edit outside this
      file's ownership, written up in NOTES-FOR-INTEGRATION.md.
    - `lane_id` cannot be derived: the detector knows an approach and a
      lane index, not a SUMO lane id, so the caller supplies it.
    """
    kind = incident["type"]
    if kind not in INTAKE_TYPE_FOR_KIND:
        raise ValueError(f"unknown incident kind {kind!r} - must be one of {INCIDENT_KINDS}")
    return {
        "incident_type": INTAKE_TYPE_FOR_KIND[kind],
        "junction_id": incident["junction"],
        "lane_id": lane_id,
        "severity": incident["severity"],
        "affected_lanes": list(affected_lanes) if affected_lanes else [lane_id],
        "reported_at_sim_time": incident["detected_at"],
        "estimated_duration_s": float(estimated_duration_s),
    }


if __name__ == "__main__":
    from tests import test_incident_detector

    raise SystemExit(test_incident_detector.main())
