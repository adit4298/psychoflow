"""Proof obligation for the STEP 1 env seam: with both new kwargs omitted,
PsychoFlowEnv.reset() must build a BYTE-IDENTICAL traci.start() command line to
the pre-STEP-1 code. Asserted, not inspected.

Compares against the argv produced by the version of psychoflow_env.py at git
HEAD (i.e. before this change), loaded from `git show` into a temp module, so
the reference is the real previous code and not a hand-copied literal.

Launches no SUMO: traci.start is monkeypatched to capture argv and abort.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# ^ Derived, not hardcoded. This harness ran from a scratchpad during the
#   mixed-traffic work and carried an absolute path to one machine's checkout.
#   parents[2] is the repo root from sim/mixed_traffic/. See README.md here.
sys.path.insert(0, str(REPO))

# ---------------------------------------------------------------------------
# NOTE — THIS HARNESS DELIBERATELY HAS NO `require_free()` BEACON GUARD, AND
# MUST KEEP NONE. It starts no SUMO process: `traci.start` is monkeypatched
# below to capture the argv and abort, so nothing binds a port and nothing
# can collide with a training run or the backend. Same category as
# training/scripts/stage4_contamination.py and evaluation/heldout.py.
# Guarding it would protect nothing AND make it un-runnable during training —
# exactly when you most want to ask whether the demo kwargs have leaked onto
# the default path. See CLAUDE.md §8's beacon standing rule.
# ---------------------------------------------------------------------------

import traci  # noqa: E402


class _Captured(Exception):
    def __init__(self, argv):
        self.argv = argv


def capture(env_module, **kwargs) -> list[str]:
    real_start = traci.start
    box = {}

    def fake_start(cmd, *a, **kw):
        box["argv"] = list(cmd)
        raise _Captured(cmd)

    traci.start = fake_start
    try:
        env = env_module.PsychoFlowEnv(
            scenario_config=env_module.ScenarioConfig(lane_counts=(4, 3, 2)),
            seed=7, **kwargs
        )
        try:
            env.reset()
        except _Captured:
            pass
    finally:
        traci.start = real_start
    return box["argv"]


def load_head_version():
    src = subprocess.run(
        ["git", "show", "HEAD:env/psychoflow_env.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    # Write it INSIDE the repo's env/ package: the module computes REPO_ROOT
    # from its own __file__, so loading it from a temp dir would make ADD_FILE
    # resolve to the wrong place and produce a false difference.
    tmp = REPO / "env" / "_head_psychoflow_env_tmp.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("psychoflow_env_head", tmp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["psychoflow_env_head"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        tmp.unlink(missing_ok=True)
    return mod


def main() -> None:
    import warnings
    warnings.simplefilter("ignore")

    import env.psychoflow_env as cur
    head = load_head_version()

    def norm(argv):
        """The per-episode route file lands in a fresh mkdtemp() every
        construction, so its path differs by design. Normalise ONLY that."""
        return ["<episode.rou.xml>" if a.endswith("episode.rou.xml") else a
                for a in argv]

    argv_head = capture(head)
    argv_default = capture(cur)
    argv_demo = capture(
        cur,
        vtype_file=REPO / "sim" / "networks" / "vehicle_types_demo.add.xml",
        lateral_resolution=0.4,
    )

    print("HEAD (pre-STEP-1) argv:")
    print("   ", " ".join(argv_head))
    print("\nCURRENT, kwargs omitted:")
    print("   ", " ".join(argv_default))
    print("\nCURRENT, demo kwargs passed:")
    print("   ", " ".join(argv_demo))

    ok = True
    if norm(argv_head) == norm(argv_default):
        print("\n[PASS] default argv is BYTE-IDENTICAL to HEAD "
              f"({len(argv_head)} tokens)")
    else:
        ok = False
        print("\n[FAIL] default argv DIFFERS from HEAD")
        for i, (a, b) in enumerate(zip(norm(argv_head), norm(argv_default))):
            if a != b:
                print(f"       token {i}: HEAD={a!r} CURRENT={b!r}")
        if len(argv_head) != len(argv_default):
            print(f"       length HEAD={len(argv_head)} CURRENT={len(argv_default)}")

    # The demo argv is the default argv with EXACTLY two intended changes:
    # the -a vType file swapped, and the demo flags appended. Assert precisely
    # that, rather than a pure suffix-extension (which the -a swap breaks).
    extra = norm(argv_demo)[len(argv_default):]
    rebuilt = [
        str(REPO / "sim" / "networks" / "vehicle_types_demo.add.xml")
        if a.endswith("vehicle_types.add.xml") else a
        for a in norm(argv_default)
    ] + extra
    if rebuilt == norm(argv_demo):
        print(f"[PASS] demo argv = default argv, -a swapped, + appended {extra}")
    else:
        ok = False
        print("[FAIL] demo argv changes more than the -a file + appended flags")
        for i, (a, b) in enumerate(zip(rebuilt, norm(argv_demo))):
            if a != b:
                print(f"       token {i}: expected={a!r} actual={b!r}")

    # Nothing demo-only may leak into the default path.
    leaked = [f for f in ("--lateral-resolution", "--collision.action",
                          "--collision.mingap-factor")
              if f in argv_default]
    if leaked:
        ok = False
        print(f"[FAIL] demo-only flags present on the DEFAULT path: {leaked}")
    else:
        print("[PASS] no demo-only flag appears on the default path")

    if "vehicle_types_demo" in " ".join(argv_demo) and \
            "vehicle_types_demo" not in " ".join(argv_default):
        print("[PASS] demo vType file reachable ONLY via the explicit kwarg")
    else:
        ok = False
        print("[FAIL] demo vType file leaked into the default path")

    print("\nRESULT:", "ALL CHECKS PASS" if ok else "FAILURES ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
