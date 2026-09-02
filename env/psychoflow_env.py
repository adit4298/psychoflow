"""Gymnasium environment wrapping SUMO/TraCI for the J1->J2->J3 corridor
(§9.2, §9.4).

One agent, one combined action per step covering all three junctions
(§9.5 — see obs_action_spec.action_space for why this is forced rather
than chosen). One corridor-level scalar reward.

Per-step flow — the single pull the whole architecture rests on:

    step(action)
      1. traci.switch(label)
      2a. mask check on the PROPOSED action (§9.2)
      2b. §10 SAFETY VALIDATOR — the mandatory gate
      2c. apply the VALIDATED action per junction (yellow transition, or hold)
      3. run DECISION_INTERVAL_S SUMO sub-steps
           - drive the yellow -> green state machine
           - accumulate arrived counts
      4. snapshot = twin.update(sim_time)        <-- the single pull
      5. obs    = build_observation(snapshot, runtime)
      6. reward = compute_reward(snapshot, interval_stats)
      7. masks  = make_action_masks(runtime)

Step 2b sits INSIDE step(), immediately before the only code path that
reaches traci.trafficlight.setPhase(), rather than in a Gymnasium wrapper
or in the caller. That is what makes §10's claim ("nothing reaches the
road without passing through here") a structural fact — a wrapper would
leave step() directly callable and unshielded. It also means the policy
TRAINS inside the same shield it deploys into; a shield bolted on only at
deployment produces train/deploy mismatch. §9.4's w_emergency=20.0 was
chosen so the reward agrees with the gate rather than fighting it, and
that only pays off if the gate is present during training.

Known and accepted: SB3 logs the PROPOSED action while the VALIDATED one
executes. That is standard for shielded RL, and overrides should become
rare as the policy improves.

The validator reads self._snapshot — the snapshot from the END of the
previous step, which is exactly the one build_observation() was called
on. So it judges the action against the same reality the policy saw when
it chose. That is §7.6's guarantee extended one module further.

Steps 5-7 all read the SAME snapshot object, so observation, reward and
mask can never disagree about what the corridor looked like. That is the
property §7.6 exists to guarantee.

On §7.6's "no module queries TraCI except perception": this env calls
traci.simulationStep(), trafficlight.setPhase() and
simulation.getArrivedNumber() directly. The first two are actuation and
simulation driving, which never went through the twin by design — §10
explicitly has the validator make "the actual TraCI signal-set call". The
third is step-integrated actuation feedback that must be accumulated
across all sub-steps while the twin updates only once per interval, so
routing it through the twin would drop 4 of every 5 readings. All LANE
AND CORRIDOR STATE still comes through the twin, which is the rule's
substance.

BUILD-ORDER GATE (CLAUDE.md §3): this env must not be used for a real
training run until Phase 5 lands. Observation indices JS_SPILLOVER_DELTA
and JS_SPILLOVER_CONFIDENCE are zero-filled while `spillover_predictor`
is None, and training against permanently-zero inputs teaches the policy
those features carry no information — a lesson it will not unlearn when
Phase 5 makes them live. Smoke tests and random-action rollouts are fine.
"""

from __future__ import annotations

import random
import shutil
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import gymnasium as gym
import numpy as np
import sumolib
import traci

from prediction.spillover import as_junction_dict

from env.obs_action_spec import (
    MAX_PHASES,
    action_space,
    build_observation,
    make_action_masks,
    observation_space,
)
from env.reward import IntervalStats, RewardConfig, compute_reward
from perception.lane_sensor import WAITING_TIME_MEMORY_S
from safety.validator import ValidatedAction, validate
from sim.networks.generate_corridor import GENERATED_DIR, VALID_LANE_COUNTS, generate_corridor
from sim.scenario_generator import CORRIDOR_ROUTES, write_route_file
from twin.digital_twin import CORRIDOR_JUNCTIONS, DigitalTwin

REPO_ROOT = Path(__file__).resolve().parents[1]
ADD_FILE = REPO_ROOT / "sim" / "networks" / "vehicle_types.add.xml"

# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------
STEP_LENGTH_S = 1.0
DECISION_INTERVAL_S = 5.0  # agent acts every 5 simulated seconds
MIN_GREEN_S = 10.0  # enforced by masking, not by penalty
EPISODE_HORIZON_S = 3600.0  # §9.2 / §0.1, confirmed unchanged

# Set after every setPhase so SUMO's own static timer never advances the
# program behind our back — this env owns every transition.
HOLD_PHASE_S = 1e5

# SUMO teleports a vehicle blocked longer than this. The default (300s)
# sits inside the starvation regime the reward is built to measure, so a
# badly starved lane would silently have its worst vehicle removed —
# erasing the exact signal §9.4 penalizes. 600s is clear of the 90-200s
# band the reward operates over while still leaving a deadlock escape
# hatch (disabling teleport entirely risks permanent gridlock at high
# density, which would hang an episode rather than score it badly).
TIME_TO_TELEPORT_S = 600


class InvalidActionError(ValueError):
    """Raised when an action violates the mask (§9.2).

    MaskablePPO cannot produce one of these — the mask is applied to the
    logits before sampling. It exists so the mask is load-bearing rather
    than advisory: a hand-written controller, a voice command (§14), or a
    bug in a later phase that proposes an invalid or unsafe phase gets a
    hard failure instead of a silently-ignored action.
    """


@dataclass
class ScenarioConfig:
    """What reset() draws, per §16's curriculum stage.

    §9.2 says reset "draws a fresh randomized topology/lane-count/density
    scenario", but §16 Stage 1 says "single topology, fixed moderate
    density", with lane-count variation only at Stage 2 and density only
    at Stage 3. Those contradict unless reset is stage-parameterized —
    so it is. §9.2 describes this config fully enabled; §16's stages are
    restrictions of it. Defaults below are Stage 1.
    """

    lane_counts: tuple[int, int, int] = (4, 3, 2)
    randomize_lane_counts: bool = False
    randomize_density: bool = False
    spawn_emergencies: bool = False

    corridor_veh_per_hour: float = 1000.0
    cross_veh_per_hour: float = 600.0
    density_range: tuple[float, float] = (0.6, 1.4)

    # Flows must stop before the horizon or the corridor never empties and
    # §9.2's "vehicles-cleared target" is unreachable by construction.
    flows_end_s: float = 3000.0
    episode_horizon_s: float = EPISODE_HORIZON_S

    # Let some traffic build before the first observation, so step 1 is
    # not a degenerate all-zero state.
    warmup_s: float = 10.0

    n_emergencies: int = 1
    emergency_window_s: tuple[float, float] = (300.0, 2400.0)


@dataclass
class _PhaseState:
    """Signal-timing state the digital twin does not hold."""

    green_slots: list[int] = field(default_factory=list)  # raw program indices
    yellow_after: dict[int, int] = field(default_factory=dict)  # slot -> raw yellow index
    yellow_duration_s: dict[int, float] = field(default_factory=dict)
    # Which lanes each green slot WOULD green. Static for the episode, so
    # computed once. NOT the same map as _green_lanes(), which reads the
    # LIVE state and mid-yellow returns the yellow phase's greens — correct
    # for §9.4's "is the ambulance moving right now", wrong for §9.1's phase
    # scoring and §10's override targeting. Keep both.
    served_lanes: dict[int, frozenset[str]] = field(default_factory=dict)
    cur_slot: int = 0
    time_since_switch_s: float = 0.0
    transition: dict | None = None  # {"target_slot": int, "remaining_s": float}


class PsychoFlowEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(
        self,
        scenario_config: ScenarioConfig | None = None,
        reward_config: RewardConfig | None = None,
        spillover_predictor=None,
        use_gui: bool = False,
        seed: int | None = None,
        label: str | None = None,
        strict_action_masking: bool = True,
        enable_safety_validator: bool = True,
        vtype_file: str | Path = ADD_FILE,
        lateral_resolution: float | None = None,
    ):
        super().__init__()
        self.scenario = scenario_config or ScenarioConfig()
        self.reward_config = reward_config or RewardConfig()
        self.spillover_predictor = spillover_predictor  # §8.1, Phase 5
        if spillover_predictor is None:
            # Legal — smoke tests, random-action rollouts and unit tests may
            # construct the env without a predictor (CLAUDE.md §3). Not legal
            # for a kept training run; that hard check lives in Phase 6's
            # training/train.py, not here (see CLAUDE.md §3/§8). This warning
            # is the cheap, breaks-nothing tripwire in between.
            warnings.warn(
                "PsychoFlowEnv constructed with spillover_predictor=None — "
                "observation indices 10/11 (spillover) will be zero-filled. "
                "Do not use this instance for a kept training run (CLAUDE.md §3).",
                stacklevel=2,
            )
        self.use_gui = use_gui
        self.strict_action_masking = strict_action_masking

        # DEMO-ONLY DRIVING MODEL (STEP 1) — both default to today's exact
        # behaviour, so an omitted kwarg produces a BYTE-IDENTICAL traci.start()
        # command line to the one every recorded number was measured under.
        # `vtype_file` swaps in sim/networks/vehicle_types_demo.add.xml (SUMO
        # sublane driving); `lateral_resolution` adds --lateral-resolution,
        # which is what actually enables SL2015. Neither is reachable without
        # passing it explicitly, and training/train.py asserts both are at
        # their defaults before model.learn(). Same additive-param pattern as
        # Tier0Controller.act(lane_weights=None).
        #
        # NEVER measure a checkpoint under these and compare the result to a
        # recorded number — the dynamics differ, so they describe different
        # worlds (see the demo file's own header).
        self.vtype_file = Path(vtype_file)
        self.lateral_resolution = lateral_resolution

        # TEST-HARNESS ONLY — see CLAUDE.md §8's standing rule.
        # False disables §10 entirely: no starvation ceiling, no emergency
        # override. It exists for exactly two things — the same-seed A/B in
        # sim/run_tier0_episode.py that PROVES the ceiling is what bounds the
        # wait, and reproducing Phase 3's pre-validator numbers. It must never
        # be reachable from backend/, control_api.py (§13.1) or §14's voice
        # intents; there is no operator-facing reason to switch off the safety
        # validator, and §10's guarantee only holds if the off-switch is
        # unreachable from anything driving a real sim.
        self.enable_safety_validator = enable_safety_validator

        # §13.1's trigger_emergency(lane_id): an operator forcing the same
        # override §10 raises automatically. Wired in Phase 9.
        self.forced_emergency_lanes: frozenset[str] = frozenset()

        self.observation_space = observation_space()
        self.action_space = action_space()

        self._rng = random.Random(seed)
        self._seed = seed
        # Distinct TraCI label per instance so SB3 can run several SUMO
        # processes in parallel. traci.switch() sets the module-level
        # connection the Phase 2 perception modules already use, so this
        # works under DummyVecEnv and SubprocVecEnv without touching them.
        self._label = label or f"psychoflow_{id(self):x}"
        self._started = False
        self._tmpdir = Path(tempfile.mkdtemp(prefix="psychoflow_"))

        self.twin: DigitalTwin | None = None
        self._phase_state: dict[str, _PhaseState] = {}
        self._snapshot: dict | None = None
        self._sim_time = 0.0
        self._arrived_total = 0
        self._lane_counts = self.scenario.lane_counts
        self._density_mult: dict[str, float] = {"corridor_mean": 1.0, "cross_mean": 1.0}
        # §16 Stage 4 checkpoint needs — see write_route_file()'s docstring.
        # "" / nan when spawn_emergencies=False (no ambulance this episode).
        self._emergency_route: str = ""
        self._emergency_route_type: str = ""
        self._emergency_depart_s: float = float("nan")

    # ------------------------------------------------------------------
    # Network / scenario setup
    # ------------------------------------------------------------------
    def _ensure_corridor(self, lane_counts: tuple[int, int, int]) -> Path:
        """Return the .net.xml for this lane-count combo, building once.

        Generated on demand and cached on disk rather than regenerating at
        every reset — netconvert at reset time would dominate episode
        setup cost once lane-count randomization is on (Stage 2).
        """
        j1, j2, j3 = lane_counts
        net_path = GENERATED_DIR / f"corridor_{j1}{j2}{j3}.net.xml"
        if not net_path.exists():
            generate_corridor(j1, j2, j3, output_name=f"corridor_{j1}{j2}{j3}")
        return net_path

    def _draw_scenario(self) -> tuple[Path, Path]:
        if self.scenario.randomize_lane_counts:
            self._lane_counts = tuple(self._rng.choice(VALID_LANE_COUNTS) for _ in range(3))
        else:
            self._lane_counts = self.scenario.lane_counts

        net_path = self._ensure_corridor(self._lane_counts)

        emergencies: tuple[float, ...] = ()
        if self.scenario.spawn_emergencies:
            lo, hi = self.scenario.emergency_window_s
            emergencies = tuple(
                self._rng.uniform(lo, hi) for _ in range(self.scenario.n_emergencies)
            )

        route_path, self._density_mult, emergency_info = write_route_file(
            self._tmpdir / "episode.rou.xml",
            rng=self._rng,
            corridor_veh_per_hour=self.scenario.corridor_veh_per_hour,
            cross_veh_per_hour=self.scenario.cross_veh_per_hour,
            flows_end_s=self.scenario.flows_end_s,
            randomize_density=self.scenario.randomize_density,
            density_range=self.scenario.density_range,
            emergency_departures=emergencies,
        )
        if emergency_info:
            # n_emergencies=1 for Stage 4; only the first is logged if more
            # are ever configured — info_keywords needs one scalar per key.
            first = emergency_info[0]
            self._emergency_route = first["route"]
            self._emergency_route_type = (
                "corridor" if first["route"] in CORRIDOR_ROUTES else "cross"
            )
            self._emergency_depart_s = first["depart_s"]
        else:
            self._emergency_route = ""
            self._emergency_route_type = ""
            self._emergency_depart_s = float("nan")
        return net_path, route_path

    # ------------------------------------------------------------------
    # TLS program inspection
    # ------------------------------------------------------------------
    @staticmethod
    def _is_green(state: str) -> bool:
        """A phase is green if no signal shows yellow.

        Testing for 'G'/'g' alone over-counts: netconvert's yellow phases
        keep a permissive 'g' on minor movements, e.g.
        'rrrrrryyyyygrrrrrryyyyg'.
        """
        return "y" not in state and ("G" in state or "g" in state)

    def _read_phase_programs(self) -> None:
        self._phase_state = {}
        for junction_id in CORRIDOR_JUNCTIONS:
            logic = traci.trafficlight.getAllProgramLogics(junction_id)[0]
            phases = logic.phases

            green_slots = [i for i, p in enumerate(phases) if self._is_green(p.state)]
            if not green_slots:
                raise RuntimeError(f"{junction_id}: no green phases in program")
            if len(green_slots) > MAX_PHASES:
                raise RuntimeError(
                    f"{junction_id}: {len(green_slots)} green phases exceeds MAX_PHASES="
                    f"{MAX_PHASES} — the measured bound in obs_action_spec is stale"
                )

            # The yellow that clears a given green is the phase immediately
            # AFTER it in program order. Keyed off the current green rather
            # than the destination, so it stays correct for non-adjacent
            # jumps: that yellow clears exactly the movements running now.
            yellow_after: dict[int, float] = {}
            yellow_duration: dict[int, float] = {}
            for slot, raw in enumerate(green_slots):
                nxt = (raw + 1) % len(phases)
                if "y" in phases[nxt].state:
                    yellow_after[slot] = nxt
                    yellow_duration[slot] = float(phases[nxt].duration)

            # Static phase -> served-lane map. getControlledLanes() is
            # indexed by link, aligned with the phase state string, and may
            # repeat a lane that carries several links — hence the set.
            controlled = traci.trafficlight.getControlledLanes(junction_id)
            served_lanes = {
                slot: frozenset(
                    lane
                    for i, lane in enumerate(controlled)
                    if i < len(phases[raw].state) and phases[raw].state[i] in ("g", "G")
                )
                for slot, raw in enumerate(green_slots)
            }

            self._phase_state[junction_id] = _PhaseState(
                green_slots=green_slots,
                yellow_after=yellow_after,
                yellow_duration_s=yellow_duration,
                served_lanes=served_lanes,
            )

    def _set_green(self, junction_id: str, slot: int) -> None:
        state = self._phase_state[junction_id]
        traci.trafficlight.setPhase(junction_id, state.green_slots[slot])
        traci.trafficlight.setPhaseDuration(junction_id, HOLD_PHASE_S)
        state.cur_slot = slot
        state.time_since_switch_s = 0.0

    # ------------------------------------------------------------------
    # Runtime view (feeds obs + masks)
    # ------------------------------------------------------------------
    def _runtime(self) -> dict[str, dict]:
        return {
            junction_id: {
                "current_green_slot": st.cur_slot,
                "n_green_phases": len(st.green_slots),
                "time_since_switch_s": st.time_since_switch_s,
                "transition_target": (
                    st.transition["target_slot"] if st.transition is not None else None
                ),
            }
            for junction_id, st in self._phase_state.items()
        }

    def action_masks(self) -> np.ndarray:
        """sb3-contrib MaskablePPO calls this by name."""
        return make_action_masks(self._runtime(), MIN_GREEN_S)

    def phase_served_lanes(self) -> dict[str, dict[int, frozenset[str]]]:
        """{junction: {green slot: lanes that slot would green}}.

        Static for the episode. Consumed by §9.1's Tier 0 phase scoring and
        §10's override targeting, neither of which may query TraCI (§7.6).
        """
        return {jid: dict(st.served_lanes) for jid, st in self._phase_state.items()}

    def _green_lanes(self) -> dict[str, set[str]]:
        green: dict[str, set[str]] = {}
        for junction_id in CORRIDOR_JUNCTIONS:
            state = traci.trafficlight.getRedYellowGreenState(junction_id)
            controlled = traci.trafficlight.getControlledLanes(junction_id)
            green[junction_id] = {
                lane for i, lane in enumerate(controlled)
                if i < len(state) and state[i] in ("g", "G")
            }
        return green

    def _spillover(self) -> dict[str, tuple[float, float]] | None:
        if self.spillover_predictor is None:
            return None  # obs slots stay zero — see the constructor warning
        forecast = self.spillover_predictor.forecast(self._snapshot)
        return as_junction_dict(forecast)

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------
    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        if seed is not None:
            self._rng = random.Random(seed)
            self._seed = seed

        self.close()

        net_path, route_path = self._draw_scenario()

        binary = sumolib.checkBinary("sumo-gui" if self.use_gui else "sumo")
        cmd = [
            binary,
            "-n", str(net_path),
            "-a", str(self.vtype_file),
            "-r", str(route_path),
            # Standing rule (CLAUDE.md §8): the 100s default saturates
            # against the 90s starvation threshold and destroys the
            # magnitude signal §9.1 and §9.4 both consume.
            "--waiting-time-memory", str(WAITING_TIME_MEMORY_S),
            "--time-to-teleport", str(TIME_TO_TELEPORT_S),
            "--step-length", str(STEP_LENGTH_S),
            "--no-step-log",
            "--duration-log.disable",
            "--no-warnings",
        ]
        if self._seed is not None:
            cmd += ["--seed", str(self._seed)]
        if self.lateral_resolution is not None:
            # Demo-only. Appended LAST and only when explicitly requested, so
            # the default command line is byte-identical to the pre-STEP-1 one.
            cmd += ["--lateral-resolution", str(self.lateral_resolution)]
            # Aggressive gap acceptance makes contact possible. SUMO's DEFAULT
            # collision.action is `teleport`, which silently REMOVES a vehicle
            # and would corrupt arrived/throughput without raising — the same
            # trap --time-to-teleport 600 exists for. Keep the vehicle instead.
            cmd += ["--collision.action", "warn",
                    # minGap is a PREFERENCE in mixed traffic, not a safety
                    # envelope; 0 counts only real physical overlap.
                    "--collision.mingap-factor", "0"]

        traci.start(cmd, label=self._label)
        traci.switch(self._label)
        self._started = True

        self.twin = DigitalTwin(net_path, seed=self._seed)
        self.twin.attach()  # must follow traci.start() — snapshots weather baselines
        self.twin.reset(0.0)
        if self.spillover_predictor is not None:
            # The predictor is stateful (keeps the previous snapshot to
            # compute a rate, §8.1) — without this, episode 2's first
            # forecast would compute a rate against episode 1's last
            # snapshot, a huge and meaningless sim_time jump.
            self.spillover_predictor.reset()

        self._read_phase_programs()
        for junction_id in CORRIDOR_JUNCTIONS:
            self._set_green(junction_id, 0)

        self._sim_time = 0.0
        self._arrived_total = 0

        for _ in range(int(self.scenario.warmup_s / STEP_LENGTH_S)):
            traci.simulationStep()
            self._arrived_total += traci.simulation.getArrivedNumber()
            for st in self._phase_state.values():
                st.time_since_switch_s += STEP_LENGTH_S
        self._sim_time = traci.simulation.getTime()

        self._snapshot = self.twin.update(self._sim_time)
        obs = build_observation(self._snapshot, self._runtime(), self._spillover())
        return obs, {
            "lane_counts": self._lane_counts,
            "density_mult_corridor": self._density_mult["corridor_mean"],
            "density_mult_cross": self._density_mult["cross_mean"],
            "emergency_route": self._emergency_route,
            "emergency_route_type": self._emergency_route_type,
            "emergency_depart_s": self._emergency_depart_s,
            "action_masks": self.action_masks(),
        }

    def step(self, action):
        if not self._started:
            raise RuntimeError("reset() must be called before step()")
        traci.switch(self._label)

        action = np.asarray(action, dtype=int).reshape(-1)
        masks = self.action_masks()

        # ---- 2a. mask check, on the PROPOSAL ----------------------------
        # Deliberately before the validator: a masked proposal is a caller
        # bug (a hand-written controller, a §14 voice command, a later-phase
        # mistake) and must fail loudly. The validator's own output is
        # applied unchecked below — see 2b.
        proposed: list[int] = []
        for j, junction_id in enumerate(CORRIDOR_JUNCTIONS):
            choice = int(action[j])
            if not masks[j * MAX_PHASES + choice]:
                if self.strict_action_masking:
                    raise InvalidActionError(
                        f"{junction_id}: phase slot {choice} is masked at this step "
                        f"(valid slots: "
                        f"{[s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]]}). "
                        f"n_green_phases={len(self._phase_state[junction_id].green_slots)}, "
                        f"time_since_switch={self._phase_state[junction_id].time_since_switch_s}s, "
                        f"min_green={MIN_GREEN_S}s"
                    )
                choice = self._phase_state[junction_id].cur_slot  # non-strict: hold
            proposed.append(choice)

        # ---- 2b. §10 SAFETY VALIDATOR — the mandatory gate --------------
        # Judged against self._snapshot, the state the observation was built
        # from, i.e. what the agent saw when it chose.
        if self.enable_safety_validator:
            validated = validate(
                proposed,
                self._snapshot,
                self._runtime(),
                self.phase_served_lanes(),
                MIN_GREEN_S,
                forced_emergency_lanes=self.forced_emergency_lanes,
            )
        else:
            validated = ValidatedAction.passthrough(proposed)

        # ---- 2c. apply the VALIDATED action ----------------------------
        switched: list[str] = []
        for j, junction_id in enumerate(CORRIDOR_JUNCTIONS):
            choice = validated.action[j]
            st = self._phase_state[junction_id]

            if st.transition is not None:
                # Mid-yellow. The yellow is never broken or shortened —
                # doing so releases conflicting movements before the
                # previous ones have cleared. It can only be RE-AIMED.
                if junction_id in validated.retarget_transition:
                    st.transition["target_slot"] = choice
                continue

            if choice == st.cur_slot:
                continue

            # A validator emitting a slot this junction does not have is a
            # bug, not a runtime condition — fail loudly rather than let
            # §10's gate quietly corrupt the program.
            if not 0 <= choice < len(st.green_slots):
                raise InvalidActionError(
                    f"{junction_id}: validated slot {choice} outside "
                    f"0..{len(st.green_slots) - 1}"
                )

            yellow_raw = st.yellow_after.get(st.cur_slot)
            if yellow_raw is None:
                self._set_green(junction_id, choice)  # no yellow defined
            else:
                traci.trafficlight.setPhase(junction_id, yellow_raw)
                traci.trafficlight.setPhaseDuration(junction_id, HOLD_PHASE_S)
                st.transition = {
                    "target_slot": choice,
                    "remaining_s": st.yellow_duration_s[st.cur_slot],
                }
            switched.append(junction_id)

        # ---- 3. advance the simulation ---------------------------------
        arrived = 0
        for _ in range(int(DECISION_INTERVAL_S / STEP_LENGTH_S)):
            traci.simulationStep()
            arrived += traci.simulation.getArrivedNumber()
            for junction_id, st in self._phase_state.items():
                if st.transition is not None:
                    st.transition["remaining_s"] -= STEP_LENGTH_S
                    if st.transition["remaining_s"] <= 0:
                        self._set_green(junction_id, st.transition["target_slot"])
                        st.transition = None
                else:
                    st.time_since_switch_s += STEP_LENGTH_S

        self._arrived_total += arrived
        self._sim_time = traci.simulation.getTime()

        # ---- 4. the single pull ----------------------------------------
        self._snapshot = self.twin.update(self._sim_time)

        # ---- 5/6/7. all derived from that one snapshot ------------------
        obs = build_observation(self._snapshot, self._runtime(), self._spillover())
        reward, breakdown = compute_reward(
            self._snapshot,
            IntervalStats(
                arrived=arrived,
                switched_junctions=tuple(switched),
                green_lanes=self._green_lanes(),
            ),
            self.reward_config,
        )

        # terminated = the task genuinely completed (every vehicle cleared,
        # none pending). truncated = ran out of time. The split is not
        # cosmetic: Gymnasium bootstraps the value function past a
        # truncation but not past a termination, so swapping them would
        # bias every value estimate without ever surfacing as a crash.
        terminated = traci.simulation.getMinExpectedNumber() == 0
        truncated = self._sim_time >= self.scenario.episode_horizon_s

        info = {
            "sim_time": self._sim_time,
            "reward_breakdown": breakdown,
            "arrived_total": self._arrived_total,
            "lane_counts": self._lane_counts,
            # §16 Stage 3 confound check (same need that drove lane_counts'
            # logging fix at Stage 2) — mean drawn density multiplier per
            # route group this episode, 1.0/1.0 when randomize_density=False.
            "density_mult_corridor": self._density_mult["corridor_mean"],
            "density_mult_cross": self._density_mult["cross_mean"],
            # §16 Stage 4 checkpoint — which route the spawned ambulance
            # took and when, since a cross-street draw is structurally
            # different (and potentially harder to serve) than a
            # corridor-through one. "" / nan when spawn_emergencies=False.
            "emergency_route": self._emergency_route,
            "emergency_route_type": self._emergency_route_type,
            "emergency_depart_s": self._emergency_depart_s,
            "action_masks": self.action_masks(),
            "switched_junctions": switched,
            # §10 / §12.1 — what was asked for, what actually ran, and why
            # they differ. Phase 8's decision log reads these directly.
            "proposed_action": validated.proposed,
            "executed_action": validated.action,
            "safety_overrides": [record.to_dict() for record in validated.overrides],
            "safety_bypass_min_green": sorted(validated.bypass_min_green),
        }
        return obs, float(reward), bool(terminated), bool(truncated), info

    def close(self) -> None:
        if self._started:
            try:
                traci.switch(self._label)
                traci.close()
            except Exception:
                pass  # SUMO already gone; nothing to salvage
            self._started = False

    def __del__(self):
        try:
            self.close()
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass
