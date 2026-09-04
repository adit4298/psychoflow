"""The four PsychoFlow MQTT topics: build them, parse them, refuse injection.

    psychoflow/sensor/<junction>/<lane>/counts   per-lane loop/sensor counts (§7.1)
    psychoflow/sensor/<junction>/camera          per-junction camera summary (§7.2)
    psychoflow/incident                          incident report (§7.3)
    psychoflow/weather                           weather change (§7.4)

Why the builders validate rather than just format
-------------------------------------------------
`junction_id` and `lane_id` are interpolated straight into a topic string.
MQTT gives `+` and `#` structural meaning and `/` is the level separator,
so an unvalidated id can *widen a subscription* or *forge a level*: an id
of `"J1/+"` turns a publish into `psychoflow/sensor/J1/+/N1_J1_0/counts`,
and a subscriber id of `"#"` silently subscribes to the whole tree. Both
are quiet failures — nothing raises, the wrong thing just happens. So the
charset is allowlisted at build time, not sanitised after the fact.

`parse_topic` returns None for anything it does not recognise rather than
raising, because it runs on every inbound message including ones this
system never published; `iot.schema.decode` is where an unroutable topic
becomes an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = "psychoflow"

KIND_COUNTS = "counts"
KIND_CAMERA = "camera"
KIND_INCIDENT = "incident"
KIND_WEATHER = "weather"

KINDS = (KIND_COUNTS, KIND_CAMERA, KIND_INCIDENT, KIND_WEATHER)

INCIDENT_TOPIC = f"{PREFIX}/incident"
WEATHER_TOPIC = f"{PREFIX}/weather"

#: Allowlist for anything interpolated into a topic level. Deliberately
#: excludes `/`, `+`, `#`, whitespace and every control character.
SEGMENT_PATTERN = re.compile(r"\A[A-Za-z0-9_.\-]{1,64}\Z")

#: What a client that wants everything this system emits should subscribe to.
#: Scoped to PREFIX rather than `#` so a shared broker stays possible.
ALL_TOPICS = f"{PREFIX}/#"

#: Narrower subscriptions, when a consumer only wants one feed.
SUBSCRIPTION_FOR_KIND = {
    KIND_COUNTS: f"{PREFIX}/sensor/+/+/counts",
    KIND_CAMERA: f"{PREFIX}/sensor/+/camera",
    KIND_INCIDENT: INCIDENT_TOPIC,
    KIND_WEATHER: WEATHER_TOPIC,
}


@dataclass(frozen=True)
class ParsedTopic:
    """What a topic string resolves to. `junction_id`/`lane_id` are None
    on the flat topics, which carry their location in the body instead."""

    kind: str
    junction_id: str | None = None
    lane_id: str | None = None


def validate_segment(value: object, field: str) -> str:
    """Allowlist one topic level. Raises ValueError, never sanitises.

    Sanitising (stripping the bad characters and carrying on) would turn a
    forged id into a *plausible* one and publish it anyway. Refusing keeps
    the failure loud and local to the caller that built it.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    if not SEGMENT_PATTERN.match(value):
        raise ValueError(
            f"{field}={value!r} is not a legal topic level - "
            f"must match {SEGMENT_PATTERN.pattern} (no '/', '+', '#', or whitespace)"
        )
    return value


def sensor_counts_topic(junction_id: str, lane_id: str) -> str:
    """`psychoflow/sensor/<junction>/<lane>/counts` (§7.1)."""
    junction_id = validate_segment(junction_id, "junction_id")
    lane_id = validate_segment(lane_id, "lane_id")
    return f"{PREFIX}/sensor/{junction_id}/{lane_id}/counts"


def camera_topic(junction_id: str) -> str:
    """`psychoflow/sensor/<junction>/camera` (§7.2)."""
    junction_id = validate_segment(junction_id, "junction_id")
    return f"{PREFIX}/sensor/{junction_id}/camera"


def parse_topic(topic: object) -> ParsedTopic | None:
    """Resolve a topic string, or None if it is not one of the four.

    Every level is re-validated on the way in: a topic arriving off the
    wire was built by somebody else, possibly not by `sensor_counts_topic`.
    """
    if not isinstance(topic, str):
        return None

    parts = topic.split("/")
    if not parts or parts[0] != PREFIX:
        return None

    try:
        if len(parts) == 2 and parts[1] == KIND_INCIDENT:
            return ParsedTopic(kind=KIND_INCIDENT)
        if len(parts) == 2 and parts[1] == KIND_WEATHER:
            return ParsedTopic(kind=KIND_WEATHER)
        if len(parts) == 4 and parts[1] == "sensor" and parts[3] == KIND_CAMERA:
            return ParsedTopic(
                kind=KIND_CAMERA, junction_id=validate_segment(parts[2], "junction_id")
            )
        if len(parts) == 5 and parts[1] == "sensor" and parts[4] == KIND_COUNTS:
            return ParsedTopic(
                kind=KIND_COUNTS,
                junction_id=validate_segment(parts[2], "junction_id"),
                lane_id=validate_segment(parts[3], "lane_id"),
            )
    except ValueError:
        # A structurally-correct topic carrying an illegal level (e.g. a
        # literal '+' that slipped through a broker) is not routable.
        return None

    return None
