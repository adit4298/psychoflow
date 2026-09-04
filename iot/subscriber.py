"""paho-mqtt subscriber helper - the perception layer's inbound edge.

Run:  `python -m iot.subscriber --seconds 10`

This is the module that turns untrusted bytes into typed payloads, so its
two design rules are both about *not* being the thing that takes the demo
down:

1. **A bad message is dropped and counted, never raised.** `on_message`
   runs on paho's network thread; an exception there is swallowed by paho
   and the subscriber keeps running in an unknown state, or takes the
   thread down. Neither is visible. Every message is decoded inside a
   `try`, failures increment `dropped` and log at DEBUG, and the first few
   log at WARNING so a genuinely misconfigured publisher is still loud.

2. **The buffer is bounded and drops the OLDEST.** A publisher faster than
   the consumer must cost memory in a bounded way. Newest-wins is the
   right eviction for traffic state - a stale queue length is worthless,
   so the reading being discarded is always the least useful one. This
   mirrors `DigitalTwin`'s `deque(maxlen=...)` for `v2x_messages_recent`.

Consumers can work either way: pass `on_payload` for push, or read
`buffer` / `latest_counts()` for pull. Nothing here calls into perception
directly - wiring the twin to this feed is an integration step, and the
shape it should take is written up in NOTES-FOR-INTEGRATION.md.
"""

from __future__ import annotations

import argparse
import logging
import random
import threading
import time
from collections import deque
from typing import Callable

import paho.mqtt.client as mqtt

from iot import topics
from iot.schema import CameraPayload, LaneCountsPayload, PayloadError, _Payload, decode

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1883
DEFAULT_QOS = 1
DEFAULT_QUEUE_SIZE = 500
CONNECT_TIMEOUT_S = 15.0
KEEPALIVE_S = 30

#: How many decode failures get a WARNING before dropping to DEBUG. A
#: misconfigured publisher should be visible; a hostile one should not be
#: able to fill the log.
_LOUD_DROPS = 5

logger = logging.getLogger(__name__)

PayloadHandler = Callable[[str, _Payload], None]


class IoTSubscriber:
    """Subscribes, decodes, and hands back validated payloads."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        on_payload: PayloadHandler | None = None,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        client_id: str | None = None,
        qos: int = DEFAULT_QOS,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.qos = int(qos)
        self.on_payload = on_payload
        self.received = 0
        self.dropped = 0

        self.buffer: deque[tuple[str, _Payload]] = deque(maxlen=int(queue_size))
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._connect_rc = None

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or f"psychoflow-sub-{random.randrange(1 << 30):08x}",
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # -- callbacks ------------------------------------------------------
    def _on_connect(self, _client, _userdata, _flags, reason_code, _properties=None) -> None:
        self._connect_rc = reason_code
        self._connected.set()

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties=None) -> None:
        self._connected.clear()
        if reason_code:
            logger.warning("subscriber disconnected from %s:%s: %s", self.host, self.port, reason_code)

    def _on_message(self, _client, _userdata, message) -> None:
        """Runs on paho's network thread. Must never raise - see rule 1."""
        try:
            payload = decode(message.topic, message.payload)
        except PayloadError as exc:
            self._record_drop(message.topic, exc)
            return
        except Exception as exc:  # noqa: BLE001 - an unexpected decode bug must not kill the feed
            self._record_drop(message.topic, exc, unexpected=True)
            return

        self._enqueue((message.topic, payload))
        if self.on_payload is not None:
            try:
                self.on_payload(message.topic, payload)
            except Exception:  # noqa: BLE001 - a consumer bug is not this thread's to die from
                logger.exception("on_payload handler raised for topic %s", message.topic)

    # -- internals ------------------------------------------------------
    def _record_drop(self, topic: str, exc: BaseException, *, unexpected: bool = False) -> None:
        with self._lock:
            self.dropped += 1
            count = self.dropped
        detail = "unexpected decode failure" if unexpected else "rejected payload"
        if count <= _LOUD_DROPS or unexpected:
            logger.warning("%s on %s: %s", detail, topic, exc)
        else:
            logger.debug("%s on %s: %s", detail, topic, exc)

    def _enqueue(self, item: tuple[str, _Payload]) -> None:
        with self._lock:
            self.buffer.append(item)  # deque(maxlen=) evicts the oldest
            self.received += 1

    # -- lifecycle ------------------------------------------------------
    def connect(self) -> IoTSubscriber:
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

    def __enter__(self) -> IoTSubscriber:
        return self.connect()

    def __exit__(self, *_exc) -> None:
        self.disconnect()

    # -- subscriptions --------------------------------------------------
    def subscribe_all(self) -> None:
        """Everything under `psychoflow/`. Scoped to the prefix rather than
        `#` so this can share a broker with something else."""
        self._client.subscribe(topics.ALL_TOPICS, qos=self.qos)

    def subscribe_kinds(self, *kinds: str) -> None:
        """Subscribe to named topic kinds only (`topics.KIND_*`)."""
        for kind in kinds:
            if kind not in topics.SUBSCRIPTION_FOR_KIND:
                raise ValueError(f"unknown topic kind {kind!r} - must be one of {topics.KINDS}")
            self._client.subscribe(topics.SUBSCRIPTION_FOR_KIND[kind], qos=self.qos)

    # -- pull-side reads ------------------------------------------------
    def drain(self) -> list[tuple[str, _Payload]]:
        """Take everything buffered so far and clear the buffer."""
        with self._lock:
            items = list(self.buffer)
            self.buffer.clear()
        return items

    def latest_counts(self) -> dict[str, LaneCountsPayload]:
        """Most recent §7.1 reading per lane, newest wins.

        Returned keyed by `lane_id`, which is the key `DigitalTwin` already
        uses for lane readings - see NOTES-FOR-INTEGRATION.md.
        """
        with self._lock:
            items = list(self.buffer)
        latest: dict[str, LaneCountsPayload] = {}
        for _topic, payload in items:
            if isinstance(payload, LaneCountsPayload):
                latest[payload.lane_id] = payload
        return latest

    def latest_camera(self) -> dict[str, CameraPayload]:
        """Most recent §7.2 camera summary per junction, newest wins."""
        with self._lock:
            items = list(self.buffer)
        latest: dict[str, CameraPayload] = {}
        for _topic, payload in items:
            if isinstance(payload, CameraPayload):
                latest[payload.junction_id] = payload
        return latest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Subscribe to PsychoFlow telemetry and print it.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--quiet", action="store_true", help="counts only, do not print each message")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def show(topic: str, payload: _Payload) -> None:
        if not args.quiet:
            print(f"  {topic}  ->  {type(payload).__name__}")

    try:
        with IoTSubscriber(args.host, args.port, on_payload=show) as sub:
            sub.subscribe_all()
            print(f"subscribed to {topics.ALL_TOPICS} on {args.host}:{args.port} for {args.seconds}s ...")
            deadline = time.time() + args.seconds
            while time.time() < deadline:
                time.sleep(0.1)
            print(f"received {sub.received}, dropped {sub.dropped}")
    except (ConnectionRefusedError, TimeoutError) as exc:
        print(f"could not reach a broker at {args.host}:{args.port} - "
              f"is `python -m iot.broker` running? ({exc})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
