"""Local MQTT broker launcher (amqtt, pure Python - no mosquitto install).

Run standalone:      `python -m iot.broker`
Run the test suite:  `python -m iot.broker --selftest`

Two threading facts, both learned by measurement, both load-bearing
----------------------------------------------------------------------
1. **`amqtt.broker.Broker` must be CONSTRUCTED inside a running event
   loop.** Its `__init__` calls `asyncio.get_running_loop()`, so building
   it on the main thread and handing it to a loop later raises
   `RuntimeError: no running event loop`. It is therefore constructed
   inside `_serve()`, not in `start()`.

2. **`plugins={}` does not mean "defaults" - it means "no auth plugin",
   and every client is then refused with `Not authorized`.** amqtt 0.12
   loads `AnonymousAuthPlugin` from `default_broker_plugins()`; passing an
   empty dict replaces that default rather than extending it. The failure
   is a clean CONNACK rejection with no broker-side error, so it reads as
   a client bug. `_BROKER_PLUGINS` names the plugin explicitly.

amqtt is asyncio and paho is threaded, so the two never share a loop: the
broker owns a private loop on its own thread, and paho clients run their
own network threads via `loop_start()`. That is the arrangement that
avoids the "cannot call asyncio.run from a running loop" trap in a single
process, which is what the done-bar and every test in `tests.test_iot`
section E rely on.

SECURITY - this broker is a LOCAL DEMO SURFACE
----------------------------------------------
Anonymous auth, no TLS, no ACL. It binds `127.0.0.1` by default and
REFUSES a non-loopback bind unless `allow_lan=True` is passed explicitly,
mirroring `backend/main.py`'s `_host_rejection()` rule. Do not weaken
this: there is no authentication layer behind it, and any process that
can reach the port can publish to any topic. `iot/schema.py`'s decoder is
what makes that survivable, not the broker.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import socket
import threading
import time

from amqtt.broker import Broker

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1883

#: Hosts that need no `allow_lan` opt-in.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

#: See docstring point 2 - naming this is what keeps clients authorised.
_BROKER_PLUGINS = {
    "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True},
}

_START_TIMEOUT_S = 15.0
_STOP_TIMEOUT_S = 10.0

logger = logging.getLogger(__name__)


def _free_port(host: str) -> int:
    """Reserve an ephemeral port so `port=0` is usable in tests.

    amqtt binds a `host:port` string and offers no way to read back an
    OS-assigned port, so the port is chosen here and passed in concretely.
    The socket is closed before the broker binds - a narrow race that is
    acceptable for a test helper and is never used by the demo path.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host if host not in {"localhost", "::1"} else "127.0.0.1", 0))
        return int(sock.getsockname()[1])


class IoTBroker:
    """A broker on its own thread, with a bounded, checked startup.

    Usable as a context manager (`with IoTBroker(port=0) as b: ...`) or
    driven manually with `start()` / `stop()`.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        allow_lan: bool = False,
        max_connections: int = 64,
    ) -> None:
        if host not in LOOPBACK_HOSTS and not allow_lan:
            raise ValueError(
                f"refusing to bind {host!r}: this broker has NO authentication. "
                "Pass allow_lan=True only if you understand that any host on the "
                "network can then publish to every psychoflow/ topic."
            )
        self.host = host
        self.port = _free_port(host) if port == 0 else int(port)
        self.allow_lan = allow_lan
        self.max_connections = int(max_connections)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._broker: Broker | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    # -- config ---------------------------------------------------------
    @property
    def config(self) -> dict:
        return {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"{self.host}:{self.port}",
                    "max_connections": self.max_connections,
                }
            },
            "plugins": dict(_BROKER_PLUGINS),
        }

    @property
    def url(self) -> str:
        return f"mqtt://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._ready.is_set()

    # -- lifecycle ------------------------------------------------------
    async def _serve(self) -> None:
        self._broker = Broker(self.config)  # MUST be inside the loop - see docstring
        await self._broker.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def start(self) -> IoTBroker:
        if self.is_running:
            return self
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="iot-broker", daemon=True)
        self._thread.start()
        if not self._ready.wait(_START_TIMEOUT_S):
            raise TimeoutError(f"broker did not start within {_START_TIMEOUT_S}s on {self.url}")
        if self._error is not None:
            raise RuntimeError(f"broker failed to start on {self.url}: {self._error}") from self._error
        logger.info("IoT broker listening on %s", self.url)
        return self

    def stop(self) -> None:
        loop, broker = self._loop, self._broker
        if loop is None or not loop.is_running():
            return
        if broker is not None:
            future = asyncio.run_coroutine_threadsafe(broker.shutdown(), loop)
            with contextlib.suppress(Exception):
                future.result(_STOP_TIMEOUT_S)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(_STOP_TIMEOUT_S)
        self._ready.clear()
        self._broker = None
        logger.info("IoT broker on %s stopped", self.url)

    def __enter__(self) -> IoTBroker:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--allow-lan", action="store_true",
        help="bind a non-loopback host. NO AUTHENTICATION - local demo only.",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--verbose", action="store_true",
        help="let amqtt's own INFO logging through (very noisy - broker debugging only).",
    )
    parser.add_argument(
        "--selftest", action="store_true",
        help="run tests.test_iot instead of serving (the done-bar).",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        from tests import test_iot

        return test_iot.main()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")
    # amqtt logs its whole transitions state machine at INFO - dozens of
    # lines per connection. Useful when debugging the broker, unreadable
    # during a demo, so it is pinned to WARNING unless --verbose asks.
    if not args.verbose:
        for name in ("amqtt", "transitions"):
            logging.getLogger(name).setLevel(logging.WARNING)

    try:
        broker = IoTBroker(host=args.host, port=args.port, allow_lan=args.allow_lan)
    except ValueError as exc:
        # A refused --host is operator error, not a crash. Say what to do.
        print(f"error: {exc}")
        print("       pass --allow-lan if that is genuinely what you want.")
        return 2

    if args.allow_lan and args.host not in LOOPBACK_HOSTS:
        print(f"!! WARNING: binding {args.host} with NO AUTHENTICATION. Local demo surface only.")
    broker.start()
    print(f"IoT broker listening on {broker.url}  (Ctrl-C to stop)")
    print("topics: psychoflow/sensor/<junction>/<lane>/counts | .../camera | "
          "psychoflow/incident | psychoflow/weather")
    try:
        while broker.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        broker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
