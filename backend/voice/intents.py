"""§14 intent normalisation — the deterministic half of the voice layer.

`intent_agent.py` owns the model call. THIS module owns everything that must
be predictable: pulling a JSON object out of whatever the model returned,
resolving a spoken lane number to a real SUMO lane id, and turning the model's
loosely-shaped arguments into the exact keyword arguments
`control_api.dispatch()` expects. No network, no model, no SUMO — so it is
unit-testable offline and its behaviour does not drift with the weights.

THE TWO NUMBERING DECISIONS (CLAUDE.md's APPROVED VOICE DESIGN item 3 asked
for these to be reconciled EXPLICITLY rather than assumed to match)
-------------------------------------------------------------------------
`explainability/narrator.py` renders `{lane}` as the RAW 0-BASED SUMO lane
index — `entry.lane_slot`, the trailing integer of `N1_J1_0`, with no `+1`. So
the narration says "Lane 0" for the first lane. §14's own demo command is
"give lane 3 more priority", spoken by a human.

**DECISION: voice "lane N" is 1-BASED. It resolves to 0-based SUMO slot N-1.**
(`VOICE_LANE_BASE = 1`.) Three reasons, in order of weight:

  1. Nobody says "lane zero" out loud. The voice channel is the one surface in
     this system whose input is a spoken human sentence, and matching human
     counting there costs nothing.
  2. It makes §14's own required demo command WORK on the demo corridor. J2 on
     the (4,3,2) corridor has 3 lanes per approach — slots 0, 1, 2. Under a
     0-based reading "lane 3" is out of range and the required command fails;
     under 1-based it is slot 2 and resolves.
  3. The alternative — changing the narrator to render `lane_slot + 1` — would
     move numbers already recorded in Phase 8's verified figures, and
     `explainability/` is not this module's to change.

**CONSEQUENCE, stated rather than hidden:** the voice echo and the decision-log
narration will disagree by one for the same lane. "Lane 3" spoken is "Lane 2"
narrated. Every result this module produces therefore carries BOTH — the
resolved `lane_id` (unambiguous) and an assumption string naming the
conversion — so a panel can show the real lane id and never has to guess.
`NOTES-FOR-INTEGRATION.md` records the display recommendation for Phase 10.

**Phase indices follow the same rule** (`VOICE_PHASE_BASE = 1`): spoken
"phase 2" is `force_phase(phase=1)`. `control_api.force_phase` takes a 0-based
green-slot index in `[0, 3)`, so spoken phases are 1, 2, 3.

WHAT COUNTS AS "GUESSING" — the line this module holds
------------------------------------------------------
§14: on an unparseable intent, "do not guess, do not apply a random action".
That rule is about INVENTING content. This module never invents a function
name and never invents a value the operator did not supply. What it DOES do is
deterministic, disclosed resolution of things the operator's own words already
contain:

  * unit disambiguation — "five minutes" is 300 seconds, not 5 (a live
    finding: gemma3:4b returns `{"duration": 5}` for that utterance, and a
    literal 5 is REJECTED by `control_api`'s [10, 900] bound);
  * word-to-number mapping for weights — §14's own spec writes
    `set_lane_bias(lane=3, weight=high, ...)`, so a `high -> 3.0` table is
    required by the spec, not invented by it;
  * a spoken lane number to a lane id, using the junction/approach the
    operator named.

Where the operator named NEITHER a junction NOR an approach, resolution falls
back to `default_junction` / `default_approach`. That fallback is a documented,
deterministic, DISCLOSED default (it appears in `assumptions` on every result),
and it can be switched off entirely with `strict=True`, which turns an
ambiguous lane into a fail-closed no-op. See `LaneResolver`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.control_api import (
    CONTROL_FUNCTIONS,
    INCIDENT_DURATION_RANGE_S,
    LANE_BIAS_DURATION_RANGE_S,
    LANE_BIAS_WEIGHT_RANGE,
    VALID_BASELINES,
    VALID_MODES,
)
from backend.control_api import CONTROL_FUNCTIONS, VALID_BASELINES, VALID_MODES
from backend.voice._parsing import (  # noqa: F401  (re-exported)
    APPROACHES,
    _BASELINE_KEYS,
    _BASELINE_WORDS,
    _DURATION_AMBIGUOUS_KEYS,
    _DURATION_MINUTE_KEYS,
    _DURATION_SECOND_KEYS,
    _INCIDENT_WORDS,
    _JUNCTION_KEYS,
    _LANE_KEYS,
    _MODE_KEYS,
    _MODE_WORDS,
    _PHASE_KEYS,
    _TOPOLOGY_KEYS,
    _WEIGHT_KEYS,
    _first_key,
    _match_word_table,
    extract_json_object,
    find_approach,
    find_duration_s,
    find_duration_unit,
    find_junction,
    find_lane_number,
    find_phase_number,
    find_weight,
    find_weight_number,
    lane_slot_of,
    parse_number,
)

from perception.incident_intake import INCIDENT_TYPES, SEVERITIES

# ---------------------------------------------------------------------------
# Numbering conventions (see module docstring)
# ---------------------------------------------------------------------------
VOICE_LANE_BASE = 1
VOICE_PHASE_BASE = 1

#: §0.1's locked corridor. Literal for the same reason `control_api` keeps it
#: literal — this module must stay importable with no SUMO dependency.
JUNCTIONS = ("J1", "J2", "J3")

#: Demo defaults for a lane the operator did not fully qualify. J2 is the
#: middle junction and the one `sim/run_demo_gui.py` points its camera at, so
#: it is what an operator watching the screen means by an unqualified "lane 3".
DEFAULT_JUNCTION = "J2"
DEFAULT_APPROACH = "north"


# ---------------------------------------------------------------------------
# Lane resolution
# ---------------------------------------------------------------------------
@dataclass
class LaneResolver:
    """Spoken lane number -> a real lane id from the sim's published lane set.

    `lanes` is `control_api.ControlState.snapshot_stats()["lanes"]` — the same
    dict `set_lane_bias` / `trigger_emergency` / `inject_incident` validate
    against, so a lane this resolver returns is one those functions accept.

    FAIL CLOSED when the sim has published nothing yet: with no lane set there
    is no way to confirm a lane exists, and inventing one is exactly the
    "guess an argument" §14 forbids.

    `strict=True` removes the default junction/approach fallback entirely: an
    under-specified lane then becomes a fail-closed no-op with a message naming
    what the operator must add. `strict=False` (the default) applies
    `default_junction` / `default_approach` and DISCLOSES each fallback it used
    in the returned assumptions list.
    """

    lanes: dict
    default_junction: str | None = DEFAULT_JUNCTION
    default_approach: str | None = DEFAULT_APPROACH
    strict: bool = False

    def _meta(self, lane_id: str) -> dict:
        meta = self.lanes.get(lane_id)
        return meta if isinstance(meta, dict) else {}

    def resolve(
        self,
        *,
        spoken: object = None,
        lane_id: object = None,
        junction: str | None = None,
        approach: str | None = None,
    ) -> tuple[str | None, list[str], str | None]:
        """-> (lane_id, assumptions, error). Exactly one of lane_id / error is set."""
        assumptions: list[str] = []
        if not self.lanes:
            return None, assumptions, ("the simulation has not published a lane "
                                       "set yet — try again in a moment")

        # An explicit, real lane id wins outright — nothing to resolve.
        if isinstance(lane_id, str) and lane_id in self.lanes:
            return lane_id, assumptions, None
        if isinstance(lane_id, str) and lane_id and lane_slot_of(lane_id) is not None:
            return None, assumptions, (
                f"unknown lane id {lane_id!r} (not in the current network)")

        number = parse_number(spoken, allow_homophones=True)
        if number is None:
            number = parse_number(lane_id, allow_homophones=True)
        if number is None:
            return None, assumptions, "no lane number in the command"
        slot = number - VOICE_LANE_BASE
        if slot < 0:
            return None, assumptions, (
                f"lane numbering starts at {VOICE_LANE_BASE}, got {number}")

        candidates = [lid for lid in self.lanes if lane_slot_of(lid) == slot]
        if not candidates:
            return None, assumptions, (
                f"there is no lane {number} in the current network")

        if junction is None and not self.strict and self.default_junction:
            junction = self.default_junction
            assumptions.append(f"junction not stated — assumed {junction}")
        if junction is not None:
            narrowed = [l for l in candidates
                        if self._meta(l).get("junction_id") == junction]
            if not narrowed:
                return None, assumptions, (
                    f"there is no lane {number} at junction {junction}")
            candidates = narrowed

        if approach is None and not self.strict and self.default_approach:
            approach = self.default_approach
            assumptions.append(f"approach not stated — assumed {approach}")
        if approach is not None:
            narrowed = [l for l in candidates
                        if self._meta(l).get("approach") == approach]
            if not narrowed:
                return None, assumptions, (
                    f"there is no {approach} lane {number}"
                    + (f" at {junction}" if junction else ""))
            candidates = narrowed

        if len(candidates) > 1:
            options = ", ".join(sorted(candidates)[:6])
            return None, assumptions, (
                f"lane {number} is ambiguous — say a junction and an approach "
                f"(candidates: {options})")

        resolved = candidates[0]
        assumptions.append(
            f"spoken lane {number} (1-based) -> SUMO slot {slot} -> {resolved}")
        return resolved, assumptions, None


# ---------------------------------------------------------------------------
# Argument normalisation
# ---------------------------------------------------------------------------
@dataclass
class NormalisedCall:
    """The outcome of turning a model's call into control-API kwargs."""

    function: str | None = None
    args: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.function is not None


def _fail(reason: str, assumptions: list[str] | None = None) -> NormalisedCall:
    return NormalisedCall(error=reason, assumptions=list(assumptions or []))


#: Marks a failure that is a BOUNDS problem rather than a hearing problem, so
#: the caller can show the operator the bound instead of "didn't catch a
#: command". They were perfectly clear; they asked for something not allowed,
#: and telling them otherwise sends them back to re-speaking a fine sentence.
RANGE_ERROR_MARKER = "outside the allowed range"


def _out_of_range(name: str, value, bounds: tuple[float, float],
                  unit: str = "") -> str | None:
    """`None` if `value` is inside `bounds`, else the operator-facing reason.

    `bounds` is always a constant imported from `control_api` — never a literal
    typed here — so the two layers cannot disagree about what is legal.
    """
    lo, hi = bounds
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return f"{name} must be a number, got {value!r}"
    if not (lo <= float(value) <= hi):
        return (f"{name} {value:g}{unit} is {RANGE_ERROR_MARKER} "
                f"{lo:g}{unit}-{hi:g}{unit}")
    return None


def _resolve_duration_s(args: dict, transcript: str,
                        assumptions: list[str]) -> tuple[float | None, str | None]:
    """Model duration argument -> seconds. (value, error).

    Explicit-unit keys are taken at face value. A bare `duration` is
    disambiguated against the OPERATOR'S OWN WORDS, never against the number's
    magnitude — a magnitude heuristic ("small numbers must be minutes") is the
    kind of guess §14 forbids, and it would silently turn a deliberate
    `duration: 60` into an hour.
    """
    key, raw = _first_key(args, _DURATION_SECOND_KEYS)
    if key is not None:
        try:
            return float(raw), None
        except (TypeError, ValueError):
            return None, f"{key} must be numeric, got {raw!r}"

    key, raw = _first_key(args, _DURATION_MINUTE_KEYS)
    if key is not None:
        try:
            value = float(raw) * 60.0
        except (TypeError, ValueError):
            return None, f"{key} must be numeric, got {raw!r}"
        assumptions.append(f"{key}={raw} read as minutes -> {value:g}s")
        return value, None

    key, raw = _first_key(args, _DURATION_AMBIGUOUS_KEYS)
    if key is not None:
        number = parse_number(raw)
        if number is None:
            try:
                number = float(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                number = None
        if number is not None:
            unit = find_duration_unit(transcript)
            if unit == "m":
                value = float(number) * 60.0
                assumptions.append(
                    f"{key}={raw} + \"minutes\" in the command -> {value:g}s")
                return value, None
            value = float(number)
            if unit is None:
                assumptions.append(
                    f"{key}={raw} with no unit spoken — read as seconds")
            return value, None

    spoken = find_duration_s(transcript)
    if spoken is not None:
        assumptions.append(f"duration taken from the command -> {spoken:g}s")
        return spoken, None
    return None, None


def _resolve_lane_arg(args: dict, transcript: str, resolver: LaneResolver,
                      assumptions: list[str], *, value=None
                      ) -> tuple[str | None, str | None]:
    junction = (_first_key(args, _JUNCTION_KEYS)[1]
                if _first_key(args, _JUNCTION_KEYS)[0] else None)
    junction = junction if junction in JUNCTIONS else find_junction(transcript)
    approach = args.get("approach") or args.get("direction")
    approach = (str(approach).lower() if isinstance(approach, str) else None)
    if approach not in APPROACHES:
        approach = find_approach(transcript)

    if value is None:
        _key, value = _first_key(args, _LANE_KEYS)
    if value is None:
        value = find_lane_number(transcript)

    lane_id, notes, error = resolver.resolve(
        spoken=value,
        lane_id=value if isinstance(value, str) else None,
        junction=junction,
        approach=approach,
    )
    assumptions.extend(notes)
    return lane_id, error


def _junction_from(args: dict, transcript: str) -> str | None:
    """Junction named in the args, else in the operator's words, else None."""
    _k, raw = _first_key(args, _JUNCTION_KEYS)
    junction = str(raw).upper().replace(" ", "") if raw is not None else None
    if junction in JUNCTIONS:
        return junction
    return find_junction(str(raw)) or find_junction(transcript)


def _n_get_stats(args, transcript, resolver, notes) -> NormalisedCall:
    return NormalisedCall("get_stats", {}, notes)


def _n_set_mode(args, transcript, resolver, notes) -> NormalisedCall:
    _k, raw = _first_key(args, _MODE_KEYS)
    mode = _match_word_table(str(raw), _MODE_WORDS) if raw is not None else None
    if mode is None:
        mode = _match_word_table(transcript, _MODE_WORDS)
        if mode is not None:
            notes.append(f"mode taken from the command -> {mode}")
    if mode not in VALID_MODES:
        return _fail(f"mode must be one of {VALID_MODES}", notes)
    return NormalisedCall("set_mode", {"mode": mode}, notes)


def _n_set_baseline_mode(args, transcript, resolver, notes) -> NormalisedCall:
    _k, raw = _first_key(args, _BASELINE_KEYS)
    baseline = (_match_word_table(str(raw), _BASELINE_WORDS)
                if raw is not None else None)
    if baseline is None:
        baseline = _match_word_table(transcript, _BASELINE_WORDS)
        if baseline is not None:
            notes.append(f"baseline taken from the command -> {baseline}")
    if baseline not in VALID_BASELINES:
        return _fail(f"baseline must be one of {VALID_BASELINES}", notes)
    return NormalisedCall("set_baseline_mode", {"baseline": baseline}, notes)


def _n_trigger_emergency(args, transcript, resolver, notes) -> NormalisedCall:
    lane_id, error = _resolve_lane_arg(args, transcript, resolver, notes)
    if error:
        return _fail(error, notes)
    return NormalisedCall("trigger_emergency", {"lane_id": lane_id}, notes)


def _resolve_weight(args: dict, transcript: str,
                    notes: list[str]) -> float | None:
    """Priority multiplier for `set_lane_bias`.

    THE ONE ARGUMENT WHERE THE TRANSCRIPT OUTRANKS THE MODEL, and it is a
    measured decision rather than a preference. gemma3:4b was observed
    returning `weight: 1.0` for "give lane 3 MORE priority" — a silent no-op
    reported to the operator as success — and `weight: 60` for "lower the
    priority on lane 1 for SIXTY seconds", having copied the duration into the
    wrong field. Both parse cleanly and both do the wrong thing. The
    operator's own qualifier is the reliable signal, so the order is:

      1. a number the operator actually spoke, anchored to a weight word;
      2. the operator's qualitative word, via §14's own high/low table;
      3. only then the model's own value.
    """
    weight = find_weight_number(transcript)
    if weight is not None:
        notes.append(f"weight spoken explicitly -> {weight:g}")
        return weight
    weight = find_weight(transcript)
    if weight is not None:
        notes.append(f"weight taken from the command -> {weight:g}")
        return weight
    _k, raw_weight = _first_key(args, _WEIGHT_KEYS)
    if raw_weight is None:
        return None
    if isinstance(raw_weight, (int, float)) and not isinstance(raw_weight, bool):
        weight = float(raw_weight)
    else:
        weight = find_weight(str(raw_weight))
    if weight is not None:
        notes.append(f"weight {raw_weight!r} from the model -> {weight:g}")
    return weight


def _n_set_lane_bias(args, transcript, resolver, notes) -> NormalisedCall:
    lane_id, error = _resolve_lane_arg(args, transcript, resolver, notes)
    if error:
        return _fail(error, notes)
    weight = _resolve_weight(args, transcript, notes)
    if weight is None:
        return _fail("no priority level in the command "
                     "(say 'more priority' or a number)", notes)
    duration_s, error = _resolve_duration_s(args, transcript, notes)
    if error:
        return _fail(error, notes)
    if duration_s is None:
        return _fail("no duration in the command "
                     "(say 'for the next five minutes')", notes)
    # RANGE-CHECKED HERE TOO, against control_api's OWN constants — imported,
    # never retyped, so there is still one source of truth for each bound.
    # `control_api` would reject these anyway; catching them here is what makes
    # an impossible value a fail-closed "didn't catch that" rather than a
    # confidently-parsed command the dashboard then declines, which reads to an
    # operator as the system being broken rather than as them misspeaking.
    err = _out_of_range("weight", weight, LANE_BIAS_WEIGHT_RANGE)
    if err:
        return _fail(err, notes)
    err = _out_of_range("duration", duration_s, LANE_BIAS_DURATION_RANGE_S,
                        unit="s")
    if err:
        return _fail(err, notes)
    return NormalisedCall("set_lane_bias", {
        "lane_id": lane_id, "weight": weight, "duration_s": duration_s,
    }, notes)


#: Spoken axis -> green-slot index, for "hold north-south green at J2".
#:
#: **THIS IS AN ASSUMPTION AND IT IS DISCLOSED ON EVERY RESULT THAT USES IT.**
#: The authoritative map is `PsychoFlowEnv.phase_served_lanes()`, which is
#: per-episode, lives on the sim thread, and is NOT published on
#: `snapshot_stats()` — so this module genuinely cannot read it. The values
#: below follow netconvert's convention for this corridor (the through street
#: W->E takes the first green, the cross street the next) and match
#: `frontend/src/assistant/intent.ts`'s `AXIS_PHASE`, so the panel and the
#: voice channel at least agree with each other.
#:
#: It is bounded rather than trusted: `control_api.force_phase` range-checks
#: the index, the sim thread mask-checks it against the live topology and
#: silently drops an invalid pin, and §10 still validates the result. The worst
#: case of a wrong entry here is the other axis going green — visible on screen
#: within one decision step, and undoable with "clear the override on J2".
#:
#: TO VERIFY IT PROPERLY (one line, but a `backend/sim_runner.py` edit and so
#: out of this part's scope): publish `phase_served_lanes()` on
#: `_stats_payload`, resolve the axis from the lanes each slot actually serves,
#: and delete this table.
AXIS_GREEN_SLOT = {"ew": 0, "ns": 1}

_AXIS_RE = (
    ("ns", re.compile(r"\bn\s*[-/–]?\s*s\b|\bnorth\s*[-/– ]?\s*south\b",
                      re.IGNORECASE)),
    ("ew", re.compile(r"\be\s*[-/–]?\s*w\b|\beast\s*[-/– ]?\s*west\b",
                      re.IGNORECASE)),
)


def find_axis(text: str) -> str | None:
    """'north-south' / 'N-S' -> 'ns'. None when no axis was named."""
    for axis, pattern in _AXIS_RE:
        if pattern.search(text or ""):
            return axis
    return None


def _n_force_phase(args, transcript, resolver, notes) -> NormalisedCall:
    junction = _junction_from(args, transcript)
    if junction not in JUNCTIONS:
        return _fail(f"junction must be one of {JUNCTIONS}", notes)

    # THE MODEL'S OWN `phase` ARGUMENT IS NOT TRUSTED — a measured decision of
    # the same class as `_resolve_weight`'s. On "hold north-south green at J2
    # for 20 seconds" gemma3:4b returns `{"phase": 20}`, having copied the
    # DURATION into the phase field; that parses cleanly and asks for phase
    # index 19. So the order is the operator's own words first:
    #   1. a phase number they actually spoke, anchored to the word "phase";
    #   2. the axis they named, via the disclosed AXIS_GREEN_SLOT table;
    #   3. nothing — fail closed, and say what to add.
    spoken = find_phase_number(transcript)
    if spoken is not None:
        phase = spoken - VOICE_PHASE_BASE
        notes.append(f"spoken phase {spoken} ({VOICE_PHASE_BASE}-based) "
                     f"-> phase index {phase}")
    else:
        axis = find_axis(transcript)
        if axis is None:
            return _fail("no phase in the command — say 'phase 2', or name an "
                         "axis like 'north-south green'", notes)
        phase = AXIS_GREEN_SLOT[axis]
        notes.append(f"'{axis}' -> green slot {phase} (assumed from the "
                     f"corridor's phase convention, not read from the live "
                     f"phase map — see AXIS_GREEN_SLOT)")

    if phase < 0:
        return _fail(f"phase numbering starts at {VOICE_PHASE_BASE}", notes)
    if find_duration_s(transcript) is not None:
        # Said rather than silently dropped: `force_phase` has no duration
        # argument, it pins until released, and an operator who said "for 20
        # seconds" will otherwise assume it expired on its own.
        notes.append("a pin holds until released — the spoken duration was "
                     f"not applied; say 'clear the override on {junction}' "
                     "to release it")
    _k, raw_phase = _first_key(args, _PHASE_KEYS)
    spoken = parse_number(raw_phase, allow_homophones=True)
    if spoken is None:
        spoken = find_phase_number(transcript)
    if spoken is None:
        return _fail("no phase number in the command", notes)
    phase = spoken - VOICE_PHASE_BASE
    notes.append(f"spoken phase {spoken} ({VOICE_PHASE_BASE}-based) "
                 f"-> phase index {phase}")
    return NormalisedCall("force_phase",
                          {"junction_id": junction, "phase": phase}, notes)


def _n_clear_override(args, transcript, resolver, notes) -> NormalisedCall:
    junction = _junction_from(args, transcript)
    if junction not in JUNCTIONS:
        junction = None
        notes.append("no junction stated — clearing every pinned junction")
    return NormalisedCall("clear_override", {"junction_id": junction}, notes)


def _n_set_topology(args, transcript, resolver, notes) -> NormalisedCall:
    _k, raw_topology = _first_key(args, _TOPOLOGY_KEYS)
    if raw_topology is None:
        digits = re.findall(r"\b([234])\b", transcript or "")
        if len(digits) == 3:
            raw_topology = "".join(digits)
            notes.append(f"topology taken from the command -> {raw_topology}")
    if raw_topology is None:
        return _fail("no topology in the command (say '4 3 2')", notes)
    # DEFENCE IN DEPTH, added after security review (2026-09-04). This was the
    # ONE normaliser that passed the model's raw argument through untouched,
    # and `control_api._parse_topology` accepts a list — so a reply of
    # `[4, 3, Infinity]` reached `int(inf)` and raised `OverflowError` out of a
    # pipeline documented as never raising. `_reject_constant` in `_parsing.py`
    # now stops a non-finite value entering at all; this is the second layer,
    # and it also brings this normaliser in line with every other one, each of
    # which builds a checked argument rather than forwarding a model value.
    if isinstance(raw_topology, (list, tuple)):
        digits = [d for d in raw_topology
                  if isinstance(d, int) and not isinstance(d, bool)]
        if len(digits) != len(raw_topology):
            return _fail("topology must be three whole numbers, e.g. '4 3 2'",
                         notes)
        raw_topology = "".join(str(d) for d in digits)
    if not isinstance(raw_topology, (str, int)) or isinstance(raw_topology, bool):
        return _fail("topology must be three whole numbers, e.g. '4 3 2'", notes)
    return NormalisedCall("set_topology", {"topology_id": str(raw_topology)},
                          notes)
    return NormalisedCall("set_topology", {"topology_id": raw_topology}, notes)


def _n_inject_incident(args, transcript, resolver, notes) -> NormalisedCall:
    junction = _junction_from(args, transcript)
    raw_lanes = args.get("affected_lanes")
    if not isinstance(raw_lanes, (list, tuple)) or not raw_lanes:
        single = _first_key(args, _LANE_KEYS)[1]
        raw_lanes = [single] if single is not None else [None]
    resolved: list[str] = []
    for item in raw_lanes:
        lane_id, error = _resolve_lane_arg(args, transcript, resolver, notes,
                                           value=item)
        if error:
            return _fail(error, notes)
        if lane_id not in resolved:
            resolved.append(lane_id)
    if junction not in JUNCTIONS:
        # Take it from the resolved lane rather than fail — the lane is real
        # and its junction is a fact of the published lane set, not an
        # assumption about what the operator meant.
        junction = resolver.lanes.get(resolved[0], {}).get("junction_id")
        if junction in JUNCTIONS:
            notes.append(f"junction taken from lane {resolved[0]} -> {junction}")
    if junction not in JUNCTIONS:
        return _fail(f"junction must be one of {JUNCTIONS}", notes)

    incident_type = args.get("incident_type") or args.get("type")
    if incident_type not in INCIDENT_TYPES:
        incident_type = _match_word_table(transcript, _INCIDENT_WORDS)
    if incident_type not in INCIDENT_TYPES:
        return _fail(f"incident type must be one of {INCIDENT_TYPES}", notes)

    severity = args.get("severity")
    if severity not in SEVERITIES:
        severity = next(
            (s for s in SEVERITIES
             if re.search(rf"\b{s}\b", transcript or "", re.IGNORECASE)), None)
    if severity not in SEVERITIES:
        severity = "high"
        notes.append("severity not stated — assumed high")

    out = {"junction_id": junction, "affected_lanes": resolved,
           "incident_type": incident_type, "severity": severity}
    duration_s, error = _resolve_duration_s(args, transcript, notes)
    if error:
        return _fail(error, notes)
    if duration_s is not None:
        err = _out_of_range("estimated duration", duration_s,
                            INCIDENT_DURATION_RANGE_S, unit="s")
        if err:
            return _fail(err, notes)
        out["estimated_duration_s"] = duration_s
    return NormalisedCall("inject_incident", out, notes)


#: One normaliser per allowlisted function. The assert is the same
#: anti-drift device `control_api._DISPATCH_TABLE` uses: a function added to
#: the allowlist without a normaliser fails at IMPORT, not at the first
#: spoken command during a demo.
_NORMALISERS = {
    "set_mode": _n_set_mode,
    "set_lane_bias": _n_set_lane_bias,
    "get_stats": _n_get_stats,
    "trigger_emergency": _n_trigger_emergency,
    "set_topology": _n_set_topology,
    "set_baseline_mode": _n_set_baseline_mode,
    "inject_incident": _n_inject_incident,
    "force_phase": _n_force_phase,
    "clear_override": _n_clear_override,
}
assert set(_NORMALISERS) == set(CONTROL_FUNCTIONS), (
    "voice: _NORMALISERS and control_api.CONTROL_FUNCTIONS have drifted — "
    f"missing {sorted(set(CONTROL_FUNCTIONS) - set(_NORMALISERS))}, "
    f"extra {sorted(set(_NORMALISERS) - set(CONTROL_FUNCTIONS))}"
)


# ---------------------------------------------------------------------------
# Operator-facing confirmation
# ---------------------------------------------------------------------------
# ONE builder, living here because `intents.py` is imported by both
# `intent_agent.py` and `bridge.py` and imports neither — so there is no cycle
# and, more to the point, no second copy. There were two (code review,
# 2026-09-04) and they had ALREADY drifted: one said "phase index 1" and the
# other "phase 1" for the same action, which is the exact 0-vs-1-based
# ambiguity CLAUDE.md's APPROVED VOICE DESIGN item 3 requires be reconciled
# explicitly rather than left to chance.
def _c_get_stats(args, outcome) -> str:
    if not outcome.get("ready"):
        return str(outcome.get("reason", "No statistics yet."))
    return (f"Mean max wait {outcome.get('mean_wait_max', '?')}s across "
            f"{len(outcome.get('lanes', {}))} lanes; "
            f"{outcome.get('starvation_events_total', 0)} starvation events; "
            f"throughput {outcome.get('throughput_total', 0)}.")


def _c_force_phase(args, outcome) -> str:
    # BOTH numbers, always. The officer said "phase 2" and the corridor acted
    # on index 1; showing only one of those makes the decision log look like it
    # disagrees with what they just asked for.
    index = args["phase"]
    return (f"{args['junction_id']} pinned to phase {index + VOICE_PHASE_BASE} "
            f"(index {index}); it applies at the next decision step, and §10 "
            f"still validates it.")


#: One confirmation per allowlisted function, asserted against the allowlist
#: for the same reason `_NORMALISERS` is: a function added to `control_api`
#: without a confirmation would otherwise fall through to a generic "applied"
#: string in front of an audience, and nothing would raise.
_CONFIRMATIONS = {
    "get_stats": _c_get_stats,
    "set_mode": lambda a, o: f"Mode set to {a['mode']}.",
    "set_baseline_mode": lambda a, o: f"Controller set to {a['baseline']}.",
    "set_lane_bias": lambda a, o: (
        f"Lane {a['lane_id']} weighted ×{a['weight']:g} for "
        f"{a['duration_s']:g}s."),
    "trigger_emergency": lambda a, o: (
        f"Emergency corridor requested for {a['lane_id']}."),
    "force_phase": _c_force_phase,
    "clear_override": lambda a, o: (
        f"Override cleared on {a.get('junction_id') or 'every junction'}."),
    "set_topology": lambda a, o: (
        f"Corridor rebuilding as {o.get('topology_id') or a['topology_id']}."),
    "inject_incident": lambda a, o: (
        f"{a['incident_type']} reported at {a['junction_id']} on "
        f"{', '.join(a['affected_lanes'])}."),
}
assert set(_CONFIRMATIONS) == set(CONTROL_FUNCTIONS), (
    "voice: _CONFIRMATIONS and control_api.CONTROL_FUNCTIONS have drifted — "
    f"missing {sorted(set(CONTROL_FUNCTIONS) - set(_CONFIRMATIONS))}, "
    f"extra {sorted(set(_CONFIRMATIONS) - set(CONTROL_FUNCTIONS))}"
)


def confirmation(function: str, args: dict, outcome: dict) -> str:
    """Short operator-facing echo. Names the RESOLVED lane, not the spoken
    number — the officer said "lane 3" and the corridor acted on `N1_J2_2`, and
    only one of those is checkable against the decision log on screen.

    Never raises: a formatting slip must not drop an action already applied.
    """
    try:
        return _CONFIRMATIONS[function](args, outcome)
    except Exception:
        return f"{function} applied."


def normalise_call(function, raw_args, transcript: str,
                   resolver: LaneResolver) -> NormalisedCall:
    """Model call -> exact `control_api` kwargs, or a fail-closed error.

    The FUNCTION always comes from the model and is allowlist-checked by
    `control_api.dispatch()`; this never derives one from the transcript. The
    transcript is used only to fill an argument the model omitted, to
    disambiguate a unit, or — for `weight` alone, and for the measured reason
    given in `_resolve_weight` — to override a value the model got wrong.
    """
    if not isinstance(function, str) or not function:
        return _fail("the model did not name a function")
    if raw_args is not None and not isinstance(raw_args, dict):
        return _fail(f"args must be an object, got {type(raw_args).__name__}")
    args = dict(raw_args) if isinstance(raw_args, dict) else {}
    # Reject keys that are not valid parameter names. No normaliser passes a
    # model-supplied key through to `dispatch(**kwargs)` — each builds a fresh
    # dict — so this is belt-and-braces, kept because the cost is one line and
    # the failure it guards against is a crash on the control surface.
    if any(not isinstance(k, str) or not k.isidentifier() for k in args):
        return _fail("args contains a key that is not a valid parameter name")

    handler = _NORMALISERS.get(function)
    if handler is None:
        # Unknown name: NOT normalised, NOT dispatched. `intent_agent` has
        # already allowlist-checked, and `control_api.dispatch()` checks again.
        return _fail(f"no argument mapping for {function!r}")
    return handler(args, transcript, resolver, [])
