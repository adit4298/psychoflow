"""Hand-scored assertions for the YOLO vision detector and its factory.

Run: `python -m tests.test_vision_detector`
     (also reached by `python -m perception.vision_detector --selftest`)

Sections
  A. COCO -> project type mapping   - the measured gap, and refusing to guess
  B. ROI polygon assignment         - which approach a detection belongs to
  C. density / queue derivation     - boundary-correct, monotonic
  D. output contract                - MUST match perception/vision_mock.py
  E. config validation              - the placeholder ROIs are schema-checked
  F. get_vision_source factory      - mode dispatch, default, refusal
  G. real inference                 - a synthetic clip through the real model

Everything except G is pure-function and runs with no video, no camera and
no model load.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_results: list[tuple[bool, str]] = []


def check(label: str, fn) -> None:
    try:
        fn()
        _results.append((True, label))
    except Exception as exc:  # noqa: BLE001
        _results.append((False, f"{label}  ->  {type(exc).__name__}: {exc}"))


def expect_raises(exc_type, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"expected {exc_type.__name__}, nothing raised")


# ----------------------------------------------------------------------
# A. COCO -> project type mapping
# ----------------------------------------------------------------------
def a1_coco_labels_map_to_project_types() -> None:
    from perception.vision_detector import project_type_for_coco_label

    assert project_type_for_coco_label("bicycle") == "bike"
    assert project_type_for_coco_label("motorcycle") == "bike"
    assert project_type_for_coco_label("car") == "car"
    assert project_type_for_coco_label("truck") == "truck"
    assert project_type_for_coco_label("bus") == "truck"


def a2_unmappable_coco_labels_return_none_not_car() -> None:
    """sim/media/README.md: 'an auto-rickshaw silently counted as a car is
    a wrong number on the dashboard, not a rounding error.'"""
    from perception.vision_detector import project_type_for_coco_label

    for label in ("person", "traffic light", "dog", "boat", "", "auto", "ambulance"):
        assert project_type_for_coco_label(label) is None, f"{label!r} must not map to a vehicle type"


def a3_auto_and_ambulance_are_structurally_undetectable_and_stay_zero() -> None:
    """COCO has no class for either. They must be present-and-zero, never
    absent (which reads as 'field missing') and never folded into car."""
    from perception.vision_detector import UNDETECTABLE_TYPES, compose_types

    assert set(UNDETECTABLE_TYPES) == {"auto", "ambulance"}, UNDETECTABLE_TYPES
    comp = compose_types(["bicycle", "motorcycle", "car", "truck", "bus", "person", "car"])
    assert comp == {"bike": 2, "auto": 0, "car": 2, "truck": 2, "ambulance": 0}, comp


def a4_emergency_flag_never_increments_the_ambulance_count() -> None:
    """The heuristic is EXPERIMENTAL. Letting it write type_composition
    would conflate 'flagged, unverified' with 'counted'."""
    from perception.vision_detector import ApproachAggregate

    agg = ApproachAggregate.from_labels("north", ["car", "truck"], emergency_vehicle_flag=True)
    assert agg.emergency_vehicle_flag is True
    assert agg.type_composition["ambulance"] == 0, agg.type_composition


# ----------------------------------------------------------------------
# B. ROI polygon assignment
# ----------------------------------------------------------------------
def b1_point_in_polygon_is_correct_on_a_known_square() -> None:
    from perception.vision_detector import point_in_polygon

    square = [(10, 10), (110, 10), (110, 110), (10, 110)]
    assert point_in_polygon((60, 60), square) is True
    assert point_in_polygon((5, 60), square) is False
    assert point_in_polygon((200, 200), square) is False


def b2_detection_foot_point_is_the_bottom_centre_of_the_box() -> None:
    """A vehicle's ground contact is the bottom edge of its box; using the
    centroid puts a tall truck a lane further away than it is."""
    from perception.vision_detector import foot_point

    assert foot_point((100.0, 40.0, 200.0, 140.0)) == (150.0, 140.0)


def b3_detections_assign_to_the_containing_roi_only() -> None:
    from perception.vision_detector import ApproachROI, assign_to_approaches

    rois = [
        ApproachROI(approach="north", polygon=[(0, 0), (100, 0), (100, 100), (0, 100)]),
        ApproachROI(approach="west", polygon=[(200, 0), (300, 0), (300, 100), (200, 100)]),
    ]
    dets = [
        ("car", (10.0, 10.0, 30.0, 50.0)),      # foot (20, 50)  -> north
        ("truck", (210.0, 10.0, 240.0, 60.0)),  # foot (225, 60) -> west
        ("car", (500.0, 500.0, 520.0, 540.0)),  # foot (510,540) -> nowhere
    ]
    by_approach, unassigned = assign_to_approaches(dets, rois)
    assert [lbl for lbl, _ in by_approach["north"]] == ["car"], by_approach
    assert [lbl for lbl, _ in by_approach["west"]] == ["truck"], by_approach
    assert unassigned == 1, unassigned


def b4_an_unassigned_detection_is_dropped_not_misattributed() -> None:
    from perception.vision_detector import ApproachROI, assign_to_approaches

    rois = [ApproachROI(approach="north", polygon=[(0, 0), (10, 0), (10, 10), (0, 10)])]
    by_approach, unassigned = assign_to_approaches([("car", (900.0, 900.0, 910.0, 910.0))], rois)
    assert by_approach["north"] == []
    assert unassigned == 1


# ----------------------------------------------------------------------
# C. density / queue derivation
# ----------------------------------------------------------------------
def c1_density_buckets_are_boundary_correct_and_monotonic() -> None:
    from perception.vision_detector import DENSITY_FREE_MAX, DENSITY_MODERATE_MAX, density_for

    assert density_for(0) == "free"
    assert density_for(DENSITY_FREE_MAX) == "free"
    assert density_for(DENSITY_FREE_MAX + 1) == "moderate"
    assert density_for(DENSITY_MODERATE_MAX) == "moderate"
    assert density_for(DENSITY_MODERATE_MAX + 1) == "congested"

    order = {"free": 0, "moderate": 1, "congested": 2}
    seen = [order[density_for(n)] for n in range(0, 60)]
    assert seen == sorted(seen), "density must be monotonic in vehicle count"


def c2_density_is_normalised_by_roi_capacity_when_given() -> None:
    """A 2-lane approach and a 4-lane approach at the same raw count are
    not equally congested. Capacity is optional and defaults to raw count."""
    from perception.vision_detector import density_for

    assert density_for(12, capacity=40) == "free"
    assert density_for(12, capacity=12) == "congested"


def c3_queue_estimate_counts_only_stationary_tracks() -> None:
    from perception.vision_detector import queue_estimate

    tracks = [
        {"speed_px_per_s": 0.2}, {"speed_px_per_s": 0.0},
        {"speed_px_per_s": 40.0}, {"speed_px_per_s": 12.0},
    ]
    assert queue_estimate(tracks, stationary_speed_px_per_s=1.0) == 2


def c4_queue_estimate_falls_back_to_count_when_no_speeds_are_known() -> None:
    """Frame 1 has no track history. Reporting 0 would read as 'no queue',
    which is a stronger claim than 'not measurable yet'."""
    from perception.vision_detector import queue_estimate

    assert queue_estimate([{"speed_px_per_s": None}, {"speed_px_per_s": None}]) == 2


# ----------------------------------------------------------------------
# D. output contract - must match perception/vision_mock.py
# ----------------------------------------------------------------------
def d1_observation_carries_every_vision_mock_key() -> None:
    """VisionMock.observe() emits LaneReading.to_dict() + confidence +
    source. The detector must emit the same key set or a consumer written
    against one feed breaks on the other."""
    from dataclasses import fields as dc_fields

    from perception.lane_sensor import LaneReading

    from perception.vision_detector import ApproachAggregate

    obs = ApproachAggregate.from_labels("north", ["car", "bicycle"]).to_observation(
        junction_id="J2", lane_id="N2_J2_0", confidence=0.9, detected_at=12.0
    )
    expected = {f.name for f in dc_fields(LaneReading)} | {"confidence", "source"}
    missing = expected - set(obs)
    assert not missing, f"detector observation is missing vision_mock keys: {sorted(missing)}"


def d2_observation_adds_the_four_new_fields() -> None:
    from perception.vision_detector import ApproachAggregate

    obs = ApproachAggregate.from_labels("north", ["car"]).to_observation(
        junction_id="J2", lane_id="N2_J2_0", confidence=0.9, detected_at=12.0
    )
    for key in ("density", "queue_estimate", "emergency_vehicle_flag", "emergency_flag_is_experimental"):
        assert key in obs, f"missing new field {key!r}"
    assert obs["emergency_flag_is_experimental"] is True, "the heuristic must self-label"
    assert obs["source"] == "vision_detector", obs["source"]


def d3_fields_the_camera_cannot_measure_are_explicitly_unknown() -> None:
    """A camera cannot see accumulated waiting time. The contract requires
    the keys, so they are present - but as declared-unknown zeros with a
    flag, not as a measurement."""
    from perception.vision_detector import ApproachAggregate

    obs = ApproachAggregate.from_labels("north", ["car"]).to_observation(
        junction_id="J2", lane_id="N2_J2_0", confidence=0.9, detected_at=12.0
    )
    assert obs["wait_time_current"] == 0.0
    assert obs["wait_time_max_single_vehicle"] == 0.0
    assert obs["starvation_flag"] is False
    assert obs["wait_times_measured"] is False, "must declare the wait fields are not measured"


def d4_observation_is_json_serialisable_and_feeds_a_camera_payload() -> None:
    import json

    from iot.schema import CameraPayload

    from perception.vision_detector import FrameObservation

    frame = FrameObservation(
        junction_id="J2", frame_index=3, detected_at=15.0,
        approaches={"north": {"vehicle_count": 5, "density": "moderate", "queue_estimate": 2}},
        emergency_vehicle_flag=False, confidence=0.88, unassigned_detections=1,
    )
    json.dumps(frame.to_dict())
    payload = frame.to_camera_payload()
    assert isinstance(payload, CameraPayload)
    assert payload.approaches["north"]["density"] == "moderate"


# ----------------------------------------------------------------------
# E. config validation
# ----------------------------------------------------------------------
def e1_roi_config_is_schema_validated() -> None:
    from perception.vision_detector import ApproachROI, VisionConfigError

    expect_raises(VisionConfigError, ApproachROI, approach="north", polygon=[(0, 0), (1, 1)])
    expect_raises(VisionConfigError, ApproachROI, approach="upward", polygon=[(0, 0), (1, 0), (1, 1)])
    expect_raises(VisionConfigError, ApproachROI, approach="north", polygon=[(0, 0), (1, 0), ("x", 1)])
    ApproachROI(approach="north", polygon=[(0, 0), (1, 0), (1, 1)])  # minimum legal polygon


def e2_config_round_trips_through_json() -> None:
    from perception.vision_detector import VisionConfig

    cfg = VisionConfig.default(junction_id="J2", frame_size=(1280, 720))
    assert VisionConfig.from_dict(cfg.to_dict()) == cfg
    assert cfg.junction_id == "J2"
    assert len(cfg.rois) >= 2


def e3_default_config_is_marked_a_placeholder() -> None:
    """No real footage exists yet, so the shipped polygons were never
    measured against a real camera. That must be self-evident in the data,
    not only in a docstring."""
    from perception.vision_detector import VisionConfig

    cfg = VisionConfig.default(junction_id="J2", frame_size=(1280, 720))
    assert cfg.is_placeholder is True
    assert VisionConfig.from_dict(cfg.to_dict()).is_placeholder is True


def e4_config_rejects_rois_outside_the_frame() -> None:
    from perception.vision_detector import ApproachROI, VisionConfig, VisionConfigError

    expect_raises(
        VisionConfigError, VisionConfig,
        junction_id="J2", frame_size=(640, 480),
        rois=[ApproachROI(approach="north", polygon=[(0, 0), (5000, 0), (5000, 5000)])],
    )


# ----------------------------------------------------------------------
# F. get_vision_source factory
# ----------------------------------------------------------------------
def f1_default_mode_is_mock() -> None:
    from perception.vision_mock import VisionMock
    from perception.vision_source import DEFAULT_MODE, get_vision_source

    assert DEFAULT_MODE == "mock"
    assert isinstance(get_vision_source(), VisionMock)
    assert isinstance(get_vision_source("mock"), VisionMock)


def f2_detector_mode_returns_a_detector() -> None:
    from perception.vision_detector import VisionDetector
    from perception.vision_source import get_vision_source

    src = get_vision_source("detector", source=0, junction_id="J2", lazy=True)
    assert isinstance(src, VisionDetector)


def f3_unknown_mode_raises() -> None:
    from perception.vision_source import get_vision_source

    expect_raises(ValueError, get_vision_source, "yolo")
    expect_raises(ValueError, get_vision_source, "")
    expect_raises(ValueError, get_vision_source, None)


def f4_both_modes_expose_the_same_call_surface() -> None:
    """The factory is only useful if the two are interchangeable."""
    from perception.vision_source import VISION_MODES, get_vision_source

    assert set(VISION_MODES) == {"mock", "detector"}
    mock = get_vision_source("mock")
    det = get_vision_source("detector", source=0, junction_id="J2", lazy=True)
    for method in ("observe", "observe_all"):
        assert callable(getattr(mock, method, None)), f"mock lacks {method}"
        assert callable(getattr(det, method, None)), f"detector lacks {method}"


# ----------------------------------------------------------------------
# G. real inference over a synthetic clip
# ----------------------------------------------------------------------
def g1_detector_runs_real_frames_without_crashing() -> None:
    """Exercises the whole path - decode, model, box parsing, ROI
    assignment, aggregation - on a generated clip, so the done-bar needs
    no downloaded footage. It asserts the PIPELINE runs, deliberately NOT
    that YOLO finds anything: synthetic rectangles are not vehicles."""
    from perception.vision_detector import DEFAULT_WEIGHTS, VisionConfig, VisionDetector, make_sample_video

    if not Path(DEFAULT_WEIGHTS).exists():
        raise AssertionError(f"weights missing at {DEFAULT_WEIGHTS} - see sim/media/README.md")

    video = make_sample_video(frames=12, size=(640, 360))
    cfg = VisionConfig.default(junction_id="J2", frame_size=(640, 360))
    det = VisionDetector(source=str(video), config=cfg)
    frames = list(det.run(max_frames=12))
    det.close()

    assert len(frames) == 12, f"expected 12 frames, got {len(frames)}"
    for frame in frames:
        assert set(frame.approaches) == {roi.approach for roi in cfg.rois}
        for body in frame.approaches.values():
            assert body["density"] in ("free", "moderate", "congested")
            assert body["vehicle_count"] >= 0
    assert frames[0].frame_index == 0 and frames[-1].frame_index == 11


def g2_the_sample_clip_actually_produces_detections() -> None:
    """The guard against a vacuous done-bar.

    G1's first version drew rectangles: it ran 100 frames cleanly and
    found ZERO vehicles in every one, so assignment and aggregation were
    never exercised at all. `make_sample_video` now composites a real
    photographed vehicle; this asserts the counts come out non-zero, so
    "100 frames without crashing" cannot be satisfied by a pipeline that
    silently detects nothing.
    """
    from perception.vision_detector import VisionConfig, VisionDetector, make_sample_video

    video = make_sample_video(frames=10, size=(640, 360))
    cfg = VisionConfig.default(junction_id="J2", frame_size=(640, 360))
    det = VisionDetector(source=str(video), config=cfg)
    frames = list(det.run(max_frames=10))
    det.close()

    total = sum(body["vehicle_count"] for f in frames for body in f.approaches.values())
    assert total > 0, (
        "the sample clip produced ZERO detections across every approach - "
        "the done-bar would pass while proving nothing"
    )
    hit = [f for f in frames if any(b["vehicle_count"] for b in f.approaches.values())]
    assert len(hit) >= 5, f"only {len(hit)}/10 frames detected anything"
    # `bus` maps to `truck`; `person` must be dropped, never counted.
    comps = [agg.type_composition for f in frames for agg in f.aggregates.values()]
    assert any(c["truck"] > 0 for c in comps), f"expected the bus to map to truck: {comps[:4]}"
    assert all(c["auto"] == 0 and c["ambulance"] == 0 for c in comps), "undetectable types must stay 0"


CHECKS = [
    ("A1 COCO labels map to project types", a1_coco_labels_map_to_project_types),
    ("A2 unmappable COCO labels return None, not car", a2_unmappable_coco_labels_return_none_not_car),
    ("A3 auto/ambulance are undetectable and stay zero", a3_auto_and_ambulance_are_structurally_undetectable_and_stay_zero),
    ("A4 emergency flag never increments ambulance count", a4_emergency_flag_never_increments_the_ambulance_count),
    ("B1 point-in-polygon correct on a known square", b1_point_in_polygon_is_correct_on_a_known_square),
    ("B2 foot point is the bottom centre of the box", b2_detection_foot_point_is_the_bottom_centre_of_the_box),
    ("B3 detections assign to the containing ROI only", b3_detections_assign_to_the_containing_roi_only),
    ("B4 an unassigned detection is dropped, not misattributed", b4_an_unassigned_detection_is_dropped_not_misattributed),
    ("C1 density buckets boundary-correct and monotonic", c1_density_buckets_are_boundary_correct_and_monotonic),
    ("C2 density normalises by ROI capacity when given", c2_density_is_normalised_by_roi_capacity_when_given),
    ("C3 queue estimate counts only stationary tracks", c3_queue_estimate_counts_only_stationary_tracks),
    ("C4 queue estimate falls back when speeds unknown", c4_queue_estimate_falls_back_to_count_when_no_speeds_are_known),
    ("D1 observation carries every vision_mock key", d1_observation_carries_every_vision_mock_key),
    ("D2 observation adds the four new fields", d2_observation_adds_the_four_new_fields),
    ("D3 unmeasurable fields are explicitly unknown", d3_fields_the_camera_cannot_measure_are_explicitly_unknown),
    ("D4 observation is JSON-serialisable and feeds CameraPayload", d4_observation_is_json_serialisable_and_feeds_a_camera_payload),
    ("E1 ROI config is schema-validated", e1_roi_config_is_schema_validated),
    ("E2 config round-trips through JSON", e2_config_round_trips_through_json),
    ("E3 default config is marked a placeholder", e3_default_config_is_marked_a_placeholder),
    ("E4 config rejects ROIs outside the frame", e4_config_rejects_rois_outside_the_frame),
    ("F1 default mode is mock", f1_default_mode_is_mock),
    ("F2 detector mode returns a detector", f2_detector_mode_returns_a_detector),
    ("F3 unknown mode raises", f3_unknown_mode_raises),
    ("F4 both modes expose the same call surface", f4_both_modes_expose_the_same_call_surface),
    ("G1 detector runs real frames without crashing", g1_detector_runs_real_frames_without_crashing),
    ("G2 the sample clip actually produces detections", g2_the_sample_clip_actually_produces_detections),
]


def main() -> int:
    for label, fn in CHECKS:
        check(label, fn)
    passed = sum(1 for ok, _ in _results if ok)
    for ok, label in _results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\ntests.test_vision_detector: {passed}/{len(_results)} passed")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
