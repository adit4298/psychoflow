"""Live SUMO instance driven by the trained agent, on one dedicated thread (§13).

`SimRunner` owns a `PsychoFlowEnv` and is the ONLY code that calls `env.step()`,
`env.reset()` or anything that touches TraCI — TraCI is process-global and
single-threaded, so everything else (the FastAPI handlers, the WebSocket fan-out)
stays off it. The loop each iteration:

    1. drain queued control commands (§13.1) and apply them
    2. expire lane biases / forced emergencies whose window has passed
    3. pick an action  — Tier 0 (§9.1) in manual mode, the trained policy in auto
    4. env.step(action) — the §10 validator runs INSIDE this call, unchanged
    5. assemble the §13.2 frame, hand it to frame_sink, publish get_stats() cache
    6. on episode end, reset and keep going (a demo runs continuously)
    7. sleep to pace wall-clock

The §13.2 frame's `decision` / `narration` fields are Phase 8's territory
(§12.1 / §12.2).

STATUS (updated 2026-08-28): PHASE 8 HAS NOW LANDED (commit 9cf19af) — this
docstring previously said it "has not landed", which is no longer true. The real
`explainability/{decision_log,narrator,query_interface}.py` modules exist and
their done-bar passes. The adapter below has NOT yet been swapped out for them;
that reconciliation is the remaining work at this seam. The wire schema does not
move when it happens.

Until that swap, `_decision_entry` / `_narrate` below are a thin adapter that
produces the frozen §12.1 / §12.2 shapes from data that already exists today
(`Tier0Controller.act`'s `decisions`, `info["safety_overrides"]`,
`info["reward_breakdown"]`). Every spot where Phase 8's contract has the final
say is marked `# PHASE 8 SEAM`.

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

from agents.rule_based import REASON_COUNT, REASON_STARVATION, Tier0Controller
from backend.control_api import EMERGENCY_HOLD_S, Command, ControlState
from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig
from perception.lane_sensor import DEFAULT_STARVATION_THRESHOLD_S
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

_JUNCTION_ORDER = {jid: i for i, jid in enumerate(CORRIDOR_JUNCTIONS)}

# How often the sim thread refreshes the Tier 1 SUMO beacon. Well under
# sumo_activity.STALE_AFTER_S so a live backend never reads as stale.
_BEACON_EVERY_S = 20.0

# §12.2's narration templates, verbatim, plus the two reason values §12.2 does
# not itself list. `rl_policy` is CONFIRMED FINAL by Session 2 (Phase 8) — exact
# string, asserted in their decision_log._selftest. `starvation_ceiling` wording
# is still a Phase 8 placeholder.
_NARRATION = {
    REASON_STARVATION: "Lane {lane}, {direction} — selected. Wait threshold crossed.",
    REASON_COUNT: "Lane {lane}, {direction} — selected. Highest vehicle count.",
    RULE_EMERGENCY: "Emergency override — {direction} cleared for ambulance.",
    "voice_command": "Voice command received: '{transcript}' -> {action_taken}.",
    # PHASE 8 SEAM: not in §12.2; placeholder wording.
    RULE_STARVATION: "Lane {lane}, {direction} — forced green. Starvation ceiling reached.",
    # Lane is CONTEXT (busiest served lane), not the stated cause — the
    # trained policy's actual reason is opaque. Mirrors explainability/narrator.
    "rl_policy": "Trained policy selected phase {phase} "
                 "(busiest served lane: {lane}, {direction}).",
}


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
        self._tier0 = Tier0Controller()
        self._served: dict[str, dict[int, frozenset[str]]] = {}
        self._obs = None
        self._mode = state.mode
        self._baseline = state.baseline_mode
        self._bias: dict[str, tuple[float, float]] = {}     # lane -> (weight, expiry_sim_time)
        self._forced: dict[str, float] = {}                 # lane -> expiry_sim_time
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
        self._starved_lanes = set()
        self._starvation_events = 0
        self._step_vars = []
        self._step_maxes = []
        self._wait_var = 0.0
        self._mean_wait_max = 0.0
        self._step_idx = 0

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._load_model()
            self._build_env()
            self._started.set()
            while not self._stop.is_set():
                # Tier 1 SUMO beacon: the backend owns a live SUMO instance for
                # as long as it runs, so it BEATS like a training run rather
                # than checking like a sweep. Rate-limited to once per
                # _BEACON_EVERY_S: under --fast, _sleep_s is 0.0 and this loop
                # runs flat out, so an unguarded beat would rewrite the file
                # thousands of times a second.
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
                self._env.forced_emergency_lanes = frozenset(self._forced)

                action, decisions = self._pick_action()
                obs, reward, terminated, truncated, info = self._env.step(action)
                self._obs = obs
                self._step_idx += 1

                self._update_metrics(info)
                frame = self._assemble_frame(info, decisions)
                if self._frame_sink is not None:
                    self._frame_sink(frame)
                self.state.publish_stats(self._stats_payload(info))

                if terminated or truncated:
                    print(f"[sim] episode end (terminated={terminated} "
                          f"truncated={truncated}) — resetting")
                    self._obs, _ = self._env.reset()
                    self._served = self._env.phase_served_lanes()
                    self._reset_counters()

                if self._sleep_s:
                    time.sleep(self._sleep_s)
        except Exception:
            self._error = traceback.format_exc()
            print(f"[sim] FATAL:\n{self._error}")
            self._started.set()
        finally:
            if self._env is not None:
                self._env.close()
            _sumo_clear()   # release the Tier 1 beacon when the sim thread exits

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
        elif cmd.kind == "set_topology":
            self._pending_lane_counts = tuple(cmd.args["lane_counts"])
        else:  # pragma: no cover - control_api never emits anything else
            print(f"[sim] ignoring unknown command {cmd.kind!r}")

    def _expire_windows(self) -> None:
        now = self._env._sim_time
        self._bias = {l: (w, e) for l, (w, e) in self._bias.items() if e > now}
        self._forced = {l: e for l, e in self._forced.items() if e > now}

    # ------------------------------------------------------------------
    # action
    # ------------------------------------------------------------------
    def _pick_action(self):
        use_rl = self._mode == "auto" and self._model is not None
        if use_rl:
            masks = self._env.action_masks()
            action, _ = self._model.predict(
                self._obs, action_masks=masks, deterministic=True
            )
            action = np.asarray(action, dtype=int)
            # A per-junction decision row for EVERY junction, even though the
            # trained policy has no Tier-0-style score breakdown. Phase 8's
            # decision_log.record_step drops (now: raises on) any §10 override
            # whose junction is missing from this dict, so an empty/partial
            # dict here would lose the shield's overrides for the deployed
            # policy. reason == decision_log.REASON_RL_POLICY (kept a literal,
            # consistent with _reason_for / _NARRATION in this file; that
            # module asserts the string).
            decisions = {
                jid: {
                    "junction_id": jid,
                    "phase_selected": int(action[i]),
                    "score_breakdown": {},
                    "alternative_scores": {},
                    "reason": "rl_policy",
                }
                for i, jid in enumerate(CORRIDOR_JUNCTIONS)
            }
            return action, decisions

        snap = self._env.twin.snapshot
        runtime = self._env._runtime()
        masks = self._env.action_masks()
        weights = {l: w for l, (w, _e) in self._bias.items()} or None
        action, decisions = self._tier0.act(
            snap, runtime, masks, self._served, lane_weights=weights
        )
        return action, decisions

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
    # §13.2 frame  (decision / narration = PHASE 8 SEAM)
    # ------------------------------------------------------------------
    def _emit_junction(self, info: dict) -> str:
        overrides = info["safety_overrides"]
        for rule in (RULE_EMERGENCY, RULE_STARVATION):
            hits = sorted(
                (o["junction_id"] for o in overrides if o["rule"] == rule),
                key=lambda j: _JUNCTION_ORDER[j],
            )
            if hits:
                return hits[0]
        switched = list(info["switched_junctions"])
        if switched:
            return sorted(switched, key=lambda j: _JUNCTION_ORDER[j])[0]
        # nothing switched this step — rotate so the log stays live
        return CORRIDOR_JUNCTIONS[self._step_idx % len(CORRIDOR_JUNCTIONS)]

    def _reason_for(self, jid: str, info: dict, decisions: dict) -> str:
        overrides = info["safety_overrides"]
        # Mirror Phase 8's reconciliation order exactly: an emergency override
        # outranks a starvation-ceiling override at the same junction.
        for rule in (RULE_EMERGENCY, RULE_STARVATION):
            if any(o["junction_id"] == jid and o["rule"] == rule for o in overrides):
                return rule
        if jid in decisions:
            return decisions[jid]["reason"]
        # Sixth §12.1 reason value, for a no-override decision made by the
        # trained policy. CONFIRMED FINAL by Session 2 (Phase 8): exact string
        # "rl_policy", asserted in their decision_log._selftest.
        return "rl_policy"

    def _decision_entry(self, info: dict, decisions: dict) -> dict:
        snap = self._env.twin.snapshot
        jid = self._emit_junction(info)
        idx = _JUNCTION_ORDER[jid]
        reason = self._reason_for(jid, info, decisions)

        base = decisions.get(jid, {})
        entry = {
            "sim_time": snap["sim_time"],
            "junction_id": jid,
            "phase_selected": int(info["executed_action"][idx]),
            # PHASE 8 SEAM: {} under RL control — Phase 8's decision_log owns
            # the RL-mode breakdown. Populated verbatim from Tier 0 otherwise.
            "score_breakdown": base.get("score_breakdown", {}),
            "alternative_scores": base.get("alternative_scores", {}),
            "reason": reason,
        }
        if reason in (RULE_EMERGENCY, RULE_STARVATION):
            rec = next(
                o for o in info["safety_overrides"]
                if o["junction_id"] == jid and o["rule"] == reason
            )
            entry["override"] = {
                "from_slot": rec["from_slot"], "to_slot": rec["to_slot"],
                "lane_id": rec["lane_id"], "wait_s": round(rec["wait_s"], 3),
                "outcome": rec["outcome"],
            }
        return entry

    def _representative_lane(self, jid: str, slot: int, snap: dict):
        lanes = snap["junctions"][jid]["lanes"]
        served = self._served.get(jid, {}).get(slot, frozenset())
        candidates = [(lid, lanes[lid]) for lid in served if lid in lanes]
        if not candidates:
            return "?", "?"
        lid, reading = max(
            candidates, key=lambda kv: kv[1]["wait_time_max_single_vehicle"]
        )
        return lid, reading["approach"]

    def _narrate(self, entry: dict) -> str:
        snap = self._env.twin.snapshot
        tmpl = _NARRATION.get(entry["reason"])
        lane, direction = self._representative_lane(
            entry["junction_id"], entry["phase_selected"], snap
        )
        if tmpl is None:
            return f"{entry['junction_id']}: phase {entry['phase_selected']} selected."
        try:
            return tmpl.format(
                lane=lane, direction=str(direction).capitalize(),
                phase=entry["phase_selected"], transcript="", action_taken="",
            )
        except Exception:
            return f"{entry['junction_id']}: phase {entry['phase_selected']} selected."

    def _assemble_frame(self, info: dict, decisions: dict) -> dict:
        snap = self._env.twin.snapshot
        decision = self._decision_entry(info, decisions)
        frame = {
            "sim_time": snap["sim_time"],
            "digital_twin": snap,
            "decision": decision,
            "narration": self._narrate(decision),
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
        return _jsonable(frame)
