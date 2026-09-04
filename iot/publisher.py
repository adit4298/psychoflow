"""paho-mqtt publishers: a thin typed one, and a simulated sensor field.

Run the simulated field:  `python -m iot.publisher --steps 20`
(assumes `python -m iot.broker` is already up, or pass `--with-broker`.)

Two paho-2.x facts that cost time if you meet them at the demo
--------------------------------------------------------------
1. **`CallbackAPIVersion` is a required first argument in paho 2.x.** It
   still defaults to `VERSION1`, which is deprecated and emits a warning
   with a different `on_connect` signature. `VERSION2` is named
   explicitly everywhere here so the callback signatures cannot drift.

2. **`connect()` returns before CONNACK arrives.** Publishing immediately
   after `connect()` + `loop_start()` fails with `The client is not
   currently connected` - a real error raised from
   `wait_for_publish()`, not a queued message. `connect()` below blocks
   on an `threading.Event` set by `on_connect`, so a publisher handed
   back to a caller is genuinely connected.

Every publish is QoS 1 and waits for the broker's PUBACK. That is slower
than fire-and-forget and it is the right trade: a dropped sensor reading
that nothing notices is worse than a slow one, and the volumes here are a
few messages per simulation step.
"""

from __future__ import annotations

import argparse
import logging
import random
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt

from iot import topics
from iot.schema import (
    DENSITY_LEVELS,
    VEHICLE_TYPES,
    CameraPayload,
    IncidentPayload,
    LaneCountsPayload,
    WeatherPayload,
    _Payload,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_QOS = 1
CONNECT_TIMEOUT_S = 15.0
PUBLISH_TIMEOUT_S = 10.0
KEEPALIVE_S = 30

logger = logging.getLogger(__name__)


class IoTPublisher:
    """Publishes validated payloads onto their own topics.

    The payload types own their topic (`payload.topic()`), so a caller
    cannot publish a counts message onto the weather topic by accident.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        client_id: str | None = None,
        qos: int = DEFAULT_QOS,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.qos = int(qos)
        self.published = 0
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or f"psychoflow-pub-{random.randrange(1 << 30):08x}",
        )
        self._connected = threading.Event()
        self._connect_rc: Any = None
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    # -- callbacks ------------------------------------------------------
    def _on_connect(self, _client, _userdata, _flags, reason_code, _properties=None) -> None:
        self._connect_rc = reason_code
        self._connected.set()

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties=None) -> None:
        self._connected.clear()
        if reason_code:
            logger.warning("publisher disconnected from %s:%s: %s", self.host, self.port, reason_code)

    # -- lifecycle ------------------------------------------------------
    def connect(self) -> IoTPublisher:
        self._connected.clear()
        self._client.connect(self.host, self.port, KEEPALIVE_S)
        self._client.loop_start()
        if not self._connected.wait(CONNECT_TIMEOUT_S):
            self._client.loop_stop()
            raise TimeoutError(f"no CONNACK from {self.host}:{self.port} within {CONNECT_TIMEOUT_S}s")
        if getattr(self._connect_rc, "is_failure", False):
            self._client.loop_stop()
            raise ConnectionError(f"broker refused connection: {self._connect_rc}")
        return self

    def disconnect(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()
        self._connected.clear()

    def __enter__(self) -> IoTPublisher:
        return self.connect()

    def __exit__(self, *_exc) -> None:
        self.disconnect()

    # -- publishing -----------------------------------------------------
    def _publish(self, topic: str, payload: _Payload) -> None:
        info = self._client.publish(topic, payload.to_json(), qos=self.qos)
        info.wait_for_publish(PUBLISH_TIMEOUT_S)  # see docstring point 2
        self.published += 1

    def publish_counts(self, payload: LaneCountsPayload) -> None:
        self._publish(payload.topic(), payload)

    def publish_camera(self, payload: CameraPayload) -> None:
        self._publish(payload.topic(), payload)

    def publish_incident(self, payload: IncidentPayload) -> None:
        self._publish(topics.INCIDENT_TOPIC, payload)

    def publish_weather(self, payload: WeatherPayload) -> None:
        self._publish(topics.WEATHER_TOPIC, payload)


class SimulatedSensorPublisher(IoTPublisher):
    """A synthetic roadside sensor field for the demo and the done-bar.

    It fabricates plausible §7.1/§7.2 traffic - it is NOT connected to
    SUMO and must never be presented as sensed reality (§17). Its jobs are
    (a) to prove the transport end to end without a simulator, and (b) to
    be a fallback demo path if the live sim or the detector misbehaves.

    Deterministic under `seed`: an instance-local `random.Random`, matching
    the convention in `perception/v2x.py` and `vision_mock.py`, so it never
    perturbs any other RNG stream.
    """

    #: A three-junction corridor with a couple of approach lanes each -
    #: shaped like §0.1's J1/J2/J3 but deliberately not read from a net
    #: file, because this module must not import SUMO.
    DEFAULT_LANES: dict[str, list[tuple[str, str]]] = {
        "J1": [("N1_J1_0", "north"), ("W1_J1_0", "west")],
        "J2": [("N2_J2_0", "north"), ("J1_J2_0", "west")],
        "J3": [("N3_J3_0", "north"), ("J2_J3_0", "west")],
    }

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        lanes: dict[str, list[tuple[str, str]]] | None = None,
        seed: int | None = None,
        client_id: str | None = None,
    ) -> None:
        super().__init__(host, port, client_id=client_id or "psychoflow-sim-sensors")
        self.lanes = lanes or {j: list(v) for j, v in self.DEFAULT_LANES.items()}
        self._rng = random.Random(seed)
        self._incident_seq = 0
        self._weather_state = "clear"

    # -- synthesis ------------------------------------------------------
    def _counts_for(self, junction_id: str, lane_id: str, approach: str, sim_time: float) -> LaneCountsPayload:
        total = self._rng.randint(0, 14)
        halted = self._rng.randint(0, total)
        composition = {vtype: 0 for vtype in VEHICLE_TYPES}
        for _ in range(total):
            # Weighted toward the mixed-traffic mix the corridor models.
            composition[self._rng.choices(VEHICLE_TYPES, weights=(35, 15, 40, 9, 1))[0]] += 1
        max_wait = round(self._rng.uniform(0.0, 130.0), 2) if halted else 0.0
        return LaneCountsPayload(
            junction_id=junction_id,
            lane_id=lane_id,
            approach=approach,
            vehicle_count=total,
            halted_count=halted,
            type_composition=composition,
            wait_time_current=round(max_wait * self._rng.uniform(0.4, 1.0), 2),
            wait_time_max_single_vehicle=max_wait,
            starvation_flag=max_wait > 90.0,  # §0.1's soft threshold
            sim_time=sim_time,
            source="iot_sim_sensor",
        )

    def _camera_for(self, junction_id: str, frame_index: int, sim_time: float) -> CameraPayload:
        approaches: dict[str, dict[str, Any]] = {}
        for _lane_id, approach in self.lanes[junction_id]:
            count = self._rng.randint(0, 18)
            density = (
                DENSITY_LEVELS[0] if count <= 4 else DENSITY_LEVELS[1] if count <= 11 else DENSITY_LEVELS[2]
            )
            approaches[approach] = {
                "vehicle_count": count,
                "density": density,
                "queue_estimate": self._rng.randint(0, count),
            }
        return CameraPayload(
            junction_id=junction_id,
            frame_index=frame_index,
            detected_at=sim_time,
            approaches=approaches,
            emergency_vehicle_flag=self._rng.random() < 0.05,
            confidence=round(self._rng.uniform(0.72, 0.97), 3),
            source="iot_sim_camera",
        )

    def _incident_for(self, sim_time: float) -> IncidentPayload:
        junction_id = self._rng.choice(sorted(self.lanes))
        lane_id, _approach = self._rng.choice(self.lanes[junction_id])
        self._incident_seq += 1
        return IncidentPayload(
            incident_id=f"iot_{self._incident_seq:04d}",
            type=self._rng.choice(["lane_blocked", "accident", "roadworks"]),
            location={"junction_id": junction_id, "lane_id": lane_id},
            severity=self._rng.choice(["low", "medium", "high"]),
            affected_lanes=[lane_id],
            reported_at_sim_time=sim_time,
            estimated_duration_s=float(self._rng.choice([120, 300, 600])),
            source="iot_sim_hotline",
        )

    # -- driving --------------------------------------------------------
    def publish_step(
        self,
        sim_time: float,
        *,
        frame_index: int = 0,
        force_incident: bool = False,
        force_weather: bool = False,
    ) -> int:
        """Emit one step's worth of telemetry. Returns messages published.

        Counts and camera go every step; incidents and weather are rare by
        design (that is what they are), so the `force_*` flags exist for
        tests and the demo rather than making the rates unrealistic.
        """
        before = self.published
        for junction_id, lanes in sorted(self.lanes.items()):
            for lane_id, approach in lanes:
                self.publish_counts(self._counts_for(junction_id, lane_id, approach, sim_time))
            self.publish_camera(self._camera_for(junction_id, frame_index, sim_time))

        if force_incident or self._rng.random() < 0.02:
            self.publish_incident(self._incident_for(sim_time))

        if force_weather or self._rng.random() < 0.01:
            self._weather_state = self._rng.choice(["clear", "rain", "heavy_rain"])
            self.publish_weather(
                WeatherPayload(
                    state=self._weather_state,
                    changed_at_sim_time=sim_time,
                    source="iot_sim_weather",
                )
            )
        return self.published - before

    def run(self, steps: int = 20, interval_s: float = 1.0, step_seconds: float = 5.0) -> int:
        """Publish `steps` steps, pacing at `interval_s` wall-clock."""
        total = 0
        for step in range(steps):
            total += self.publish_step(sim_time=step * step_seconds, frame_index=step)
            if interval_s and step + 1 < steps:
                time.sleep(interval_s)
        return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish simulated PsychoFlow sensor telemetry.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--interval", type=float, default=1.0, help="wall-clock seconds between steps")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--with-broker", action="store_true",
        help="start a private loopback broker in-process instead of using a running one",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    broker = None
    port = args.port
    if args.with_broker:
        from iot.broker import IoTBroker

        broker = IoTBroker(port=0).start()
        port = broker.port
        print(f"started private broker on {broker.url}")

    try:
        with SimulatedSensorPublisher(args.host, port, seed=args.seed) as pub:
            print(f"publishing {args.steps} steps to {args.host}:{port} ...")
            total = pub.run(steps=args.steps, interval_s=args.interval)
            print(f"published {total} messages across {len(topics.KINDS)} topic kinds")
    except (ConnectionRefusedError, TimeoutError) as exc:
        print(f"could not reach a broker at {args.host}:{port} - is `python -m iot.broker` running? ({exc})")
        return 1
    finally:
        if broker is not None:
            broker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
