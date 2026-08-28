"""§15.4 held-out evaluation seed set — the standing source of eval seeds.

WHY THIS EXISTS (2026-08-28). `phase0_baselines.py` and `stage4_proposal.py`
ran under `STAGES[4]` — the full training config — and called `reset(seed=s)`,
which sets `self._rng = random.Random(s)`. Training used `--seed 7` with the
same config, so eval seed 7 reproduced Stage 4 (and `graph_attention`) TRAINING
EPISODE 1 exactly. 42% of the recorded 0.885 proposal-quality figure came from
that one memorised scenario, and nothing raised. The master plan has required
an explicit held-out set since 2026-08-18 (§15.4); this module is it, and it is
the third time it was proposed — the difference now is a measured failure to
point at.

FIVE DESIGN POINTS (from the 2026-08-28 audit proposal, approved as-is):

  1. The manifest is a list of scenario TUPLES, not just seeds. Seeds do not
     guarantee disjointness; the drawn (lane_counts, densities, emergency)
     tuples do. Built entirely offline — `_draw_scenario()` is a pure function
     of `env._rng` and touches no TraCI (verified by
     `training/scripts/stage4_contamination.py`) — so the manifest costs
     seconds and this module NEVER launches SUMO.

  2. Disjointness is asserted PER CHECKPOINT, never globally. `stage4` trained
     on 64 distinct scenarios, `stage5_graph_attention` on 81 (a strict
     superset). A seed clean against one can be dirty against the other, so
     `--verify` takes a checkpoint DIRECTORY and screens against exactly that
     directory's `monitor*.csv` files.

  3. The manifest is drawn under `STAGES[4]`, the full training config
     (lane-count + density + emergency randomisation all on). A held-out set
     restricted to density=1.0 / no-emergency would only reproduce the
     *incidental* protection `--j1-recheck` already has, and would silently
     fail the moment a harness evaluated under realistic conditions — which is
     the exact hole this closes.

  4. `HELDOUT_SEEDS` is FROZEN: the first 30 primes >= 100. None overlap the
     12 seeds already burned in 2026-08-28 investigations
     (`BURNED_SEEDS`, which includes the training seed 7). Freezing before use
     is the point — seeds must not be chosen after seeing results, and every
     seed used once is spent.

  5. `training_set_size(checkpoint_dir)` is exported so every generalization
     claim can state it. "Generalises from 64 scenarios" is a materially
     weaker claim than a raw timestep count implies (§15.4).

GOING FORWARD (flag for Phase 12): every eval harness imports `HELDOUT_SEEDS`
from here instead of hardcoding its own seed list, and runs
`python -m evaluation.heldout --verify <ckpt_dir>` before publishing any
generalization number. A harness that scores more than
`EPISODES_PER_SEED` episodes per seed must additionally call
`assert_disjoint_at_runtime()` for its actual episode count.

Usage:
  python -m evaluation.heldout --build                     # (re)write the manifest
  python -m evaluation.heldout --verify training/checkpoints/stage4
  python -m evaluation.heldout                             # verify every stage*/ dir

NOTE — no `sim.sumo_activity.require_free` guard. That guard belongs on
harnesses that call `env.reset()` (which runs `traci.start`). This module only
touches `env._rng` and `env._draw_scenario()` and never connects to SUMO, so it
is deliberately runnable alongside a live training run — which is the point of a
cheap offline CI check. Do not add the guard.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
assert (REPO_ROOT / "env" / "psychoflow_env.py").is_file(), f"bad repo root: {REPO_ROOT}"
sys.path.insert(0, str(REPO_ROOT))

warnings.filterwarnings("ignore")  # spillover_predictor=None tripwire; offline use only

from env.psychoflow_env import PsychoFlowEnv  # noqa: E402
from training.curriculum import STAGES  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "heldout_manifest.json"
CKPT_ROOT = REPO_ROOT / "training" / "checkpoints"
MANIFEST_CONFIG = "STAGES[4]"

# Seeds already used AS EVAL SEEDS in 2026-08-28 investigations, plus 7 (the
# training seed itself). Anything here is permanently unusable as "held-out":
#   stage4_proposal.py / stage4_contamination.py : 1 2 3 5 7 10 13 21 42 99 123 777
#   phase0_baselines.py / checkpoint_bakeoff.py  : 1 7 42
#   evaluate_stage.py --j1-recheck               : 1 3 7 42
BURNED_SEEDS = frozenset({1, 2, 3, 5, 7, 10, 13, 21, 42, 99, 123, 777})

EPISODES_PER_SEED = 3  # margin: current harnesses score 1 episode/seed


def _first_primes_from(start: int, n: int) -> tuple[int, ...]:
    def is_prime(k: int) -> bool:
        if k < 2:
            return False
        i = 2
        while i * i <= k:
            if k % i == 0:
                return False
            i += 1
        return True

    out: list[int] = []
    k = start
    while len(out) < n:
        if is_prime(k):
            out.append(k)
        k += 1
    return tuple(out)


# FROZEN. Do not edit this to chase a result — pick genuinely new seeds and
# record them, never reuse.
HELDOUT_SEEDS: tuple[int, ...] = _first_primes_from(100, 30)

assert not (set(HELDOUT_SEEDS) & BURNED_SEEDS), (
    f"held-out seed overlaps a burned seed: "
    f"{sorted(set(HELDOUT_SEEDS) & BURNED_SEEDS)}"
)
assert len(HELDOUT_SEEDS) == len(set(HELDOUT_SEEDS)) == 30


# --------------------------------------------------------------------------
# scenario key — the string form MUST stay byte-identical to
# training/scripts/stage4_proposal.py's `training_key_set` / `eval_key`, or a
# comparison between the two would silently miss collisions. The gate
# self-check (`_gate_self_check`, run by every --verify) re-derives seed 7's
# episode-1 key here and asserts it matches the recorded training scenario,
# so a format drift fails loudly rather than passing while proving nothing.
# --------------------------------------------------------------------------
def _scenario_key(lane_counts, dc: float, dx: float, route: str, depart: float) -> str:
    return f"{lane_counts}|{dc:.9f}|{dx:.9f}|{route}|{depart:.6f}"


def training_key_set(monitor_dir: Path) -> set[str]:
    """Every distinct scenario the checkpoint in `monitor_dir` trained on.

    Reads EVERY monitor*.csv in the directory — a resumed run writes one per
    burst (`graph_attention` has five). Missing one under-reports
    contamination, which fails in the dangerous direction.
    """
    monitor_dir = Path(monitor_dir)
    files = sorted(monitor_dir.glob("monitor*.csv*"))
    if not files:
        raise FileNotFoundError(f"no monitor*.csv* in {monitor_dir}")
    keys: set[str] = set()
    for p in files:
        lines = [ln for ln in p.open() if not ln.startswith("#")]
        for row in csv.DictReader(lines):
            if not row.get("emergency_route"):
                continue  # pre-Stage-4 logging had no emergency columns
            keys.add(_scenario_key(
                row["lane_counts"],
                float(row["density_mult_corridor"]),
                float(row["density_mult_cross"]),
                row["emergency_route"],
                float(row["emergency_depart_s"]),
            ))
    return keys


def training_set_size(monitor_dir: Path) -> int:
    """§15.4 point 5 — cite this alongside any generalization claim made
    against the checkpoint in `monitor_dir`."""
    return len(training_key_set(monitor_dir))


def is_screenable(monitor_dir: Path) -> bool:
    """True iff this checkpoint's training log records emergency-config
    scenarios, i.e. it trained under `STAGES[4]` (or a Stage 5 variant of it).

    Stage 1-3 checkpoints predate emergency logging, so `training_key_set`
    comes back empty and disjointness against the `STAGES[4]` manifest would
    read as a trivial PASS while proving nothing — those checkpoints are
    simply not comparable to this manifest and are SKIPPED, not passed.
    """
    try:
        return len(training_key_set(monitor_dir)) > 0
    except FileNotFoundError:
        return False


# --------------------------------------------------------------------------
# drawing scenarios offline (no SUMO)
# --------------------------------------------------------------------------
def drawn_scenarios(seed: int, n_episodes: int) -> list[dict]:
    """The first `n_episodes` scenarios `reset(seed=seed)` then `reset()`...
    would draw, reproduced without starting SUMO.

    `reset(seed=s)` does `self._rng = random.Random(s)` then calls
    `_draw_scenario()`; each subsequent `reset()` calls it again on the
    advanced rng. `_draw_scenario()` uses only `self._rng` (verified in
    stage4_contamination.py, which reproduced a recorded training episode
    exactly), so this is exact.
    """
    env = PsychoFlowEnv(scenario_config=STAGES[4], seed=seed)
    env._rng = random.Random(seed)
    rows: list[dict] = []
    for ep in range(1, n_episodes + 1):
        env._draw_scenario()
        lc = tuple(env._lane_counts)
        dc = float(env._density_mult["corridor_mean"])
        dx = float(env._density_mult["cross_mean"])
        rt = env._emergency_route
        dp = float(env._emergency_depart_s)
        rows.append({
            "seed": seed, "episode": ep,
            "lane_counts": list(lc),
            "density_mult_corridor": dc,
            "density_mult_cross": dx,
            "emergency_route": rt,
            "emergency_depart_s": dp,
            "key": _scenario_key(lc, dc, dx, rt, dp),
        })
    env.close()
    return rows


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------
def build_manifest(n_episodes: int = EPISODES_PER_SEED) -> dict:
    scenarios: list[dict] = []
    for s in HELDOUT_SEEDS:
        scenarios.extend(drawn_scenarios(s, n_episodes))
    return {
        "purpose": "§15.4 held-out evaluation scenario set — see evaluation/heldout.py",
        "generated": "2026-08-28",
        "config": MANIFEST_CONFIG,
        "config_detail": "lane-count + density + emergency randomisation all on",
        "seed_rule": "first 30 primes >= 100 (frozen; never reuse a spent seed)",
        "n_seeds": len(HELDOUT_SEEDS),
        "episodes_per_seed": n_episodes,
        "heldout_seeds": list(HELDOUT_SEEDS),
        "burned_seeds_excluded": sorted(BURNED_SEEDS),
        "scenario_key_format": "str(lane_counts)|dc:.9f|dx:.9f|route|depart:.6f",
        "n_scenarios": len(scenarios),
        "scenarios": scenarios,
    }


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"{MANIFEST_PATH} not found — run `python -m evaluation.heldout --build`")
    return json.loads(MANIFEST_PATH.read_text())


def manifest_key_set(manifest: dict | None = None) -> set[str]:
    return {row["key"] for row in (manifest or load_manifest())["scenarios"]}


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------
def verify(checkpoint_dir: Path, manifest: dict | None = None) -> tuple[bool, dict]:
    """Return (ok, report). `ok` is False on ANY collision between the
    held-out manifest and this checkpoint's training scenarios."""
    m = manifest or load_manifest()
    train = training_key_set(checkpoint_dir)
    collisions = [r for r in m["scenarios"] if r["key"] in train]
    return (not collisions), {
        "checkpoint_dir": str(checkpoint_dir),
        "training_set_size": len(train),
        "heldout_scenarios": len(m["scenarios"]),
        "heldout_seeds": m["n_seeds"],
        "collisions": [
            {"seed": r["seed"], "episode": r["episode"], "key": r["key"]}
            for r in collisions
        ],
        "ok": not collisions,
    }


def assert_disjoint_at_runtime(
    seeds, n_episodes: int, checkpoint_dir: Path
) -> None:
    """Runtime gate for a harness that scores more episodes per seed than the
    static manifest records. Raises AssertionError on any overlap."""
    train = training_key_set(checkpoint_dir)
    if not train:
        raise ValueError(
            f"{Path(checkpoint_dir).name} has no emergency-config training rows "
            f"— it is not comparable to the STAGES[4] held-out manifest "
            f"(see is_screenable)"
        )
    bad = []
    for s in seeds:
        for row in drawn_scenarios(int(s), n_episodes):
            if row["key"] in train:
                bad.append((s, row["episode"], row["key"]))
    assert not bad, (
        f"{len(bad)} eval scenario(s) collide with {Path(checkpoint_dir).name}'s "
        f"training set: {bad[:5]}"
    )


def _gate_self_check(checkpoint_dir: Path) -> bool:
    """Prove --verify CAN detect a collision, not just report zero.

    Seed 7 episode 1 is a KNOWN training scenario for both `stage4` and
    `stage5_graph_attention` (2026-08-28 audit). Re-derive its key here and
    confirm the training set contains it. If this fails, either the key
    format has drifted from `stage4_proposal.py` or the monitor CSVs moved —
    and every "0 collisions" result from this module is untrustworthy.
    """
    key7 = drawn_scenarios(7, 1)[0]["key"]
    return key7 in training_key_set(checkpoint_dir)


# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--build", action="store_true",
                    help="(re)generate heldout_manifest.json")
    ap.add_argument("--verify", metavar="CKPT_DIR", action="append", default=[],
                    help="assert the manifest is disjoint from this checkpoint "
                         "directory's training scenarios (repeatable)")
    ap.add_argument("--episodes-per-seed", type=int, default=EPISODES_PER_SEED)
    args = ap.parse_args()

    print(f"held-out seeds ({len(HELDOUT_SEEDS)}, frozen): {list(HELDOUT_SEEDS)}")
    print(f"burned seeds excluded ({len(BURNED_SEEDS)}): {sorted(BURNED_SEEDS)}")
    overlap = set(HELDOUT_SEEDS) & BURNED_SEEDS
    print(f"overlap with burned seeds: {sorted(overlap) if overlap else 'NONE'}")

    if args.build:
        m = build_manifest(args.episodes_per_seed)
        MANIFEST_PATH.write_text(json.dumps(m, indent=2) + "\n")
        print(f"\nwrote {MANIFEST_PATH}")
        print(f"  {m['n_seeds']} seeds x {m['episodes_per_seed']} episodes "
              f"= {m['n_scenarios']} scenario tuples, config {m['config']}")

    explicit = [Path(t) for t in args.verify]
    if not args.build and not explicit:
        explicit = [p for p in sorted(CKPT_ROOT.glob("stage*"))
                    if p.is_dir() and list(p.glob("monitor*.csv*"))]
    if not explicit:
        return

    m = load_manifest()
    print(f"\nmanifest: {m['n_scenarios']} scenarios from {m['n_seeds']} seeds, "
          f"config {m['config']}, generated {m['generated']}")

    all_ok = True
    n_screened = 0
    for d in explicit:
        print(f"\n=== {d} ===")
        if not list(Path(d).glob("monitor*.csv*")):
            print(f"  SKIP — no monitor*.csv* (not a checkpoint directory)")
            continue
        if not is_screenable(d):
            # Stage 1-3: trained before emergency logging. Not comparable to a
            # STAGES[4] manifest — SKIP, do not report a hollow PASS.
            print(f"  SKIP — no emergency-config training rows; this checkpoint "
                  f"predates STAGES[4] and is not comparable to this manifest")
            continue

        n_screened += 1
        ok, rep = verify(d, m)
        print(f"  training scenarios (this checkpoint): {rep['training_set_size']}"
              f"   <- cite this with any generalization claim (§15.4 pt 5)")
        print(f"  held-out scenarios screened         : {rep['heldout_scenarios']} "
              f"({rep['heldout_seeds']} seeds x {m['episodes_per_seed']} episodes)")

        gate_ok = _gate_self_check(d)
        print(f"  gate self-check (known seed-7 training scenario is detectable): "
              f"{'PASS' if gate_ok else '*** FAIL — key format drift, results untrustworthy ***'}")
        if not gate_ok:
            all_ok = False

        if ok:
            print(f"  RESULT: DISJOINT — 0 collisions. GATE PASS.")
        else:
            all_ok = False
            print(f"  RESULT: *** {len(rep['collisions'])} COLLISION(S) — GATE FAIL ***")
            for c in rep["collisions"][:10]:
                print(f"      seed {c['seed']} ep {c['episode']}: {c['key']}")

    print()
    print("=" * 78)
    if n_screened == 0:
        print("NOTHING SCREENED — no explicit target was a STAGES[4] checkpoint")
    else:
        print(f"ALL {n_screened} GATE(S) PASS" if all_ok else "GATE FAILURE — see above")
    print("=" * 78)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
