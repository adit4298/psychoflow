"""Live MQTT telemetry -> the digital twin (§7.1/§7.2/§7.3/§7.4), additively.

This module is the ONLY thing that knows both `iot/` and `backend/` exist.
`iot/` is a transport that never imports SUMO; `twin/` and `env/` are LOCKED and
were not modified. What joins them is one attribute swap on an already-built
twin plus two public mutator calls the twin already exposes for exactly this:

    twin.lane_sensor  ->  _LaneSensorOverlay(real_sensor, provider)   (§7.1)
    twin.weather.set_state(state, sim_time)                           (§7.4)
    twin.incidents.report(**payload.to_intake_kwargs())               (§7.3)

Why an overlay rather than an edit to `twin/digital_twin.py`
------------------------------------------------------------
`DigitalTwin.update()` calls `self.lane_sensor.read_lanes(...)` and feeds the
result into BOTH the §7.6 snapshot the observation is built from AND
`self.vision.observe_all(readings)`. Merging MQTT counts there by editing that
function would touch a locked file; so would teaching `env/` to read a second
source. Wrapping the ATTRIBUTE reaches the same readings and touches neither —
it is the same seam, and the same category of change, as
`SimRunner._apply_vision_source()` swapping `twin.vision` for §7.2.

BLAST RADIUS — stated plainly, because it is larger than the §7.2 swap.
Lane readings feed §9.1's phase scoring and §9.4's reward terms, so a wrong
MQTT reading perturbs signal a wrong vision reading would not. Three things
bound it: the overlay is installed ONLY under `--iot`, which is OFF by default
and unreachable from any recorded-number path; a lane with no fresh message is
returned as the REAL sensor produced it, with its original object identity; and
the whole overlay unwraps the moment the feed goes quiet. Nothing under `env/`,
`safety/`, `twin/` or the reward changed.

What an MQTT counts message is allowed to replace
-------------------------------------------------
The whole §7.1 reading, not just the count. `LaneCountsPayload` carries the
exact field set `LaneReading` holds and `iot/schema.py` exposes
`to_lane_reading_dict()` as "the §7.1 subset only - what LaneReading.to_dict()
produces", i.e. it was built for this hand-off. A sensor that reports a count
but leaves TraCI's ground-truth waiting times underneath it would produce a
reading that never existed at any instant — a blend, not a measurement.

Staleness — a deliberate, recorded deviation from NOTES §9.3
------------------------------------------------------------
§9.3 derives freshness as `now_sim_time - payload.sim_time`. That is right for a
sensor network sharing the corridor's clock and WRONG for anything else:
`iot.publisher.SimulatedSensorPublisher` runs its own `sim_time` from 0 on its
own schedule, so differencing the two clocks yields a number with no physical
meaning — and, being arbitrarily large in either direction, it would either drop
every message or accept every stale one.

So staleness is judged on ARRIVAL WALL-CLOCK, which is always meaningful, and
`last_seen_s` is stamped in the twin's `sim_time` frame at ingest. `fresh_s`,
which `backend.frame_sources.build_iot_sensors` still derives as
`sim_time - last_seen_s`, therefore comes out right by construction and the
wire shape §A3/§9.3 pinned is unchanged. The payload's own clock is preserved
on the telemetry entry as `payload_sim_time` for anyone comparing the two
deliberately.

Nothing here raises into the sim thread. A broker that is down, a message that
does not decode, a lane absent from the live network: all are dropped, and the
corridor keeps running on §7.1 ground truth exactly as it does with `--iot` off.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

# `iot` imports neither SUMO nor torch; importing it here keeps that true.
from iot.schema import (
    CameraPayload,
    IncidentPayload,
    LaneCountsPayload,
    WeatherPayload,
)
from iot.subscriber import DEFAULT_HOST, DEFAULT_PORT, IoTSubscriber

#: A reading whose message arrived longer ago than this (WALL-CLOCK seconds —
#: see the module docstring) is ignored and the lane falls back to §7.1 ground
#: truth. Sized against `SimulatedSensorPublisher`'s 1.0s default interval with
#: room for a slow decode: a feed that misses ~10 consecutive publishes is
#: genuinely gone, not merely jittering.
FRESHNESS_WINDOW_S = 10.0

#: Telemetry `source` tag for a lane whose reading came off the wire.
SOURCE_MQTT = "mqtt"

#: Hard cap on tracked lanes. `LaneCountsPayload.lane_id` is charset-validated
#: by `iot/topics.py` but — unlike `junction_id` — is NOT checked against the
#: real corridor, so a publisher can name arbitrary lane ids. Such a lane can
#: never reach the sim (the overlay iterates the LIVE topology, not the wire's
#: keys), but without a cap the dict would grow forever and `current_readings()`
#: — which runs inside `twin.update()` on the sim-thread hot path — would scan
#: a growing history every step. The corridor tops out at 3 junctions x 16 lane
#: slots = 48; 256 leaves generous headroom for a topology rebuild while still
#: bounding the memory and the scan.
MAX_TRACKED_LANES = 256


@dataclass(frozen=True)
class IoTUpdate:
    """One poll's worth of fresh telemetry, already staleness-filtered."""

    telemetry: dict[str, dict] = field(default_factory=dict)
    weather: str | None = None
    incidents: list[dict] = field(default_factory=list)
    camera: dict[str, dict] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.telemetry or self.weather or self.incidents or self.camera)


class _LaneSensorOverlay:
    """Delegates to the real §7.1 sensor, then lays fresh MQTT readings on top.

    Every attribute other than `read_lane`/`read_lanes` passes through to the
    wrapped sensor, so constants like `WAITING_TIME_MEMORY_S` and any method
    added later keep resolving — this is a decorator, not a reimplementation.

    A lane with no fresh message is returned EXACTLY as the real sensor
    produced it, same object, so "no traffic on the wire" is indistinguishable
    from the overlay not being installed.
    """

    def __init__(
        self, inner: Any, provider: Callable[[], Mapping[str, LaneCountsPayload]]
    ) -> None:
        self._inner = inner
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this class does not define itself.
        return getattr(self._inner, name)

    @property
    def inner(self) -> Any:
        """The wrapped sensor, so the swap can be undone exactly."""
        return self._inner

    @staticmethod
    def _from_payload(payload: LaneCountsPayload, approach: str, truth):
        """Build a §7.1 reading from the wire, WITHOUT the ambulance count.

        `approach` comes from the LIVE topology, not the payload: the twin
        knows which way a lane faces and a publisher's claim about it is not
        authoritative.

        SAFETY — `type_composition["ambulance"]` is taken from `truth` (the
        real TraCI sensor's reading for this lane) and NEVER from the wire.
        `safety/validator.py` raises its emergency override on exactly this
        expression:

            reading["type_composition"].get("ambulance", 0) > 0

        so letting a payload set that key would hand an anonymous publisher
        RULE_EMERGENCY on any corridor lane it names — a second, unaudited
        route into the rule whose whole contract is that it "cannot be
        delayed/blocked/deprioritized by anything else". Corridor lane ids are
        not secret; they ride on the public §13.2 frame.

        This is the SAME decision NOTES §9.2 already made for the vision
        detector's `emergency_vehicle_flag`, for the same reason and with the
        same conclusion: a roadside counter cannot identify an ambulance, so
        the honest count is the one the twin actually measured. Everything an
        MQTT sensor genuinely observes — total count, halted, waits,
        starvation, and the other four vehicle classes — still comes from the
        wire. Actuation stays with a real detected ambulance or the operator's
        audited `trigger_emergency`.
        """
        from perception.lane_sensor import LaneReading

        fields = payload.to_lane_reading_dict()
        fields["approach"] = approach
        composition = dict(fields.get("type_composition") or {})
        composition["ambulance"] = int(
            (getattr(truth, "type_composition", None) or {}).get("ambulance", 0)
        )
        fields["type_composition"] = composition
        return LaneReading(**fields)

    def read_lane(self, lane_id: str, approach: str):
        # The real sensor is read even when a payload is present: it is the
        # only trustworthy source for the ambulance count (see _from_payload).
        truth = self._inner.read_lane(lane_id, approach)
        payload = self._provider().get(lane_id)
        if payload is None:
            return truth
        return self._from_payload(payload, approach, truth)

    def read_lanes(self, lane_approach_map: dict[str, str]) -> dict:
        readings = self._inner.read_lanes(lane_approach_map)
        fresh = self._provider()
        if not fresh:
            return readings
        # Rebuild rather than mutate (coding-style.md); a lane absent from the
        # feed keeps its original object identity. A payload naming a lane that
        # is not in the live topology is ignored entirely — this comprehension
        # iterates the REAL readings, never the wire's key set.
        return {
            lane_id: (
                self._from_payload(
                    fresh[lane_id],
                    lane_approach_map.get(lane_id, reading.approach),
                    reading,
                )
                if lane_id in fresh
                else reading
            )
            for lane_id, reading in readings.items()
        }


class IoTBridge:
    """Owns the subscriber and turns its queue into one `IoTUpdate` per step.

    Constructed and polled ONLY on the SimRunner thread (the TraCI-single-
    thread standing rule). paho runs its own network loop, which is why
    `_lock` guards the small dict of last-known readings that loop fills.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        freshness_window_s: float = FRESHNESS_WINDOW_S,
    ) -> None:
        self.host = host
        self.port = port
        self.freshness_window_s = float(freshness_window_s)
        self._sub: IoTSubscriber | None = None
        self._lock = threading.Lock()
        # lane_id -> (payload, arrived_monotonic)
        self._readings: dict[str, tuple[LaneCountsPayload, float]] = {}
        self._connected = False
        self._messages_seen = 0
        self._dropped_stale = 0

    # -- lifecycle ----------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> dict:
        return {
            "connected": self._connected,
            "messages_seen": self._messages_seen,
            "dropped_stale": self._dropped_stale,
            "lanes_fresh": len(self.current_readings()),
        }

    def start(self) -> bool:
        """Connect and subscribe. Returns False instead of raising.

        A broker that is not up is a normal condition — `--iot` may be passed
        before it starts — and must not take the demo down.
        """
        try:
            sub = IoTSubscriber(host=self.host, port=self.port)
            sub.connect()
            sub.subscribe_all()
        except Exception as exc:  # noqa: BLE001 — deliberate: never fatal
            print(f"[iot] subscriber could not reach mqtt://{self.host}:{self.port} "
                  f"({type(exc).__name__}: {exc}); the corridor continues on "
                  f"§7.1 ground truth")
            self._sub = None
            self._connected = False
            return False
        self._sub = sub
        self._connected = True
        print(f"[iot] subscribed to mqtt://{self.host}:{self.port} — counts "
              f"overlay §7.1 lane readings, weather sets §7.4, incidents feed "
              f"§7.3 and the priority agent")
        return True

    def stop(self) -> None:
        sub, self._sub = self._sub, None
        self._connected = False
        if sub is None:
            return
        try:
            sub.disconnect()
        except Exception as exc:  # noqa: BLE001
            print(f"[iot] disconnect ignored ({type(exc).__name__}: {exc})")

    # -- per-step ingest ----------------------------------------------
    def current_readings(self) -> dict[str, LaneCountsPayload]:
        """Fresh lane readings only — the provider `_LaneSensorOverlay` calls.

        Called on the sim thread from inside `twin.update()`, so it takes the
        lock and returns a plain snapshot rather than a live view.
        """
        cutoff = time.monotonic() - self.freshness_window_s
        with self._lock:
            return {
                lane_id: payload
                for lane_id, (payload, arrived) in self._readings.items()
                if arrived >= cutoff
            }

    def poll(self, sim_time: float) -> IoTUpdate:
        """Drain the queue and return this step's fresh telemetry.

        Never raises. The subscriber has already validated and decoded every
        payload against the §7.x schemas, so anything arriving here is
        well-formed by construction.
        """
        if self._sub is None:
            return IoTUpdate()
        try:
            drained = self._sub.drain()
        except Exception as exc:  # noqa: BLE001
            print(f"[iot] drain failed ({type(exc).__name__}: {exc}); holding "
                  f"last-known readings")
            return IoTUpdate(telemetry=self._telemetry(sim_time))

        now = time.monotonic()
        weather: str | None = None
        incidents: list[dict] = []
        camera: dict[str, dict] = {}

        for _topic, payload in drained:
            self._messages_seen += 1
            if isinstance(payload, LaneCountsPayload):
                with self._lock:
                    self._readings[payload.lane_id] = (payload, now)
                    self._prune(now)
            elif isinstance(payload, CameraPayload):
                camera[payload.junction_id] = payload.to_dict()
            elif isinstance(payload, WeatherPayload):
                weather = payload.state          # last one wins within a step
            elif isinstance(payload, IncidentPayload):
                incidents.append(payload.to_intake_kwargs())

        return IoTUpdate(
            telemetry=self._telemetry(sim_time),
            weather=weather,
            incidents=incidents,
            camera=camera,
        )

    def _prune(self, now: float) -> None:
        """DELETE stale entries, not merely filter them out on read.

        Caller must hold `_lock`. Filtering alone left `_readings` growing
        without bound under a publisher spraying unique lane ids, and made both
        read paths O(history) every step. Dropping stale rows bounds the dict
        to "lanes heard from inside the freshness window"; `MAX_TRACKED_LANES`
        then bounds it even against a burst faster than that window.
        """
        cutoff = now - self.freshness_window_s
        for lane_id in [k for k, (_p, a) in self._readings.items() if a < cutoff]:
            del self._readings[lane_id]
            self._dropped_stale += 1
        if len(self._readings) > MAX_TRACKED_LANES:
            # Oldest-first, so live lanes survive a flood of invented ids.
            for lane_id, _ in sorted(
                self._readings.items(), key=lambda kv: kv[1][1]
            )[: len(self._readings) - MAX_TRACKED_LANES]:
                del self._readings[lane_id]

    def _telemetry(self, sim_time: float) -> dict[str, dict]:
        """`SimRunner.iot_telemetry` shape: {lane: {source, last_seen_s}}.

        `last_seen_s` is the twin's `sim_time` at ingest, NOT the payload's —
        see the module docstring. `build_iot_sensors` differences it against
        the frame's `sim_time`, so a lane heard from this step reports
        `fresh_s` ~0.0 and one heard from three steps ago reports ~15.0.
        """
        now = time.monotonic()
        cutoff = now - self.freshness_window_s
        out: dict[str, dict] = {}
        with self._lock:
            for lane_id, (payload, arrived) in self._readings.items():
                if arrived < cutoff:
                    continue    # counted, and deleted, by _prune
                out[lane_id] = {
                    "source": SOURCE_MQTT,
                    "last_seen_s": float(sim_time) - (now - arrived),
                    "payload_sim_time": float(payload.sim_time),
                }
        return out


def demo() -> int:
    """Self-check for the two pieces with real logic. No broker, no SUMO."""
    from perception.lane_sensor import LaneReading

    def _payload(lane_id: str, count: int) -> LaneCountsPayload:
        return LaneCountsPayload(
            junction_id="J1", lane_id=lane_id, approach="north",
            vehicle_count=count, halted_count=0,
            type_composition={"bike": 0, "auto": 0, "car": count,
                              "truck": 0, "ambulance": 0},
            wait_time_current=0.0, wait_time_max_single_vehicle=0.0,
            starvation_flag=False, sim_time=100.0,
        )

    class _Sensor:
        """Memoised on purpose: the real `LaneSensor` builds a fresh reading
        every call, so identity across two calls is never stable and could not
        distinguish "passed through" from "rebuilt". Caching here makes the
        pass-through claim testable at all."""
        WAITING_TIME_MEMORY_S = 1000
        def __init__(self):
            self._cache = {}
        def read_lane(self, lane_id, approach):
            key = (lane_id, approach)
            if key not in self._cache:
                self._cache[key] = LaneReading(
                    lane_id=lane_id, approach=approach, vehicle_count=1,
                    halted_count=0,
                    type_composition={"bike": 0, "auto": 0, "car": 1,
                                      "truck": 0, "ambulance": 0},
                    wait_time_current=0.0, wait_time_max_single_vehicle=0.0,
                    starvation_flag=False,
                )
            return self._cache[key]
        def read_lanes(self, m):
            return {lid: self.read_lane(lid, ap) for lid, ap in m.items()}

    inner = _Sensor()
    fresh: dict[str, LaneCountsPayload] = {}
    ov = _LaneSensorOverlay(inner, lambda: fresh)
    lanes = {"A_0": "north", "B_0": "south"}

    base = ov.read_lanes(lanes)
    assert base["A_0"].vehicle_count == 1 and base["B_0"].vehicle_count == 1, base
    assert ov.WAITING_TIME_MEMORY_S == 1000, "pass-through attribute lost"

    fresh["A_0"] = _payload("A_0", 42)
    got = ov.read_lanes(lanes)
    assert got["A_0"].vehicle_count == 42, got["A_0"]
    assert got["B_0"].vehicle_count == 1, "a lane with no message must be untouched"
    assert got["B_0"] is base["B_0"], "unoverlaid readings must not be rebuilt"
    # Approach comes from the LIVE topology, never the payload (which says north).
    assert ov.read_lanes({"A_0": "east"})["A_0"].approach == "east", "approach not from topology"
    assert ov.read_lane("A_0", "north").vehicle_count == 42
    assert inner.read_lanes(lanes)["A_0"].vehicle_count == 1, "inner sensor mutated"

    fresh.clear()
    assert ov.read_lanes(lanes)["A_0"].vehicle_count == 1, "overlay did not unwrap"

    # -- SAFETY: the wire can never forge an ambulance, and can never erase a
    # real one. safety/validator.py raises RULE_EMERGENCY on exactly this key.
    forged = _payload("A_0", 3)
    object.__setattr__(forged, "type_composition",
                       {"bike": 0, "auto": 0, "car": 3, "truck": 0,
                        "ambulance": 1})
    fresh["A_0"] = forged
    got = ov.read_lanes(lanes)["A_0"]
    assert got.type_composition["ambulance"] == 0, (
        "an MQTT payload FORGED an ambulance — this hands an anonymous "
        f"publisher the emergency override: {got.type_composition}")
    assert got.vehicle_count == 3, "the honest fields must still come off the wire"

    class _AmbulanceSensor(_Sensor):
        def read_lane(self, lane_id, approach):
            r = super().read_lane(lane_id, approach)
            object.__setattr__(r, "type_composition",
                               {"bike": 0, "auto": 0, "car": 1, "truck": 0,
                                "ambulance": 1})
            return r

    amb = _LaneSensorOverlay(_AmbulanceSensor(), lambda: fresh)
    # The wire says no ambulance (its own composition has ambulance 0 after the
    # forgery strip); ground truth says there IS one. Ground truth must win, or
    # a publisher could SUPPRESS a real emergency, which is the worse direction.
    fresh["A_0"] = _payload("A_0", 3)
    assert amb.read_lanes(lanes)["A_0"].type_composition["ambulance"] == 1, (
        "an MQTT payload SUPPRESSED a real ambulance")
    assert amb.read_lane("A_0", "north").type_composition["ambulance"] == 1
    fresh.clear()

    # Staleness: a reading older than the window leaves both views.
    b = IoTBridge(freshness_window_s=0.5)
    with b._lock:
        b._readings["A_0"] = (_payload("A_0", 7), time.monotonic())
        b._readings["B_0"] = (_payload("B_0", 9), time.monotonic() - 5.0)
    assert set(b.current_readings()) == {"A_0"}, b.current_readings()
    tel = b._telemetry(sim_time=200.0)
    assert set(tel) == {"A_0"}, tel
    assert tel["A_0"]["source"] == SOURCE_MQTT
    # fresh_s as build_iot_sensors derives it: ~0 against a 200.0 frame clock,
    # even though the payload's own clock says 100.0 — the case §9.3's literal
    # formula gets wrong (it would claim 100s stale and drop a live reading).
    assert abs(200.0 - tel["A_0"]["last_seen_s"]) < 1.0, tel
    assert tel["A_0"]["payload_sim_time"] == 100.0, tel

    # -- growth is BOUNDED: _prune deletes, it does not merely filter.
    b2 = IoTBridge(freshness_window_s=0.5)
    now = time.monotonic()
    with b2._lock:
        for i in range(50):
            b2._readings[f"fake_{i}"] = (_payload(f"fake_{i}", 1), now - 5.0)
        b2._readings["live"] = (_payload("live", 1), now)
        b2._prune(time.monotonic())
        assert set(b2._readings) == {"live"}, (
            f"stale rows were filtered but not DELETED: {len(b2._readings)} left")
    # ...and the hard cap holds even against a burst faster than the window.
    b3 = IoTBridge(freshness_window_s=3600.0)
    with b3._lock:
        for i in range(MAX_TRACKED_LANES + 40):
            b3._readings[f"spam_{i}"] = (_payload(f"spam_{i}", 1),
                                         time.monotonic() + i * 1e-6)
        b3._prune(time.monotonic())
        assert len(b3._readings) == MAX_TRACKED_LANES, len(b3._readings)
        # Oldest evicted first, so the newest arrivals survive.
        assert f"spam_{MAX_TRACKED_LANES + 39}" in b3._readings

    # A bridge with no subscriber polls to an empty, falsy update.
    assert not IoTBridge().poll(0.0), "no subscriber must yield nothing"

    print("backend.iot_bridge: 24/24 assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(demo())
