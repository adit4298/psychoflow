"""§14 voice text scanning — word tables, small parsers, model-output extraction.

Split out of `intents.py` so that module stays inside the 800-line
maintainability ceiling and so the two concerns stay separable: everything here
turns TEXT into a value, and nothing here knows what a control function is.
Pure and side-effect free — no network, no model, no SUMO.

The parsers are all ANCHORED rather than free-scanning. "give lane 3 more
priority for the next five minutes" contains three numbers, and a scanner that
takes the first one it sees gets the lane wrong roughly as often as it gets it
right.
"""

from __future__ import annotations

import json
import re

#: Compass approaches, matching the twin's geometric `approach` tag
#: (`env/obs_action_spec.APPROACH_ORDER`).
APPROACHES = ("north", "east", "south", "west")

# ---------------------------------------------------------------------------
# Word tables
# ---------------------------------------------------------------------------
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "ninety": 90,
    # Homophones Web Speech genuinely returns for spoken digits.
    "to": 2, "too": 2, "for": 4, "won": 1, "ate": 8,
}
#: Homophones are accepted ONLY where a number is already syntactically
#: required ("lane to" -> lane 2). They are excluded from free scanning, where
#: "for the next five minutes" would otherwise read as the number 4.
_HOMOPHONES = frozenset({"to", "too", "for", "won", "ate"})

_ORDINALS = {"first": 1, "second": 2, "third": 3, "middle": 2, "centre": 2,
             "center": 2, "last": 3}

#: §14 writes `weight=high`, so a qualitative-word -> numeric table is required
#: by the spec. Values sit inside `control_api.LANE_BIAS_WEIGHT_RANGE`
#: (0.1-10.0); the bound itself is NOT duplicated here — control_api stays the
#: single source of truth for ranges and rejects anything outside them.
#: ORDER MATTERS: negatives are tested first, so "less priority" does not match
#: on the bare word "priority".
_WEIGHT_RULES: tuple[tuple[str, float], ...] = (
    (r"\bdeprioriti[sz]e\w*\b", 0.5),
    (r"\b(?:less|lower|reduce|reduced|decrease|down)\b", 0.5),
    (r"\b(?:lowest|minimum|min)\b", 0.1),
    (r"\blow\b", 0.5),
    (r"\b(?:highest|maximum|max)\b", 10.0),
    (r"\b(?:more|higher|boost|increase|raise|prefer|favou?r)\b", 3.0),
    (r"\b(?:high|priority|prioriti[sz]e\w*)\b", 3.0),
    (r"\b(?:normal|default|medium|neutral)\b", 1.0),
)

_MODE_WORDS = {"manual": "manual", "auto": "auto", "automatic": "auto",
               "automated": "auto", "autonomous": "auto", "rl": "auto",
               "self": "auto"}

_BASELINE_WORDS = {"greedy": "greedy", "psychoflow": "psychoflow",
                   "psycho flow": "psychoflow", "baseline": "greedy"}

_INCIDENT_WORDS = {"accident": "accident", "crash": "accident",
                   "collision": "accident", "roadworks": "roadworks",
                   "road works": "roadworks", "construction": "roadworks",
                   "blocked": "lane_blocked", "blockage": "lane_blocked",
                   "lane blocked": "lane_blocked", "breakdown": "lane_blocked",
                   "stalled": "lane_blocked"}

#: Argument key aliases the model has been observed to (or may plausibly) emit
#: in place of the control API's own parameter names.
_LANE_KEYS = ("lane_id", "lane", "lane_number", "lane_index", "lane_no",
              "laneId", "laneid")
_JUNCTION_KEYS = ("junction_id", "junction", "intersection", "junctionId",
                  "node")
_MODE_KEYS = ("mode", "value", "state", "target")
_BASELINE_KEYS = ("baseline", "baseline_mode", "controller", "mode", "value")
_WEIGHT_KEYS = ("weight", "priority", "bias", "multiplier", "level")
_PHASE_KEYS = ("phase", "phase_index", "phase_id", "signal_phase")
_TOPOLOGY_KEYS = ("topology_id", "topology", "lane_counts", "combo", "value")

_DURATION_SECOND_KEYS = ("duration_s", "duration_seconds", "duration_sec",
                         "seconds", "estimated_duration_s")
_DURATION_MINUTE_KEYS = ("duration_m", "duration_min", "duration_minutes",
                         "minutes")
_DURATION_AMBIGUOUS_KEYS = ("duration", "for", "time", "length")


# ---------------------------------------------------------------------------
# Small parsers
# ---------------------------------------------------------------------------
def lane_slot_of(lane_id) -> int | None:
    """Trailing 0-based index of a SUMO lane id: 'N1_J1_0' -> 0.

    Same rule as `env.obs_action_spec._lane_index`, reimplemented rather than
    imported so this module pulls in no numpy/SUMO dependency (the constraint
    `backend/control_api.py`'s docstring places on the whole voice path).
    """
    if not isinstance(lane_id, str) or "_" not in lane_id:
        return None
    try:
        return int(lane_id.rsplit("_", 1)[1])
    except ValueError:
        return None


def parse_number(token, *, allow_homophones: bool = False) -> int | None:
    """'3' / 3 / 'three' -> 3. Returns None for anything else."""
    if isinstance(token, bool):
        return None
    if isinstance(token, (int, float)):
        return int(token) if float(token).is_integer() else None
    if not isinstance(token, str):
        return None
    text = token.strip().lower()
    if text.isdigit():
        return int(text)
    if text in _ORDINALS:
        return _ORDINALS[text]
    if text in _NUMBER_WORDS:
        if text in _HOMOPHONES and not allow_homophones:
            return None
        return _NUMBER_WORDS[text]
    return None


_NUM_TOKEN = r"(\d+|[a-z]+)"


def _search_number(text: str, prefix: str) -> int | None:
    """Find the number that follows `prefix` ('lane', 'phase', ...)."""
    m = re.search(rf"\b{prefix}\b\s*(?:number|no\.?|#)?\s*{_NUM_TOKEN}",
                  text, re.IGNORECASE)
    if not m:
        return None
    return parse_number(m.group(1), allow_homophones=True)


def find_lane_number(text: str) -> int | None:
    """Spoken (1-based) lane number, anchored to the word 'lane'.

    Anchored on purpose: a free scan of 'give lane 3 more priority for the next
    five minutes' would find 5 as readily as 3.
    """
    return _search_number(text or "", "lanes?")


def find_phase_number(text: str) -> int | None:
    return _search_number(text or "", "phases?")


def find_junction(text: str) -> str | None:
    """'J2' / 'junction two' / 'second junction' / 'middle junction' -> 'J2'."""
    text = (text or "")
    m = re.search(r"\bj\s*-?\s*([123])\b", text, re.IGNORECASE)
    if m:
        return f"J{m.group(1)}"
    m = re.search(rf"\bjunctions?\b\s*{_NUM_TOKEN}", text, re.IGNORECASE)
    if m:
        n = parse_number(m.group(1), allow_homophones=True)
        if n in (1, 2, 3):
            return f"J{n}"
    m = re.search(rf"{_NUM_TOKEN}\s+\b(?:junction|intersection)\b",
                  text, re.IGNORECASE)
    if m:
        n = parse_number(m.group(1), allow_homophones=True)
        if n in (1, 2, 3):
            return f"J{n}"
    return None


def find_approach(text: str) -> str | None:
    for approach in APPROACHES:
        if re.search(rf"\b{approach}(?:bound|ern)?\b", text or "", re.IGNORECASE):
            return approach
    return None


def find_duration_s(text: str) -> float | None:
    """Duration in SECONDS, with the unit taken from the operator's own words.

    'for the next five minutes' -> 300.0; 'for 30 seconds' -> 30.0.
    """
    m = re.search(
        rf"{_NUM_TOKEN}\s*(seconds?|secs?|s|minutes?|mins?|m)\b",
        text or "", re.IGNORECASE)
    if not m:
        return None
    value = parse_number(m.group(1), allow_homophones=False)
    if value is None:
        return None
    unit = m.group(2).lower()
    return float(value) * (60.0 if unit.startswith("m") else 1.0)


def find_duration_unit(text: str) -> str | None:
    """'minutes' -> 'm', 'seconds' -> 's', nothing said -> None."""
    m = re.search(r"\b(seconds?|secs?|minutes?|mins?)\b", text or "",
                  re.IGNORECASE)
    if not m:
        return None
    return "m" if m.group(1).lower().startswith("m") else "s"


def find_weight(text: str) -> float | None:
    for pattern, value in _WEIGHT_RULES:
        if re.search(pattern, text or "", re.IGNORECASE):
            return value
    return None


_WEIGHT_NUMBER_RE = re.compile(
    r"\b(?:weight|bias|multiplier|priority)\b\s*(?:of|to|at|=)?\s*"
    r"(\d+(?:\.\d+)?)", re.IGNORECASE)


def find_weight_number(text: str) -> float | None:
    """An explicitly spoken numeric weight: 'set lane 2 weight to 2.5' -> 2.5.

    Anchored to a weight word so it cannot pick up the lane number or the
    duration — which is exactly the mistake the model makes (see
    `normalise_call`'s set_lane_bias branch).
    """
    m = _WEIGHT_NUMBER_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _match_word_table(text: str, table: dict[str, str]) -> str | None:
    """Longest-key-first scan so 'psycho flow' beats a stray 'flow'."""
    lowered = (text or "").lower()
    for key in sorted(table, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", lowered):
            return table[key]
    return None


# ---------------------------------------------------------------------------
# Model-output extraction
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*|\s*```\s*$")


def _reject_constant(name: str):
    """Make `Infinity` / `-Infinity` / `NaN` in a model reply UNPARSEABLE.

    Python's `json.loads` accepts these three bare tokens as a non-standard
    extension, so a reply of `{"topology_id": [4, 3, Infinity]}` parses cleanly
    into a real `float('inf')` — and a non-finite number then flows into
    argument normalisation as though the model had named a value. Measured
    consequence before this guard: `int(float('inf'))` raises `OverflowError`,
    which is a sibling of `ValueError` rather than a subclass and so slipped
    through `control_api._parse_topology`'s `except (TypeError, ValueError)`
    AND `dispatch()`'s own `except TypeError`, crashing a pipeline whose entire
    safety argument rests on it never raising. (Found by security review,
    2026-09-04; nothing was queued — the crash landed inside `set_topology`'s
    own validation — so it was availability, not an allowlist bypass.)

    Rejecting at the JSON boundary fixes it for EVERY function at once, rather
    than per-normaliser. There is no legitimate traffic command containing an
    infinity, so refusing the whole reply is the right blast radius: it becomes
    a fail-closed "didn't catch a command" like any other bad reply.
    """
    raise ValueError(f"non-finite JSON constant {name!r} in the model reply")


def extract_json_object(raw) -> dict | None:
    """Pull the first balanced JSON object out of a model reply. None if absent.

    `gemma3:4b` was measured returning 2 of 4 replies wrapped in markdown code
    fences (BUILD_LOG 2026-09-03 §6, finding 2), and models routinely prepend
    a sentence of prose. Fences are stripped, then the first `{` is scanned to
    its matching `}` with string/escape awareness so a brace inside a quoted
    value does not end the object early.
    """
    if not isinstance(raw, str):
        return None
    text = _FENCE_RE.sub("", raw.strip())
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1],
                                     parse_constant=_reject_constant)
                    obj = json.loads(text[start:i + 1])
                except (ValueError, TypeError):
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def _first_key(args: dict, keys) -> tuple[str | None, object]:
    for key in keys:
        if key in args and args[key] is not None:
            return key, args[key]
    return None, None


