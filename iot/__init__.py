"""Local MQTT ingestion layer for PsychoFlow (hackathon addition).

This package is a *transport*, not a perception source. It carries the
already-defined §7.1 / §7.2 / §7.3 / §7.4 shapes between processes so the
demo can show real sensors, a camera box, and a hotline feeding the same
digital twin the simulator does — without any module in the core system
learning that MQTT exists.

Deliberate boundaries:

- **Nothing here imports SUMO.** No `traci`, no `sumolib`, at module level
  or otherwise. The broker and clients must be startable on a laptop with
  no `SUMO_HOME`, and `tests.test_iot` D1 asserts this in a subprocess.
- **Every payload is validated on construction and again on decode.** A
  broker with anonymous auth is an untrusted input surface; see
  `iot/schema.py`'s module docstring for the threat model.
- **The broker binds loopback by default** and refuses a non-loopback bind
  without an explicit `allow_lan=True`, mirroring `backend/main.py`'s rule.
  There is no authentication behind it (see `iot/broker.py`).

Modules
  topics.py      the four documented topics: build, parse, refuse injection
  schema.py      JSON-serialisable payload dataclasses + hardened `decode()`
  broker.py      amqtt broker launcher (`python -m iot.broker`)
  publisher.py   paho publisher + a simulated sensor publisher
  subscriber.py  paho subscriber helper the perception layer consumes
"""

__all__ = ["broker", "publisher", "schema", "subscriber", "topics"]
