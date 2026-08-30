"""Verification harness for the §13.2 `shadow_advisor` field (S1-S6).

The shadow advisor runs the §9.5 MARL checkpoint's forward pass alongside the
deployed Stage 4 policy every decision step and rides the §13.2 frame as an
ADDITIVE third top-level key. It is READ-ONLY: it never reaches `env.step()`
and the deployed policy drives the road unconditionally.

Read `backend/sim_runner.py`'s DEFAULT_SHADOW_CHECKPOINT honesty note before
building anything on this field. Short version: `graph_attention` @51,624 is
the WORSE policy, on the demo corridor specifically (4 starvation events /
1 override / 121-125s worst, against Stage 4's 0 / 0 / 38-42s). This field
shows what the MARL architecture WOULD have done, not a better idea being
ignored.

THE SIX CHECKS, and what each would actually catch:

  S1  (no SUMO)  a stubbed shadow model produces the full documented payload
                 for all 3 junctions, with agreement computed against the
                 DEPLOYED PRE-SHIELD proposal.
  S2  (no SUMO)  a RAISING shadow model disables the advisor cleanly, returns
                 None, and does not propagate. This is the failure-isolation
                 guarantee: a broken advisor must not touch the sim thread.
  S3  (no SUMO)  Stage 4 predicts solo -> actions recorded -> the graph
                 attention checkpoint is loaded and interleaved -> Stage 4
                 re-predicts -> BIT-EQUAL. `MaskablePPO.load()` calls
                 `set_random_seed()`, which reseeds Python's `random`, numpy's
                 global RNG and torch — so "loading a second model cannot move
                 the deployed policy's decisions" is a claim worth measuring
                 rather than assuming.
  S4  (live)     two consecutive PRE-STEP `env.action_masks()` calls are equal.
                 The advisor calls `action_masks()` a second time (the spec
                 forbids modifying `_pick_action()`), so the mask it hands the
                 shadow model must be the one the deployed policy just used.
                 Sampled across min-green-locked and mid-yellow steps, which
                 are the states where the mask is at its most dynamic.
  S5  (live)     the frame's `shadow_advisor` is well-formed for all 3
                 junctions and every `recommended_phase` is mask-valid.
  S6  (live)     PAIRED EQUALITY. Two SimRunners, run SEQUENTIALLY (never
                 concurrently — TraCI is process-global), same seed and same
                 pinned scenario, one with the advisor off and one on. The
                 (sim_time, junction_id, phase_selected, reason) sequences and
                 the throughput series must be IDENTICAL. This is the check
                 that actually proves "advisory" — S1/S2 test the payload,
                 S6 tests that turning the feature on changed nothing.

S6 captures frames via `frame_sink` DIRECTLY rather than over the WebSocket:
`Hub`'s per-client queue is bounded and drops frames for a slow consumer, so a
WebSocket capture could produce two unequal sequences for a reason that has
nothing to do with the advisor — a test failing (or passing) while proving
nothing, which is this repo's named failure mode.

Not part of §6's folder structure — verification scaffolding, same category as
sim/run_backend_smoke.py and sim/run_tier0_episode.py.

    python sim/run_shadow_advisor_check.py            # all six
    python sim/run_shadow_advisor_check.py --s1 --s3   # a subset
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from backend.control_api import ControlState  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.sim_runner import (  # noqa: E402
    DEFAULT_CHECKPOINT,
    DEFAULT_SHADOW_CHECKPOINT,
    SHADOW_COORDINATION_MODE,
    SimRunner,
)
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

RULE = "=" * 78
_passed = 0
_failed = 0

# The exact §13.2 payload contract, pinned here so a silent field rename in
# sim_runner.py fails this harness rather than a frontend.
SHADOW_KEYS = {
    "advisory_only", "drives_the_road", "coordination_mode", "checkpoint",
    "recommended_phase", "deployed_proposed_phase", "executed_phase",
    "agrees_with_deployed", "agreement_count", "n_junctions",
    "episode_agreement_rate", "inference_ms",
}
_JSET = set(CORRIDOR_JUNCTIONS)


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {label}" + (f"  --  {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def section(title: str) -> None:
    print()
    print(RULE)
    print(title)
    print(RULE)


# ---------------------------------------------------------------------------
# S1 / S2 — payload shape and failure isolation. Pure, no SUMO.
# ---------------------------------------------------------------------------
class _StubShadow:
    """Returns a fixed recommendation, so agreement is hand-checkable."""

    def __init__(self, action):
        self._action = np.asarray(action, dtype=int)

    def predict(self, obs, action_masks=None, deterministic=True):
        return self._action, None


class _RaisingShadow:
    def predict(self, obs, action_masks=None, deterministic=True):
        raise RuntimeError("synthetic shadow-model failure (S2)")


def _bare_runner(shadow_model, *, enabled=True) -> SimRunner:
    """A SimRunner with only the attributes _shadow_advice touches.

    `__new__` bypasses __init__ so no thread starts, no env is built and no
    SUMO process is launched.
    """
    r = SimRunner.__new__(SimRunner)
    r._shadow_model = shadow_model
    r._shadow_enabled = enabled
    r._shadow_agree = 0
    r._shadow_slots = 0
    r.shadow_checkpoint = DEFAULT_SHADOW_CHECKPOINT
    return r


def s1_payload_shape() -> None:
    section("S1  (no SUMO)  stubbed shadow model -> full payload, 3 junctions")

    # Deployed proposes [0,1,0]; shadow recommends [0,2,0] -> agrees on J1/J3.
    r = _bare_runner(_StubShadow([0, 2, 0]))
    masks = np.array([1, 1, 1] * 3, dtype=bool)
    p = r._shadow_advice(np.zeros((3, 191), dtype=np.float32), masks,
                         np.array([0, 1, 0]))

    check("S1 payload carries exactly the documented §13.2 key set",
          p is not None and set(p) == SHADOW_KEYS,
          f"missing={sorted(SHADOW_KEYS - set(p or {}))} "
          f"extra={sorted(set(p or {}) - SHADOW_KEYS)}")
    check("S1 the advisory flags are asserted on the wire, every frame",
          p["advisory_only"] is True and p["drives_the_road"] is False
          and p["coordination_mode"] == SHADOW_COORDINATION_MODE
          and p["checkpoint"] == DEFAULT_SHADOW_CHECKPOINT.name,
          f"mode={p['coordination_mode']!r} ckpt={p['checkpoint']!r}")
    check("S1 all three per-junction maps cover all 3 junctions",
          set(p["recommended_phase"]) == _JSET
          and set(p["deployed_proposed_phase"]) == _JSET
          and set(p["agrees_with_deployed"]) == _JSET
          and p["n_junctions"] == len(CORRIDOR_JUNCTIONS),
          f"recommended={p['recommended_phase']}")
    check("S1 recommendation and deployed proposal are read off correctly",
          p["recommended_phase"] == {"J1": 0, "J2": 2, "J3": 0}
          and p["deployed_proposed_phase"] == {"J1": 0, "J2": 1, "J3": 0},
          f"{p['recommended_phase']} vs {p['deployed_proposed_phase']}")
    check("S1 agreement is per-junction equality of the two PRE-SHIELD "
          "proposals, and agreement_count matches it",
          p["agrees_with_deployed"] == {"J1": True, "J2": False, "J3": True}
          and p["agreement_count"] == 2,
          f"agrees={p['agrees_with_deployed']} count={p['agreement_count']}")
    check("S1 executed_phase is a placeholder pre-step (the loop fills it "
          "from info after step()), and inference_ms was measured",
          p["executed_phase"] == {} and isinstance(p["inference_ms"], float)
          and p["inference_ms"] >= 0.0,
          f"inference_ms={p['inference_ms']}")

    # Second step: 2 of 3 agreed, then 3 of 3 -> 5/6 cumulative.
    p2 = r._shadow_advice(np.zeros((3, 191), dtype=np.float32), masks,
                          np.array([0, 2, 0]))
    check("S1 episode_agreement_rate accumulates across steps "
          "(2/3 then 3/3 -> 5/6)",
          abs(p["episode_agreement_rate"] - 2 / 3) < 1e-12
          and abs(p2["episode_agreement_rate"] - 5 / 6) < 1e-12,
          f"{p['episode_agreement_rate']:.6f} -> "
          f"{p2['episode_agreement_rate']:.6f}")

    # And the per-episode reset actually zeroes them (the counters live in
    # _reset_counters alongside every other per-episode counter).
    r._shadow_agree = 0
    r._shadow_slots = 0
    p3 = r._shadow_advice(np.zeros((3, 191), dtype=np.float32), masks,
                          np.array([0, 2, 0]))
    check("S1 the rate restarts from the episode boundary, not from the run",
          abs(p3["episode_agreement_rate"] - 1.0) < 1e-12,
          f"{p3['episode_agreement_rate']:.6f}")


def s2_failure_isolation() -> None:
    section("S2  (no SUMO)  a raising shadow model disables cleanly")

    r = _bare_runner(_RaisingShadow())
    masks = np.array([1, 1, 1] * 3, dtype=bool)

    raised = None
    try:
        out = r._shadow_advice(np.zeros((3, 191), dtype=np.float32), masks,
                               np.array([0, 1, 0]))
    except Exception as exc:                    # pragma: no cover - the bug
        out, raised = "PROPAGATED", exc

    check("S2 the exception does NOT propagate to the sim loop",
          raised is None,
          "clean" if raised is None else f"{type(raised).__name__}: {raised}")
    check("S2 _shadow_advice returns None, so the frame key is omitted",
          out is None, f"returned {out!r}")
    check("S2 the advisor latches OFF (model dropped, flag cleared)",
          r._shadow_enabled is False and r._shadow_model is None,
          f"enabled={r._shadow_enabled} model={r._shadow_model!r}")

    # Latched: a later call short-circuits on the guard, so the traceback is
    # printed exactly once however long the demo runs.
    out2 = r._shadow_advice(np.zeros((3, 191), dtype=np.float32), masks,
                            np.array([0, 1, 0]))
    check("S2 subsequent steps short-circuit (one log, not one per step)",
          out2 is None and r._shadow_enabled is False)

    # An advisor that was never enabled is silent, not an error.
    off = _bare_runner(None, enabled=False)
    check("S2 an advisor that is off returns None without touching anything",
          off._shadow_advice(np.zeros((3, 191), dtype=np.float32), masks,
                             np.array([0, 1, 0])) is None)


# ---------------------------------------------------------------------------
# S3 — loading + running the shadow model does not move Stage 4's decisions.
# Pure, no SUMO: synthetic observations and masks, real checkpoints.
# ---------------------------------------------------------------------------
def s3_deployed_actions_unmoved(n: int = 24) -> None:
    section("S3  (no SUMO)  Stage 4's decisions are bit-equal with the "
            "shadow loaded and interleaved")

    from sb3_contrib import MaskablePPO

    if not DEFAULT_CHECKPOINT.exists() or not DEFAULT_SHADOW_CHECKPOINT.exists():
        check("S3 both checkpoints present", False,
              f"deployed={DEFAULT_CHECKPOINT.exists()} "
              f"shadow={DEFAULT_SHADOW_CHECKPOINT.exists()}")
        return

    # Fixed synthetic inputs — the point is to hold the INPUT constant and vary
    # only whether the shadow model exists, so the observations need to be
    # in-range and reproducible, not realistic.
    rng = np.random.default_rng(20260830)
    obs_batch = [rng.uniform(-1.0, 1.0, size=(3, 191)).astype(np.float32)
                 for _ in range(n)]
    # Every junction keeps >=1 valid slot (MaskablePPO requires it); slot 2 is
    # masked on a third of the steps so the mask is not a constant either.
    masks_batch = []
    for i in range(n):
        m = np.ones(9, dtype=bool)
        if i % 3 == 0:
            m[2] = m[5] = m[8] = False
        masks_batch.append(m)

    deployed = MaskablePPO.load(str(DEFAULT_CHECKPOINT))
    before = [np.asarray(deployed.predict(o, action_masks=m,
                                          deterministic=True)[0], dtype=int)
              for o, m in zip(obs_batch, masks_batch)]

    # Load the shadow AFTER, exactly as the backend does. MaskablePPO.load()
    # reseeds Python/numpy/torch global RNGs; this is what that would break.
    shadow = MaskablePPO.load(str(DEFAULT_SHADOW_CHECKPOINT))
    check("S3 the shadow checkpoint carries the §9.5 attention extractor",
          type(shadow.policy.features_extractor).__name__
          == "GraphAttentionExtractor",
          type(shadow.policy.features_extractor).__name__)
    check("S3 the deployed checkpoint is the single-agent one (§20: the demo "
          "runs SINGLE-AGENT PPO)",
          type(deployed.policy.features_extractor).__name__
          == "FlattenExtractor",
          type(deployed.policy.features_extractor).__name__)

    # Interleaved, in the backend's own order: shadow forward pass, then the
    # deployed one re-run on the identical input.
    after = []
    shadow_actions = []
    for o, m in zip(obs_batch, masks_batch):
        shadow_actions.append(
            np.asarray(shadow.predict(o, action_masks=m,
                                      deterministic=True)[0], dtype=int))
        after.append(
            np.asarray(deployed.predict(o, action_masks=m,
                                        deterministic=True)[0], dtype=int))

    equal = all(np.array_equal(a, b) for a, b in zip(before, after))
    diffs = [(i, before[i].tolist(), after[i].tolist())
             for i in range(n) if not np.array_equal(before[i], after[i])]
    check(f"S3 Stage 4's {n} decisions are BIT-EQUAL before and after the "
          f"shadow model is loaded and interleaved",
          equal, "identical" if equal else f"{len(diffs)} differ: {diffs[:3]}")

    # The check would be vacuous if the two policies simply always agreed —
    # then "unchanged" would be indistinguishable from "overwritten by the
    # shadow". Assert they genuinely differ somewhere.
    n_diff = sum(1 for a, b in zip(before, shadow_actions)
                 if not np.array_equal(a, b))
    check("S3 the two policies DO disagree on this input set, so the "
          "bit-equality above is not vacuous",
          n_diff > 0, f"{n_diff}/{n} steps differ between the two policies")

    # Every masked slot must be respected by both — a mask violation here
    # would make S5's live mask-validity claim meaningless.
    def mask_ok(actions):
        return all(bool(m[3 * j + int(a[j])])
                   for a, m in zip(actions, masks_batch) for j in range(3))
    check("S3 both policies respect the action mask on every step",
          mask_ok(before) and mask_ok(shadow_actions))


# ---------------------------------------------------------------------------
# S4 — two consecutive pre-step action_masks() calls are equal. LIVE, but on
# a standalone single-threaded env: calling action_masks() from a test thread
# against the backend's env would be a cross-thread TraCI call, which the
# CLAUDE.md §8 standing rule forbids.
# ---------------------------------------------------------------------------
def s4_action_masks_stable(steps: int = 60) -> None:
    section("S4  (live)  two consecutive pre-step env.action_masks() calls "
            "are equal")

    from env.psychoflow_env import PsychoFlowEnv, ScenarioConfig
    from prediction.spillover import SpilloverPredictor

    env = PsychoFlowEnv(
        scenario_config=ScenarioConfig(
            lane_counts=(4, 3, 2), randomize_lane_counts=False,
            randomize_density=False, spawn_emergencies=False),
        spillover_predictor=SpilloverPredictor(),
        seed=7,
    )
    try:
        env.reset()
        mismatches = []
        distinct = set()
        rng = np.random.default_rng(11)
        for i in range(steps):
            a = env.action_masks()
            b = env.action_masks()           # the advisor's second read
            if not np.array_equal(a, b):
                mismatches.append((i, a.tolist(), b.tolist()))
            distinct.add(tuple(bool(x) for x in a))
            # Step with a mask-valid action so the run visits genuinely
            # different mask regimes (min-green locked, mid-yellow, free).
            action = []
            for j in range(3):
                valid = [s for s in range(3) if a[3 * j + s]]
                action.append(int(rng.choice(valid)))
            _o, _r, term, trunc, _i = env.step(np.array(action, dtype=int))
            if term or trunc:
                break
        check(f"S4 across {steps} live decision steps, the second "
              f"action_masks() call always equals the first",
              not mismatches,
              "identical" if not mismatches else f"{len(mismatches)} differ: "
                                                 f"{mismatches[:2]}")
        # If the mask never changed, equality would be trivially true and the
        # check would prove nothing about a DYNAMIC mask.
        check("S4 the mask genuinely varied over the run, so the equality "
              "above is not trivially true",
              len(distinct) > 1, f"{len(distinct)} distinct mask patterns")
    finally:
        env.close()


# ---------------------------------------------------------------------------
# S5 — the live frame's shadow_advisor is well-formed and mask-valid.
# ---------------------------------------------------------------------------
def s5_live_frame(n_frames: int = 40) -> None:
    section("S5  (live)  the §13.2 frame's shadow_advisor is well-formed and "
            "every recommended_phase is mask-valid")

    from fastapi.testclient import TestClient

    app = create_app(
        checkpoint=DEFAULT_CHECKPOINT,
        shadow_checkpoint=DEFAULT_SHADOW_CHECKPOINT,
        lane_counts=(4, 3, 2),
        randomize_density=False,
        spawn_emergencies=False,
        realtime_factor=0.0,
        fast=True,
    )
    runner = app.state.runner
    with TestClient(app) as client:
        deadline = time.time() + 120.0
        health = {}
        while time.time() < deadline:
            health = client.get("/health").json()
            if health.get("sim_error"):
                raise RuntimeError(f"sim thread crashed:\n{health['sim_error']}")
            if health.get("sim_ready"):
                break
            time.sleep(0.5)
        check("S5 sim thread came up with the advisor loaded",
              bool(health.get("sim_ready")) and not health.get("sim_error"))

        with client.websocket_connect("/ws") as ws:
            frames = [ws.receive_json() for _ in range(n_frames)]

        # `_served` is {junction: {green slot: lanes}} — a plain per-episode
        # dict on the runner, REPLACED not mutated, so reading it here is not
        # a TraCI call and not a cross-thread mutation hazard.
        served = runner._served

        present = [f for f in frames if "shadow_advisor" in f]
        check(f"S5 shadow_advisor rides every live frame "
              f"({len(present)}/{len(frames)})",
              len(present) == len(frames),
              f"{len(frames) - len(present)} frames lacked the key")

        bad_keys, bad_flags, bad_maps, bad_agree, bad_rate, bad_mask = (
            [], [], [], [], [], [])
        for f in present:
            s = f["shadow_advisor"]
            if set(s) != SHADOW_KEYS:
                bad_keys.append(sorted(set(s) ^ SHADOW_KEYS))
            if not (s["advisory_only"] is True
                    and s["drives_the_road"] is False
                    and s["coordination_mode"] == SHADOW_COORDINATION_MODE
                    and s["checkpoint"] == DEFAULT_SHADOW_CHECKPOINT.name):
                bad_flags.append(f["sim_time"])
            if not (set(s["recommended_phase"]) == _JSET
                    and set(s["deployed_proposed_phase"]) == _JSET
                    and set(s["executed_phase"]) == _JSET
                    and set(s["agrees_with_deployed"]) == _JSET
                    and s["n_junctions"] == 3):
                bad_maps.append(f["sim_time"])
            expect = {j: s["recommended_phase"][j] == s["deployed_proposed_phase"][j]
                      for j in CORRIDOR_JUNCTIONS}
            if (s["agrees_with_deployed"] != expect
                    or s["agreement_count"] != sum(expect.values())):
                bad_agree.append(f["sim_time"])
            if not 0.0 <= s["episode_agreement_rate"] <= 1.0:
                bad_rate.append(f["sim_time"])
            for j, slot in s["recommended_phase"].items():
                if slot not in served.get(j, {}):
                    bad_mask.append((f["sim_time"], j, slot,
                                     sorted(served.get(j, {}))))

        check("S5 every frame carries exactly the documented key set",
              not bad_keys, str(bad_keys[:2]))
        check("S5 advisory_only / drives_the_road / coordination_mode / "
              "checkpoint are correct on every frame",
              not bad_flags, str(bad_flags[:3]))
        check("S5 all four per-junction maps cover J1/J2/J3 on every frame "
              "(executed_phase included, i.e. the post-step fill ran)",
              not bad_maps, str(bad_maps[:3]))
        check("S5 agrees_with_deployed / agreement_count are internally "
              "consistent with the two proposals on every frame",
              not bad_agree, str(bad_agree[:3]))
        check("S5 episode_agreement_rate stays in [0, 1]",
              not bad_rate, str(bad_rate[:3]))
        # Mask-validity: a recommended slot must be a REAL green phase of that
        # junction. This is the padding half of §9.2's masking (J3 is 2-lane
        # and has only 2 green slots against MAX_PHASES=3). The dynamic half
        # (min-green / mid-yellow) cannot be violated by construction —
        # MaskablePPO applies the mask to the logits before the argmax — and
        # S3 confirms that holds for this checkpoint on masked inputs.
        check("S5 every recommended_phase is a real green slot for its "
              "junction (mask-valid; J3 has only 2)",
              not bad_mask, str(bad_mask[:3]))

        rates = [f["shadow_advisor"]["episode_agreement_rate"] for f in present]
        counts = [f["shadow_advisor"]["agreement_count"] for f in present]
        infer = [f["shadow_advisor"]["inference_ms"] for f in present]
        print(f"       observed: agreement_count {min(counts)}-{max(counts)} "
              f"of 3, episode_agreement_rate {rates[-1]:.3f} at the last "
              f"frame, inference {min(infer):.2f}-{max(infer):.2f} ms")
        check("S5 the two policies genuinely disagree somewhere on this run, "
              "so the agreement fields are not a constant",
              min(counts) < 3,
              f"min agreement_count={min(counts)}")
        check("S5 slot map used for the mask check is non-empty",
              all(served.get(j) for j in CORRIDOR_JUNCTIONS),
              {j: sorted(served.get(j, {})) for j in CORRIDOR_JUNCTIONS})


# ---------------------------------------------------------------------------
# S6 — paired equality. Two SimRunners, SEQUENTIALLY.
# ---------------------------------------------------------------------------
def _capture_run(shadow_checkpoint, n_frames: int) -> list[dict]:
    """Run one SimRunner to `n_frames` frames and return them.

    Frames are taken from `frame_sink` DIRECTLY, not over the WebSocket: the
    Hub's per-client queue is bounded and drops frames for a slow consumer,
    which would make the two sequences incomparable for a reason that has
    nothing to do with the shadow advisor.
    """
    frames: list[dict] = []
    done = threading.Event()

    def sink(frame: dict) -> None:
        if not done.is_set():
            frames.append(frame)
            if len(frames) >= n_frames:
                done.set()

    state = ControlState()
    runner = SimRunner(
        state,
        checkpoint=DEFAULT_CHECKPOINT,
        shadow_checkpoint=shadow_checkpoint,
        lane_counts=(4, 3, 2),
        randomize_density=False,
        spawn_emergencies=False,
        fast=True,
        seed=7,
        frame_sink=sink,
    )
    runner.start()
    try:
        if not runner.wait_until_ready(timeout=120.0):
            raise TimeoutError("sim thread never became ready")
        if runner.error:
            raise RuntimeError(f"sim thread crashed:\n{runner.error}")
        if not done.wait(timeout=300.0):
            raise TimeoutError(
                f"only {len(frames)}/{n_frames} frames after 300s "
                f"(error={runner.error})")
    finally:
        runner.stop()
    if runner.error:
        raise RuntimeError(f"sim thread crashed:\n{runner.error}")
    return frames[:n_frames]


def _signature(frames: list[dict]) -> list[tuple]:
    return [(f["sim_time"], f["decision"]["junction_id"],
             f["decision"]["phase_selected"], f["decision"]["reason"])
            for f in frames]


def s6_paired_equality(n_frames: int = 120) -> None:
    section("S6  (live)  paired equality — advisor OFF vs ON changes nothing")

    print(f"  run 1/2: shadow advisor OFF ({n_frames} frames) ...")
    off = _capture_run(None, n_frames)
    print(f"  run 2/2: shadow advisor ON  ({n_frames} frames) ...")
    # SEQUENTIAL by construction: _capture_run has already stopped run 1's
    # thread and closed its env. TraCI is process-global; two live SimRunners
    # would collide, not run in parallel.
    on = _capture_run(DEFAULT_SHADOW_CHECKPOINT, n_frames)

    check("S6 both runs produced the full frame budget",
          len(off) == len(on) == n_frames, f"off={len(off)} on={len(on)}")
    check("S6 the advisor was actually ON in run 2 and OFF in run 1 "
          "(otherwise the comparison is vacuous)",
          all("shadow_advisor" not in f for f in off)
          and all("shadow_advisor" in f for f in on),
          f"off has key: {sum('shadow_advisor' in f for f in off)}; "
          f"on has key: {sum('shadow_advisor' in f for f in on)}")

    sig_off, sig_on = _signature(off), _signature(on)
    first_diff = next((i for i, (a, b) in enumerate(zip(sig_off, sig_on))
                       if a != b), None)
    check("S6 the (sim_time, junction_id, phase_selected, reason) sequences "
          "are IDENTICAL",
          sig_off == sig_on,
          "identical" if first_diff is None
          else f"first differs at {first_diff}: "
               f"{sig_off[first_diff]} vs {sig_on[first_diff]}")

    thr_off = [f["metrics_snapshot"]["throughput_total"] for f in off]
    thr_on = [f["metrics_snapshot"]["throughput_total"] for f in on]
    check("S6 the throughput series are IDENTICAL",
          thr_off == thr_on,
          f"off[-1]={thr_off[-1]} on[-1]={thr_on[-1]}")

    # The executed action is what actually reached the road. Comparing it
    # directly closes the "the decision field happened to match but the road
    # differed" gap that the signature alone leaves open.
    exec_off = [tuple(sorted(f["digital_twin"]["junctions"][j]["current_phase"]
                             for j in CORRIDOR_JUNCTIONS)) for f in off]
    exec_on = [tuple(sorted(f["digital_twin"]["junctions"][j]["current_phase"]
                            for j in CORRIDOR_JUNCTIONS)) for f in on]
    check("S6 the live signal phases on the road are IDENTICAL "
          "(the advisor changed nothing about what was actuated)",
          exec_off == exec_on,
          "identical" if exec_off == exec_on else "DIVERGED")

    n_dis = sum(1 for f in on if f["shadow_advisor"]["agreement_count"] < 3)
    print(f"       the advisor disagreed with the deployed policy on "
          f"{n_dis}/{len(on)} frames and changed nothing on any of them")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    for flag in ("s1", "s2", "s3", "s4", "s5", "s6"):
        ap.add_argument(f"--{flag}", action="store_true")
    args = ap.parse_args()
    selected = [f for f in ("s1", "s2", "s3", "s4", "s5", "s6")
                if getattr(args, f)] or ["s1", "s2", "s3", "s4", "s5", "s6"]

    print(RULE)
    print("SHADOW ADVISOR CHECK (§13.2 `shadow_advisor`)  —  S1-S6")
    print(f"  deployed : {DEFAULT_CHECKPOINT.name} "
          f"({'present' if DEFAULT_CHECKPOINT.exists() else 'MISSING'})")
    print(f"  shadow   : {DEFAULT_SHADOW_CHECKPOINT.name} "
          f"({'present' if DEFAULT_SHADOW_CHECKPOINT.exists() else 'MISSING'})")
    print("  the shadow is the WORSE policy (4a bake-off) — advisory only")
    print(RULE)

    runners = {
        "s1": s1_payload_shape, "s2": s2_failure_isolation,
        "s3": s3_deployed_actions_unmoved, "s4": s4_action_masks_stable,
        "s5": s5_live_frame, "s6": s6_paired_equality,
    }
    for name in selected:
        runners[name]()

    print()
    print(RULE)
    print(f"  {_passed} passed, {_failed} failed   (ran: {', '.join(selected)})")
    print(RULE)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    # Tier 1 SUMO beacon (sim/sumo_activity.py): S4/S5/S6 launch SUMO, so
    # refuse to start while a training run or the backend is already live.
    from sim.sumo_activity import require_free

    require_free("shadow advisor check (S1-S6)")
    main()
