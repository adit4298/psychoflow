"""Hand-scored assertions for the `iot/` MQTT ingestion layer.

Run: `python -m tests.test_iot`   (also reached by `python -m iot.broker --selftest`)

Sections
  A. topic schema        - build, parse, and REFUSE injection
  B. payload schema      - dataclasses, JSON round-trip, validation
  C. decode hardening    - untrusted-bytes surface (this is the security boundary)
  D. import hygiene      - no SUMO import at module level
  E. live round-trip     - broker up, publisher connects, subscriber receives
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_results: list[tuple[bool, str]] = []


def check(label: str, fn) -> None:
    try:
        fn()
        _results.append((True, label))
    except Exception as exc:  # noqa: BLE001 - a test runner reports, never propagates
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
# A. Topic schema
# ----------------------------------------------------------------------
def a1_topic_builders_match_the_documented_schema() -> None:
    from iot import topics

    assert topics.sensor_counts_topic("J1", "N1_J1_0") == "psychoflow/sensor/J1/N1_J1_0/counts"
    assert topics.camera_topic("J2") == "psychoflow/sensor/J2/camera"
    assert topics.INCIDENT_TOPIC == "psychoflow/incident"
    assert topics.WEATHER_TOPIC == "psychoflow/weather"


def a2_counts_topic_round_trips() -> None:
    from iot import topics

    parsed = topics.parse_topic(topics.sensor_counts_topic("J3", "S3_J3_1"))
    assert parsed.kind == topics.KIND_COUNTS, parsed
    assert parsed.junction_id == "J3" and parsed.lane_id == "S3_J3_1", parsed


def a3_camera_and_flat_topics_parse() -> None:
    from iot import topics

    assert topics.parse_topic("psychoflow/sensor/J2/camera").kind == topics.KIND_CAMERA
    assert topics.parse_topic("psychoflow/sensor/J2/camera").junction_id == "J2"
    assert topics.parse_topic(topics.INCIDENT_TOPIC).kind == topics.KIND_INCIDENT
    assert topics.parse_topic(topics.WEATHER_TOPIC).kind == topics.KIND_WEATHER


def a4_topic_builders_refuse_wildcard_and_separator_injection() -> None:
    """A junction/lane id is interpolated into a topic. Anything that can
    widen a subscription or forge a level MUST be refused at build time."""
    from iot import topics

    bad_ids = ("J1/+", "#", "+", "J1/../J2", "", "J1 J2", "J" * 200, "J1\n", "psychoflow/x")
    for bad in bad_ids:
        expect_raises(ValueError, topics.sensor_counts_topic, bad, "N1_J1_0")
        expect_raises(ValueError, topics.sensor_counts_topic, "J1", bad)
        expect_raises(ValueError, topics.camera_topic, bad)


def a5_unknown_topics_do_not_parse() -> None:
    from iot import topics

    for bad in ("psychoflow/sensor/J1/N1_J1_0/speeds", "other/incident", "psychoflow", ""):
        assert topics.parse_topic(bad) is None, f"{bad!r} should not parse"


# ----------------------------------------------------------------------
# B. Payload schema
# ----------------------------------------------------------------------
def _counts_kwargs() -> dict:
    return dict(
        junction_id="J1",
        lane_id="N1_J1_0",
        approach="north",
        vehicle_count=7,
        halted_count=4,
        type_composition={"bike": 2, "auto": 1, "car": 3, "truck": 1, "ambulance": 0},
        wait_time_current=42.5,
        wait_time_max_single_vehicle=61.0,
        starvation_flag=False,
        sim_time=1840.0,
    )


def b1_counts_payload_json_round_trip() -> None:
    from iot.schema import LaneCountsPayload

    p = LaneCountsPayload(**_counts_kwargs())
    assert LaneCountsPayload.from_json(p.to_json()) == p
    assert json.loads(p.to_json())["type_composition"]["bike"] == 2


def b2_counts_payload_carries_the_7_1_field_names() -> None:
    """The subscriber feeds perception. Field names must match §7.1's
    LaneReading exactly or the hand-off silently loses fields."""
    from iot.schema import LaneCountsPayload

    d = LaneCountsPayload(**_counts_kwargs()).to_dict()
    for key in (
        "lane_id", "approach", "vehicle_count", "halted_count", "type_composition",
        "wait_time_current", "wait_time_max_single_vehicle", "starvation_flag",
    ):
        assert key in d, f"§7.1 field {key!r} missing from LaneCountsPayload"


def b3_counts_payload_validates() -> None:
    from iot.schema import LaneCountsPayload, PayloadError

    expect_raises(PayloadError, LaneCountsPayload, **{**_counts_kwargs(), "vehicle_count": -1})
    expect_raises(PayloadError, LaneCountsPayload, **{**_counts_kwargs(), "halted_count": 9})
    expect_raises(PayloadError, LaneCountsPayload, **{**_counts_kwargs(), "wait_time_current": float("nan")})
    expect_raises(
        PayloadError, LaneCountsPayload,
        **{**_counts_kwargs(), "type_composition": {"bike": 1, "spaceship": 2}},
    )
    expect_raises(PayloadError, LaneCountsPayload, **{**_counts_kwargs(), "junction_id": "J1/+"})


def b4_incident_payload_matches_7_3_enums() -> None:
    from iot.schema import IncidentPayload, PayloadError

    ok = dict(
        incident_id="inc_0007", type="lane_blocked",
        location={"junction_id": "J2", "lane_id": "N2_J2_1"},
        severity="high", affected_lanes=["N2_J2_1", "N2_J2_0"],
        reported_at_sim_time=1840.0, estimated_duration_s=600.0,
    )
    p = IncidentPayload(**ok)
    assert IncidentPayload.from_json(p.to_json()) == p
    expect_raises(PayloadError, IncidentPayload, **{**ok, "type": "meteor_strike"})
    expect_raises(PayloadError, IncidentPayload, **{**ok, "severity": "catastrophic"})
    expect_raises(PayloadError, IncidentPayload, **{**ok, "estimated_duration_s": -5.0})


def b5_incident_payload_feeds_incident_intake_unchanged() -> None:
    """The whole point of matching §7.3: to_intake_kwargs() must be
    accepted by IncidentIntake.report() with no field translation."""
    from perception.incident_intake import IncidentIntake

    from iot.schema import IncidentPayload

    p = IncidentPayload(
        incident_id="inc_0001", type="accident",
        location={"junction_id": "J2", "lane_id": "N2_J2_1"},
        severity="medium", affected_lanes=["N2_J2_1"],
        reported_at_sim_time=100.0, estimated_duration_s=300.0,
    )
    inc = IncidentIntake().report(**p.to_intake_kwargs())
    assert inc.type == "accident" and inc.location["junction_id"] == "J2"


def b6_weather_payload_matches_7_4_enum() -> None:
    from iot.schema import PayloadError, WeatherPayload

    p = WeatherPayload(state="heavy_rain", changed_at_sim_time=900.0)
    assert WeatherPayload.from_json(p.to_json()) == p
    expect_raises(PayloadError, WeatherPayload, state="hail", changed_at_sim_time=900.0)


def b7_camera_payload_round_trip_and_validation() -> None:
    from iot.schema import CameraPayload, PayloadError

    ok = dict(
        junction_id="J2", frame_index=41, detected_at=1840.0,
        approaches={
            "north": {"vehicle_count": 9, "density": "congested", "queue_estimate": 6},
            "west": {"vehicle_count": 2, "density": "free", "queue_estimate": 0},
        },
        emergency_vehicle_flag=False, confidence=0.91, source="vision_detector",
    )
    p = CameraPayload(**ok)
    assert CameraPayload.from_json(p.to_json()) == p
    expect_raises(PayloadError, CameraPayload, **{**ok, "confidence": 1.4})
    bad = {**ok, "approaches": {"north": {"vehicle_count": 9, "density": "gridlock", "queue_estimate": 6}}}
    expect_raises(PayloadError, CameraPayload, **bad)


def b8_restated_enums_have_not_drifted_from_perception() -> None:
    """`iot/schema.py` restates four enums instead of importing them,
    because `perception.lane_sensor`/`weather` import traci at module level
    and `iot/` must stay SUMO-free (D1). This is the guard that makes the
    duplication safe: it imports the real sources and asserts equality."""
    from perception.incident_intake import INCIDENT_TYPES, SEVERITIES
    from perception.lane_sensor import VEHICLE_TYPES
    from perception.weather import WEATHER_STATES

    from iot import schema

    assert schema.VEHICLE_TYPES == VEHICLE_TYPES, (schema.VEHICLE_TYPES, VEHICLE_TYPES)
    assert schema.WEATHER_STATES == WEATHER_STATES, (schema.WEATHER_STATES, WEATHER_STATES)
    assert schema.INCIDENT_TYPES == INCIDENT_TYPES, (schema.INCIDENT_TYPES, INCIDENT_TYPES)
    assert schema.SEVERITIES == SEVERITIES, (schema.SEVERITIES, SEVERITIES)


def b9_corridor_junctions_have_not_drifted_from_control_api() -> None:
    """Same duplication trade as B8. `backend/control_api` holds the same
    literal for the same reason (it must stay SUMO-free too), and both
    guard the SAME `IncidentIntake.report()`."""
    from backend.control_api import _CORRIDOR_JUNCTIONS

    from iot import schema

    assert schema.CORRIDOR_JUNCTIONS == _CORRIDOR_JUNCTIONS, (
        schema.CORRIDOR_JUNCTIONS, _CORRIDOR_JUNCTIONS
    )


def b10_a_junction_off_the_corridor_is_refused() -> None:
    """Raised by security review. `control_api.inject_incident` rejects a
    junction outside §0.1's corridor; the IoT path reaches the same
    registry, so it must too. An incident filed against `J99` is indexed by
    junction by §8.1/§8.2, surfaced by no snapshot, and clearable by no
    operator - a permanently orphaned entry."""
    from iot.schema import CameraPayload, IncidentPayload, LaneCountsPayload, PayloadError

    expect_raises(PayloadError, LaneCountsPayload, **{**_counts_kwargs(), "junction_id": "J99"})
    expect_raises(
        PayloadError, IncidentPayload,
        incident_id="inc_0001", type="accident",
        location={"junction_id": "J99", "lane_id": "not_a_real_lane"},
        severity="high", affected_lanes=["not_a_real_lane"],
        reported_at_sim_time=1.0, estimated_duration_s=60.0,
    )
    expect_raises(
        PayloadError, CameraPayload,
        junction_id="J99", frame_index=0, detected_at=0.0,
        approaches={}, emergency_vehicle_flag=False, confidence=0.5,
    )


def b11_type_composition_cannot_exceed_the_lane_occupancy() -> None:
    """Raised by security review. Measured before the fix: a 310-byte
    payload with `vehicle_count: 0` and 10,000 of each of the five types
    was ACCEPTED, and `to_lane_reading_dict()` - which advertises §7.1
    parity - handed it straight on. `lane_sensor.py` builds both by
    iterating one vehicle list, so the shape is impossible from the real
    sensor, and any consumer taking a type RATIO divides by that zero."""
    from iot.schema import LaneCountsPayload, PayloadError

    expect_raises(PayloadError, LaneCountsPayload, **{
        **_counts_kwargs(), "vehicle_count": 0, "halted_count": 0,
        "type_composition": {v: 10_000 for v in
                             ("bike", "auto", "car", "truck", "ambulance")},
    })
    expect_raises(PayloadError, LaneCountsPayload, **{
        **_counts_kwargs(), "vehicle_count": 5, "halted_count": 0,
        "type_composition": {"bike": 3, "car": 3},  # totals 6 > 5
    })
    # Under-counting stays legal: a producer that could not classify every
    # vehicle is reporting honestly.
    p = LaneCountsPayload(**{
        **_counts_kwargs(), "vehicle_count": 7, "halted_count": 0,
        "type_composition": {"car": 3},
    })
    assert sum(p.type_composition.values()) == 3


def b12_starvation_flag_must_be_consistent_with_the_wait_it_derives_from() -> None:
    """Raised by security review. In `lane_sensor.py` the flag is DERIVED
    (`max_single_wait > threshold`), so a True flag over a sub-threshold
    wait is a state the sensor cannot produce - and it feeds §9.1's
    starvation_bonus and §9.4's penalty. Refused, not silently recomputed:
    correcting it would hide a broken producer."""
    from iot.schema import STARVATION_THRESHOLD_S, LaneCountsPayload, PayloadError

    expect_raises(PayloadError, LaneCountsPayload, **{
        **_counts_kwargs(), "starvation_flag": True,
        "wait_time_max_single_vehicle": 0.0, "wait_time_current": 0.0,
    })
    expect_raises(PayloadError, LaneCountsPayload, **{
        **_counts_kwargs(), "starvation_flag": True,
        "wait_time_max_single_vehicle": STARVATION_THRESHOLD_S,  # boundary: not ABOVE
        "wait_time_current": 10.0,
    })
    p = LaneCountsPayload(**{
        **_counts_kwargs(), "starvation_flag": True,
        "wait_time_max_single_vehicle": STARVATION_THRESHOLD_S + 0.1,
    })
    assert p.starvation_flag is True
    # A False flag over a high wait stays legal - a producer using a
    # different threshold is under-reporting, not fabricating.
    LaneCountsPayload(**{**_counts_kwargs(), "starvation_flag": False,
                        "wait_time_max_single_vehicle": 200.0})


# ----------------------------------------------------------------------
# C. Decode hardening - the untrusted-bytes boundary
# ----------------------------------------------------------------------
def c1_decode_dispatches_on_topic() -> None:
    from iot.schema import LaneCountsPayload, decode

    p = LaneCountsPayload(**_counts_kwargs())
    assert decode("psychoflow/sensor/J1/N1_J1_0/counts", p.to_json().encode()) == p


def c2_decode_rejects_malformed_and_hostile_bytes() -> None:
    from iot.schema import PayloadError, decode

    topic = "psychoflow/sensor/J1/N1_J1_0/counts"
    hostile = [
        b"",
        b"{not json",
        b"[1,2,3]",
        b'"a string"',
        b"null",
        b"\xff\xfe\x00bad",
        json.dumps({"vehicle_count": 1}).encode(),
        json.dumps({**_counts_kwargs(), "extra": 1}).encode(),
    ]
    for raw in hostile:
        expect_raises(PayloadError, decode, topic, raw)


def c3_decode_enforces_a_size_cap() -> None:
    """An unauthenticated broker means any local process can publish. A
    payload cap is what stops one flooding memory through the decoder."""
    from iot.schema import MAX_PAYLOAD_BYTES, PayloadError, decode

    huge = json.dumps({**_counts_kwargs(), "lane_id": "N" * (MAX_PAYLOAD_BYTES + 10)}).encode()
    assert len(huge) > MAX_PAYLOAD_BYTES
    expect_raises(PayloadError, decode, "psychoflow/sensor/J1/N1_J1_0/counts", huge)


def c3b_numeric_fields_have_ceilings_the_size_cap_does_not_give_them() -> None:
    """Found by adversarial probing AFTER C2/C3 were already green, which
    is the point of recording it: the size cap bounds BYTES, not VALUES.

    Measured before the fix: a 691-byte message - comfortably under
    MAX_PAYLOAD_BYTES - carrying `vehicle_count: 10**400` and
    `wait_time_current: 1e300` passed every check, and 1e300 casts to
    **inf** in the float32 that §9.2's observation vector holds. A payload
    that validates cleanly and then silently corrupts the fairness signal
    is worse than one that is rejected loudly.
    """
    from iot.schema import (
        MAX_PAYLOAD_BYTES,
        MAX_SIM_TIME_S,
        MAX_VEHICLE_COUNT,
        MAX_WAIT_TIME_S,
        LaneCountsPayload,
        PayloadError,
    )

    for field, bad in (
        ("vehicle_count", 10 ** 400),
        ("vehicle_count", MAX_VEHICLE_COUNT + 1),
        ("wait_time_current", 1e300),
        ("wait_time_max_single_vehicle", MAX_WAIT_TIME_S + 1),
        ("sim_time", MAX_SIM_TIME_S * 10),
    ):
        kwargs = {**_counts_kwargs(), field: bad}
        if field == "vehicle_count":
            kwargs["halted_count"] = 0
        expect_raises(PayloadError, LaneCountsPayload, **kwargs)
        assert len(json.dumps(kwargs, default=str)) < MAX_PAYLOAD_BYTES, (
            "the point of this check is that these bodies are UNDER the size cap"
        )

    # The ceilings must not reject legitimate traffic.
    LaneCountsPayload(**{**_counts_kwargs(), "vehicle_count": 200, "halted_count": 200})
    LaneCountsPayload(**{**_counts_kwargs(), "wait_time_current": 900.0})


def c4_decode_rejects_an_unroutable_topic() -> None:
    from iot.schema import PayloadError, decode

    expect_raises(PayloadError, decode, "psychoflow/sensor/J1/N1_J1_0/speeds", b"{}")


def c5_topic_and_body_must_agree() -> None:
    """A counts message whose body claims a different junction than its
    topic is a forged message, not a routing convenience."""
    from iot.schema import LaneCountsPayload, PayloadError, decode

    body = LaneCountsPayload(**_counts_kwargs()).to_json().encode()  # says J1 / N1_J1_0
    expect_raises(PayloadError, decode, "psychoflow/sensor/J2/N1_J1_0/counts", body)
    expect_raises(PayloadError, decode, "psychoflow/sensor/J1/S1_J1_0/counts", body)


# ----------------------------------------------------------------------
# D. Import hygiene
# ----------------------------------------------------------------------
def d1_no_sumo_import_at_module_level() -> None:
    """`iot/` must import with no SUMO_HOME and no live simulator."""
    code = (
        "import sys;"
        "import iot, iot.topics, iot.schema, iot.broker, iot.publisher, iot.subscriber;"
        "bad=[m for m in ('traci','sumolib','libsumo') if m in sys.modules];"
        "print('LEAKED:'+','.join(bad) if bad else 'CLEAN')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=180
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert "CLEAN" in out.stdout, out.stdout + out.stderr[-2000:]


def d2_broker_binds_loopback_by_default() -> None:
    """Mirrors backend/main.py's loopback-by-default rule - there is no
    auth layer behind this broker."""
    from iot import broker

    assert broker.DEFAULT_HOST == "127.0.0.1", broker.DEFAULT_HOST
    expect_raises(ValueError, broker.IoTBroker, host="0.0.0.0")
    assert broker.IoTBroker(host="0.0.0.0", allow_lan=True).host == "0.0.0.0"


# ----------------------------------------------------------------------
# E. Live round-trip - the done-bar
# ----------------------------------------------------------------------
def e1_broker_publisher_subscriber_round_trip() -> None:
    from iot import topics
    from iot.broker import IoTBroker
    from iot.publisher import IoTPublisher
    from iot.schema import IncidentPayload, LaneCountsPayload, WeatherPayload
    from iot.subscriber import IoTSubscriber

    counts = LaneCountsPayload(**_counts_kwargs())
    received: list = []

    with IoTBroker(port=0) as b:
        with IoTSubscriber(port=b.port, on_payload=lambda t, p: received.append((t, p))) as sub:
            sub.subscribe_all()
            with IoTPublisher(port=b.port) as pub:
                pub.publish_counts(counts)
                pub.publish_weather(WeatherPayload(state="rain", changed_at_sim_time=900.0))
                pub.publish_incident(IncidentPayload(
                    incident_id="inc_0002", type="roadworks",
                    location={"junction_id": "J3", "lane_id": "N3_J3_0"},
                    severity="low", affected_lanes=["N3_J3_0"],
                    reported_at_sim_time=10.0, estimated_duration_s=60.0,
                ))
                deadline = time.time() + 10.0
                while len(received) < 3 and time.time() < deadline:
                    time.sleep(0.05)

    assert len(received) == 3, f"expected 3 messages, got {len(received)}: {received}"
    by_topic = dict(received)
    assert by_topic[topics.sensor_counts_topic("J1", "N1_J1_0")] == counts
    assert by_topic[topics.WEATHER_TOPIC].state == "rain"
    assert by_topic[topics.INCIDENT_TOPIC].type == "roadworks"


def e2_subscriber_drops_undecodable_messages_without_dying() -> None:
    """A garbage publisher must not take the subscriber down - it is the
    perception layer's feed."""
    import paho.mqtt.client as mqtt

    from iot.broker import IoTBroker
    from iot.publisher import IoTPublisher
    from iot.schema import WeatherPayload
    from iot.subscriber import IoTSubscriber

    received: list = []
    with IoTBroker(port=0) as b:
        with IoTSubscriber(port=b.port, on_payload=lambda t, p: received.append((t, p))) as sub:
            sub.subscribe_all()
            raw = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hostile")
            raw.connect("127.0.0.1", b.port, 30)
            raw.loop_start()
            time.sleep(0.5)
            raw.publish("psychoflow/weather", b"{not json", qos=1).wait_for_publish(5)
            raw.publish("psychoflow/weather", b"\xff\xfe\x00", qos=1).wait_for_publish(5)
            raw.loop_stop()

            with IoTPublisher(port=b.port) as pub:
                pub.publish_weather(WeatherPayload(state="clear", changed_at_sim_time=0.0))
                deadline = time.time() + 10.0
                while not received and time.time() < deadline:
                    time.sleep(0.05)

            assert len(received) == 1, f"only the valid message should decode: {received}"
            assert sub.dropped >= 2, f"drops not counted: dropped={sub.dropped}"


def e3_subscriber_queue_is_bounded() -> None:
    from iot.subscriber import DEFAULT_QUEUE_SIZE, IoTSubscriber

    assert DEFAULT_QUEUE_SIZE > 0
    sub = IoTSubscriber(port=1, queue_size=4)
    for i in range(20):
        sub._enqueue(("psychoflow/weather", i))  # noqa: SLF001 - asserting the bound
    assert len(sub.buffer) == 4, f"queue not bounded: {len(sub.buffer)}"
    assert sub.buffer[-1][1] == 19, "bounded queue must drop OLDEST, keep newest"


def e4_simulated_publisher_emits_all_four_topic_kinds() -> None:
    from iot import topics
    from iot.broker import IoTBroker
    from iot.publisher import SimulatedSensorPublisher
    from iot.subscriber import IoTSubscriber

    received: list = []
    with IoTBroker(port=0) as b:
        with IoTSubscriber(port=b.port, on_payload=lambda t, p: received.append((t, p))) as sub:
            sub.subscribe_all()
            with SimulatedSensorPublisher(port=b.port, seed=7) as simpub:
                sent = simpub.publish_step(sim_time=100.0, force_incident=True, force_weather=True)
                # Wait on the four KINDS, not on a message count. One step
                # emits 10 messages (6 counts + 3 camera + 1 incident) before
                # the single weather one, so `len(received) < 4` exits while
                # weather is still in flight - which is exactly how this check
                # failed once, flakily, rather than honestly.
                deadline = time.time() + 15.0
                while time.time() < deadline:
                    if {topics.parse_topic(t).kind for t, _ in received} == set(topics.KINDS):
                        break
                    time.sleep(0.05)

    kinds = {topics.parse_topic(t).kind for t, _ in received}
    assert len(received) == sent, f"published {sent}, received {len(received)}"
    assert kinds == {
        topics.KIND_COUNTS, topics.KIND_CAMERA, topics.KIND_INCIDENT, topics.KIND_WEATHER,
    }, f"simulated publisher did not cover all four topics: {kinds}"


CHECKS = [
    ("A1 topic builders match the documented schema", a1_topic_builders_match_the_documented_schema),
    ("A2 counts topic round-trips", a2_counts_topic_round_trips),
    ("A3 camera/incident/weather topics parse", a3_camera_and_flat_topics_parse),
    ("A4 topic builders refuse wildcard/separator injection", a4_topic_builders_refuse_wildcard_and_separator_injection),
    ("A5 unknown topics do not parse", a5_unknown_topics_do_not_parse),
    ("B1 counts payload JSON round-trip", b1_counts_payload_json_round_trip),
    ("B2 counts payload carries the 7.1 field names", b2_counts_payload_carries_the_7_1_field_names),
    ("B3 counts payload validates", b3_counts_payload_validates),
    ("B4 incident payload matches 7.3 enums", b4_incident_payload_matches_7_3_enums),
    ("B5 incident payload feeds IncidentIntake unchanged", b5_incident_payload_feeds_incident_intake_unchanged),
    ("B6 weather payload matches 7.4 enum", b6_weather_payload_matches_7_4_enum),
    ("B7 camera payload round-trip and validation", b7_camera_payload_round_trip_and_validation),
    ("B8 restated enums have not drifted from perception", b8_restated_enums_have_not_drifted_from_perception),
    ("C1 decode dispatches on topic", c1_decode_dispatches_on_topic),
    ("C2 decode rejects malformed/hostile bytes", c2_decode_rejects_malformed_and_hostile_bytes),
    ("C3 decode enforces a size cap", c3_decode_enforces_a_size_cap),
    ("C3b numeric fields have ceilings the size cap does not give them",
     c3b_numeric_fields_have_ceilings_the_size_cap_does_not_give_them),
    ("C4 decode rejects an unroutable topic", c4_decode_rejects_an_unroutable_topic),
    ("C5 topic and body must agree", c5_topic_and_body_must_agree),
    ("D1 no SUMO import at module level", d1_no_sumo_import_at_module_level),
    ("D2 broker binds loopback by default", d2_broker_binds_loopback_by_default),
    ("E1 broker/publisher/subscriber round-trip", e1_broker_publisher_subscriber_round_trip),
    ("E2 subscriber survives undecodable messages", e2_subscriber_drops_undecodable_messages_without_dying),
    ("E3 subscriber queue is bounded", e3_subscriber_queue_is_bounded),
    ("E4 simulated publisher emits all four topic kinds", e4_simulated_publisher_emits_all_four_topic_kinds),
]


def main() -> int:
    for label, fn in CHECKS:
        check(label, fn)
    passed = sum(1 for ok, _ in _results if ok)
    for ok, label in _results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\ntests.test_iot: {passed}/{len(_results)} passed")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
