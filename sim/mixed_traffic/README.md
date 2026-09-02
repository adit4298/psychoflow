# `sim/mixed_traffic/` — measurement harnesses for the demo-only driving model

Every number in **`docs/MIXED_TRAFFIC_RESEARCH.md`** §3 and §6 was produced by a
script in this directory. They lived in a session-scoped OS temp scratchpad until
**2026-09-02**, which meant the entire evidence base for the mixed-traffic work
was one directory-clear away from being unreproducible. Relocating them here is
the fix; nothing about what they measure changed.

**Read `docs/MIXED_TRAFFIC_RESEARCH.md` before retuning anything.** It separates,
by section, what is *sourced* (relayed second-hand, directional only), what is a
*reasoned default* (every parameter value and every tier ratio), and what this
repo *measured*. Only the last category may be quoted as something PsychoFlow
measured.

## What these are — and what they deliberately are not

All of these are **standalone raw SUMO**. They construct no `PsychoFlowEnv`, load
no checkpoint, and touch no reward, validator or policy code. That is the point:
they measure the **driving model**, so they cannot perturb Stage 4 or any recorded
figure. They are *not* part of any phase done-bar and no CI runs them.

They target the **demo-only** model
(`sim/networks/vehicle_types_demo.add.xml` + `--lateral-resolution`), which is
never on the training or evaluation path. **Never re-measure a checkpoint under
it and compare the result to a recorded number** — the dynamics differ, so they
describe different worlds.

## The scripts

| script | what it measures | cited in |
|---|---|---|
| `measure_overtake.py` | The main harness. Overtake mechanism vs. outcome bucketed by speed delta, strict same-lane sharing share, per-tier queue advancement, red-running population, collisions. | research §3.1–3.4, §6.1–6.5 |
| `measure_driving.py` | STEP 1's original A/B: queue-front filtering (arrival-rank vs. stop-line-rank advancement), per-type speed calibration, spawn-gap patterning. | CLAUDE.md §8 "STEP 1 MEASURED RESULTS" |
| `measure_phases.py` | Signal phase/slot interval distribution — the "signals switch too fast" disentangling. Needs a checkpoint path; the only script here that touches one. | CLAUDE.md §8 "ITEM 3" |
| `probe_collisions.py` | Collision stream detail: which types, on-lane vs. junction-internal, where. | research §3.4, §6.2 |
| `check_fix.py` | Minimal A/B of one vType table: arrived count + collision events. The harness behind the collision fix. Takes the vType file as `argv[1]`. | research §6.6 |
| `diagnose_collision.py` / `2` / `3` | Per-step lane / position / speed / lateral-offset traces for a named vehicle pair, insertion to contact. What actually root-caused the truck sideswipe. | research §6.6 |
| `inspect_geometry.py` | Lane widths, centre spacing and internal-lane connections — the geometry the collision trace is read against. | research §6.6 |
| `probe_dist.py` | That SUMO resolves the route file's `<vTypeDistribution id="mixed">` when its members are themselves distributions. Why `scenario_generator.py` needed no change. | research §3.3 |
| `probe_vtypedomain.py` | That `traci.vehicletype.getTau`/`setTau` accept a **distribution id** without raising, and resolve to one randomly sampled member. | research §4b |
| `probe_weather_propagation.py` | The consequence of the above: a §7.4 write through a base name reaches **1 of 3** tiers. The bug `WeatherModel._resolve_members()` fixes. | research §4b |
| `verify_perception_fixes.py` | BEFORE/AFTER `type_composition` on the shipped code path, **plus the inertness control on the default file** (identical on every row — that control is what makes the fix safe). | research §4, §4b |
| `verify_argv.py` | That `PsychoFlowEnv`'s default `traci.start()` argv is **byte-identical** to pre-STEP-1 HEAD, and that no demo-only flag appears on the default path. Starts no SUMO. | research §5 |
| `preview_ambulance.py` | Small scripted GUI preview of ambulance behaviour. Design-review aid, not a measurement. | — |

For the human sign-off watch itself use **`sim/run_demo_gui.py`**, not anything
here — it launches the full corridor with the tiered model and the saved
colour-by-type GUI settings.

## Pinned inputs — do not regenerate casually

| file | why it is pinned |
|---|---|
| `measure.rou.xml` | **The single route file every §6 arm shares.** This is what makes §6 internally controlled and same-route-file throughout. STEP 1's own baseline does *not* reproduce bit-for-bit against a freshly generated file (1868 vehicles vs. its recorded 1870), which is exactly why STEP 1's recorded **37.13%** figure is not a valid comparator and why §6 re-ran STEP 1's table here instead. Regenerating this file invalidates cross-arm comparison. |
| `measure3000.rou.xml` | The 3000s variant used for the longer STEP 1 run. |
| `_probe_routes.rou.xml`, `_probe_types.add.xml` | Tiny synthetic fixtures for `probe_dist.py` / `probe_weather_propagation.py`. **Not** the shipped model — their values differ on purpose. |
| `vehicle_types_demo_step1.add.xml` | STEP 1's uniform (untiered) table — the "STEP 1" column of §6.1. |
| `vt_step1_tau10.add.xml` | STEP 1's table with **only** bike `tau` raised to 1.0 — the column that isolates the tau fix from tiering. |
| `vt_tiered_asl1.add.xml` | Tiered with truck `actionStepLength` removed — the arm that refuted it as the collision cause. (Since found to be inert anyway: SUMO clamps `1.5` to the 1.0 step length. See BUILD_LOG 2026-09-01.) |
| `vehicle_types_demo_TESTFIX.add.xml` | The candidate carrying the truck lateral-recentring fix, tested before it was applied to the shipped file. |
| `data/*.json` | Raw results. `ot_*.json` are `measure_overtake.py` output (baseline / STEP 1 / tiered); `dr_*.json` are `measure_driving.py` output. Cited by name in research §3. |

## Two conventions these must keep

**1. The beacon guard.** Every script here that launches SUMO calls
`require_free()` as the first statement inside its `if __name__ == "__main__":`
block — inside the guard on purpose, so the check fires on invocation and never
on import. Two scripts deliberately have **no** guard and must keep none:
`verify_argv.py` (monkeypatches `traci.start` to capture argv and abort) and
`inspect_geometry.py` (static `sumolib` read). Both carry a NOTE block saying so,
because the obvious "fix" is to add the guard back. Same category as
`training/scripts/stage4_contamination.py` and `evaluation/heldout.py`.

**2. Bucket an outcome at the instant it occurs, never at the instant the
situation opened.** This is the standing lesson from research §3.1, where a
conclusion was written and then withdrawn: passes bucketed by the speed delta at
relationship *open* looked flat, because a low-delta relationship persists 16–17s
while the leader slows. Re-bucketed at the pass instant the curve is cleanly
monotonic and the original diagnosis was wrong. Before writing any comparative
claim from these harnesses, ask which regions the sampling would have had to
cover for the opposite conclusion to be visible.

## Status

The mixed-traffic work these measure is **built and measured but NOT signed off**
— its done-bar is a human watching `sumo-gui`, and until that passes the work is
also uncommitted. See `CLAUDE.md` §10 and §11.
