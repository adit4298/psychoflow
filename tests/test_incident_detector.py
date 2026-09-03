"""Hand-scored assertions for `perception/incident_detector.py`.

Run: `python -m tests.test_incident_detector`
     (also reached by `python -m perception.incident_detector`)

Sections
  A. pixel -> metre calibration        (distance helper a)
  B. twin-frame distance to stop line  (distance helper b, net-file sourced)
  C. breakdown
  D. accident
  E. major_congestion
  F. precedence + output contract
  G. purity and import hygiene
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NET_432 = REPO / "sim" / "networks" / "generated" / "corridor_432.net.xml"

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
# Fixtures
# ----------------------------------------------------------------------
def _track(track_id, *, stationary_for_s=0.0, x=100.0, y=200.0, speed_mps=8.0,
           approach="north", lane_index=0):
    from perception.incident_detector import VehicleTrack

    return VehicleTrack(
        track_id=track_id, approach=approach, lane_index=lane_index,
        x=x, y=y, speed_mps=speed_mps, stationary_for_s=stationary_for_s,
    )


def _flow(*, mean_speed_mps=9.0, free_flow_speed_mps=13.89, queue_length_m=20.0, vehicle_count=8):
    from perception.incident_detector import LaneFlowState

    return LaneFlowState(
        lane_id="N2_J2_0", approach="north", lane_index=0,
        mean_speed_mps=mean_speed_mps, free_flow_speed_mps=free_flow_speed_mps,
        queue_length_m=queue_length_m, vehicle_count=vehicle_count,
    )


# ----------------------------------------------------------------------
# A. pixel -> metre calibration
# ----------------------------------------------------------------------
def a1_two_point_calibration_computes_the_expected_scale() -> None:
    """A 300px separation known to be 12m -> 0.04 m/px. Hand-computed."""
    from perception.incident_detector import calibrate_pixels

    cal = calibrate_pixels((100.0, 100.0), (400.0, 100.0), 12.0)
    assert abs(cal.metres_per_pixel - 0.04) < 1e-9, cal.metres_per_pixel
    assert abs(cal.pixels_to_metres(150.0) - 6.0) < 1e-9
    assert abs(cal.pixels_to_metres(0.0)) < 1e-9


def a2_calibration_uses_euclidean_pixel_distance() -> None:
    """A 3-4-5 triangle: 30/40 px apart is 50 px, known to be 10m."""
    from perception.incident_detector import calibrate_pixels

    cal = calibrate_pixels((0.0, 0.0), (30.0, 40.0), 10.0)
    assert abs(cal.pixel_distance - 50.0) < 1e-9, cal.pixel_distance
    assert abs(cal.metres_per_pixel - 0.2) < 1e-9
    assert abs(cal.pixels_to_metres(25.0) - 5.0) < 1e-9


def a3_calibration_refuses_degenerate_input() -> None:
    from perception.incident_detector import CalibrationError, calibrate_pixels

    expect_raises(CalibrationError, calibrate_pixels, (10.0, 10.0), (10.0, 10.0), 12.0)
    expect_raises(CalibrationError, calibrate_pixels, (0.0, 0.0), (100.0, 0.0), 0.0)
    expect_raises(CalibrationError, calibrate_pixels, (0.0, 0.0), (100.0, 0.0), -3.0)
    expect_raises(CalibrationError, calibrate_pixels, (0.0, 0.0), (100.0, 0.0), float("nan"))


def a4_calibration_reports_a_bounded_confidence() -> None:
    """A calibration over 8px of image is far less trustworthy than one
    over 400px, and that must be legible in the output, not implied."""
    from perception.incident_detector import calibrate_pixels

    wide = calibrate_pixels((0.0, 0.0), (500.0, 0.0), 20.0)
    narrow = calibrate_pixels((0.0, 0.0), (8.0, 0.0), 20.0)
    assert 0.0 <= narrow.confidence < wide.confidence <= 1.0, (narrow.confidence, wide.confidence)


# ----------------------------------------------------------------------
# B. twin-frame distance to stop line
# ----------------------------------------------------------------------
def b1_junction_coord_comes_from_the_net_file_not_the_generator() -> None:
    """CLAUDE.md standing rule: netconvert shifts the corridor by
    [150, 150], so J2 authored at (300, 0) is really at (450, 150).
    Reading it from the net file is what makes this correct."""
    if not NET_432.exists():
        raise AssertionError(f"net file missing: {NET_432}")
    from perception.incident_detector import load_junction_coord

    x, y = load_junction_coord(NET_432, "J2")
    assert (round(x, 3), round(y, 3)) == (450.0, 150.0), (x, y)
    assert (x, y) != (300.0, 0.0), "read the shifted net-file coord, not the authored one"
    assert round(load_junction_coord(NET_432, "J1")[0], 3) == 150.0


def b2_stop_line_coord_comes_from_the_lane_shape() -> None:
    """A lane's shape ends AT its stop line, which sits ~13.6m short of
    the junction centre - using the centre overstates the distance."""
    from perception.incident_detector import load_junction_coord, load_stop_line_coord

    stop_x, stop_y = load_stop_line_coord(NET_432, "J1_J2_0")
    jx, _jy = load_junction_coord(NET_432, "J2")
    assert stop_x < jx, (stop_x, jx)
    assert 5.0 < (jx - stop_x) < 30.0, f"stop line {jx - stop_x:.1f}m short of centre - implausible"


def b3_distance_to_a_known_stop_line_is_exact_and_confident() -> None:
    from perception.incident_detector import distance_to_stop_line

    est = distance_to_stop_line((400.0, 142.0), junction_xy=(450.0, 150.0), stop_line_xy=(436.4, 142.0))
    assert abs(est.distance_m - 36.4) < 1e-6, est.distance_m
    assert est.method == "stop_line"
    assert est.confidence >= 0.9, est.confidence


def b4_distance_from_the_junction_centre_is_less_confident_and_offset() -> None:
    """With no stop-line coord the fallback subtracts a nominal offset
    from the centre distance, and says so via a lower confidence."""
    from perception.incident_detector import DEFAULT_STOP_LINE_OFFSET_M, distance_to_stop_line

    exact = distance_to_stop_line((400.0, 150.0), junction_xy=(450.0, 150.0), stop_line_xy=(436.4, 150.0))
    fallback = distance_to_stop_line((400.0, 150.0), junction_xy=(450.0, 150.0))
    assert fallback.method == "junction_centre"
    assert fallback.confidence < exact.confidence
    assert abs(fallback.distance_m - (50.0 - DEFAULT_STOP_LINE_OFFSET_M)) < 1e-6, fallback.distance_m


def b5_distance_never_goes_negative() -> None:
    """A vehicle inside the junction is 0m from the stop line, not -8m."""
    from perception.incident_detector import distance_to_stop_line

    est = distance_to_stop_line((449.0, 150.0), junction_xy=(450.0, 150.0))
    assert est.distance_m == 0.0, est.distance_m


def b6_pixel_positions_route_through_the_calibration() -> None:
    from perception.incident_detector import calibrate_pixels, distance_to_stop_line_px

    cal = calibrate_pixels((0.0, 0.0), (400.0, 0.0), 20.0)  # 0.05 m/px
    est = distance_to_stop_line_px((100.0, 0.0), stop_line_px=(500.0, 0.0), calibration=cal)
    assert abs(est.distance_m - 20.0) < 1e-9, est.distance_m
    assert est.method == "pixel_calibration"
    assert est.confidence == cal.confidence


# ----------------------------------------------------------------------
# C. breakdown
# ----------------------------------------------------------------------
def c1_one_stationary_vehicle_while_others_flow_is_a_breakdown() -> None:
    from perception.incident_detector import classify_breakdown

    tracks = [
        _track("t1", stationary_for_s=22.0, speed_mps=0.0),
        _track("t2", speed_mps=9.0), _track("t3", speed_mps=8.5), _track("t4", speed_mps=10.0),
    ]
    found = classify_breakdown(tracks, _flow(mean_speed_mps=9.0))
    assert found is not None, "a 22s stationary vehicle in a flowing lane is a breakdown"
    assert found.type == "breakdown"
    assert found.origin_track_id == "t1"
    assert found.lane_index == 0 and found.approach == "north"


def c2_ten_seconds_stationary_is_below_the_threshold() -> None:
    from perception.incident_detector import STATIONARY_MIN_S, classify_breakdown

    assert STATIONARY_MIN_S == 15.0
    tracks = [_track("t1", stationary_for_s=10.0, speed_mps=0.0), _track("t2"), _track("t3")]
    assert classify_breakdown(tracks, _flow()) is None


def c3_a_stationary_vehicle_in_a_stopped_lane_is_not_a_breakdown() -> None:
    """'others flowing' is load-bearing: at a red light every vehicle is
    stationary and nothing has broken down."""
    from perception.incident_detector import classify_breakdown

    tracks = [
        _track("t1", stationary_for_s=40.0, speed_mps=0.0),
        _track("t2", stationary_for_s=30.0, speed_mps=0.0),
        _track("t3", stationary_for_s=25.0, speed_mps=0.0),
    ]
    assert classify_breakdown(tracks, _flow(mean_speed_mps=0.2)) is None


def c4_two_stationary_vehicles_are_not_a_breakdown() -> None:
    """Must fall through to the accident classifier instead - the two are
    mutually exclusive, not merely ordered."""
    from perception.incident_detector import classify_breakdown

    tracks = [
        _track("t1", stationary_for_s=30.0, speed_mps=0.0, x=100.0, y=200.0),
        _track("t2", stationary_for_s=28.0, speed_mps=0.0, x=112.0, y=206.0),
        _track("t3", speed_mps=9.0), _track("t4", speed_mps=9.0),
    ]
    assert classify_breakdown(tracks, _flow(mean_speed_mps=9.0)) is None


# ----------------------------------------------------------------------
# D. accident
# ----------------------------------------------------------------------
def d1_two_clustered_stationary_vehicles_with_speed_collapse_is_an_accident() -> None:
    from perception.incident_detector import classify_accident

    tracks = [
        _track("t1", stationary_for_s=30.0, speed_mps=0.0, x=100.0, y=200.0),
        _track("t2", stationary_for_s=26.0, speed_mps=0.0, x=118.0, y=208.0),
        _track("t3", speed_mps=1.0),
    ]
    found = classify_accident(tracks, _flow(mean_speed_mps=1.2, free_flow_speed_mps=13.89))
    assert found is not None
    assert found.type == "accident"
    assert set(found.member_track_ids) == {"t1", "t2"}, found.member_track_ids


def d2_stationary_but_scattered_is_not_an_accident() -> None:
    """Two stopped vehicles 400px apart are a queue, not a collision."""
    from perception.incident_detector import classify_accident

    tracks = [
        _track("t1", stationary_for_s=30.0, speed_mps=0.0, x=50.0, y=100.0),
        _track("t2", stationary_for_s=30.0, speed_mps=0.0, x=470.0, y=460.0),
    ]
    assert classify_accident(tracks, _flow(mean_speed_mps=1.0)) is None


def d3_clustered_but_no_speed_collapse_is_not_an_accident() -> None:
    """Two vehicles parked side by side while the lane still flows is not
    a collision - the speed collapse is what distinguishes them."""
    from perception.incident_detector import classify_accident

    tracks = [
        _track("t1", stationary_for_s=30.0, speed_mps=0.0, x=100.0, y=200.0),
        _track("t2", stationary_for_s=30.0, speed_mps=0.0, x=115.0, y=205.0),
    ]
    assert classify_accident(tracks, _flow(mean_speed_mps=11.0, free_flow_speed_mps=13.89)) is None


def d4_accident_severity_outranks_breakdown_severity() -> None:
    from perception.incident_detector import SEVERITY_RANK, classify_accident, classify_breakdown

    acc = classify_accident(
        [
            _track("t1", stationary_for_s=30.0, speed_mps=0.0, x=100.0, y=200.0),
            _track("t2", stationary_for_s=30.0, speed_mps=0.0, x=118.0, y=208.0),
        ],
        _flow(mean_speed_mps=1.0),
    )
    brk = classify_breakdown(
        [_track("t1", stationary_for_s=30.0, speed_mps=0.0), _track("t2"), _track("t3")],
        _flow(mean_speed_mps=9.0),
    )
    assert SEVERITY_RANK[acc.severity] > SEVERITY_RANK[brk.severity], (acc.severity, brk.severity)


# ----------------------------------------------------------------------
# E. major_congestion
# ----------------------------------------------------------------------
def e1_a_growing_queue_with_no_stationary_origin_is_major_congestion() -> None:
    from perception.incident_detector import classify_major_congestion

    tracks = [_track(f"t{i}", stationary_for_s=6.0, speed_mps=1.2) for i in range(10)]
    found = classify_major_congestion(tracks, _flow(queue_length_m=95.0), queue_history=[30.0, 50.0, 72.0, 95.0])
    assert found is not None
    assert found.type == "major_congestion"
    assert found.origin_track_id is None, "congestion has no single origin, by definition"


def e2_a_flat_queue_is_not_major_congestion() -> None:
    from perception.incident_detector import classify_major_congestion

    tracks = [_track(f"t{i}", stationary_for_s=6.0, speed_mps=1.2) for i in range(10)]
    assert classify_major_congestion(tracks, _flow(queue_length_m=95.0),
                                     queue_history=[94.0, 95.0, 95.0, 95.0]) is None


def e3_a_shrinking_queue_is_not_major_congestion() -> None:
    from perception.incident_detector import classify_major_congestion

    tracks = [_track(f"t{i}", stationary_for_s=6.0, speed_mps=1.2) for i in range(10)]
    assert classify_major_congestion(tracks, _flow(queue_length_m=40.0),
                                     queue_history=[95.0, 80.0, 60.0, 40.0]) is None


def e4_a_growing_queue_with_a_stationary_origin_is_not_major_congestion() -> None:
    """A queue growing BEHIND a broken-down vehicle is a breakdown's
    consequence. Reporting it as congestion would send the wrong crew."""
    from perception.incident_detector import classify_major_congestion

    tracks = [_track(f"t{i}", stationary_for_s=6.0, speed_mps=1.2) for i in range(9)]
    tracks.append(_track("stuck", stationary_for_s=40.0, speed_mps=0.0))
    assert classify_major_congestion(tracks, _flow(queue_length_m=95.0),
                                     queue_history=[30.0, 50.0, 72.0, 95.0]) is None


def e5_a_short_queue_does_not_trigger_regardless_of_growth() -> None:
    from perception.incident_detector import CONGESTION_QUEUE_MIN_M, classify_major_congestion

    tracks = [_track(f"t{i}", stationary_for_s=6.0, speed_mps=1.2) for i in range(4)]
    assert CONGESTION_QUEUE_MIN_M > 0
    assert classify_major_congestion(tracks, _flow(queue_length_m=8.0),
                                     queue_history=[1.0, 3.0, 5.0, 8.0]) is None


# ----------------------------------------------------------------------
# F. precedence + output contract
# ----------------------------------------------------------------------
def f1_accident_outranks_breakdown_and_congestion() -> None:
    from perception.incident_detector import detect_incident

    tracks = [
        _track("t1", stationary_for_s=30.0, speed_mps=0.0, x=100.0, y=200.0),
        _track("t2", stationary_for_s=28.0, speed_mps=0.0, x=116.0, y=207.0),
    ]
    out = detect_incident(tracks, _flow(mean_speed_mps=0.8, queue_length_m=95.0),
                          junction="J2", detected_at=1840.0, queue_history=[30.0, 55.0, 78.0, 95.0])
    assert out is not None and out["type"] == "accident", out


def f2_breakdown_outranks_congestion() -> None:
    from perception.incident_detector import detect_incident

    tracks = [_track(f"t{i}", stationary_for_s=6.0, speed_mps=1.2) for i in range(9)]
    tracks.append(_track("stuck", stationary_for_s=40.0, speed_mps=0.0))
    out = detect_incident(tracks, _flow(mean_speed_mps=9.0, queue_length_m=95.0),
                          junction="J2", detected_at=1840.0, queue_history=[30.0, 55.0, 78.0, 95.0])
    assert out is not None and out["type"] == "breakdown", out


def f3_nothing_wrong_returns_none() -> None:
    from perception.incident_detector import detect_incident

    tracks = [_track(f"t{i}", speed_mps=9.0) for i in range(6)]
    assert detect_incident(tracks, _flow(), junction="J2", detected_at=100.0) is None


def f4_output_has_exactly_the_nine_specified_keys() -> None:
    from perception.incident_detector import detect_incident

    out = detect_incident(
        [_track("t1", stationary_for_s=30.0, speed_mps=0.0), _track("t2"), _track("t3")],
        _flow(mean_speed_mps=9.0), junction="J2", detected_at=1840.0,
    )
    expected = {
        "type", "junction", "approach", "lane_index", "distance_m",
        "distance_confidence", "severity", "detected_at", "source",
    }
    assert set(out) == expected, f"key set drift: {sorted(set(out) ^ expected)}"


def f5_output_field_values_are_well_formed() -> None:
    from perception.incident_intake import SEVERITIES

    from perception.incident_detector import INCIDENT_KINDS, SOURCE_TAG, detect_incident

    out = detect_incident(
        [_track("t1", stationary_for_s=30.0, speed_mps=0.0), _track("t2"), _track("t3")],
        _flow(mean_speed_mps=9.0), junction="J2", detected_at=1840.0,
        junction_xy=(450.0, 150.0), vehicle_xy=(400.0, 150.0),
    )
    assert out["type"] in INCIDENT_KINDS
    assert out["junction"] == "J2"
    assert out["approach"] == "north"
    assert out["lane_index"] == 0
    assert out["severity"] in SEVERITIES, "severity must use §7.3's enum"
    assert out["detected_at"] == 1840.0, "detected_at is caller-supplied, never a wall clock"
    assert out["source"] == SOURCE_TAG
    assert isinstance(out["distance_m"], float) and out["distance_m"] >= 0.0
    assert 0.0 <= out["distance_confidence"] <= 1.0


def f6_distance_is_none_and_confidence_zero_when_no_geometry_is_supplied() -> None:
    """No calibration and no junction coord means distance is UNKNOWN. A
    plausible-looking 0.0m would be a fabricated measurement in front of a
    responder - the same class of error as §11.2's served_on_arrival."""
    from perception.incident_detector import detect_incident

    out = detect_incident(
        [_track("t1", stationary_for_s=30.0, speed_mps=0.0), _track("t2"), _track("t3")],
        _flow(mean_speed_mps=9.0), junction="J2", detected_at=1840.0,
    )
    assert out["distance_m"] is None, out["distance_m"]
    assert out["distance_confidence"] == 0.0


def f7_detected_at_is_not_a_wall_clock() -> None:
    import time

    from perception.incident_detector import detect_incident

    out = detect_incident(
        [_track("t1", stationary_for_s=30.0, speed_mps=0.0), _track("t2"), _track("t3")],
        _flow(mean_speed_mps=9.0), junction="J2", detected_at=42.0,
    )
    assert out["detected_at"] == 42.0
    assert abs(out["detected_at"] - time.time()) > 1e6, "detected_at looks like a wall clock"


def f8_output_feeds_incident_intake_through_a_declared_mapping() -> None:
    """§7.3's Incident has no home for distance_m / lane_index, so the
    mapping has to be explicit rather than assumed to line up."""
    from perception.incident_intake import IncidentIntake

    from perception.incident_detector import detect_incident, to_intake_kwargs

    out = detect_incident(
        [_track("t1", stationary_for_s=30.0, speed_mps=0.0), _track("t2"), _track("t3")],
        _flow(mean_speed_mps=9.0), junction="J2", detected_at=1840.0,
    )
    inc = IncidentIntake().report(**to_intake_kwargs(out, lane_id="N2_J2_0", estimated_duration_s=600.0))
    assert inc.type in ("lane_blocked", "accident", "roadworks"), inc.type
    assert inc.location == {"junction_id": "J2", "lane_id": "N2_J2_0"}
    assert inc.severity == out["severity"]


# ----------------------------------------------------------------------
# G. purity and import hygiene
# ----------------------------------------------------------------------
def g1_classification_is_pure_and_repeatable() -> None:
    from perception.incident_detector import detect_incident

    args = (
        [_track("t1", stationary_for_s=30.0, speed_mps=0.0), _track("t2"), _track("t3")],
        _flow(mean_speed_mps=9.0),
    )
    kwargs = dict(junction="J2", detected_at=1840.0, junction_xy=(450.0, 150.0), vehicle_xy=(400.0, 150.0))
    first = detect_incident(*args, **kwargs)
    second = detect_incident(*args, **kwargs)
    assert first == second, (first, second)


def g2_classification_does_not_mutate_its_inputs() -> None:
    from perception.incident_detector import detect_incident

    tracks = [_track("t1", stationary_for_s=30.0, speed_mps=0.0), _track("t2"), _track("t3")]
    flow = _flow(mean_speed_mps=9.0)
    history = [30.0, 50.0, 72.0, 95.0]
    before = (list(tracks), flow, list(history))
    detect_incident(tracks, flow, junction="J2", detected_at=1.0, queue_history=history)
    assert (list(tracks), flow, list(history)) == before, "inputs were mutated"


def g3_no_sumo_import_at_module_level() -> None:
    """The classifiers are pure and must run with no SUMO. `sumolib` is
    imported INSIDE the two net-file readers, which is the only place a
    junction coordinate legitimately comes from."""
    import subprocess
    import sys

    code = (
        "import sys, perception.incident_detector;"
        "bad=[m for m in ('traci','sumolib','libsumo') if m in sys.modules];"
        "print('LEAKED:'+','.join(bad) if bad else 'CLEAN')"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "CLEAN" in out.stdout, out.stdout + out.stderr[-2000:]


CHECKS = [
    ("A1 two-point calibration computes the expected scale", a1_two_point_calibration_computes_the_expected_scale),
    ("A2 calibration uses Euclidean pixel distance", a2_calibration_uses_euclidean_pixel_distance),
    ("A3 calibration refuses degenerate input", a3_calibration_refuses_degenerate_input),
    ("A4 calibration reports a bounded confidence", a4_calibration_reports_a_bounded_confidence),
    ("B1 junction coord comes from the net file", b1_junction_coord_comes_from_the_net_file_not_the_generator),
    ("B2 stop-line coord comes from the lane shape", b2_stop_line_coord_comes_from_the_lane_shape),
    ("B3 distance to a known stop line is exact and confident", b3_distance_to_a_known_stop_line_is_exact_and_confident),
    ("B4 junction-centre fallback is offset and less confident", b4_distance_from_the_junction_centre_is_less_confident_and_offset),
    ("B5 distance never goes negative", b5_distance_never_goes_negative),
    ("B6 pixel positions route through the calibration", b6_pixel_positions_route_through_the_calibration),
    ("C1 one stationary vehicle while others flow is a breakdown", c1_one_stationary_vehicle_while_others_flow_is_a_breakdown),
    ("C2 ten seconds stationary is below the threshold", c2_ten_seconds_stationary_is_below_the_threshold),
    ("C3 a stationary vehicle in a stopped lane is not a breakdown", c3_a_stationary_vehicle_in_a_stopped_lane_is_not_a_breakdown),
    ("C4 two stationary vehicles are not a breakdown", c4_two_stationary_vehicles_are_not_a_breakdown),
    ("D1 two clustered stationary + speed collapse is an accident", d1_two_clustered_stationary_vehicles_with_speed_collapse_is_an_accident),
    ("D2 stationary but scattered is not an accident", d2_stationary_but_scattered_is_not_an_accident),
    ("D3 clustered but no speed collapse is not an accident", d3_clustered_but_no_speed_collapse_is_not_an_accident),
    ("D4 accident severity outranks breakdown severity", d4_accident_severity_outranks_breakdown_severity),
    ("E1 a growing queue with no stationary origin is major_congestion", e1_a_growing_queue_with_no_stationary_origin_is_major_congestion),
    ("E2 a flat queue is not major_congestion", e2_a_flat_queue_is_not_major_congestion),
    ("E3 a shrinking queue is not major_congestion", e3_a_shrinking_queue_is_not_major_congestion),
    ("E4 a growing queue with a stationary origin is not major_congestion", e4_a_growing_queue_with_a_stationary_origin_is_not_major_congestion),
    ("E5 a short queue does not trigger regardless of growth", e5_a_short_queue_does_not_trigger_regardless_of_growth),
    ("F1 accident outranks breakdown and congestion", f1_accident_outranks_breakdown_and_congestion),
    ("F2 breakdown outranks congestion", f2_breakdown_outranks_congestion),
    ("F3 nothing wrong returns None", f3_nothing_wrong_returns_none),
    ("F4 output has exactly the nine specified keys", f4_output_has_exactly_the_nine_specified_keys),
    ("F5 output field values are well-formed", f5_output_field_values_are_well_formed),
    ("F6 distance is None when no geometry is supplied", f6_distance_is_none_and_confidence_zero_when_no_geometry_is_supplied),
    ("F7 detected_at is not a wall clock", f7_detected_at_is_not_a_wall_clock),
    ("F8 output feeds IncidentIntake through a declared mapping", f8_output_feeds_incident_intake_through_a_declared_mapping),
    ("G1 classification is pure and repeatable", g1_classification_is_pure_and_repeatable),
    ("G2 classification does not mutate its inputs", g2_classification_does_not_mutate_its_inputs),
    ("G3 no SUMO import at module level", g3_no_sumo_import_at_module_level),
]


def main() -> int:
    for label, fn in CHECKS:
        check(label, fn)
    passed = sum(1 for ok, _ in _results if ok)
    for ok, label in _results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\ntests.test_incident_detector: {passed}/{len(_results)} passed")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
