"""Part 4b — prove the orchestrator cannot change the control path.

    venv/Scripts/python.exe sim/run_orchestrator_check.py   [--o1 --o2]

The claim under test is the one that matters for Part 4b: wiring the
orchestrator into `backend/sim_runner.py` is ADDITIVE — the existing
Tier0/PPO -> §10 validator -> setPhase path behaves identically with it on
and with it off.

The argument has three layers, and this harness is only the third:

  1. STRUCTURAL (the primary argument, and free). The single call site sits
     AFTER `_pick_action()`, AFTER `env.step()` (inside which §10's validator
     already ran), after `record_step()`, after `_coord.observe()`, after
     `_update_metrics()` and after `_assemble_frame()`. Its only write is
     `frame["agent_activity"]`, one statement from `frame_sink`. By the time
     any wrapper runs the phase has already reached the road. No empirical
     run can be stronger than that.
  2. UNIT (cheap). `orchestrator/selftest.py` W5 (deep-equality of the whole
     context across a round), W7 (no heavy import, no numeric literal in
     wrappers.py, no actuation import) and W9 (advisory).
  3. O2 BELOW — paired equality. This exists to catch the one class layers 1
     and 2 structurally cannot: an accidental GLOBAL side effect. That is not
     hypothetical — `sim/run_shadow_advisor_check.py` S3 found exactly one
     for the shadow advisor (`MaskablePPO.load()` calls `set_random_seed()`,
     reseeding Python/numpy/torch). W7 already asserts this package never
     imports `random`, so that specific mechanism is designed out; O2 is the
     belt to that braces.

O2 runs two SimRunners **SEQUENTIALLY — never concurrently**: TraCI is
process-global and two live SUMO connections in one process collide. Frames
are captured through `frame_sink` DIRECTLY rather than over the WebSocket,
because `Hub`'s per-client queue is bounded and drops frames for a slow
consumer — a WebSocket capture could produce two unequal sequences for a
reason having nothing to do with the feature, which is precisely this repo's
documented "a verification run that passes while proving nothing" failure.

HONEST LIMIT: O2 proves equality on the captured frames at the pinned seed
and topology. It is a strong empirical check, not a proof.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.control_api import ControlState                    # noqa: E402
from backend.sim_runner import DEFAULT_CHECKPOINT, SimRunner    # noqa: E402

RULE = "=" * 74
FRAMES = 40
SEED = 7
LANE_COUNTS = (4, 3, 2)

_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  --  {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def _capture(enable_orchestrator: bool, n: int = FRAMES) -> list[dict]:
    """Run ONE SimRunner to completion of `n` frames and return them.

    Sequential by construction: the caller must let this return before
    starting the next run.
    """
    frames: list[dict] = []
    done = threading.Event()

    def sink(frame: dict) -> None:
        if len(frames) < n:
            frames.append(frame)
            if len(frames) >= n:
                done.set()

    runner = SimRunner(
        ControlState(),
        checkpoint=DEFAULT_CHECKPOINT,
        # The shadow advisor is OFF for both arms: it loads a checkpoint, and
        # MaskablePPO.load() reseeds the global RNGs (S3). Leaving it on would
        # add a variable this comparison is not about.
        shadow_checkpoint=None,
        lane_counts=LANE_COUNTS,
        randomize_density=False,
        spawn_emergencies=False,
        fast=True,
        seed=SEED,
        frame_sink=sink,
        enable_orchestrator=enable_orchestrator,
    )
    runner.start()
    if not runner.wait_until_ready(timeout=180.0):
        raise RuntimeError(f"sim thread never became ready: {runner.error}")
    done.wait(timeout=300.0)
    runner.stop()
    if runner.error:
        raise RuntimeError(f"sim thread errored: {runner.error}")
    return frames[:n]


def _signature(frame: dict) -> tuple:
    """Everything about a frame that describes what reached the road."""
    dec = frame["decision"]
    twin = frame["digital_twin"]
    return (
        frame["sim_time"],
        dec["junction_id"], dec["phase_selected"], dec["reason"],
        frame["metrics_snapshot"]["throughput_total"],
        frame["metrics_snapshot"]["starvation_events_total"],
        tuple(twin["junctions"][j]["current_phase"] for j in ("J1", "J2", "J3")),
    )


def o1_offline() -> None:
    """Delegate the no-SUMO argument to the orchestrator's own self-test."""
    print("\nO1  offline checks (delegated to orchestrator.selftest)")
    from orchestrator.selftest import main as selftest_main

    code = selftest_main([])
    check("O1 orchestrator.selftest W1-W9 all pass", code == 0,
          f"exit={code}")


def o2_paired_equality() -> None:
    print("\nO2  paired equality — orchestrator OFF vs ON, same seed")
    print(f"     two SEQUENTIAL runs, {FRAMES} frames each, seed={SEED}, "
          f"topology={''.join(map(str, LANE_COUNTS))}")

    print("     [1/2] capturing with the orchestrator OFF ...")
    off = _capture(enable_orchestrator=False)
    print(f"           {len(off)} frames")
    print("     [2/2] capturing with the orchestrator ON ...")
    on = _capture(enable_orchestrator=True)
    print(f"           {len(on)} frames")

    check("O2 both runs produced the same number of frames",
          len(off) == len(on) == FRAMES, f"{len(off)} vs {len(on)}")

    # ANTI-VACUITY FIRST. Without this the equality below could pass simply
    # because the feature never ran.
    off_has = [i for i, f in enumerate(off) if "agent_activity" in f]
    on_has = [i for i, f in enumerate(on) if "agent_activity" in f]
    check("O2 ANTI-VACUITY: `agent_activity` absent from EVERY off-frame and "
          "present in EVERY on-frame",
          not off_has and len(on_has) == len(on),
          f"off={len(off_has)}/{len(off)}  on={len(on_has)}/{len(on)}")

    sig_off = [_signature(f) for f in off]
    sig_on = [_signature(f) for f in on]
    first_diff = next((i for i, (a, b) in enumerate(zip(sig_off, sig_on))
                       if a != b), None)
    check("O2 the decision / phase / throughput signature series are "
          "IDENTICAL",
          sig_off == sig_on,
          "identical" if first_diff is None
          else f"first divergence at frame {first_diff}: "
               f"{sig_off[first_diff]} != {sig_on[first_diff]}")

    check("O2 what actually reached the road (digital_twin.current_phase) is "
          "identical on every frame",
          [s[-1] for s in sig_off] == [s[-1] for s in sig_on])

    check("O2 the five-key §13.2 core is byte-equal on every frame",
          all({k: f[k] for k in ("sim_time", "decision", "narration",
                                 "metrics_snapshot")}
              == {k: g[k] for k in ("sim_time", "decision", "narration",
                                    "metrics_snapshot")}
              for f, g in zip(off, on)))


CHECKS = {"o1": o1_offline, "o2": o2_paired_equality}


def main() -> None:
    selected = [a.lstrip("-") for a in sys.argv[1:] if a.lstrip("-") in CHECKS]
    print(RULE)
    print("  PART 4b — ORCHESTRATOR IS ADDITIVE  (checkpoint:",
          DEFAULT_CHECKPOINT.name if DEFAULT_CHECKPOINT.exists() else "MISSING",
          ")")
    print(RULE)
    for key in (selected or CHECKS):
        CHECKS[key]()
    print("\n" + RULE)
    print(f"  {_passed} passed, {_failed} failed")
    print(RULE)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Tier 1 SUMO beacon (CLAUDE.md §8): O2 calls env.reset(), which is where
    # traci.start() lives, so this harness MUST refuse to launch alongside a
    # training run or a live backend.
    from sim.sumo_activity import require_free

    require_free("orchestrator additive check")
    main()
