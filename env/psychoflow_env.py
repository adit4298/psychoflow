"""Gymnasium environment wrapping SUMO/TraCI for the J1->J2->J3 corridor
(§9.2, §9.4).

One agent, one combined action per step covering all three junctions
(§9.5 — see obs_action_spec.action_space for why this is forced rather
than chosen). One corridor-level scalar reward.

Per-step flow — the single pull the whole architecture rests on:

    step(action)
      1. traci.switch(label)
      2. apply action per junction (begin yellow transition, or hold)
      3. run DECISION_INTERVAL_S SUMO sub-steps
           - drive the yellow -> green state machine
           - accumulate arrived counts
      4. snapshot = twin.update(sim_time)        <-- the single pull
      5. obs    = build_observation(snapshot, runtime)
      6. reward = compute_reward(snapshot, interval_stats)
      7. masks  = make_action_masks(runtime)

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
from dataclasses import dataclass, field
from pathlib import Path

import gymnasium as gym
import numpy as np
import sumolib
import traci

from env.obs_action_spec import (
    MAX_PHASES,
    action_space,
    build_observation,
    make_action_masks,
    observation_space,
)
from env.reward import IntervalStats, RewardConfig, compute_reward
from perception.lane_sensor import WAITING_TIME_MEMORY_S
from sim.networks.generate_corridor import GENERATED_DIR, VALID_LANE_COUNTS, generate_corridor
from sim.scenario_generator import write_route_file
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
    ):
        super().__init__()
        self.scenario = scenario_config or ScenarioConfig()
        self.reward_config = reward_config or RewardConfig()
        self.spillover_predictor = spillover_predictor  # §8.1, None until Phase 5
        self.use_gui = use_gui
        self.strict_action_masking = strict_action_masking

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

        route_path = write_route_file(
            self._tmpdir / "episode.rou.xml",
            rng=self._rng,
            corridor_veh_per_hour=self.scenario.corridor_veh_per_hour,
            cross_veh_per_hour=self.scenario.cross_veh_per_hour,
            flows_end_s=self.scenario.flows_end_s,
            randomize_density=self.scenario.randomize_density,
            density_range=self.scenario.density_range,
            emergency_departures=emergencies,
        )
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

            self._phase_state[junction_id] = _PhaseState(
                green_slots=green_slots,
                yellow_after=yellow_after,
                yellow_duration_s=yellow_duration,
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
            return None  # §8.1 not built — obs slots stay zero
        return self.spillover_predictor.predict(self._snapshot)

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
            "-a", str(ADD_FILE),
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

        traci.start(cmd, label=self._label)
        traci.switch(self._label)
        self._started = True

        self.twin = DigitalTwin(net_path, seed=self._seed)
        self.twin.attach()  # must follow traci.start() — snapshots weather baselines
        self.twin.reset(0.0)

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
        return obs, {"lane_counts": self._lane_counts, "action_masks": self.action_masks()}

    def step(self, action):
        if not self._started:
            raise RuntimeError("reset() must be called before step()")
        traci.switch(self._label)

        action = np.asarray(action, dtype=int).reshape(-1)
        masks = self.action_masks()

        # ---- 2. apply action -------------------------------------------
        switched: list[str] = []
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
                continue  # non-strict: hold current phase

            st = self._phase_state[junction_id]
            if choice != st.cur_slot and st.transition is None:
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
            "action_masks": self.action_masks(),
            "switched_junctions": switched,
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
