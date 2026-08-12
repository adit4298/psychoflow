"""Observation / action space definition, padding and masking (§9.2).

The observation is a NODE-FEATURE MATRIX, shape (3, 191): one row per
junction, in the fixed order (J1, J2, J3). It is deliberately not a flat
Box and not a Dict — §9.5's graph-attention extractor needs exactly an
[N_nodes, F] tensor to attend over CORRIDOR_ADJACENCY, and the
shared-policy fallback applies the same per-row MLP with the attention
step removed. Handing both extractors this shape is what makes §9.5's
config flag a one-line swap rather than a rewrite.

Corridor adjacency is NOT part of the observation: §0.1 locks it as a
fixed J1-J2-J3 chain, so it is a constant the extractor imports
(CORRIDOR_ADJACENCY, twin/digital_twin.py), not per-step data.

Row layout (F = 191):
    [  0:176]  lane block        16 lane slots x 11 features
    [176:188]  junction scalars  12
    [188:191]  weather one-hot   3   (corridor-global, replicated per row)

MAX_PHASES is 3, and that is a measured value, not an assumption. Phase
count is not a function of a junction's own lane count — it depends on
whether the junction's approach lane count matches its outgoing edge.
Sweeping all 27 lane-count combinations of the generator:
    symmetric junction  -> 4 total phases, 2 green
    asymmetric junction -> 6 total phases, 3 green
so the largest green-phase count across every reachable topology is 3.
(Counting green phases requires testing for 'y' NOT in the state string;
testing for 'G'/'g' over-counts, because netconvert's yellow phases keep
a permissive 'g' on minor movements.)

Normalization uses fixed constants rather than VecNormalize, so an
observation is reproducible across runs and across the backend/demo — and
because §10's validator and §12's narrator reason about raw values
anyway, a running normalizer would only add drift between what the policy
sees and what the operator is told.
"""

from __future__ import annotations

import numpy as np
from gymnasium import spaces

from perception.incident_intake import SEVERITIES
from perception.lane_sensor import VEHICLE_TYPES
from perception.weather import WEATHER_STATES
from twin.digital_twin import CORRIDOR_JUNCTIONS

# --------------------------------------------------------------------------
# Shape constants
# --------------------------------------------------------------------------
N_JUNCTIONS = len(CORRIDOR_JUNCTIONS)

# §9.2: "padded to MAX_LANES = 4 per approach"
MAX_LANES = 4
APPROACH_ORDER = ("north", "east", "south", "west")
MAX_APPROACHES = len(APPROACH_ORDER)
LANE_SLOTS = MAX_APPROACHES * MAX_LANES  # 16

LANE_FEATURES = 11
JUNCTION_SCALARS = 12
WEATHER_FEATURES = len(WEATHER_STATES)  # 3

LANE_BLOCK_WIDTH = LANE_SLOTS * LANE_FEATURES  # 176
LANE_BLOCK_START = 0
JUNCTION_BLOCK_START = LANE_BLOCK_WIDTH  # 176
WEATHER_BLOCK_START = JUNCTION_BLOCK_START + JUNCTION_SCALARS  # 188
OBS_FEATURES = WEATHER_BLOCK_START + WEATHER_FEATURES  # 191

OBS_SHAPE = (N_JUNCTIONS, OBS_FEATURES)
OBS_LOW = -10.0
OBS_HIGH = 10.0

# Measured across all 27 lane-count combinations — see module docstring.
MAX_PHASES = 3

# --------------------------------------------------------------------------
# Per-lane feature indices (within one 11-wide lane slot)
# --------------------------------------------------------------------------
LF_VEHICLE_COUNT = 0
LF_HALTED_COUNT = 1
LF_WAIT_CURRENT = 2
LF_WAIT_MAX = 3
LF_STARVATION_FLAG = 4
LF_TYPE_START = 5  # 5 slots, one per VEHICLE_TYPES entry
LF_VALID_MASK = 10

# --------------------------------------------------------------------------
# Junction scalar indices (offsets from JUNCTION_BLOCK_START)
# --------------------------------------------------------------------------
JS_PHASE_ONEHOT = 0  # 3 wide, over MAX_PHASES
JS_TIME_SINCE_SWITCH = 3
JS_LANECOUNT_ONEHOT = 4  # 3 wide: 2-lane / 3-lane / 4-lane
JS_N_GREEN_PHASES = 7
JS_INCIDENT_ACTIVE = 8
JS_INCIDENT_SEVERITY = 9
JS_SPILLOVER_DELTA = 10  # §8.1 — ZERO-FILLED until Phase 5 lands
JS_SPILLOVER_CONFIDENCE = 11  # §8.1 — ZERO-FILLED until Phase 5 lands

LANE_COUNT_ORDER = (2, 3, 4)

# --------------------------------------------------------------------------
# Normalization constants
# --------------------------------------------------------------------------
NORM_VEHICLE_COUNT = 20.0
NORM_HALTED_COUNT = 20.0
NORM_WAIT_CURRENT = 200.0
NORM_WAIT_MAX = 200.0
NORM_TYPE_COUNT = 10.0
NORM_TIME_SINCE_SWITCH = 120.0
NORM_SPILLOVER_DELTA = 10.0

SEVERITY_VALUE = {"low": 0.33, "medium": 0.67, "high": 1.0}
assert set(SEVERITY_VALUE) == set(SEVERITIES), "severity map drifted from §7.3's enum"


# --------------------------------------------------------------------------
# Spaces
# --------------------------------------------------------------------------
def observation_space() -> spaces.Box:
    return spaces.Box(low=OBS_LOW, high=OBS_HIGH, shape=OBS_SHAPE, dtype=np.float32)


def action_space() -> spaces.MultiDiscrete:
    """One combined action per step covering all three junctions (§9.5).

    Not three separate agent decisions: §9.5 describes ONE policy network
    in both coordination modes and requires that flipping the config flag
    "never requires new code, only a re-run". Three independent agents
    would need a different env and training loop per mode. The fallback
    also depends on "the shared reward signal across the corridor", which
    means one corridor-level scalar per step — i.e. one agent's step.

    Honest boundary (§17): this is centralized execution — one network
    sees all three junctions and emits all three phases in a single
    forward pass.
    """
    return spaces.MultiDiscrete([MAX_PHASES] * N_JUNCTIONS)


# --------------------------------------------------------------------------
# Lane slot ordering
# --------------------------------------------------------------------------
def _lane_index(lane_id: str) -> int:
    """Trailing index of a SUMO lane id, e.g. 'N1_J1_0' -> 0."""
    return int(lane_id.rsplit("_", 1)[1])


def ordered_lane_slots(lanes: dict[str, dict]) -> list[str | None]:
    """Map a junction's §7.1 lane readings onto 16 fixed slots.

    Ordered by APPROACH_ORDER, then lane index ascending within each
    approach, padded with None. The ordering must be stable step to step
    or the policy cannot learn what a slot means — so it is derived from
    the twin's geometric `approach` tag, not from dict iteration order.
    """
    by_approach: dict[str, list[str]] = {a: [] for a in APPROACH_ORDER}
    for lane_id, reading in lanes.items():
        approach = reading["approach"]
        if approach in by_approach:
            by_approach[approach].append(lane_id)

    slots: list[str | None] = []
    for approach in APPROACH_ORDER:
        ids = sorted(by_approach[approach], key=_lane_index)[:MAX_LANES]
        slots.extend(ids)
        slots.extend([None] * (MAX_LANES - len(ids)))
    return slots


# --------------------------------------------------------------------------
# Observation construction
# --------------------------------------------------------------------------
def build_observation(
    snapshot: dict,
    runtime: dict[str, dict],
    spillover: dict[str, tuple[float, float]] | None = None,
) -> np.ndarray:
    """Build the (3, 191) observation from ONE digital-twin snapshot.

    `runtime` carries the signal-timing state the twin does not hold —
    per junction: current_green_slot, n_green_phases, time_since_switch_s.

    `spillover` is §8.1's forecast, {junction_id: (delta, confidence)}.
    None until Phase 5 — indices JS_SPILLOVER_* stay zero, which is why
    CLAUDE.md §3 gates real training on Phase 5 landing first.
    """
    obs = np.zeros(OBS_SHAPE, dtype=np.float32)

    weather_state = snapshot["weather"]["state"]
    weather_onehot = [1.0 if weather_state == s else 0.0 for s in WEATHER_STATES]

    # §7.3 incidents carry a junction_id, so per-junction flags are a
    # direct lookup rather than a geometric inference.
    incidents_by_junction: dict[str, list[dict]] = {}
    for incident in snapshot["active_incidents"]:
        jid = incident["location"]["junction_id"]
        incidents_by_junction.setdefault(jid, []).append(incident)

    for row, junction_id in enumerate(CORRIDOR_JUNCTIONS):
        jdata = snapshot["junctions"][junction_id]
        jruntime = runtime[junction_id]

        # ---- lane block ------------------------------------------------
        for slot, lane_id in enumerate(ordered_lane_slots(jdata["lanes"])):
            if lane_id is None:
                continue  # zero-filled, valid_mask stays 0 (§9.2)
            reading = jdata["lanes"][lane_id]
            base = LANE_BLOCK_START + slot * LANE_FEATURES

            obs[row, base + LF_VEHICLE_COUNT] = reading["vehicle_count"] / NORM_VEHICLE_COUNT
            obs[row, base + LF_HALTED_COUNT] = reading["halted_count"] / NORM_HALTED_COUNT
            obs[row, base + LF_WAIT_CURRENT] = reading["wait_time_current"] / NORM_WAIT_CURRENT
            obs[row, base + LF_WAIT_MAX] = reading["wait_time_max_single_vehicle"] / NORM_WAIT_MAX
            obs[row, base + LF_STARVATION_FLAG] = 1.0 if reading["starvation_flag"] else 0.0
            for t, vtype in enumerate(VEHICLE_TYPES):
                obs[row, base + LF_TYPE_START + t] = (
                    reading["type_composition"][vtype] / NORM_TYPE_COUNT
                )
            obs[row, base + LF_VALID_MASK] = 1.0

        # ---- junction scalars ------------------------------------------
        jb = JUNCTION_BLOCK_START
        green_slot = jruntime["current_green_slot"]
        if 0 <= green_slot < MAX_PHASES:
            obs[row, jb + JS_PHASE_ONEHOT + green_slot] = 1.0

        obs[row, jb + JS_TIME_SINCE_SWITCH] = (
            jruntime["time_since_switch_s"] / NORM_TIME_SINCE_SWITCH
        )

        lane_count = jdata["lane_count"]
        if lane_count in LANE_COUNT_ORDER:
            obs[row, jb + JS_LANECOUNT_ONEHOT + LANE_COUNT_ORDER.index(lane_count)] = 1.0

        obs[row, jb + JS_N_GREEN_PHASES] = jruntime["n_green_phases"] / MAX_PHASES

        active = incidents_by_junction.get(junction_id, [])
        if active:
            obs[row, jb + JS_INCIDENT_ACTIVE] = 1.0
            obs[row, jb + JS_INCIDENT_SEVERITY] = max(
                SEVERITY_VALUE[i["severity"]] for i in active
            )

        if spillover is not None and junction_id in spillover:
            delta, confidence = spillover[junction_id]
            obs[row, jb + JS_SPILLOVER_DELTA] = delta / NORM_SPILLOVER_DELTA
            obs[row, jb + JS_SPILLOVER_CONFIDENCE] = confidence

        # ---- weather (corridor-global, replicated per node) -------------
        obs[row, WEATHER_BLOCK_START : WEATHER_BLOCK_START + WEATHER_FEATURES] = weather_onehot

    return np.clip(obs, OBS_LOW, OBS_HIGH, out=obs)


# --------------------------------------------------------------------------
# Action masking
# --------------------------------------------------------------------------
def make_action_masks(runtime: dict[str, dict], min_green_s: float) -> np.ndarray:
    """Per-junction masks, concatenated to length sum(nvec) = 9.

    That flat concatenated layout is the format sb3-contrib's MaskablePPO
    expects for a MultiDiscrete action space.

    Three rules, in precedence order:
      1. Mid-yellow transition -> only the committed target is legal.
         Changing target mid-yellow would clear one set of movements and
         then release a different, possibly conflicting set.
      2. Green younger than min_green_s -> only "stay" is legal. §9.4's
         switch penalty discourages flicker; this forbids it.
      3. Otherwise -> every green phase this junction actually has.
         Padding slots above n_green_phases are always masked, which is
         what makes §9.2's "the agent physically cannot select an invalid
         phase" literally true on 2-green junctions.

    The current phase is always legal, so every sub-space always has at
    least one valid action — MaskablePPO raises if one does not.
    """
    masks = np.zeros(N_JUNCTIONS * MAX_PHASES, dtype=bool)

    for j, junction_id in enumerate(CORRIDOR_JUNCTIONS):
        jruntime = runtime[junction_id]
        base = j * MAX_PHASES

        target = jruntime.get("transition_target")
        if target is not None:
            masks[base + target] = True
            continue

        current = jruntime["current_green_slot"]
        if jruntime["time_since_switch_s"] < min_green_s:
            masks[base + current] = True
            continue

        for slot in range(jruntime["n_green_phases"]):
            masks[base + slot] = True

    return masks


# --------------------------------------------------------------------------
# Debug rendering
# --------------------------------------------------------------------------
def describe(obs: np.ndarray) -> dict:
    """Render an observation back to named values.

    573 floats is not inspectable by eye, and a silently mis-indexed
    feature is exactly the kind of bug that shows up as "training just
    doesn't work" hours later. This is the readback.
    """
    out: dict[str, dict] = {}
    for row, junction_id in enumerate(CORRIDOR_JUNCTIONS):
        jb = JUNCTION_BLOCK_START
        lanes = {}
        for slot in range(LANE_SLOTS):
            base = LANE_BLOCK_START + slot * LANE_FEATURES
            if obs[row, base + LF_VALID_MASK] == 0.0:
                continue
            approach = APPROACH_ORDER[slot // MAX_LANES]
            lanes[f"slot{slot:02d}_{approach}_{slot % MAX_LANES}"] = {
                "vehicle_count": round(float(obs[row, base + LF_VEHICLE_COUNT]) * NORM_VEHICLE_COUNT, 2),
                "halted_count": round(float(obs[row, base + LF_HALTED_COUNT]) * NORM_HALTED_COUNT, 2),
                "wait_current": round(float(obs[row, base + LF_WAIT_CURRENT]) * NORM_WAIT_CURRENT, 2),
                "wait_max": round(float(obs[row, base + LF_WAIT_MAX]) * NORM_WAIT_MAX, 2),
                "starved": bool(obs[row, base + LF_STARVATION_FLAG]),
                "types": {
                    vtype: round(float(obs[row, base + LF_TYPE_START + t]) * NORM_TYPE_COUNT)
                    for t, vtype in enumerate(VEHICLE_TYPES)
                },
            }

        phase_onehot = obs[row, jb + JS_PHASE_ONEHOT : jb + JS_PHASE_ONEHOT + MAX_PHASES]
        lane_onehot = obs[row, jb + JS_LANECOUNT_ONEHOT : jb + JS_LANECOUNT_ONEHOT + 3]
        weather_onehot = obs[row, WEATHER_BLOCK_START : WEATHER_BLOCK_START + WEATHER_FEATURES]

        out[junction_id] = {
            "active_lane_slots": len(lanes),
            "current_green_slot": int(np.argmax(phase_onehot)) if phase_onehot.any() else None,
            "time_since_switch_s": round(
                float(obs[row, jb + JS_TIME_SINCE_SWITCH]) * NORM_TIME_SINCE_SWITCH, 1
            ),
            "lane_count": LANE_COUNT_ORDER[int(np.argmax(lane_onehot))] if lane_onehot.any() else None,
            "n_green_phases": round(float(obs[row, jb + JS_N_GREEN_PHASES]) * MAX_PHASES),
            "incident_active": bool(obs[row, jb + JS_INCIDENT_ACTIVE]),
            "incident_severity": round(float(obs[row, jb + JS_INCIDENT_SEVERITY]), 2),
            "spillover_delta": round(
                float(obs[row, jb + JS_SPILLOVER_DELTA]) * NORM_SPILLOVER_DELTA, 3
            ),
            "spillover_confidence": round(float(obs[row, jb + JS_SPILLOVER_CONFIDENCE]), 3),
            "weather": WEATHER_STATES[int(np.argmax(weather_onehot))] if weather_onehot.any() else None,
            "lanes": lanes,
        }
    return out
