"""Live SUMO instance driven by the trained agent, on one dedicated thread (§13).

`SimRunner` owns a `PsychoFlowEnv` and is the ONLY code that calls `env.step()`,
`env.reset()` or anything that touches TraCI — TraCI is process-global and
single-threaded, so everything else (the FastAPI handlers, the WebSocket fan-out)
stays off it. The loop each iteration:

    1. drain queued control commands (§13.1) and apply them
    2. expire lane biases / forced emergencies whose window has passed
    3. pick an action  — Tier 0 (§9.1) in manual mode, the trained policy in auto
    3b. run the SHADOW ADVISOR's forward pass on the SAME pre-step obs/mask —
        read-only, its output never reaches step() (see DEFAULT_SHADOW_CHECKPOINT)
    4. env.step(action) — the §10 validator runs INSIDE this call, unchanged
    5. record the step into §12.1's DecisionLog and §11.1's clearance coordinator
    6. assemble the §13.2 frame, hand it to frame_sink, publish get_stats() cache
    7. on episode end, reset and keep going (a demo runs continuously)
    8. sleep to pace wall-clock

SHADOW ADVISOR PLACEMENT IS LOAD-BEARING. Step 3b sits between `_pick_action()`
and `env.step()` on purpose: it must read the SAME pre-step observation and the
same action mask the deployed policy just used. Running it after `step()` would
compare the two policies' proposals against DIFFERENT states — the recommendation
would silently be an answer to the next question, and nothing would raise.
Both `recommended_phase` and `deployed_proposed_phase` are therefore PRE-SHIELD
proposals; `executed_phase` (post-§10) is carried alongside for context and is
deliberately NOT what agreement is computed against, since comparing a proposal
to a post-shield action conflates a policy disagreement with the shield's own
intervention. The advisor is failure-isolated: any exception disables it for the
rest of the process and the key simply stops being emitted.

PHASE 8 SEAM — CLOSED (2026-08-29). The §13.2 frame's `decision` and
`narration` are now produced by Phase 8's real modules
(`explainability/{decision_log,narrator,query_interface}.py`), not by the
hand-rolled `_decision_entry` / `_narrate` / `_reason_for` /
`_representative_lane` / `_NARRATION` adapter this file used to carry. That
adapter and its placeholder narration templates are DELETED, not deprecated.
The wire schema did not move; `_emit_junction` survives as a pure selector,
now over `DecisionLogEntry` objects rather than raw dicts.

TWO SNAPSHOTS, AND THE ORDER MATTERS. `env.step()` rebinds the twin's
snapshot, so there are two distinct objects per iteration:

  * the PRE-step snapshot goes to `DecisionLog.record_step()`. That is the
    state §10's validator judged the action against (`psychoflow_env.py`
    step 2b) and the state the observation was built from, so an override's
    `lane_id` / `wait_s` and §12.1's triggering-lane pick both resolve
    against what the decision was actually made on.
  * the POST-step snapshot is the frame's `digital_twin` field (what the
    dashboard must draw) and what §11.1's clearance coordinator observes,
    matching `sim/run_explainability_episode.py`.

Getting these the wrong way round fails SILENTLY — lane ids exist in both,
so every field still populates, just describing the wrong instant.

ONE DECISION LOG PER EPISODE. `_reset_counters()` REPLACES `self._log`
rather than clearing it; `env.reset()` sends sim_time back to ~0 and
`DecisionLog` refuses a backwards sim_time (its at-or-before queries read
the deque positionally). Reusing one across a reset would raise on the
first post-reset step.

STANDING RULE (CLAUDE.md §8): the env is constructed with the default
`enable_safety_validator=True` and that parameter is never named here.
"""

from __future__ import annotations

import statistics
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

import numpy as np

from agents.rule_based import Tier0Controller
from backend.control_api import EMERGENCY_HOLD_S, Command, ControlState
from coordinator.emergency_clearance import EmergencyClearanceCoordinator
from coordinator.responder_messaging import (
    build_responder_message,
    estimate_baseline_clearance_s,
)
from env.obs_action_spec import MAX_PHASES
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig
from explainability.decision_log import (
    REASON_RL_POLICY,
    REASON_VOICE_COMMAND,
    DecisionLog,
)
from explainability.narrator import narrate
from explainability.query_interface import QueryInterface
from perception.lane_sensor import DEFAULT_STARVATION_THRESHOLD_S
from prediction.incident_impact import predict_incident_impact
from prediction.spillover import SpilloverPredictor
from sim.sumo_activity import beat as _sumo_beat, clear as _sumo_clear
from safety.validator import RULE_EMERGENCY, RULE_STARVATION
from twin.digital_twin import CORRIDOR_JUNCTIONS

REPO_ROOT = Path(__file__).resolve().parents[1]

# DEPLOYED POLICY — decided by MEASUREMENT (4a bake-off, 2026-08-28), not by
# §9.5's architecture A/B, which answered a different question (attention vs
# shared_policy, not learned-vs-single-agent-vs-rule).
#
# 48 episodes, 4 controllers x 4 topologies x 3 seeds, pinned config. Means:
#
#   controller       starv_ev   wait_var   starved%   reward   ovrS
#   tier0                0.00       50.7       0.00   1.2011   0.00
#   stage4_153600        0.08       72.2       0.08   1.3450   0.00   <-- DEPLOYED
#   ga_51624             3.33      109.8       1.20   1.2347   1.08
#   ga_154024          132.75      592.9      25.82   0.2414  15.75
#
# On the DEMO CORRIDOR (4,3,2) specifically, Stage 4 is 0 starvation events /
# 0 overrides / 38-42s worst on all 3 seeds, where ga_51624 is 4 events /
# 1 override / 121-125s worst. THAT fairness grid is the whole deployment case.
#
# EMERGENCY PROPOSAL QUALITY — CORRECTED 2026-08-28, and it is NOT a pillar of
# this decision. An earlier version of this comment cited "0.885 vs 0.778" from
# _sweeps/phase0_baselines.json; those figures are CONTAMINATED (that harness
# runs STAGES[4], the TRAINING config, so eval seed 7 replays Stage 4 training
# episode 1). Re-measured held-out on 11 clean seeds of 12, same methodology
# both sides: Stage 4 = 39/47 = 0.8298, graph_attention @154,024 = 49/64 =
# 0.7656. The gap (0.0642) is NOT significant: z = +0.824, p = 0.410. Both do
# beat a matched random control (z = +3.388 / p = 0.0007 and +2.904 / 0.0037).
# Contamination inflated STAGE 4 ~8x more than graph_attention (+0.2176 vs
# +0.0264), so cleaning narrows the gap. Treat as a non-significant directional
# edge. Data: _sweeps/{stage4,ga154,ga102}_proposal.json.
#
# §9.5 IS NOT REOPENED: COORDINATION_MODE stays `graph_attention` as the MARL
# architecture answer (attention beat shared_policy 12/12). This constant picks
# which TRAINED CHECKPOINT the backend serves, which is a separate axis — and
# the single-agent one measures better. See CLAUDE.md's Stage 5 ESSENTIAL
# QUALIFIER, which already recorded that single-agent beats both MARL modes.
#
# Re-evaluate once D1 (persistent-seed-counter) completes: if D1 shows the
# post-51k collapse was data-diversity-driven, a re-trained MARL run may
# overtake this. Until then the deployed default is the measured best.
DEFAULT_CHECKPOINT = (
    REPO_ROOT / "training" / "checkpoints" / "stage4"
    / "psychoflow_stage4_153600_steps_final.zip"
)

# ---------------------------------------------------------------------------
# SHADOW ADVISOR (§13.2 `shadow_advisor`) — READ-ONLY, ADVISORY, NEVER DRIVES.
# ---------------------------------------------------------------------------
# The §9.5 MARL checkpoint runs its own forward pass alongside the deployed
# policy every decision step and its recommendation rides the frame as an
# ADDITIVE third top-level key. It never reaches `env.step()`; the deployed
# checkpoint above is the sole driver, unconditionally.
#
# ***THE HONESTY NOTE — READ THIS BEFORE BUILDING ANY PANEL ON THIS FIELD.***
#
#   `graph_attention` @51,624 is the WORSE policy. Not marginally, and not
#   only in aggregate — on the DEMO CORRIDOR (4,3,2) specifically, the one
#   topology §19 actually shows:
#
#              starvation events   §10 overrides   worst wait
#     Stage 4          0                 0            38-42s   <-- DEPLOYED
#     ga_51624         4                 1           121-125s   <-- SHADOW
#
#   (4a bake-off, 3 seeds, _sweeps/checkpoint_bakeoff.json. Across the full
#   48-episode grid: starved% 0.08 vs 1.20, reward 1.3450 vs 1.2347.)
#
#   So this field shows WHAT THE MARL ARCHITECTURE WOULD HAVE DONE. It is
#   NOT a better idea being ignored, and it is NOT a second opinion worth
#   deferring to. A disagreement is not evidence the deployed policy erred;
#   on the measured record the prior runs the other way. Any UI built on
#   this must say so — do not label it "recommended", "suggested" or
#   "alternative plan" without that context attached.
#
# Why carry it at all: §9.5's architecture A/B (attention beat shared_policy
# 12/12) is a real measured result, and showing the two policies' proposals
# side by side is an honest way to exhibit it. §20 requires saying out loud
# that the demo runs SINGLE-AGENT PPO; this field makes the distinction
# visible rather than rhetorical — provided the note above travels with it.
#
# Default ON when the file exists; `--no-shadow` disables it; an absent file
# is not an error, the key is simply never emitted.
DEFAULT_SHADOW_CHECKPOINT = (
    REPO_ROOT / "training" / "checkpoints" / "stage5_graph_attention"
    / "psychoflow_stage5_51624_steps_final.zip"
)

# Reported verbatim on the wire as `shadow_advisor.coordination_mode`. This is
# the MARL extractor the SHADOW checkpoint carries, NOT a live read of
# `agents.config.COORDINATION_MODE` — §9.5 is not reopened by this feature and
# nothing here selects an extractor. The deployed path is unaffected either way.
SHADOW_COORDINATION_MODE = "graph_attention"

_JUNCTION_ORDER = {jid: i for i, jid in enumerate(CORRIDOR_JUNCTIONS)}

# How often the sim thread refreshes the Tier 1 SUMO beacon. Well under
# sumo_activity.STALE_AFTER_S so a live backend never reads as stale.
_BEACON_EVERY_S = 20.0

# §13.2 `predictions` — a §8.1 spillover pair is streamed only when its
# forecast moves at least this many queued vehicles over the 60s horizon.
# A forecast of ~0 delta is the common case and is not worth a frame; this
# keeps `predictions` ADDITIVE-and-material, like `responder_messages`.
#
# NOT a spec value: §8.1 defines no materiality/streaming threshold — it
# only specifies the forecast heuristic. 1.0 veh was chosen as a reasonable
# default for this §13.2 streaming filter (2026-08-30). It is display-only:
# it gates nothing but the wire frame — obs indices 10/11 carry the full
# unfiltered forecast from the env's own predictor, and no control/reward
# path reads it. Tune freely if the dashboard proves too noisy or too quiet.
_SPILLOVER_MIN_DELTA = 1.0

# Per-episode cap on the §12.1 log. An episode is at most
# episode_horizon_s / DECISION_INTERVAL_S = 3600/5 = 720 steps x 3 junctions
# = 2160 entries, so this never bites in practice — it is a memory bound for
# a demo left running, not a policy.
_LOG_MAXLEN = 10_000

# ---------------------------------------------------------------------------
# CONTROL-COMMAND GUARDS (2026-08-31 security hardening). The API layer
# range-checks operator input; these bound what repeated / hostile commands
# can do to the running sim, and are enforced HERE because only the sim
# thread knows the live sim_time / topology / active-incident set.
# ---------------------------------------------------------------------------
# set_topology tears down and restarts SUMO. Wall-clock rate limit so a
# stuck key or a flood cannot thrash the rebuild.
_TOPOLOGY_COOLDOWN_S = 10.0

# Hard ceiling on simultaneously-active operator incidents, so an
# inject_incident flood cannot grow digital_twin.active_incidents (and the
# per-incident §8.2 forecast loop) without bound.
_MAX_ACTIVE_INCIDENTS = 32

# The per-iteration body of _run() is wrapped in a try/except so one
# transient error does not kill the sim thread. This many CONSECUTIVE
# failures re-raises to the outer handler (a genuinely broken env — e.g. a
# lost TraCI connection — should stop, not spin).
_MAX_CONSECUTIVE_FAILURES = 5

# §13.1 control commands the sim thread will apply. `get_stats` is a pure
# read and never queued; anything else is ignored with a log line.
_APPLIABLE_KINDS = frozenset({
    "set_mode", "set_baseline_mode", "set_lane_bias", "trigger_emergency",
    "inject_incident", "set_topology", "force_phase", "clear_override",
})


def _jsonable(x):
    """Coerce numpy scalars/arrays and sets to JSON-native types, recursively."""
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, np.ndarray):
        return [_jsonable(v) for v in x.tolist()]
    return x


class SimRunner:
    def __init__(
        self,
        state: ControlState,
        *,
        checkpoint: Path | None = DEFAULT_CHECKPOINT,
        shadow_checkpoint: Path | None = DEFAULT_SHADOW_CHECKPOINT,
        lane_counts: tuple[int, int, int] = (4, 3, 2),
        randomize_density: bool = True,
        spawn_emergencies: bool = True,
        realtime_factor: float = 0.3,
        fast: bool = False,
        seed: int = 7,
        frame_sink: Callable[[dict], None] | None = None,
    ):
        self.state = state
        self.checkpoint = Path(checkpoint) if checkpoint else None
        self.shadow_checkpoint = (
            Path(shadow_checkpoint) if shadow_checkpoint else None
        )
        self._lane_counts = tuple(lane_counts)
        self._randomize_density = randomize_density
        self._spawn_emergencies = spawn_emergencies
        self._sleep_s = 0.0 if fast else max(0.0, float(realtime_factor))
        self._seed = seed
        self._frame_sink = frame_sink

        self._thread = threading.Thread(
            target=self._run, name="psychoflow-sim", daemon=True
        )
        self._stop = threading.Event()
        self._started = threading.Event()
        self._error: str | None = None

        # sim-thread-only state
        self._env: PsychoFlowEnv | None = None
        self._model = None
        # SHADOW ADVISOR — advisory only, never consulted for the action that
        # is executed. `_shadow_enabled` is the single latch: it is set False
        # and never re-armed the moment the advisor raises, so a broken
        # advisor costs exactly one logged traceback and then disappears from
        # the wire. Nothing on the deployed path reads any of these.
        self._shadow_model = None
        self._shadow_enabled = False
        self._tier0 = Tier0Controller()
        self._served: dict[str, dict[int, frozenset[str]]] = {}
        self._obs = None
        self._mode = state.mode
        self._baseline = state.baseline_mode
        self._bias: dict[str, tuple[float, float]] = {}     # lane -> (weight, expiry_sim_time)
        self._forced: dict[str, float] = {}                 # lane -> expiry_sim_time
        # §13.1 force_phase: junction -> operator-pinned green phase. Applied
        # on the normal action path (mask-checked, §10 still runs); cleared
        # by clear_override, a set_topology rebuild, or an episode boundary.
        self._forced_phase: dict[str, int] = {}
        self._last_topology_change = 0.0                    # wall-clock; cooldown guard
        # READ-SIDE spillover predictor for the §13.2 `predictions` field.
        # Deliberately a SECOND SpilloverPredictor, separate from the one
        # inside the env that feeds obs indices 10/11: forecast() is
        # stateful (it stores the previous snapshot to compute a rate), so
        # calling the env's would double-advance it and corrupt the next
        # observation. Fed the same post-step snapshots at the same 5s
        # cadence, so it produces the same numbers the policy sees.
        self._spillover_view = SpilloverPredictor()
        self._pending_lane_counts: tuple[int, int, int] | None = None
        self._step_idx = 0
        self._starved_lanes: set[str] = set()
        self._starvation_events = 0
        self._last_beat = 0.0
        # §15.2 metrics — per-step samples, averaged over the episode.
        self._step_vars: list[float] = []      # pvariance across lanes / step
        self._step_maxes: list[float] = []     # across-lane max wait / step
        self._wait_var = 0.0
        self._mean_wait_max = 0.0
        # Shadow-advisor agreement, per EPISODE (reset in _reset_counters).
        self._shadow_agree = 0                 # junction-slots agreeing
        self._shadow_slots = 0                 # junction-slots compared
        # Phase 8 (§11/§12) — all three are per-EPISODE and are replaced,
        # never cleared in place, by _reset_counters().
        self._log: DecisionLog | None = None
        self._query: QueryInterface | None = None
        self._coord: EmergencyClearanceCoordinator | None = None
        self._last_voice = None  # reserved for Phase 11

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=15)

    def wait_until_ready(self, timeout: float = 90.0) -> bool:
        return self._started.wait(timeout)

    @property
    def error(self) -> str | None:
        return self._error

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def _load_model(self) -> None:
        if self.checkpoint is None or not self.checkpoint.exists():
            self._model = None
            self.state.has_checkpoint = False
            print(f"[sim] no checkpoint at {self.checkpoint} — auto mode disabled, "
                  f"starting in manual (Tier 0)")
            if self._mode == "auto":
                self._mode = "auto"  # left as requested; picker falls back to Tier 0
            return
        from sb3_contrib import MaskablePPO  # local import: keeps control_api light

        print(f"[sim] loading checkpoint {self.checkpoint.name} ...")
        self._model = MaskablePPO.load(str(self.checkpoint))
        self.state.has_checkpoint = True
        print(f"[sim] checkpoint loaded (policy: {type(self._model.policy).__name__})")

    def _load_shadow_model(self) -> None:
        """Load the §9.5 MARL checkpoint for READ-ONLY advisory use (§13.2
        `shadow_advisor`). See DEFAULT_SHADOW_CHECKPOINT's honesty note.

        A missing file is NOT an error — the advisor stays off and the frame
        key is simply never emitted, exactly as `--no-shadow` produces. This
        must never be able to stop the backend: the deployed policy does not
        depend on it in any way.
        """
        if self.shadow_checkpoint is None:
            print("[sim] shadow advisor OFF (disabled by --no-shadow)")
            return
        if not self.shadow_checkpoint.exists():
            print(f"[sim] shadow advisor OFF — no checkpoint at "
                  f"{self.shadow_checkpoint} (not an error; the §13.2 "
                  f"`shadow_advisor` key is simply not emitted)")
            return
        try:
            from sb3_contrib import MaskablePPO  # local: keeps control_api light

            self._shadow_model = MaskablePPO.load(str(self.shadow_checkpoint))
            self._shadow_enabled = True
            print(f"[sim] shadow advisor ON: {self.shadow_checkpoint.name} "
                  f"(extractor: "
                  f"{type(self._shadow_model.policy.features_extractor).__name__}) "
                  f"— ADVISORY ONLY, it does not drive the road")
        except Exception:
            # Same isolation as a runtime failure: the advisor is optional,
            # the backend is not. Never let it take the sim thread down.
            self._shadow_model = None
            self._shadow_enabled = False
            print("[sim] shadow advisor OFF — failed to load, continuing "
                  "without it (deployed policy unaffected):\n"
                  + traceback.format_exc())

    def _build_env(self) -> None:
        if self._env is not None:
            self._env.close()
        self._env = PsychoFlowEnv(
            scenario_config=ScenarioConfig(
                lane_counts=self._lane_counts,
                randomize_lane_counts=False,
                randomize_density=self._randomize_density,
                spawn_emergencies=self._spawn_emergencies,
            ),
            spillover_predictor=SpilloverPredictor(),
            seed=self._seed,
        )
        self._obs, _info = self._env.reset()
        self._served = self._env.phase_served_lanes()
        self._reset_counters()

    def _reset_counters(self) -> None:
        """Everything that is scoped to ONE episode. Called after every
        `env.reset()` — the natural episode end AND a `set_topology` rebuild.
        """
        self._starved_lanes = set()
        self._starvation_events = 0
        self._step_vars = []
        self._step_maxes = []
        self._wait_var = 0.0
        self._mean_wait_max = 0.0
        self._step_idx = 0
        # §13.2 shadow_advisor.episode_agreement_rate is a PER-EPISODE rate,
        # so its two accumulators reset on the boundary like every other
        # per-episode counter above. Carrying them across a reset would blend
        # two different scenarios into one ratio.
        self._shadow_agree = 0
        self._shadow_slots = 0
        # force_phase pins do not survive an episode boundary or a topology
        # rebuild — the situation the operator pinned for is over, and the
        # phase index may not even be valid for a new topology.
        self._forced_phase = {}
        # The read-side spillover predictor is STATEFUL and per-episode,
        # exactly like the env's own (psychoflow_env.reset() resets that
        # one). Without this, episode 2's first forecast computes a rate
        # against episode 1's last snapshot.
        self._spillover_view.reset()
        # REPLACED, not cleared. reset() sends sim_time back to ~0 and
        # DecisionLog refuses a backwards sim_time, so a reused log would
        # raise on the first post-reset record_step. The QueryInterface is
        # rebuilt against the new log because it holds a direct reference,
        # and the clearance coordinator because it holds the (possibly
        # rebuilt) phase->lane map.
        self._log = DecisionLog(maxlen=_LOG_MAXLEN)
        self._query = QueryInterface.from_twin_topology(
            self._log, self._env.twin.topology
        )
        self._coord = EmergencyClearanceCoordinator(self._served)

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._load_model()
            self._load_shadow_model()
            self._build_env()
            self._started.set()
            # INNER GUARD (2026-08-31): one transient error in an iteration must
            # not kill the sim thread and take the whole demo down. Each
            # iteration is caught, logged and retried; _MAX_CONSECUTIVE_FAILURES
            # in a row re-raises to the outer handler (a genuinely broken env —
            # a dropped TraCI connection — should stop, not spin forever).
            consecutive_failures = 0
            while not self._stop.is_set():
                try:
                    self._run_iteration()
                    consecutive_failures = 0
                except Exception:
                    consecutive_failures += 1
                    print(f"[sim] iteration error "
                          f"({consecutive_failures}/{_MAX_CONSECUTIVE_FAILURES}):\n"
                          + traceback.format_exc())
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        raise
                    time.sleep(0.5)
        except Exception:
            self._error = traceback.format_exc()
            print(f"[sim] FATAL:\n{self._error}")
            self._started.set()
        finally:
            if self._env is not None:
                self._env.close()
            _sumo_clear()   # release the Tier 1 beacon when the sim thread exits

    def _run_iteration(self) -> None:
        """One decision step of the live loop.

        Called once per `while` pass by `_run()`, which wraps it in a
        per-iteration try/except so a transient failure is survivable
        rather than fatal to the sim thread.
        """
        # Tier 1 SUMO beacon: the backend owns a live SUMO instance for as
        # long as it runs, so it BEATS like a training run rather than
        # checking like a sweep. Rate-limited to once per _BEACON_EVERY_S:
        # under --fast, _sleep_s is 0.0 and this loop runs flat out, so an
        # unguarded beat would rewrite the file thousands of times a second.
        now = time.time()
        if now - self._last_beat > _BEACON_EVERY_S:
            self._last_beat = now
            _sumo_beat("backend", f"live demo, lane_counts={self._lane_counts}")
        self._drain_commands()
        if self._pending_lane_counts is not None:
            self._lane_counts = self._pending_lane_counts
            self._pending_lane_counts = None
            print(f"[sim] rebuilding network -> lane_counts={self._lane_counts}")
            self._build_env()

        self._expire_windows()
        # ONE tracked set of operator-forced lanes, read once per step and
        # handed to BOTH consumers: §10's validator (via the env,
        # psychoflow_env.py step 2b) and §11.1's clearance coordinator below.
        # Not two implementations of the same tracking — `self._forced` is the
        # only tracker, aged out by _expire_windows() against EMERGENCY_HOLD_S.
        forced = frozenset(self._forced)
        self._env.forced_emergency_lanes = forced

        # PRE-step snapshot: the state §10 judged the action against and the
        # observation was built from. env.step() rebinds the twin's snapshot,
        # so this is a genuinely distinct object from the post-step one — see
        # this module's docstring.
        pre_snap = self._env.twin.snapshot

        action, decisions = self._pick_action()

        # SHADOW ADVISOR (§13.2 `shadow_advisor`) — read-only. HERE, not after
        # step(): it must see the SAME pre-step observation and mask the
        # deployed policy just used, or the two proposals would be answers to
        # different states and the disagreement rate would be meaningless.
        # `self._obs` is still the pre-step observation (step() rebinds it
        # below), and action_masks() is a pure read of the current runtime —
        # the second consecutive call returns what _pick_action() just saw
        # (verified live, check S4 in sim/run_shadow_advisor_check.py).
        #
        # `action` is the deployed policy's PRE-SHIELD proposal, the only
        # like-for-like comparison against the shadow's own pre-shield
        # recommendation. `executed_phase` is filled in from info AFTER step()
        # — deliberately not used for agreement.
        shadow = self._shadow_advice(
            self._obs, self._env.action_masks(), action
        )

        obs, reward, terminated, truncated, info = self._env.step(action)
        self._obs = obs
        self._step_idx += 1
        if shadow is not None:
            # Overwrites the placeholder set by _shadow_advice, keeping the
            # key in its documented wire position.
            shadow["executed_phase"] = {
                jid: int(info["executed_action"][i])
                for i, jid in enumerate(CORRIDOR_JUNCTIONS)
            }

        # §12.1 against the PRE-step snapshot; §11.1 against POST.
        entries = self._log.record_step(
            info["sim_time"], decisions, info, pre_snap, self._served
        )
        closed = self._coord.observe(
            info["sim_time"], self._env.twin.snapshot, self._env._runtime(),
            info, forced_emergency_lanes=forced,
        )

        self._update_metrics(info)
        frame = self._assemble_frame(info, entries, closed, shadow)
        if self._frame_sink is not None:
            self._frame_sink(frame)
        self.state.publish_stats(self._stats_payload(info))

        if terminated or truncated:
            print(f"[sim] episode end (terminated={terminated} "
                  f"truncated={truncated}) — resetting")
            # Close any clearance episode still open, so its §11.2 message is
            # not lost at the boundary (matches the Phase 8 harness's
            # finalize()). Done BEFORE _reset_counters replaces the coordinator.
            self._coord.finalize(info["sim_time"])
            self._obs, _ = self._env.reset()
            self._served = self._env.phase_served_lanes()
            self._reset_counters()

        if self._sleep_s:
            time.sleep(self._sleep_s)

    # ------------------------------------------------------------------
    # control commands
    # ------------------------------------------------------------------
    def _drain_commands(self) -> None:
        while True:
            try:
                cmd: Command = self.state.pending.get_nowait()
            except Exception:
                return
            self._apply_command(cmd)

    def _apply_command(self, cmd: Command) -> None:
        now = self._env._sim_time if self._env is not None else 0.0
        # Server-side allowlist mirror: control_api.dispatch() already refuses
        # unknown NAMES; this refuses unknown queued KINDS, so a bad Command
        # from anywhere is a no-op with a log line, never an AttributeError.
        if cmd.kind not in _APPLIABLE_KINDS:
            print(f"[sim] ignoring command outside the allowlist: {cmd.kind!r}")
            return
        if cmd.kind == "set_mode":
            self._mode = cmd.args["mode"]
            self.state.mode = self._mode
        elif cmd.kind == "set_baseline_mode":
            self._baseline = cmd.args["baseline"]
            self.state.baseline_mode = self._baseline
        elif cmd.kind == "set_lane_bias":
            self._bias[cmd.args["lane_id"]] = (
                float(cmd.args["weight"]), now + float(cmd.args["duration_s"]),
            )
        elif cmd.kind == "trigger_emergency":
            self._forced[cmd.args["lane_id"]] = now + EMERGENCY_HOLD_S
        elif cmd.kind == "inject_incident":
            # §7.3 registry write — not a TraCI call. The twin pulls the
            # active set on its next update(); from the next step it rides
            # digital_twin.active_incidents, §8.2's incident_impact and
            # (via the confidence penalty) §8.1's spillover forecast.
            active = len(self._env.twin.incidents.get_active(now))
            if active >= _MAX_ACTIVE_INCIDENTS:
                print(f"[sim] inject_incident dropped — {active} incidents "
                      f"already active (cap {_MAX_ACTIVE_INCIDENTS})")
                return
            a = cmd.args
            self._env.twin.incidents.report(
                a["incident_type"], a["junction_id"], a["lane_id"],
                a["severity"], a["affected_lanes"],
                reported_at_sim_time=now,
                estimated_duration_s=a["estimated_duration_s"],
            )
        elif cmd.kind == "set_topology":
            combo = tuple(cmd.args["lane_counts"])
            if combo == tuple(self._lane_counts):
                print(f"[sim] set_topology({combo}) ignored — already at this "
                      f"topology")
                return
            wall = time.time()
            since = wall - self._last_topology_change
            if since < _TOPOLOGY_COOLDOWN_S:
                print(f"[sim] set_topology({combo}) ignored — cooldown "
                      f"({_TOPOLOGY_COOLDOWN_S - since:.1f}s remaining)")
                return
            self._last_topology_change = wall
            self._pending_lane_counts = combo
        elif cmd.kind == "force_phase":
            self._forced_phase[cmd.args["junction_id"]] = int(cmd.args["phase"])
        elif cmd.kind == "clear_override":
            jid = cmd.args.get("junction_id")
            if jid is None:
                self._forced_phase.clear()
            else:
                self._forced_phase.pop(jid, None)

    def _expire_windows(self) -> None:
        now = self._env._sim_time
        self._bias = {l: (w, e) for l, (w, e) in self._bias.items() if e > now}
        self._forced = {l: e for l, e in self._forced.items() if e > now}

    # ------------------------------------------------------------------
    # action
    # ------------------------------------------------------------------
    def _pick_action(self):
        # One mask read, shared by the policy/controller AND the force_phase
        # check below — action_masks() is a pure read of the current runtime.
        masks = self._env.action_masks()
        use_rl = self._mode == "auto" and self._model is not None
        if use_rl:
            action, _ = self._model.predict(
                self._obs, action_masks=masks, deterministic=True
            )
            action = np.asarray(action, dtype=int)
            # A per-junction decision row for EVERY junction, even though the
            # trained policy has no Tier-0-style score breakdown. Phase 8's
            # decision_log.record_step RAISES on any §10 override whose
            # junction is missing from this dict, so an empty/partial dict
            # here would lose the shield's overrides for the deployed policy.
            # `score_breakdown`/`alternative_scores` stay empty: record_step
            # accepts and structurally exempts them under a non-scored reason.
            decisions = {
                jid: {
                    "junction_id": jid,
                    "phase_selected": int(action[i]),
                    "score_breakdown": {},
                    "alternative_scores": {},
                    "reason": REASON_RL_POLICY,
                }
                for i, jid in enumerate(CORRIDOR_JUNCTIONS)
            }
        else:
            snap = self._env.twin.snapshot
            runtime = self._env._runtime()
            weights = {l: w for l, (w, _e) in self._bias.items()} or None
            action, decisions = self._tier0.act(
                snap, runtime, masks, self._served, lane_weights=weights
            )

        action = self._apply_forced_phases(action, decisions, masks)
        return action, decisions

    def _apply_forced_phases(self, action, decisions, masks):
        """§13.1 force_phase — pin a junction to an operator-chosen green
        phase for this decision step.

        DEFERRED, not forced: the pinned phase becomes this junction's
        proposed action and then goes through `env.step()` exactly like any
        other, so §10's validator still runs and an emergency / starvation
        override still outranks it. MASK-CHECKED: the phase must be set in
        the live action mask AND be a real green slot in
        `phase_served_lanes()` (`self._served`) — NOT `_green_lanes()`,
        which is the live RYG state and would let a mid-yellow read through.
        An invalid pin (e.g. left over after a topology change) is dropped
        with a log line. The decision-log entry carries
        reason=voice_command (§12.2 — operator-initiated).
        """
        if not self._forced_phase:
            return action
        action = np.asarray(action, dtype=int).copy()
        for jid, phase in list(self._forced_phase.items()):
            i = _JUNCTION_ORDER[jid]
            lo = i * MAX_PHASES
            slot_mask = masks[lo:lo + MAX_PHASES]
            valid = (
                0 <= phase < len(slot_mask)
                and bool(slot_mask[phase])
                and phase in self._served.get(jid, {})
            )
            if not valid:
                self._forced_phase.pop(jid, None)
                print(f"[sim] force_phase({jid}, {phase}) dropped — not a valid "
                      f"green phase under the current mask / topology")
                continue
            action[i] = phase
            decisions[jid] = {
                "junction_id": jid,
                "phase_selected": int(phase),
                "score_breakdown": {},
                "alternative_scores": {},
                "reason": REASON_VOICE_COMMAND,
                "transcript": f"force phase {phase} at {jid}",
                "action_taken": f"force_phase({jid}, {phase})",
            }
        return action

    def _shadow_advice(self, obs, masks, deployed_action) -> dict | None:
        """§13.2 `shadow_advisor` — what the §9.5 MARL policy WOULD have done.

        READ-ONLY AND ADVISORY. The returned dict rides the frame and is read
        by nobody else; it is never fed to `env.step()`, never consulted by
        `_pick_action()`, and cannot influence the deployed policy. Stage 4
        drives the road unconditionally. See DEFAULT_SHADOW_CHECKPOINT's
        honesty note — the shadow is the WORSE policy on every 4a metric and
        on the demo corridor specifically.

        Called with the PRE-step `obs`/`masks` and the deployed policy's
        PRE-SHIELD proposal, so `agrees_with_deployed` compares two
        proposals made from one state. `executed_phase` is a placeholder here
        and is filled by the caller after `env.step()`.

        Returns None when the advisor is off, disabled or has just failed —
        the caller then omits the frame key entirely.

        FAILURE ISOLATION: any exception disables the advisor for the rest of
        the process (logged once, since the guard above short-circuits every
        later call) and returns None. A broken advisor must never be able to
        stop the sim thread or change what reaches the road.
        """
        if not self._shadow_enabled or self._shadow_model is None:
            return None
        try:
            t0 = time.perf_counter()
            recommended_action, _ = self._shadow_model.predict(
                obs, action_masks=masks, deterministic=True
            )
            inference_ms = (time.perf_counter() - t0) * 1000.0
            recommended_action = np.asarray(recommended_action, dtype=int)

            recommended = {jid: int(recommended_action[i])
                           for i, jid in enumerate(CORRIDOR_JUNCTIONS)}
            proposed = {jid: int(deployed_action[i])
                        for i, jid in enumerate(CORRIDOR_JUNCTIONS)}
            agrees = {jid: recommended[jid] == proposed[jid]
                      for jid in CORRIDOR_JUNCTIONS}
            n_agree = sum(agrees.values())

            self._shadow_agree += n_agree
            self._shadow_slots += len(CORRIDOR_JUNCTIONS)

            return {
                # Stated on the wire, every frame, so a consumer cannot read
                # this field as a control output by accident.
                "advisory_only": True,
                "drives_the_road": False,
                "coordination_mode": SHADOW_COORDINATION_MODE,
                "checkpoint": self.shadow_checkpoint.name,
                "recommended_phase": recommended,
                "deployed_proposed_phase": proposed,
                # Placeholder — the caller overwrites this from
                # info["executed_action"] after step(). Kept here so the key
                # order on the wire is stable.
                "executed_phase": {},
                "agrees_with_deployed": agrees,
                "agreement_count": n_agree,
                "n_junctions": len(CORRIDOR_JUNCTIONS),
                "episode_agreement_rate": (
                    self._shadow_agree / self._shadow_slots
                    if self._shadow_slots else 0.0
                ),
                "inference_ms": round(inference_ms, 3),
            }
        except Exception:
            self._shadow_enabled = False
            self._shadow_model = None
            print("[sim] shadow advisor DISABLED after an exception. The "
                  "deployed policy, the sim loop and the §13.2 stream are "
                  "unaffected; the `shadow_advisor` key stops being "
                  "emitted:\n" + traceback.format_exc())
            return None

    # ------------------------------------------------------------------
    # metrics
    # ------------------------------------------------------------------
    def _all_lanes(self, snap: dict):
        for jid in CORRIDOR_JUNCTIONS:
            for lane_id, reading in snap["junctions"][jid]["lanes"].items():
                yield jid, lane_id, reading

    def _update_metrics(self, info: dict) -> None:
        snap = self._env.twin.snapshot
        # §15.2 metrics — definitions verbatim from
        # training/scripts/checkpoint_bakeoff.py::LaneMetricProbe, so the live
        # dashboard, the eval suite and the bake-off share ONE implementation.
        # Deliberately wait_time_max_single_vehicle, NOT wait_time_current
        # (a per-lane SUM over vehicles, whose mean/variance track occupancy
        # and lane count rather than fairness — see §15.2's definitions and
        # agents/rule_based.py's "§9.1's UNITS" note).
        waits = [r["wait_time_max_single_vehicle"]
                 for _j, _l, r in self._all_lanes(snap)]
        if waits:
            self._step_maxes.append(max(waits))
            self._step_vars.append(
                statistics.pvariance(waits) if len(waits) > 1 else 0.0
            )
        self._wait_var = (
            sum(self._step_vars) / len(self._step_vars) if self._step_vars else 0.0
        )
        self._mean_wait_max = (
            sum(self._step_maxes) / len(self._step_maxes) if self._step_maxes else 0.0
        )

        now_starved = {
            lane_id for _j, lane_id, r in self._all_lanes(snap)
            if r["wait_time_max_single_vehicle"] > DEFAULT_STARVATION_THRESHOLD_S
        }
        # RISING-EDGE: count each lane's ENTRY into starvation as one event
        # (§15.2 starvation_events_count) — a lane already starved does not
        # re-count until it drops back under the 90s threshold.
        self._starvation_events += len(now_starved - self._starved_lanes)
        self._starved_lanes = now_starved

    def _stats_payload(self, info: dict) -> dict:
        snap = self._env.twin.snapshot
        return {
            "sim_time": snap["sim_time"],
            "mode": self._mode,
            "baseline_mode": self._baseline,
            "lane_counts": list(self._lane_counts),
            "lanes": {
                lane_id: {
                    "junction_id": jid,
                    "approach": r["approach"],
                    "vehicle_count": r["vehicle_count"],
                    "halted_count": r["halted_count"],
                    "wait_time_current": r["wait_time_current"],
                    "wait_time_max_single_vehicle": r["wait_time_max_single_vehicle"],
                    "starvation_flag": r["starvation_flag"],
                }
                for jid, lane_id, r in self._all_lanes(snap)
            },
            "wait_time_variance_across_lanes": round(self._wait_var, 4),
            "mean_wait_max": round(self._mean_wait_max, 4),
            "starvation_events_total": self._starvation_events,
            "throughput_total": info["arrived_total"],
            "lane_bias": {
                l: {"weight": w, "expires_sim_time": round(e, 1)}
                for l, (w, e) in self._bias.items()
            },
            "forced_emergency_lanes": sorted(self._forced),
        }

    # ------------------------------------------------------------------
    # §13.2 frame
    # ------------------------------------------------------------------
    def _emit_junction(self, entries: list, info: dict):
        """Pick the ONE §12.1 entry this frame carries (§13.2 has one
        `decision` field; `DecisionLog` records one entry per junction).

        Pure selector over the entries `record_step` just returned — it
        derives nothing and rewrites nothing, so what the frame shows is
        literally what the log holds. Order:

          1. a §10 override, emergency outranking the starvation ceiling,
             ties broken by corridor index (J1 < J2 < J3)
          2. else an operator force_phase this step (reason=voice_command),
             so a manual intervention is always visible on the panel
          3. else the lowest-index junction that switched this step
          4. else rotate by step index, so the panel stays live

        Rules 1/3/4 are the rule the deleted adapter used; rule 2 was added
        with §13.1 force_phase (2026-08-31).
        """
        by_j = {e.junction_id: e for e in entries}
        for rule in (RULE_EMERGENCY, RULE_STARVATION):
            hits = sorted(
                (e.junction_id for e in entries
                 if e.override is not None and e.override["rule"] == rule),
                key=lambda j: _JUNCTION_ORDER[j],
            )
            if hits:
                return by_j[hits[0]]
        voiced = sorted(
            (e.junction_id for e in entries
             if e.reason == REASON_VOICE_COMMAND),
            key=lambda j: _JUNCTION_ORDER[j],
        )
        if voiced:
            return by_j[voiced[0]]
        switched = [j for j in info["switched_junctions"] if j in by_j]
        if switched:
            return by_j[sorted(switched, key=lambda j: _JUNCTION_ORDER[j])[0]]
        rotated = CORRIDOR_JUNCTIONS[self._step_idx % len(CORRIDOR_JUNCTIONS)]
        return by_j.get(rotated) or entries[0]

    def _responder_messages(self, closed: list) -> list[dict]:
        """§11.2 messages for clearance episodes that closed this step.

        Only for episodes that actually resolved to a green —
        `build_responder_message` raises on an unresolved one, and an
        episode that never reached green has no clearance time to report.
        Baseline uses the same worst-case convention as the Phase 8 harness
        (`age=0`, the junction's own green-phase count), so the live number
        and the offline one mean the same thing.
        """
        out: list[dict] = []
        for ev in closed:
            if ev.clearance_time_s is None:
                continue
            n_green = len(self._served.get(ev.junction_id, {})) or 1
            out.append(build_responder_message(
                ev, estimate_baseline_clearance_s(0.0, n_green)
            ))
        return out

    def _predictions(self, snap: dict) -> dict:
        """§8.1 spillover + §8.2 incident-impact for the §13.2 frame.

        ADDITIVE and MATERIAL, same contract as `responder_messages`:

          * `spillover`       — §8.1's list shape, but only the adjacency
            pairs whose forecast moves >= _SPILLOVER_MIN_DELTA vehicles.
            Omitted entirely when nothing is moving (the common case).
          * `incident_impact` — §8.2's shape, one per currently-active
            incident (§7.3). Omitted when there are no active incidents.

        Returns `{}` when neither has anything to say, and the caller then
        omits the whole `predictions` key.
        """
        out: dict = {}

        spill = [
            f for f in self._spillover_view.forecast(snap)
            if abs(f["predicted_queue_delta"]) >= _SPILLOVER_MIN_DELTA
        ]
        if spill:
            out["spillover"] = spill

        incidents = [
            predict_incident_impact(inc)
            for inc in snap.get("active_incidents", [])
        ]
        if incidents:
            out["incident_impact"] = incidents

        return out

    def _assemble_frame(self, info: dict, entries: list, closed: list,
                        shadow: dict | None = None) -> dict:
        # POST-step snapshot — what the dashboard draws. The PRE-step one
        # went to record_step(); see this module's docstring.
        snap = self._env.twin.snapshot
        entry = self._emit_junction(entries, info)
        frame = {
            "sim_time": snap["sim_time"],
            "digital_twin": snap,
            "decision": entry.to_dict(),
            "narration": narrate(entry),
            "metrics_snapshot": {
                # §15.2's pinned set. avg_wait dropped (2026-08-28): it was
                # computed from wait_time_current, a per-lane SUM, which
                # scales with occupancy rather than fairness.
                "wait_time_variance_across_lanes": round(self._wait_var, 4),
                "mean_wait_max": round(self._mean_wait_max, 4),
                "starvation_events_total": self._starvation_events,
                "throughput_total": info["arrived_total"],
            },
        }
        # §8.1 spillover + §8.2 incident impact — ADDITIVE and only when
        # material (see _predictions), same contract as responder_messages.
        preds = self._predictions(snap)
        if preds:
            frame["predictions"] = preds
        # §11.2 responder messaging — ADDITIVE and only when non-empty, so
        # the frozen §13.2 five-key shape is unchanged on the vast majority
        # of frames and no consumer has to handle an empty list.
        messages = self._responder_messages(closed)
        if messages:
            frame["responder_messages"] = messages
        # §13.2 `shadow_advisor` — ADDITIVE third key, present only while the
        # advisor is on and healthy. READ-ONLY: nothing downstream of this
        # line, and nothing in the control path, consumes it.
        if shadow is not None:
            frame["shadow_advisor"] = shadow
        return _jsonable(frame)
