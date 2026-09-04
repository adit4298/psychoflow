"""JSON-serialisable payload dataclasses, and a hardened `decode()`.

Threat model (why this file is defensive out of proportion to its size)
----------------------------------------------------------------------
`iot/broker.py` runs with anonymous auth and no TLS - it is a local demo
surface in exactly the sense `CLAUDE.md` uses for the §13 control API. So
**every byte that arrives on a subscription was written by somebody else**:
any process on the machine can publish anything to any topic. The decoder
is the only thing between that and the perception layer.

Four rules, each pinned by an assertion in `tests.test_iot`:

1. **Cap first.** Size is checked before parsing, so a 200MB "JSON" body
   is rejected without being materialised.
2. **Shape, then fields.** Non-UTF-8, non-JSON, and JSON-that-is-not-an-
   object are separate rejections. A payload of `[1,2,3]` must not reach
   field validation and produce a confusing `TypeError`.
3. **Unknown fields are an error, not extra.** Silently ignoring an
   unexpected key is how a renamed field becomes a silently-zeroed one -
   this repo's named failure mode (`CLAUDE.md`, `base_vtype`).
4. **Topic and body must agree.** A counts message whose body names a
   different junction than its topic is a forged message, not a routing
   convenience, and is refused.

Enum duplication is deliberate
------------------------------
`VEHICLE_TYPES`, `WEATHER_STATES`, `INCIDENT_TYPES` and `SEVERITIES` are
restated here rather than imported from `perception/`, because
`perception.lane_sensor` and `perception.weather` both `import traci` at
module level and this package must import with no SUMO present (see
`iot/__init__.py`). The drift risk that creates is not left unguarded:
`tests.test_iot` B8 imports the perception modules directly and asserts
every one of these tuples still matches its source of truth.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from typing import Any

from iot import topics

#: Largest payload the decoder will look at, in bytes. A §7.1 counts
#: message is ~300 bytes and a camera message with four approaches ~600;
#: 64KiB is three orders of magnitude of headroom and still bounded.
MAX_PAYLOAD_BYTES = 64 * 1024

#: Mirrors perception.lane_sensor.VEHICLE_TYPES - see module docstring.
VEHICLE_TYPES = ("bike", "auto", "car", "truck", "ambulance")
#: Mirrors perception.weather.WEATHER_STATES.
WEATHER_STATES = ("clear", "rain", "heavy_rain")
#: Mirrors perception.incident_intake.INCIDENT_TYPES / SEVERITIES.
INCIDENT_TYPES = ("lane_blocked", "accident", "roadworks")
SEVERITIES = ("low", "medium", "high")

#: §7.6 approaches are derived geometrically by the twin; "unknown" is
#: lane_sensor's own default and is accepted here for parity.
APPROACHES = ("north", "south", "east", "west", "unknown")

#: §0.1's LOCKED corridor. Mirrors `backend/control_api._CORRIDOR_JUNCTIONS`,
#: which holds the same literal for the same reason (that module must also
#: stay SUMO-free). `control_api.inject_incident` rejects a junction outside
#: this set, and the IoT path feeds the SAME `IncidentIntake.report()`, so it
#: applies the same check: an incident filed against a junction that does not
#: exist is invisible to every snapshot and unclearable by any operator.
#: If §0.1's topology ever changes, this and `control_api`'s copy move together
#: - `tests.test_iot` B9 asserts they have not drifted apart.
CORRIDOR_JUNCTIONS = ("J1", "J2", "J3")

#: Mirrors `perception.lane_sensor.DEFAULT_STARVATION_THRESHOLD_S` (§0.1's
#: soft line). Needed here because `starvation_flag` is DERIVED in the real
#: sensor (`max_single_wait > threshold`), so a payload asserting the flag
#: with a sub-threshold wait describes a state the sensor cannot produce.
STARVATION_THRESHOLD_S = 90.0

#: Coarse density buckets emitted by the camera feed (§7.2 extension).
DENSITY_LEVELS = ("free", "moderate", "congested")

#: Hard ceilings on collection sizes. A corridor junction has at most 4
#: approach lanes per direction across 4 directions; these are generous
#: bounds whose only job is to stop an unbounded allocation.
MAX_APPROACHES = 16
MAX_AFFECTED_LANES = 64

#: Ceilings on NUMERIC fields. These are not tidiness - the size cap does
#: not bound them, because JSON has no numeric limits and Python ints are
#: arbitrary precision. Measured: a 691-byte message (well under
#: MAX_PAYLOAD_BYTES) carrying `vehicle_count: 10**400` and
#: `wait_time_current: 1e300` validated cleanly, and 1e300 becomes **inf**
#: the moment it is cast to the float32 that §9.2's observation vector
#: holds. That is silent corruption of the fairness signal from a payload
#: that passed every other check - so the ranges are pinned here, at the
#: boundary, rather than trusted to stay sane downstream.
#: A 4-lane approach holds well under 100 vehicles; 10,000 is four orders
#: of magnitude of headroom and still finite.
MAX_VEHICLE_COUNT = 10_000
#: One day. Any real wait is under 1,000s (WAITING_TIME_MEMORY_S).
MAX_WAIT_TIME_S = 86_400.0
#: An episode is ~3,000s; 1e9 is absurdly generous and safely float32-able.
MAX_SIM_TIME_S = 1e9
#: A 24h incident. §13.1's INCIDENT_DURATION_RANGE_S caps at 7200.
MAX_INCIDENT_DURATION_S = 86_400.0
MAX_FRAME_INDEX = 100_000_000


class PayloadError(ValueError):
    """An MQTT payload was rejected. Subclasses ValueError so a caller
    that only knows about bad input still catches it."""


# ----------------------------------------------------------------------
# Field validators - small, total, and raising PayloadError only
# ----------------------------------------------------------------------
def _segment(value: object, name: str) -> str:
    try:
        return topics.validate_segment(value, name)
    except ValueError as exc:
        raise PayloadError(str(exc)) from exc


def _corridor_junction(value: object, name: str) -> str:
    """A junction id that is BOTH a legal topic level and on the corridor.

    Segment-legality alone lets any 64-char string name a junction. An
    incident filed against `J99` is accepted by `IncidentIntake`, indexed
    by junction by §8.1/§8.2, surfaced by no snapshot and clearable by no
    operator. `backend/control_api.inject_incident` already refuses this;
    the IoT path reaches the same registry, so it refuses it too.
    """
    _segment(value, name)  # charset first, so the error names the real problem
    if value not in CORRIDOR_JUNCTIONS:
        raise PayloadError(
            f"{name}={value!r} is not on the corridor - must be one of {CORRIDOR_JUNCTIONS}"
        )
    return str(value)


def _enum(value: object, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise PayloadError(f"{name}={value!r} invalid - must be one of {allowed}")
    return str(value)


def _non_negative_int(value: object, name: str, *, maximum: int = MAX_VEHICLE_COUNT) -> int:
    # bool is an int subclass; a True vehicle_count is a bug, not a 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise PayloadError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise PayloadError(f"{name}={value} must be >= 0")
    if value > maximum:
        raise PayloadError(f"{name}={value} exceeds the ceiling of {maximum}")
    return value


def _finite_float(
    value: object,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"{name} must be a number, got {type(value).__name__}")
    # A huge int converts to inf rather than raising, so the range check
    # below has to come after the conversion AND after isfinite.
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise PayloadError(f"{name} is not representable as a float: {exc}") from exc
    if not math.isfinite(value):
        raise PayloadError(f"{name}={value} must be finite (NaN/inf rejected)")
    if minimum is not None and value < minimum:
        raise PayloadError(f"{name}={value} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise PayloadError(f"{name}={value} exceeds the ceiling of {maximum}")
    return value


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise PayloadError(f"{name} must be a bool, got {type(value).__name__}")
    return value


def _composition(value: object, name: str, *, total: int) -> dict[str, int]:
    """§7.1 `type_composition`, bounded BY THE LANE'S OWN OCCUPANCY.

    `total` (the payload's `vehicle_count`) is the ceiling, not
    `MAX_VEHICLE_COUNT`. Without it each type is only independently capped,
    and a lane reporting `vehicle_count: 0` can carry a 50,000-vehicle
    composition - a shape `perception/lane_sensor.py` cannot produce, since
    it builds both by iterating the same vehicle list. `to_lane_reading_dict()`
    advertises §7.1 parity, so a consumer is entitled to that invariant, and
    any code computing a type RATIO would divide by that zero.
    """
    if not isinstance(value, dict):
        raise PayloadError(f"{name} must be an object, got {type(value).__name__}")
    unknown = set(value) - set(VEHICLE_TYPES)
    if unknown:
        raise PayloadError(f"{name} has unknown vehicle types {sorted(unknown)}")
    # Missing types are zero-filled rather than rejected: a producer that
    # only saw bikes may legitimately omit the other four keys.
    composition = {
        vtype: _non_negative_int(value.get(vtype, 0), f"{name}.{vtype}", maximum=total)
        for vtype in VEHICLE_TYPES
    }
    counted = sum(composition.values())
    if counted > total:
        # `<` stays legal: a producer that could not classify every vehicle
        # is reporting honestly, which is the same allowance made above.
        raise PayloadError(f"{name} totals {counted}, exceeding vehicle_count={total}")
    return composition


# ----------------------------------------------------------------------
# Base
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class _Payload:
    """Shared JSON plumbing. Subclasses validate in `__post_init__`."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, data: object):
        if not isinstance(data, dict):
            raise PayloadError(f"{cls.__name__} body must be a JSON object, got {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            # Rule 3 in the module docstring: extra is an error, not extra.
            raise PayloadError(f"{cls.__name__} has unexpected field(s) {sorted(unknown)}")
        try:
            return cls(**data)
        except PayloadError:
            raise
        except TypeError as exc:  # missing required field
            raise PayloadError(f"{cls.__name__}: {exc}") from exc

    @classmethod
    def from_json(cls, raw: str | bytes):
        return cls.from_dict(_load_json(raw))


def _load_json(raw: str | bytes) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise PayloadError(f"payload is {len(raw)} bytes, cap is {MAX_PAYLOAD_BYTES}")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PayloadError(f"payload is not valid UTF-8: {exc}") from exc
    elif not isinstance(raw, str):
        raise PayloadError(f"payload must be str or bytes, got {type(raw).__name__}")
    if len(raw.encode("utf-8", errors="ignore")) > MAX_PAYLOAD_BYTES:
        raise PayloadError(f"payload exceeds cap of {MAX_PAYLOAD_BYTES} bytes")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise PayloadError(f"payload is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        # 20,000 bytes of "[" is deeply nested but well under the size cap,
        # and json.loads raises RecursionError - which is NOT a ValueError,
        # so without this arm it escapes decode() and breaks the contract
        # PayloadError's own docstring states.
        raise PayloadError(f"payload nesting is too deep: {exc}") from exc


# ----------------------------------------------------------------------
# psychoflow/sensor/<junction>/<lane>/counts   (§7.1)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class LaneCountsPayload(_Payload):
    """One lane, one step. Field names match §7.1's `LaneReading` exactly
    so a subscriber can hand this to the perception layer without
    translating - a translation step is where fields go missing."""

    junction_id: str
    lane_id: str
    approach: str
    vehicle_count: int
    halted_count: int
    type_composition: dict[str, int]
    wait_time_current: float
    wait_time_max_single_vehicle: float
    starvation_flag: bool
    sim_time: float
    source: str = "iot_sensor"

    def __post_init__(self) -> None:
        s = object.__setattr__
        s(self, "junction_id", _corridor_junction(self.junction_id, "junction_id"))
        s(self, "lane_id", _segment(self.lane_id, "lane_id"))
        s(self, "approach", _enum(self.approach, APPROACHES, "approach"))
        s(self, "vehicle_count", _non_negative_int(self.vehicle_count, "vehicle_count"))
        s(self, "halted_count", _non_negative_int(self.halted_count, "halted_count"))
        if self.halted_count > self.vehicle_count:
            raise PayloadError(
                f"halted_count={self.halted_count} exceeds vehicle_count={self.vehicle_count}"
            )
        s(self, "type_composition",
          _composition(self.type_composition, "type_composition", total=self.vehicle_count))
        s(self, "wait_time_current",
          _finite_float(self.wait_time_current, "wait_time_current", minimum=0.0, maximum=MAX_WAIT_TIME_S))
        s(self, "wait_time_max_single_vehicle",
          _finite_float(self.wait_time_max_single_vehicle, "wait_time_max_single_vehicle",
                        minimum=0.0, maximum=MAX_WAIT_TIME_S))
        s(self, "starvation_flag", _bool(self.starvation_flag, "starvation_flag"))
        # In lane_sensor.py the flag is DERIVED, not reported - so a True
        # flag with a sub-threshold wait is a state the real sensor cannot
        # produce, and it feeds §9.1's starvation_bonus and §9.4's penalty.
        # Refused rather than recomputed: silently correcting it would hide
        # a broken producer.
        if self.starvation_flag and self.wait_time_max_single_vehicle <= STARVATION_THRESHOLD_S:
            raise PayloadError(
                f"starvation_flag=True but wait_time_max_single_vehicle="
                f"{self.wait_time_max_single_vehicle} is not above the "
                f"{STARVATION_THRESHOLD_S}s threshold - the flag is derived, not asserted"
            )
        s(self, "sim_time", _finite_float(self.sim_time, "sim_time", minimum=0.0, maximum=MAX_SIM_TIME_S))
        s(self, "source", _segment(self.source, "source"))

    def topic(self) -> str:
        return topics.sensor_counts_topic(self.junction_id, self.lane_id)

    def to_lane_reading_dict(self) -> dict[str, Any]:
        """The §7.1 subset only - what `LaneReading.to_dict()` produces.

        Kept explicit rather than "everything except the transport fields"
        so adding a field here cannot silently widen the perception hand-off.
        """
        return {
            "lane_id": self.lane_id,
            "approach": self.approach,
            "vehicle_count": self.vehicle_count,
            "halted_count": self.halted_count,
            "type_composition": dict(self.type_composition),
            "wait_time_current": self.wait_time_current,
            "wait_time_max_single_vehicle": self.wait_time_max_single_vehicle,
            "starvation_flag": self.starvation_flag,
        }


# ----------------------------------------------------------------------
# psychoflow/sensor/<junction>/camera   (§7.2)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class CameraPayload(_Payload):
    """One junction's camera summary for one frame.

    Keyed by APPROACH, not lane, because a camera genuinely cannot know a
    SUMO lane id - see NOTES-FOR-INTEGRATION.md. `emergency_vehicle_flag`
    is a heuristic and is deliberately NOT folded into any per-type count.
    """

    junction_id: str
    frame_index: int
    detected_at: float
    approaches: dict[str, dict[str, Any]]
    emergency_vehicle_flag: bool
    confidence: float
    source: str = "vision_detector"

    def __post_init__(self) -> None:
        s = object.__setattr__
        s(self, "junction_id", _corridor_junction(self.junction_id, "junction_id"))
        s(self, "frame_index", _non_negative_int(self.frame_index, "frame_index", maximum=MAX_FRAME_INDEX))
        s(self, "detected_at", _finite_float(self.detected_at, "detected_at", minimum=0.0, maximum=MAX_SIM_TIME_S))
        s(self, "emergency_vehicle_flag", _bool(self.emergency_vehicle_flag, "emergency_vehicle_flag"))
        conf = _finite_float(self.confidence, "confidence", minimum=0.0)
        if conf > 1.0:
            raise PayloadError(f"confidence={conf} must be in [0.0, 1.0]")
        s(self, "confidence", conf)
        s(self, "source", _segment(self.source, "source"))

        if not isinstance(self.approaches, dict):
            raise PayloadError("approaches must be an object")
        if len(self.approaches) > MAX_APPROACHES:
            raise PayloadError(f"approaches has {len(self.approaches)} entries, cap is {MAX_APPROACHES}")
        cleaned: dict[str, dict[str, Any]] = {}
        for name, body in self.approaches.items():
            name = _enum(name, APPROACHES, "approaches key")
            if not isinstance(body, dict):
                raise PayloadError(f"approaches[{name!r}] must be an object")
            unknown = set(body) - {"vehicle_count", "density", "queue_estimate"}
            if unknown:
                raise PayloadError(f"approaches[{name!r}] has unexpected field(s) {sorted(unknown)}")
            cleaned[name] = {
                "vehicle_count": _non_negative_int(body.get("vehicle_count"), f"approaches[{name}].vehicle_count"),
                "density": _enum(body.get("density"), DENSITY_LEVELS, f"approaches[{name}].density"),
                "queue_estimate": _non_negative_int(body.get("queue_estimate"), f"approaches[{name}].queue_estimate"),
            }
        s(self, "approaches", cleaned)

    def topic(self) -> str:
        return topics.camera_topic(self.junction_id)


# ----------------------------------------------------------------------
# psychoflow/incident   (§7.3)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class IncidentPayload(_Payload):
    """§7.3's shape verbatim, so `to_intake_kwargs()` feeds
    `IncidentIntake.report()` with no field translation."""

    incident_id: str
    type: str
    location: dict[str, str]
    severity: str
    affected_lanes: list[str]
    reported_at_sim_time: float
    estimated_duration_s: float
    source: str = "iot_hotline"

    def __post_init__(self) -> None:
        s = object.__setattr__
        s(self, "incident_id", _segment(self.incident_id, "incident_id"))
        s(self, "type", _enum(self.type, INCIDENT_TYPES, "type"))
        s(self, "severity", _enum(self.severity, SEVERITIES, "severity"))
        s(self, "source", _segment(self.source, "source"))
        s(self, "reported_at_sim_time",
          _finite_float(self.reported_at_sim_time, "reported_at_sim_time",
                        minimum=0.0, maximum=MAX_SIM_TIME_S))
        dur = _finite_float(self.estimated_duration_s, "estimated_duration_s",
                            minimum=0.0, maximum=MAX_INCIDENT_DURATION_S)
        if dur <= 0.0:
            raise PayloadError(f"estimated_duration_s={dur} must be > 0")
        s(self, "estimated_duration_s", dur)

        if not isinstance(self.location, dict):
            raise PayloadError("location must be an object")
        unknown = set(self.location) - {"junction_id", "lane_id"}
        if unknown:
            raise PayloadError(f"location has unexpected field(s) {sorted(unknown)}")
        s(self, "location", {
            "junction_id": _corridor_junction(
                self.location.get("junction_id"), "location.junction_id"),
            "lane_id": _segment(self.location.get("lane_id"), "location.lane_id"),
        })

        if not isinstance(self.affected_lanes, list):
            raise PayloadError("affected_lanes must be an array")
        if not self.affected_lanes:
            raise PayloadError("affected_lanes must not be empty")
        if len(self.affected_lanes) > MAX_AFFECTED_LANES:
            raise PayloadError(
                f"affected_lanes has {len(self.affected_lanes)} entries, cap is {MAX_AFFECTED_LANES}"
            )
        # De-duped, order preserved - mirrors control_api.inject_incident.
        seen: list[str] = []
        for lane in self.affected_lanes:
            lane = _segment(lane, "affected_lanes[]")
            if lane not in seen:
                seen.append(lane)
        s(self, "affected_lanes", seen)

    def to_intake_kwargs(self) -> dict[str, Any]:
        """Exactly `IncidentIntake.report()`'s signature. Note it assigns
        its own `incident_id`, so ours is transport-side provenance only."""
        return {
            "incident_type": self.type,
            "junction_id": self.location["junction_id"],
            "lane_id": self.location["lane_id"],
            "severity": self.severity,
            "affected_lanes": list(self.affected_lanes),
            "reported_at_sim_time": self.reported_at_sim_time,
            "estimated_duration_s": self.estimated_duration_s,
        }


# ----------------------------------------------------------------------
# psychoflow/weather   (§7.4)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class WeatherPayload(_Payload):
    state: str
    changed_at_sim_time: float
    source: str = "iot_weather"

    def __post_init__(self) -> None:
        s = object.__setattr__
        s(self, "state", _enum(self.state, WEATHER_STATES, "state"))
        s(self, "changed_at_sim_time",
          _finite_float(self.changed_at_sim_time, "changed_at_sim_time",
                        minimum=0.0, maximum=MAX_SIM_TIME_S))
        s(self, "source", _segment(self.source, "source"))


PAYLOAD_FOR_KIND: dict[str, type[_Payload]] = {
    topics.KIND_COUNTS: LaneCountsPayload,
    topics.KIND_CAMERA: CameraPayload,
    topics.KIND_INCIDENT: IncidentPayload,
    topics.KIND_WEATHER: WeatherPayload,
}
assert set(PAYLOAD_FOR_KIND) == set(topics.KINDS), "payload dispatch drifted from topics.KINDS"


def decode(topic: str, raw: str | bytes) -> _Payload:
    """Turn one inbound MQTT message into a validated payload, or raise.

    Order matters and is asserted by `tests.test_iot` C2/C3/C5:
    size cap -> routable topic -> UTF-8 -> JSON -> object -> fields ->
    topic/body agreement.
    """
    if isinstance(raw, (bytes, bytearray)) and len(raw) > MAX_PAYLOAD_BYTES:
        raise PayloadError(f"payload is {len(raw)} bytes, cap is {MAX_PAYLOAD_BYTES}")

    parsed = topics.parse_topic(topic)
    if parsed is None:
        raise PayloadError(f"topic {topic!r} is not one of {topics.KINDS}")

    payload = PAYLOAD_FOR_KIND[parsed.kind].from_dict(_load_json(raw))

    # Rule 4: a body that disagrees with its own topic is forged.
    if parsed.junction_id is not None and payload.junction_id != parsed.junction_id:
        raise PayloadError(
            f"topic says junction {parsed.junction_id!r}, body says {payload.junction_id!r}"
        )
    if parsed.lane_id is not None and payload.lane_id != parsed.lane_id:
        raise PayloadError(f"topic says lane {parsed.lane_id!r}, body says {payload.lane_id!r}")

    return payload
