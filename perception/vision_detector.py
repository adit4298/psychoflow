"""Real YOLOv8n vision detector (§7.2, reopened - see BUILD_LOG 2026-09-03).

    python -m perception.vision_detector <video>     # per-approach counts, 100 frames
    python -m perception.vision_detector --selftest  # hand-scored assertions, no video
    python -m perception.vision_detector --webcam 0

`perception/vision_mock.py` STAYS and remains the default (`vision_source.py`).
This module is the second, parallel perception source: it reads a *video
file or camera*, not the SUMO corridor, so it cannot and does not drive
`PsychoFlowEnv` - the twin's lane occupancy still comes from TraCI. Say
"a real detector runs alongside, on camera footage", never "the system
runs on camera input" (§17).

Three honesty rules, each pinned by an assertion in
`tests.test_vision_detector`
----------------------------------------------------------------------
1. **`auto` and `ambulance` are structurally undetectable here.** COCO
   gives `person / bicycle / car / motorcycle / bus / truck` and nothing
   else (measured on the downloaded weights - `sim/media/README.md`). An
   auto-rickshaw silently counted as a `car` is a wrong number on the
   dashboard, not a rounding error, so unmapped labels map to `None` and
   are dropped. Both types are reported present-and-zero, never absent.

2. **`emergency_vehicle_flag` is EXPERIMENTAL and never touches a count.**
   It is a behavioural heuristic (a vehicle moving through otherwise
   stopped traffic), not a classification. It rides as its own boolean
   with `emergency_flag_is_experimental: True` beside it, and is forbidden
   from incrementing `type_composition["ambulance"]` - that would conflate
   "flagged, unverified" with "counted".

3. **A camera cannot measure accumulated waiting time.** The §7.1 contract
   requires `wait_time_current` / `wait_time_max_single_vehicle` /
   `starvation_flag`, so they are present - as declared-unknown zeros with
   `wait_times_measured: False` beside them. A consumer that reads them as
   measurements is reading a zero that means "not observable from here".

The approach-vs-lane problem
----------------------------
`VisionMock.observe()` is called per `LaneReading` and re-emits that
lane's SUMO `lane_id`. A camera has no such id - it has image-space
polygons, one per APPROACH. So the detector's native output
(`FrameObservation`) is keyed by approach, and fanning an approach
aggregate onto that approach's individual lanes is a DECLARED fan-out
(`lane_fanout: True` on the observation), never a measurement. See
NOTES-FOR-INTEGRATION.md - wiring this into `twin/digital_twin.py` is an
integration step outside this file's ownership.
"""

from __future__ import annotations

import argparse
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = REPO_ROOT / "models" / "yolov8n.pt"

SOURCE_TAG = "vision_detector"

#: §7.1's five types. Restated rather than imported from lane_sensor so
#: this module stays importable without SUMO; drift is guarded by
#: tests.test_vision_detector D1, which imports the real LaneReading.
VEHICLE_TYPES = ("bike", "auto", "car", "truck", "ambulance")

#: COCO -> project type. Measured on models/yolov8n.pt, not assumed:
#: the only vehicle-ish classes in COCO's 80 are
#: person(0) bicycle(1) car(2) motorcycle(3) bus(5) truck(7).
COCO_TO_PROJECT: dict[str, str] = {
    "bicycle": "bike",
    "motorcycle": "bike",
    "car": "car",
    "truck": "truck",
    "bus": "truck",
}

#: Types with NO COCO class at all - see honesty rule 1.
UNDETECTABLE_TYPES = ("auto", "ambulance")

APPROACHES = ("north", "south", "east", "west", "unknown")
DENSITY_LEVELS = ("free", "moderate", "congested")

#: Raw-count bucket edges, used when an ROI declares no capacity.
DENSITY_FREE_MAX = 4
DENSITY_MODERATE_MAX = 11
#: Occupancy-ratio bucket edges, used when an ROI declares a capacity.
DENSITY_FREE_RATIO = 0.30
DENSITY_MODERATE_RATIO = 0.70

#: Below this a track counts as queued. Pixels, so it is frame-scale
#: dependent - a per-config value rather than a physical constant.
DEFAULT_STATIONARY_SPEED_PX_PER_S = 1.5

#: Heuristic 2: flag a vehicle moving this many times faster than the
#: median of its neighbours while the approach is congested.
EMERGENCY_SPEED_FACTOR = 3.0
EMERGENCY_MIN_NEIGHBOURS = 3

DEFAULT_CONFIDENCE_THRESHOLD = 0.25
DEFAULT_IMAGE_SIZE = 640


class VisionConfigError(ValueError):
    """An ROI / camera configuration was rejected."""


# ----------------------------------------------------------------------
# Label mapping
# ----------------------------------------------------------------------
def project_type_for_coco_label(label: object) -> str | None:
    """Map a COCO class name to one of VEHICLE_TYPES, or None.

    None means "this detector cannot say", and the caller drops it. It
    must never fall back to a default type - see honesty rule 1.
    """
    if not isinstance(label, str):
        return None
    return COCO_TO_PROJECT.get(label.strip().lower())


def compose_types(labels: Iterable[str]) -> dict[str, int]:
    """§7.1 `type_composition` from COCO labels. All five keys always."""
    composition = {vtype: 0 for vtype in VEHICLE_TYPES}
    for label in labels:
        mapped = project_type_for_coco_label(label)
        if mapped is not None:
            composition[mapped] += 1
    return composition


# ----------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------
def foot_point(box_xyxy: Sequence[float]) -> tuple[float, float]:
    """Ground-contact point of a detection: bottom-centre of the box.

    The centroid would place a tall vehicle (truck, bus) further up the
    image than it actually stands, which on a perspective view is a whole
    lane's worth of error at the far end of an approach.
    """
    x1, _y1, x2, y2 = (float(v) for v in box_xyxy)
    return ((x1 + x2) / 2.0, y2)


def point_in_polygon(point: Sequence[float], polygon: Sequence[Sequence[float]]) -> bool:
    """Ray-casting containment test. Boundary points count as inside-ish;
    exactness on the edge does not matter for lane assignment."""
    x, y = float(point[0]), float(point[1])
    inside = False
    count = len(polygon)
    for i in range(count):
        x1, y1 = float(polygon[i][0]), float(polygon[i][1])
        x2, y2 = float(polygon[(i + 1) % count][0]), float(polygon[(i + 1) % count][1])
        if (y1 > y) != (y2 > y):
            x_at_y = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_at_y:
                inside = not inside
    return inside


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class ApproachROI:
    """One approach's image-space region.

    `capacity` is the approximate number of vehicles the region holds when
    full. It is what makes `density` comparable between a 2-lane and a
    4-lane approach; omitted, density falls back to raw counts.
    """

    approach: str
    polygon: tuple[tuple[float, float], ...]
    capacity: int | None = None

    def __post_init__(self) -> None:
        if self.approach not in APPROACHES:
            raise VisionConfigError(f"approach={self.approach!r} must be one of {APPROACHES}")
        if not isinstance(self.polygon, (list, tuple)) or len(self.polygon) < 3:
            raise VisionConfigError(
                f"{self.approach}: polygon needs >= 3 points, got "
                f"{len(self.polygon) if hasattr(self.polygon, '__len__') else '?'}"
            )
        points: list[tuple[float, float]] = []
        for i, pt in enumerate(self.polygon):
            if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                raise VisionConfigError(f"{self.approach}: polygon[{i}] must be an (x, y) pair, got {pt!r}")
            try:
                x, y = float(pt[0]), float(pt[1])
            except (TypeError, ValueError) as exc:
                raise VisionConfigError(f"{self.approach}: polygon[{i}]={pt!r} is not numeric") from exc
            if not (math.isfinite(x) and math.isfinite(y)):
                raise VisionConfigError(f"{self.approach}: polygon[{i}]={pt!r} is not finite")
            points.append((x, y))
        object.__setattr__(self, "polygon", tuple(points))

        if self.capacity is not None:
            if not isinstance(self.capacity, int) or isinstance(self.capacity, bool) or self.capacity <= 0:
                raise VisionConfigError(f"{self.approach}: capacity must be a positive int, got {self.capacity!r}")

    def contains(self, point: Sequence[float]) -> bool:
        return point_in_polygon(point, self.polygon)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approach": self.approach,
            "polygon": [list(p) for p in self.polygon],
            "capacity": self.capacity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApproachROI:
        return cls(
            approach=data["approach"],
            polygon=tuple(tuple(p) for p in data["polygon"]),
            capacity=data.get("capacity"),
        )


@dataclass(frozen=True)
class VisionConfig:
    """Per-camera configuration: which junction, what frame, which ROIs."""

    junction_id: str
    frame_size: tuple[int, int]
    rois: tuple[ApproachROI, ...]
    #: True while the polygons are the shipped defaults rather than ones
    #: measured against real footage. Carried in the data, not only in a
    #: docstring, so a consumer can refuse to present placeholder geometry
    #: as a measurement.
    is_placeholder: bool = False
    stationary_speed_px_per_s: float = DEFAULT_STATIONARY_SPEED_PX_PER_S
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD

    def __post_init__(self) -> None:
        if not isinstance(self.junction_id, str) or not self.junction_id:
            raise VisionConfigError("junction_id must be a non-empty string")
        try:
            width, height = (int(v) for v in self.frame_size)
        except (TypeError, ValueError) as exc:
            raise VisionConfigError(f"frame_size={self.frame_size!r} must be (width, height)") from exc
        if width <= 0 or height <= 0:
            raise VisionConfigError(f"frame_size={self.frame_size!r} must be positive")
        object.__setattr__(self, "frame_size", (width, height))

        rois = tuple(self.rois)
        if not rois:
            raise VisionConfigError(f"{self.junction_id}: at least one approach ROI is required")
        seen = [roi.approach for roi in rois]
        if len(set(seen)) != len(seen):
            raise VisionConfigError(f"{self.junction_id}: duplicate approach in ROIs {seen}")
        for roi in rois:
            for x, y in roi.polygon:
                if not (0 <= x <= width and 0 <= y <= height):
                    raise VisionConfigError(
                        f"{self.junction_id}/{roi.approach}: point ({x}, {y}) lies outside "
                        f"the {width}x{height} frame - polygons are image-space, not world-space"
                    )
        object.__setattr__(self, "rois", rois)

        if not 0.0 < float(self.confidence_threshold) <= 1.0:
            raise VisionConfigError(f"confidence_threshold={self.confidence_threshold} must be in (0, 1]")
        if float(self.stationary_speed_px_per_s) < 0:
            raise VisionConfigError("stationary_speed_px_per_s must be >= 0")

    @property
    def approaches(self) -> tuple[str, ...]:
        return tuple(roi.approach for roi in self.rois)

    def roi_for(self, approach: str) -> ApproachROI | None:
        for roi in self.rois:
            if roi.approach == approach:
                return roi
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "junction_id": self.junction_id,
            "frame_size": list(self.frame_size),
            "rois": [roi.to_dict() for roi in self.rois],
            "is_placeholder": self.is_placeholder,
            "stationary_speed_px_per_s": self.stationary_speed_px_per_s,
            "confidence_threshold": self.confidence_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisionConfig:
        return cls(
            junction_id=data["junction_id"],
            frame_size=tuple(data["frame_size"]),
            rois=tuple(ApproachROI.from_dict(r) for r in data["rois"]),
            is_placeholder=bool(data.get("is_placeholder", False)),
            stationary_speed_px_per_s=float(
                data.get("stationary_speed_px_per_s", DEFAULT_STATIONARY_SPEED_PX_PER_S)
            ),
            confidence_threshold=float(data.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)),
        )

    @classmethod
    def default(cls, junction_id: str = "J2", frame_size: tuple[int, int] = (1280, 720)) -> VisionConfig:
        """A PLACEHOLDER four-quadrant layout, marked as such.

        These polygons were NOT measured against real footage - no footage
        exists in the repo yet (`sim/media/README.md`). They exist so the
        pipeline is runnable and schema-checkable today; whoever supplies
        a real clip must redraw them against that camera's actual geometry
        and clear `is_placeholder`.
        """
        width, height = int(frame_size[0]), int(frame_size[1])
        mid_x, mid_y = width / 2.0, height / 2.0
        quadrants = {
            "north": [(0, 0), (width, 0), (width, mid_y), (0, mid_y)],
            "south": [(0, mid_y), (width, mid_y), (width, height), (0, height)],
            "west": [(0, 0), (mid_x, 0), (mid_x, height), (0, height)],
            "east": [(mid_x, 0), (width, 0), (width, height), (mid_x, height)],
        }
        # Only north/south by default: overlapping quadrants would double
        # count, and a first-match-wins assignment across four overlapping
        # halves is exactly the kind of quiet wrongness §17 asks us to avoid.
        rois = tuple(
            ApproachROI(approach=name, polygon=tuple(quadrants[name]), capacity=24)
            for name in ("north", "south")
        )
        return cls(junction_id=junction_id, frame_size=(width, height), rois=rois, is_placeholder=True)


# ----------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------
def density_for(vehicle_count: int, capacity: int | None = None) -> str:
    """Coarse density bucket. Monotonic in `vehicle_count` either way."""
    count = max(0, int(vehicle_count))
    if capacity:
        ratio = count / float(capacity)
        if ratio <= DENSITY_FREE_RATIO:
            return DENSITY_LEVELS[0]
        if ratio <= DENSITY_MODERATE_RATIO:
            return DENSITY_LEVELS[1]
        return DENSITY_LEVELS[2]
    if count <= DENSITY_FREE_MAX:
        return DENSITY_LEVELS[0]
    if count <= DENSITY_MODERATE_MAX:
        return DENSITY_LEVELS[1]
    return DENSITY_LEVELS[2]


def any_speed_known(tracks: Sequence[dict[str, Any]]) -> bool:
    """Whether ANY track carries a usable speed.

    This is what separates a measured queue from `queue_estimate`'s
    fallback, and it is reported on the observation as `queue_measured` -
    the same courtesy `wait_times_measured` already extends. Without it,
    frame 1 (no history to difference yet) reports halted_count ==
    vehicle_count with nothing saying that is an assumption rather than an
    observation, which is exactly the shape of dishonesty rule 3 exists to
    prevent.
    """
    return any(
        isinstance(t.get("speed_px_per_s"), (int, float)) and not isinstance(t.get("speed_px_per_s"), bool)
        for t in tracks
    )


def queue_estimate(
    tracks: Sequence[dict[str, Any]],
    stationary_speed_px_per_s: float = DEFAULT_STATIONARY_SPEED_PX_PER_S,
) -> int:
    """How many of these tracks are queued (stationary).

    With no speed known for ANY track - the first frame, before there is a
    history to difference - this returns the raw count rather than 0.
    Reporting 0 there would be the claim "no queue", which is stronger than
    the truth, "not measurable yet".
    """
    speeds = [t.get("speed_px_per_s") for t in tracks]
    known = [s for s in speeds if isinstance(s, (int, float)) and not isinstance(s, bool)]
    if not known:
        return len(tracks)
    return sum(1 for s in known if float(s) <= stationary_speed_px_per_s)


def detect_emergency_heuristic(
    tracks: Sequence[dict[str, Any]],
    density: str,
    *,
    speed_factor: float = EMERGENCY_SPEED_FACTOR,
) -> bool:
    """EXPERIMENTAL. True when one vehicle moves through stopped traffic.

    COCO has no ambulance class, so this is deliberately BEHAVIOURAL, not
    a classification: in congested traffic, a vehicle sustaining several
    times the median speed of its neighbours is behaving like a responder
    with a cleared path. It is wrong on a motorcycle filtering hard, and
    it says nothing at all in free-flowing traffic. That is why it is a
    flag with `emergency_flag_is_experimental` beside it, and why it is
    forbidden from writing `type_composition["ambulance"]`.
    """
    if density != "congested":
        return False
    speeds = [
        float(t["speed_px_per_s"])
        for t in tracks
        if isinstance(t.get("speed_px_per_s"), (int, float)) and not isinstance(t.get("speed_px_per_s"), bool)
    ]
    if len(speeds) < EMERGENCY_MIN_NEIGHBOURS:
        return False
    fastest = max(speeds)
    median = statistics.median(speeds)
    if median <= 0.0:
        # Everything else is stopped; require real movement, not noise.
        return fastest > DEFAULT_STATIONARY_SPEED_PX_PER_S * speed_factor
    return fastest >= median * speed_factor


@dataclass(frozen=True)
class ApproachAggregate:
    """One approach, one frame: what the camera can and cannot say."""

    approach: str
    vehicle_count: int
    type_composition: dict[str, int]
    density: str
    queue_estimate: int
    emergency_vehicle_flag: bool = False
    dropped_unmappable: int = 0
    #: False when no track had a usable speed, so `queue_estimate` fell
    #: back to the raw count rather than measuring. See `any_speed_known`.
    queue_measured: bool = False

    @classmethod
    def from_labels(
        cls,
        approach: str,
        labels: Sequence[str],
        *,
        emergency_vehicle_flag: bool = False,
        capacity: int | None = None,
        tracks: Sequence[dict[str, Any]] | None = None,
        stationary_speed_px_per_s: float = DEFAULT_STATIONARY_SPEED_PX_PER_S,
    ) -> ApproachAggregate:
        composition = compose_types(labels)
        counted = sum(composition.values())
        tracks = list(tracks) if tracks is not None else [{"speed_px_per_s": None}] * counted
        return cls(
            approach=approach,
            vehicle_count=counted,
            type_composition=composition,
            density=density_for(counted, capacity),
            queue_estimate=queue_estimate(tracks, stationary_speed_px_per_s),
            queue_measured=any_speed_known(tracks),
            emergency_vehicle_flag=bool(emergency_vehicle_flag),
            dropped_unmappable=sum(1 for lbl in labels if project_type_for_coco_label(lbl) is None),
        )

    def to_summary(self) -> dict[str, Any]:
        """The three-field body a §7.2 `CameraPayload` approach carries."""
        return {
            "vehicle_count": self.vehicle_count,
            "density": self.density,
            "queue_estimate": self.queue_estimate,
        }

    def to_observation(
        self,
        *,
        junction_id: str,
        lane_id: str,
        confidence: float,
        detected_at: float,
        lane_fanout: bool = False,
    ) -> dict[str, Any]:
        """Emit one observation in `vision_mock.observe()`'s exact shape.

        The §7.1 keys come first and are the contract. Everything after
        them is additive, and includes the flags that say which of those
        contract fields are real measurements and which are not.
        """
        return {
            # -- §7.1 / LaneReading contract, in vision_mock's key order --
            "lane_id": lane_id,
            "approach": self.approach,
            "vehicle_count": self.vehicle_count,
            "halted_count": self.queue_estimate,
            "type_composition": dict(self.type_composition),
            # A camera cannot see accumulated waiting time - honesty rule 3.
            "wait_time_current": 0.0,
            "wait_time_max_single_vehicle": 0.0,
            "starvation_flag": False,
            # -- vision envelope (§7.2) --
            "confidence": round(float(confidence), 3),
            "source": SOURCE_TAG,
            # -- additive: what this feed can say that the mock cannot --
            "junction_id": junction_id,
            "detected_at": float(detected_at),
            "density": self.density,
            "queue_estimate": self.queue_estimate,
            "emergency_vehicle_flag": self.emergency_vehicle_flag,
            # -- additive: what this feed CANNOT say, stated explicitly --
            "emergency_flag_is_experimental": True,
            "wait_times_measured": False,
            "queue_measured": self.queue_measured,
            "undetectable_types": list(UNDETECTABLE_TYPES),
            "lane_fanout": bool(lane_fanout),
            "dropped_unmappable": self.dropped_unmappable,
        }


@dataclass(frozen=True)
class FrameObservation:
    """One junction, one frame - the detector's native, approach-keyed output."""

    junction_id: str
    frame_index: int
    detected_at: float
    approaches: dict[str, dict[str, Any]]
    emergency_vehicle_flag: bool
    confidence: float
    unassigned_detections: int = 0
    aggregates: dict[str, ApproachAggregate] = field(default_factory=dict, compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "junction_id": self.junction_id,
            "frame_index": self.frame_index,
            "detected_at": self.detected_at,
            "approaches": {k: dict(v) for k, v in self.approaches.items()},
            "emergency_vehicle_flag": self.emergency_vehicle_flag,
            "emergency_flag_is_experimental": True,
            "confidence": self.confidence,
            "unassigned_detections": self.unassigned_detections,
            "source": SOURCE_TAG,
        }

    def to_camera_payload(self):
        """Convert to the §7.2 MQTT payload. Imported lazily so this module
        stays usable with no `iot/` package present."""
        from iot.schema import CameraPayload

        return CameraPayload(
            junction_id=self.junction_id,
            frame_index=self.frame_index,
            detected_at=self.detected_at,
            approaches={k: dict(v) for k, v in self.approaches.items()},
            emergency_vehicle_flag=self.emergency_vehicle_flag,
            confidence=self.confidence,
            source=SOURCE_TAG,
        )


def assign_to_approaches(
    detections: Sequence[tuple[str, Sequence[float]]],
    rois: Sequence[ApproachROI],
) -> tuple[dict[str, list[tuple[str, Sequence[float]]]], int]:
    """Bucket detections by which ROI their foot-point falls in.

    A detection inside no ROI is DROPPED and counted, never attributed to
    the nearest region: a guessed lane is worse than a missing one, and
    `unassigned` rising is the signal that the polygons need redrawing.
    First match wins where ROIs overlap, so overlap is treated as a config
    error (see `VisionConfig.default`'s note) rather than double-counted.

    Only `item[0]` (label) and `item[1]` (box) are read, and each item is
    bucketed WHOLE, so a caller may pass longer tuples - `VisionDetector`
    passes `(label, box, track_id)` - and get them back intact.
    """
    buckets: dict[str, list[tuple]] = {roi.approach: [] for roi in rois}
    unassigned = 0
    for item in detections:
        point = foot_point(item[1])
        for roi in rois:
            if roi.contains(point):
                buckets[roi.approach].append(item)
                break
        else:
            unassigned += 1
    return buckets, unassigned


# ----------------------------------------------------------------------
# The detector
# ----------------------------------------------------------------------
class VisionDetector:
    """YOLOv8n over a video file or camera index, on CPU.

    Exposes `observe()` / `observe_all()` so it is drop-in swappable with
    `VisionMock` through `perception.vision_source.get_vision_source`.

    `lazy=True` builds the object without opening the source or loading
    the model. That matters: constructing with `source=0` eagerly would
    switch on the operator's webcam, which a factory-dispatch test has no
    business doing.
    """

    def __init__(
        self,
        source: str | int,
        config: VisionConfig | None = None,
        *,
        weights: str | Path = DEFAULT_WEIGHTS,
        junction_id: str = "J2",
        frame_size: tuple[int, int] = (1280, 720),
        lazy: bool = False,
        track: bool = True,
    ) -> None:
        self.source = source
        self.weights = Path(weights)
        self.track = track
        self.config = config or VisionConfig.default(junction_id=junction_id, frame_size=frame_size)
        self.frame_index = -1
        self.last_frame: FrameObservation | None = None

        self._model = None
        self._capture = None
        self._prev_centres: dict[int, tuple[float, float, float]] = {}
        if not lazy:
            self._ensure_model()

    # -- lazy resources -------------------------------------------------
    def _ensure_model(self):
        if self._model is None:
            if not self.weights.exists():
                raise FileNotFoundError(
                    f"YOLO weights not found at {self.weights}. "
                    "Ultralytics fetches yolov8n.pt on first use - see sim/media/README.md."
                )
            from ultralytics import YOLO  # heavy import, deferred

            self._model = YOLO(str(self.weights))
        return self._model

    def _ensure_capture(self):
        if self._capture is None:
            import cv2

            capture = cv2.VideoCapture(self.source)
            if not capture.isOpened():
                # Release before discarding: the object exists even when the
                # open failed, and every other path in this class releases
                # explicitly rather than leaning on the GC.
                capture.release()
                raise RuntimeError(f"could not open video source {self.source!r}")
            self._capture = capture
        return self._capture

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> VisionDetector:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- inference ------------------------------------------------------
    def _detections(self, frame) -> tuple[list[tuple[str, list[float], int | None]], dict[int, tuple[float, float]]]:
        """Run the model on one frame.

        Returns `(label, box, track_id)` triples plus a `{track_id: centre}`
        map used for speeds. The track id is carried ON the detection
        deliberately: an earlier version rebuilt the association afterwards
        by inverting `{track_id: foot_point}` and looking each box up by its
        foot-point. Two boxes can share a foot-point exactly - an occluded
        pair at the same lane position, verified - and the inverted dict
        then keeps only one, silently losing the other track's speed and so
        its contribution to the queue estimate.
        """
        model = self._ensure_model()
        if self.track:
            results = model.track(
                frame, persist=True, verbose=False, device="cpu",
                conf=self.config.confidence_threshold, imgsz=DEFAULT_IMAGE_SIZE,
            )
        else:
            results = model.predict(
                frame, verbose=False, device="cpu",
                conf=self.config.confidence_threshold, imgsz=DEFAULT_IMAGE_SIZE,
            )
        result = results[0]
        names = result.names

        detections: list[tuple[str, list[float], int | None]] = []
        centres: dict[int, tuple[float, float]] = {}
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return detections, centres

        xyxy = boxes.xyxy.tolist()
        classes = boxes.cls.tolist()
        ids = boxes.id.tolist() if getattr(boxes, "id", None) is not None else [None] * len(xyxy)
        for box, cls_idx, track_id in zip(xyxy, classes, ids):
            label = names.get(int(cls_idx), "") if isinstance(names, dict) else str(names[int(cls_idx)])
            if project_type_for_coco_label(label) is None:
                continue  # honesty rule 1 - never guess a type
            tid = None if track_id is None else int(track_id)
            detections.append((label, [float(v) for v in box], tid))
            if tid is not None:
                centres[tid] = foot_point(box)
        return detections, centres

    def _speeds(self, centres: dict[int, tuple[float, float]], timestamp_s: float) -> dict[int, float]:
        """Pixel speed per track, from the previous frame's foot-points."""
        speeds: dict[int, float] = {}
        for track_id, (cx, cy) in centres.items():
            previous = self._prev_centres.get(track_id)
            if previous is not None:
                px, py, pt = previous
                dt = timestamp_s - pt
                if dt > 0:
                    speeds[track_id] = math.hypot(cx - px, cy - py) / dt
            self._prev_centres[track_id] = (cx, cy, timestamp_s)
        # Forget tracks that have left the frame, so the dict stays bounded.
        for stale in set(self._prev_centres) - set(centres):
            self._prev_centres.pop(stale, None)
        return speeds

    def process_frame(self, frame, *, timestamp_s: float | None = None) -> FrameObservation:
        """One frame in, one approach-keyed FrameObservation out."""
        self.frame_index += 1
        detected_at = float(self.frame_index) if timestamp_s is None else float(timestamp_s)

        detections, centres = self._detections(frame)
        speeds = self._speeds(centres, detected_at)

        buckets, unassigned = assign_to_approaches(detections, self.config.rois)

        approaches: dict[str, dict[str, Any]] = {}
        aggregates: dict[str, ApproachAggregate] = {}
        emergency_any = False
        for roi in self.config.rois:
            items = buckets[roi.approach]
            labels = [item[0] for item in items]
            # The track id rides on the detection, so no lookup can lose it.
            tracks = [{"speed_px_per_s": speeds.get(item[2])} for item in items]
            density = density_for(len(labels), roi.capacity)
            emergency = detect_emergency_heuristic(tracks, density)
            emergency_any = emergency_any or emergency
            aggregate = ApproachAggregate.from_labels(
                roi.approach, labels,
                emergency_vehicle_flag=emergency,
                capacity=roi.capacity,
                tracks=tracks,
                stationary_speed_px_per_s=self.config.stationary_speed_px_per_s,
            )
            aggregates[roi.approach] = aggregate
            approaches[roi.approach] = aggregate.to_summary()

        observation = FrameObservation(
            junction_id=self.config.junction_id,
            frame_index=self.frame_index,
            detected_at=detected_at,
            approaches=approaches,
            emergency_vehicle_flag=emergency_any,
            confidence=self._frame_confidence(detections),
            unassigned_detections=unassigned,
            aggregates=aggregates,
        )
        self.last_frame = observation
        return observation

    @staticmethod
    def _frame_confidence(detections: Sequence[tuple]) -> float:
        """Frame-level confidence. With no detections this is 0.0 - an
        empty frame is not a confident one, it is an uninformative one."""
        return 0.0 if not detections else 0.9

    def run(self, max_frames: int = 100) -> Iterator[FrameObservation]:
        """Yield up to `max_frames` observations, stopping at end of video."""
        capture = self._ensure_capture()
        fps = capture.get(5) or 0.0  # cv2.CAP_PROP_FPS
        step = 1.0 / fps if fps and fps > 0 else 1.0
        for _ in range(max_frames):
            ok, frame = capture.read()
            if not ok:
                break
            yield self.process_frame(frame, timestamp_s=(self.frame_index + 1) * step)

    # -- vision_mock-compatible surface ----------------------------------
    def observe(self, reading) -> dict[str, Any]:
        """Emit one observation for a §7.1 `LaneReading`-shaped object.

        The camera measured an APPROACH; this hands that aggregate back
        under the caller's `lane_id`, flagged `lane_fanout: True`. That
        flag is the whole point - the per-lane split is declared, not
        observed. See the module docstring and NOTES-FOR-INTEGRATION.md.
        """
        lane_id = getattr(reading, "lane_id", None) or reading["lane_id"]
        approach = getattr(reading, "approach", None) or reading.get("approach", "unknown")

        frame = self.last_frame
        aggregate = frame.aggregates.get(approach) if frame is not None else None
        if aggregate is None:
            # No frame yet, or this approach has no ROI. Emit a shaped,
            # explicitly-empty observation rather than inventing counts.
            aggregate = ApproachAggregate.from_labels(
                approach if approach in APPROACHES else "unknown", [],
            )
            return {
                **aggregate.to_observation(
                    junction_id=self.config.junction_id, lane_id=lane_id,
                    confidence=0.0, detected_at=0.0, lane_fanout=True,
                ),
                "detector_ready": False,
            }
        return {
            **aggregate.to_observation(
                junction_id=self.config.junction_id, lane_id=lane_id,
                confidence=frame.confidence, detected_at=frame.detected_at, lane_fanout=True,
            ),
            "detector_ready": True,
        }

    def advance(self) -> FrameObservation | None:
        """Decode and process ONE frame, looping the clip at end of file.

        `observe()` reads `self.last_frame`, which only `process_frame()`
        sets — so without a pump the detector answers every lane from the
        "no frame yet" branch forever (`detector_ready: False`, all counts
        zero) while still reporting `source: vision_detector`. That is a
        feed that looks attached and measures nothing, which is exactly the
        quiet wrongness §17 forbids. `observe_all()` is the one entry point
        every consumer routes through (`DigitalTwin.update()` calls it once
        per step), so the pump belongs here rather than in each caller.

        A file source LOOPS: a 60s clip cannot cover a longer demo, and a
        detector that goes permanently blank part-way through is worse than
        one that repeats. A camera source has no end to rewind to, so a
        failed read there just leaves the previous frame standing.
        """
        capture = self._ensure_capture()
        ok, frame = capture.read()
        if not ok:
            if not isinstance(self.source, str):
                return self.last_frame  # camera hiccup — keep the last frame
            import cv2

            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
            if not ok:
                return self.last_frame
        fps = capture.get(5) or 0.0  # cv2.CAP_PROP_FPS
        step = 1.0 / fps if fps and fps > 0 else 1.0
        return self.process_frame(frame, timestamp_s=(self.frame_index + 1) * step)

    def observe_all(self, readings: dict[str, Any]) -> dict[str, dict[str, Any]]:
        self.advance()
        return {lane_id: self.observe(reading) for lane_id, reading in readings.items()}


# ----------------------------------------------------------------------
# Synthetic clip - makes the done-bar runnable with no downloaded footage
# ----------------------------------------------------------------------
def _vehicle_sprite():
    """A real, detectable vehicle image - or None.

    `ultralytics` ships `assets/bus.jpg` inside the wheel, so this needs
    no download and no committed binary. Measured on models/yolov8n.pt:
    the full photo yields `bus 0.873` plus four `person` detections -
    exactly the mix the pipeline should handle, one mappable vehicle
    (`bus -> truck`) and several labels honesty rule 1 requires it to drop.

    It is CROPPED to the bus's own bounding box, `xyxy = (23, 231, 805,
    757)`, read off that same measurement. The reason is not cosmetic:
    the source is 810x1080 portrait, so scaling the whole photo down to a
    ~120px-tall sprite leaves the bus itself around 60px and yolov8n
    finds nothing. Cropping first keeps the bus at the sprite's full
    height and it is detected at 0.81-0.92 across every size tried.
    """
    try:
        import cv2
        import ultralytics

        asset = Path(ultralytics.__file__).parent / "assets" / "bus.jpg"
        if not asset.exists():
            return None
        image = cv2.imread(str(asset))
        if image is None or image.shape[0] < 757 or image.shape[1] < 805:
            return image
        return image[231:757, 23:805]
    except Exception:  # noqa: BLE001 - a missing asset is a fallback, not a failure
        return None


def make_sample_video(
    frames: int = 100,
    size: tuple[int, int] = (640, 360),
    path: str | Path | None = None,
    fps: int = 10,
) -> Path:
    """Write a small clip with REAL detectable vehicles, and return its path.

    This exists so `python -m perception.vision_detector` proves the whole
    pipeline - decode, inference, box parsing, tracking, ROI assignment,
    aggregation - on a machine with no downloaded footage.

    The first version of this drew rectangles. It ran 100 frames cleanly
    and reported **zero vehicles in every frame**, so the assignment and
    aggregation path was never once exercised - a run that passes while
    proving nothing, which is this repo's named failure mode. It now
    composites a real photographed vehicle (`_vehicle_sprite`) moving
    through both ROIs at different speeds, and
    `tests.test_vision_detector` G2 asserts the counts actually come out
    non-zero.

    It is still NOT a detection benchmark or a stand-in for real footage -
    it is one photo on a flat background. Real fixed-camera footage
    remains a human step (`sim/media/README.md`).
    """
    import cv2
    import numpy as np

    width, height = int(size[0]), int(size[1])
    target = Path(path) if path is not None else REPO_ROOT / "sim" / "media" / "_synthetic_selftest.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)

    sprite = _vehicle_sprite()
    if sprite is not None:
        # A third of the frame height each, so both ROIs get a vehicle
        # that is comfortably above yolov8n's detection floor.
        sprite_h = max(96, height // 3)
        scale = sprite_h / sprite.shape[0]
        sprite = cv2.resize(sprite, (max(24, int(sprite.shape[1] * scale)), sprite_h))

    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"cv2 could not open a writer for {target} (missing mp4v codec?)")
    try:
        for index in range(frames):
            frame = np.full((height, width, 3), 60, dtype=np.uint8)
            cv2.line(frame, (0, height // 2), (width, height // 2), (110, 110, 110), 2)
            # One vehicle in each half of the frame, i.e. one per default
            # ROI, moving at different speeds so the tracker produces two
            # distinguishable speeds and the queue estimate has something
            # to separate.
            for lane, (speed, y_base) in enumerate(((6, 10), (2, height // 2 + 10))):
                x = (20 + index * speed + lane * 140) % max(1, width - 100)
                if sprite is not None:
                    h, w = sprite.shape[:2]
                    y0, x0 = y_base, x
                    y1, x1 = min(height, y0 + h), min(width, x0 + w)
                    if y1 > y0 and x1 > x0:
                        frame[y0:y1, x0:x1] = sprite[: y1 - y0, : x1 - x0]
                else:  # no asset available - degrade to a drawn box
                    cv2.rectangle(frame, (x, y_base), (x + 46, y_base + 26), (200, 200, 210), -1)
            writer.write(frame)
    finally:
        writer.release()
    return target


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _print_frame(frame: FrameObservation) -> None:
    parts = " | ".join(
        f"{approach}: {body['vehicle_count']:>2} veh, {body['density']:<9} q={body['queue_estimate']:>2}"
        for approach, body in sorted(frame.approaches.items())
    )
    flag = "  [EMERGENCY?]" if frame.emergency_vehicle_flag else ""
    print(f"  frame {frame.frame_index:>3}  t={frame.detected_at:7.2f}s  {parts}  "
          f"unassigned={frame.unassigned_detections}{flag}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run YOLOv8n over a video and report per-approach counts.")
    parser.add_argument("video", nargs="?", help="path to a video file")
    parser.add_argument("--webcam", type=int, default=None, help="use this camera index instead of a file")
    parser.add_argument("--junction", default="J2")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    parser.add_argument(
        "--no-track", action="store_true",
        help="predict only, no tracker. The queue estimate still runs but has no "
             "speeds to work from, so it falls back to the raw count and reports "
             "queue_measured=False - it is not disabled, it is declared unmeasured.",
    )
    parser.add_argument("--selftest", action="store_true", help="run tests.test_vision_detector and exit")
    parser.add_argument(
        "--make-sample", action="store_true",
        help="generate a synthetic clip and run against it (no footage needed)",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        from tests import test_vision_detector

        return test_vision_detector.main()

    if args.webcam is not None:
        source: str | int = args.webcam
    elif args.video:
        source = args.video
        if not Path(source).exists():
            print(f"video not found: {source}")
            print("No footage ships with this repo - see sim/media/README.md, or pass --make-sample.")
            return 1
    elif args.make_sample:
        source = str(make_sample_video(frames=args.frames))
        print(f"generated synthetic clip: {source}")
    else:
        parser.error("give a video path, --webcam N, --make-sample, or --selftest")
        return 2

    probe_size = (1280, 720)
    if args.webcam is None:
        import cv2

        probe = cv2.VideoCapture(source)
        if probe.isOpened():
            probe_size = (int(probe.get(3)) or 1280, int(probe.get(4)) or 720)
        probe.release()

    config = VisionConfig.default(junction_id=args.junction, frame_size=probe_size)
    print(f"source={source}  junction={config.junction_id}  frame={probe_size[0]}x{probe_size[1]}  "
          f"approaches={list(config.approaches)}")
    if config.is_placeholder:
        print("!! ROI polygons are PLACEHOLDERS - not measured against this camera. "
              "Redraw them before reading any per-approach number as real.")
    print("!! auto / ambulance have no COCO class and are reported as 0 by construction.")
    print("!! emergency_vehicle_flag is an EXPERIMENTAL behavioural heuristic, not a classification.\n")

    detector = VisionDetector(source, config=config, weights=args.weights, track=not args.no_track)
    count = 0
    try:
        for frame in detector.run(max_frames=args.frames):
            _print_frame(frame)
            count += 1
    finally:
        detector.close()
    print(f"\nprocessed {count} frames without error")
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
