# Build Log
Append-only. Newest entry at the bottom. Do not edit or delete past entries.

## 2026-08-11 — §18 Phase 1 (Environment + SUMO corridor generator)

**Decision:** Built `sim/networks/generate_corridor.py` using hand-built plain-XML (`.nod.xml`/`.edg.xml`) + `netconvert`, not `netgenerate`.
**Why:** `netgenerate` only produces networks from global parameters (grid/spider/random) — it has no mechanism for giving three adjacent junctions independently different lane counts. Plain-XML + `netconvert` is the standard SUMO workflow for custom topology and gives per-junction, per-approach control.
**Deviates from plan?** No — this was the approach proposed and confirmed before building.
**Verified:** `python generate_corridor.py --j1 4 --j2 3 --j3 2` → netconvert exit 0. sumolib structural check confirmed J1/J2/J3 all `traffic_light` type, 4 in/4 out edges each, J1-J2 and J2-J3 both connected, lane counts correct and independent (J1 approaches=4, J2 approaches=3, J3 approaches=2, corridor edges asymmetric per direction matching the junction they approach). `sumo -n corridor.net.xml --end 1` (headless engine, not just compiler) loaded and ran a step, exit 0. User confirmed `sumo-gui` render: one connected, empty 3-junction corridor with cross-streets at each junction.

**Decision:** Junction node IDs are the literal strings `"J1"`, `"J2"`, `"J3"`.
**Why:** These must match `digital_twin.junctions` dict keys (§7.6) and `corridor_adjacency` pair values (§9.5) verbatim in later phases — fixing the convention now avoids a rename touching multiple modules later.
**Deviates from plan?** No.
**Verified:** N/A (naming convention, confirmed by inspection of generated `.net.xml`).

**Decision:** Skipped hand-authored `.con.xml` (lane-to-lane connections) and edge-type `.typ.xml`; let `netconvert` auto-generate both connections and TLS phases from node type `traffic_light`, and set lane count/speed directly as edge attributes instead of via an edge-type file.
**Why:** Hand-computing valid turning-lane connections for variable 2/3/4-lane approaches is error-prone; `netconvert`'s automatic connection + phase computation is the standard, well-tested path and guarantees the TLS phase `state` strings are consistent with the connections actually built (hand-writing `.tll.xml` against connections not yet computed risks mismatched phase strings). An edge-type file is an unnecessary indirection when there are only 10 physical road segments to define directly.
**Deviates from plan?** Yes — the plan proposed in-session described `.con.xml` and `.typ.xml` as part of the file set. Revised after starting to build, before finishing, based on the reasoning above.
**Verified:** Raw `.net.xml` contains 3 `<tlLogic type="static">` blocks (J1/J2/J3) totaling 16 `<phase>` elements — real, netconvert-computed NSEW-style phases exist for §9.2's `MAX_PHASES` padding/masking to operate over later.

**Decision:** Vehicle types (bike/auto/car/truck/ambulance, §7.1) written to a separate SUMO "additional" file `sim/networks/vehicle_types.add.xml`, not to a netconvert `.typ.xml`.
**Why:** netconvert's `.typ.xml` defines edge/road types (lanes, speed, priority) for the network compiler — it has nothing to do with vehicle vTypes, which are a simulation-time (not compile-time) concept consumed later by route generation. Conflating the two was a mistake in the initial plan, corrected before writing code.
**Deviates from plan?** Yes, same session — corrected before building rather than after.
**Verified:** File defines 5 `<vType>` entries with distinct `length`/`maxSpeed`/`accel`/`decel`/`sigma` per type (bike 1.8m/7.0mps, auto 3.2m/11.0mps, car 4.5m/16.7mps, truck 7.5m/12.5mps, ambulance 5.5m/19.4mps + `vClass="emergency"`). Not yet load-tested against a running sim (no route file exists yet — that's §7/scenario_generator territory) — will be exercised for real in Phase 2.

**Note:** §5.1 install checklist fully verified this session — all tools/packages present except: Ollama daemon not currently running (`ollama serve` needed before Phase 11) and the `ollama` pip package not yet installed in venv. Neither blocks Phase 1; flagged so it doesn't surprise us at the voice-layer phase.

## 2026-08-11 — §18 Phase 2 (Perception Layer, §7)

**Decision:** Digital twin update loop is PULL, with a stateful-registry carve-out for incidents and weather.
**Why:** `lane_sensor`/`vision_mock`/`v2x` are pure functions of current TraCI state and are re-read fresh each step (pull). `incident_intake`/`weather` own a lifecycle nothing in TraCI holds — an incident expires after `estimated_duration_s`, weather persists until changed — so they are registries: external callers push events IN, the twin pulls current state OUT during `update()`. Pure push was rejected because write ordering becomes implicit, no instant is guaranteed to hold a consistent snapshot, and "which module wrote this field" becomes a debugging problem. Pull gives `env.step()` (§9.2) one coherent reproducible snapshot per step and hands §13.2's WebSocket the same object for free.
**Deviates from plan?** No — §7.6 specifies the merge but not the mechanism; this fills that gap.
**Verified:** `python sim/run_perception_episode.py` ran 300 steps calling `twin.update()` every step, producing a §7.6-shaped snapshot with all five perception inputs non-default.

**Decision:** SUMO must be launched with `--waiting-time-memory 1000` (constant `WAITING_TIME_MEMORY_S` in `perception/lane_sensor.py`). This is now a standing requirement for every phase that starts SUMO.
**Why:** SUMO's default accumulated-waiting-time window is 100s. The starvation threshold is 90s (§0.1), so under the default the metric saturates ~10s past the line and a lane starved for 300s is indistinguishable from one starved for 95s — destroying the magnitude signal that §9.1's non-linear `starvation_bonus` and §9.4's non-linear starvation penalty both consume. Caught before any training time was spent on it.
**Deviates from plan?** No — the master plan doesn't mention the option; this is a required implementation detail it left implicit.
**Verified:** Observed `wait_time_max_single_vehicle: 114.0` on lane `J2_J1_0` at sim_time 300 — above the 100s default cap, proving the window is genuinely enlarged and not saturating. Same lane correctly showed `starvation_flag: true` against the 90s threshold.

**Decision:** Raw SUMO lane ids (`N1_J1_0`) are canonical; added an `approach` field (`north`/`south`/`east`/`west`) to §7.1's schema, derived geometrically from node coordinates rather than parsed from edge-id strings.
**Why:** §7.1 illustrates ids like `"north_approach_0"` but the Phase 1 generator produces raw SUMO ids, and TraCI plus the §10 safety validator must act on the raw id. §12.2's narration templates ("Lane 3, North — selected") need the compass direction, so deriving it now avoids a retrofit. Geometric derivation (comparing approach-node coords to junction coords) stays correct if the generator's naming convention ever changes.
**Deviates from plan?** Yes — one field added beyond §7.1's literal schema. Flagged and confirmed with the user before building.
**Verified:** Topology dump shows all 36 approach lanes across J1/J2/J3 correctly grouped by direction (J1 east=`J2_J1_*`, west=`W1_J1_*`, etc.), 16/12/8 lanes matching lane_count 4/3/2.

**Decision:** §7.2 vision output rides alongside §7.1 in the twin as a per-junction `vision` block, not replacing `lanes`.
**Why:** §7.6's schema elides this (`"...per §7.1 shape..."`). Keeping both feeds present means a consumer can be pointed at either, which is the whole architectural point of the mock (§4 input-source agnosticism). Counts are passed through unmodified — only `confidence` (0.85–0.98) and `source` are added, per §7.2 and per explicit user instruction ("nothing more").
**Deviates from plan?** No — fills an elision in §7.6's schema.
**Verified:** Snapshot shows `vision.J2_J1_0` with identical counts to `lanes.J2_J1_0` plus `confidence: 0.9`, `source: "vision_mock"`.

**Decision:** §7.5 dropped V2X messages are omitted from the batch entirely; emitted messages always carry `dropped: false`. Drop counts tracked on the emitter (`V2XEmitter.stats()`) rather than in the schema.
**Why:** §7.5 explicitly says to drop the message entirely rather than emit malformed data, but also lists `dropped` in the schema. Retaining the field preserves shape fidelity with the contract while honouring the drop semantics; the counter keeps the drop rate observable.
**Deviates from plan?** No — resolves an internal ambiguity in §7.5.
**Verified:** `{'emitted': 33205, 'dropped': 1051, 'drop_rate_observed': 0.0307}` — inside §7.5's specified 2–5% band. Position jitter isotropic (random magnitude 0.5–1.5m at random bearing) rather than axis-biased.

**Decision:** `weather.py` snapshots baseline vType params at `attach()` and applies every profile relative to those baselines, never to current values.
**Why:** Applying multipliers to current values would compound across changes — clear→rain→clear→rain would drift the network into an unintended regime over a 3600s episode (§9.2).
**Deviates from plan?** No.
**Verified:** A/B run, identical seed and routes, 300 steps: `clear` mean vehicle speed 6.376 m/s with 131 vehicles still on road at end; `heavy_rain` 5.028 m/s (−21%) with 174 on road. Confirms §7.4's requirement that behaviour genuinely shifts rather than a label sitting unused. Params read back from SUMO after change, e.g. car tau 1.0→1.9, max_speed 16.7→11.69, sigma 0.5→0.7.

**Decision:** Built `sim/routes/test_episode.rou.xml` (hand-authored) and `sim/run_perception_episode.py` (verification harness) — neither is in §6's folder structure.
**Why:** The Phase 2 done bar requires a manually-run episode, which requires traffic on the network and something to drive it. Deliberately NOT `sim/scenario_generator.py` — randomized scenario generation is later scope and was not built.
**Deviates from plan?** Yes — two files outside §6. Both are Phase 2 verification scaffolding, not runtime components; `scenario_generator.py` remains unbuilt.
**Verified:** Episode runs clean, no unknown vTypes seen by the lane sensor, all 5 vTypes present in observed `type_composition` (bike 5, auto 11, car 19, truck 4, ambulance 1 at J1).

**Note (network fact — affects absolute-coordinate consumers only):** netconvert normalises every network so no coordinate is negative, and records the shift it applied in the `.net.xml` `<location>` element: `netOffset="150.00,150.00" convBoundary="0.00,0.00,900.00,300.00" origBoundary="-150.00,-150.00,750.00,150.00"`. `generate_corridor.py` authors J1 at the origin with boundary arms at negative x/y, so netconvert shifted everything by +150 on both axes.

| node | authored in generate_corridor.py | actual in .net.xml |
|---|---|---|
| J1 | (0, 0) | (150, 150) |
| J2 | (300, 0) | (450, 150) |
| J3 | (600, 0) | (750, 150) |
| W1 | (-150, 0) | (0, 150) |

**What it does NOT affect:** it is a rigid translation, not a distortion — all distances and geometry are preserved (verified: J1-J2 = 300.0m, J2-J3 = 300.0m, W1-J1 = 150.0m, exactly as authored). Nothing in the RL observation (§9.2), reward (§9.4), spillover prediction (§8.1) or safety validator (§10) is affected, because those consume counts, wait times and queue lengths — relative and aggregate quantities, never absolute x/y.

**What it DOES affect — anything consuming absolute x/y:**
1. §7.5 V2X messages carry absolute `position: {x, y}` straight from TraCI, so they are in the *shifted* frame. Correct and self-consistent, but a hand-computed expectation derived from the generator's authored values would be off by exactly (150, 150).
2. §6's `IntersectionView.jsx` (frontend, Phase 10). If its network-to-canvas mapping is built from `generate_corridor.py`'s parameters (J1 at origin, `junction_spacing_m=300`, `approach_arm_m=150`) rather than from the net file, every vehicle renders 150m off on both axes — half a junction spacing. Visibly wrong but not obviously broken, which is the expensive failure mode to debug live.

**Rule going forward:** never hardcode junction positions from generator parameters. Read them from the net file (`sumolib.net.readNet(...).getNode(jid).getCoord()`), or read the shift itself via `net.getLocationOffset()`. `twin/digital_twin.py` already derives all topology from the `.net.xml` for exactly this reason.

**Note (§7.6 key name confirmed for §8.1 / §9.5 consumers):** `corridor_adjacency` is literally present in the twin snapshot under that exact key, with exactly the §0.1 value. Verified against a live snapshot: top-level keys are `['sim_time', 'corridor_adjacency', 'junctions', 'active_incidents', 'weather', 'v2x_messages_recent']`; `json.dumps(snap['corridor_adjacency'])` -> `[["J1", "J2"], ["J2", "J3"]]`; identity check `snap['corridor_adjacency'] == [['J1','J2'],['J2','J3']]` -> `True`. Defined once as `CORRIDOR_ADJACENCY` in `twin/digital_twin.py` — §8.1's spillover forecaster and §9.5's graph-attention extractor should import that constant rather than re-declaring the pairs.

## 2026-08-12 — §18 Phase 3 (Gymnasium env + reward, §9.2 / §9.4)

**Decision:** `MAX_PHASES = 3`, meaning green phases only — measured, not assumed.
**Why:** §9.2 defines MAX_PHASES as "largest valid phase count across all topologies" but never states the value. Phase count turns out NOT to be a function of a junction's own lane count — it depends on whether the junction's approach lane count matches its outgoing edge. Swept all 27 lane-count combinations of `generate_corridor.py`: a symmetric junction gets 4 total phases / 2 green, an asymmetric one gets 6 total / 3 green. The locked 4/3/2 demo corridor is J1=6/3, J2=6/3, J3=4/2; a uniform 4/4/4 corridor is 4/2 everywhere. The agent selects among GREEN phases only — picking a yellow would waste an action, and jumping green->green with no intervening yellow would release conflicting movements simultaneously, which is exactly what §10 exists to prevent.
**Deviates from plan?** No — fills a value §9.2 left unspecified.
**Verified:** Sweep over all 27 combos printed `MAX total phases = 6`, `MAX green phases = 3`. One counting bug found and fixed mid-verification: testing for `G`/`g` in the phase state string over-counts, because netconvert's yellow phases keep a permissive `g` on minor movements (e.g. `rrrrrryyyyygrrrrrryyyyg`). Correct test is `"y" not in state`. Before the fix the sweep reported MAX green = 4, which would have padded the action space one slot too wide.

**Decision:** Observation is `Box(-10, 10, shape=(3, 191), float32)` — a node-feature matrix, one row per junction in fixed `(J1, J2, J3)` order. Not a flat Box, not a Dict.
**Why:** §9.5's graph-attention extractor needs an `[N_nodes, F]` tensor to attend over `CORRIDOR_ADJACENCY`; the shared-policy fallback applies the same per-row MLP with attention removed. Handing both extractors this exact shape is what makes §9.5's config flag a one-line swap instead of a rewrite. Row layout: `[0:176]` = 16 lane slots x 11 features, `[176:188]` = 12 junction scalars, `[188:191]` = weather one-hot. Weather is corridor-global but replicated into every row to keep the matrix uniform (standard GNN practice). `corridor_adjacency` is deliberately NOT in the observation — §0.1 locks it as fixed, so the extractor imports it as a constant.
**Deviates from plan?** No — §9.2 specifies the contents, not the container.
**Verified:** `observation_space = Box(-10.0, 10.0, (3, 191), float32)`; live `reset()` returned `obs.shape=(3, 191) dtype=float32` and `observation_space.contains(obs)` passed. On J3 (2-lane) lane slots 2 and 3 of each approach read `[0.0]*11` including `valid_mask=0`, confirming §9.2's zero-fill-plus-mask padding.

**Decision:** Fixed normalization constants, not `VecNormalize`.
**Why:** Reproducibility across runs and into the backend/demo, and §10's validator plus §12's narrator reason about raw values — a running normalizer would drift what the policy sees away from what the operator is told. `obs_action_spec.describe()` renders an observation back to named values, because 573 floats is not inspectable by eye and a mis-indexed feature otherwise surfaces only as "training doesn't work" hours later.
**Deviates from plan?** No.
**Verified:** `describe()` readback on a live observation recovered lane_count 4/3/2, n_green_phases 3/3/2, per-lane counts/waits/types, weather `clear`.

**Decision:** Action space is `MultiDiscrete([3, 3, 3])` — ONE combined action per step covering all three junctions, one centralized policy, one corridor-level scalar reward. Not three separate agent decisions.
**Why:** Forced by §9.5, not chosen. §9.5 describes ONE policy network in both coordination modes and requires that flipping the config flag "never requires new code, only a re-run" — three independent agents would need a different env and training loop per mode. The fallback also depends explicitly on "the shared reward signal across the corridor", i.e. one scalar per step. `sb3-contrib`'s MaskablePPO supports MultiDiscrete masking natively (concatenated masks, length `sum(nvec)` = 9). Phase 7 will add a custom `MaskableActorCriticPolicy` with `net_arch=[]` and a shared per-node action head so the two modes differ by exactly one layer; `MultiDiscrete([3,3,3])` is what keeps that possible.
**Deviates from plan?** No — resolves an ambiguity §9.5 left open.
**Verified:** `action_space = MultiDiscrete([3 3 3])`. Honest-boundary language added to master plan §17 this session, since it governs demo-day narration: "Coordination is achieved through one shared policy attending across all three junctions in a single forward pass (centralized execution), not three independently-executing agents."

**Decision:** Action masking has three rules in precedence order — (1) mid-yellow transition -> only the committed target is legal; (2) green younger than `MIN_GREEN_S = 10s` -> only "stay" is legal; (3) otherwise -> every green phase that junction actually has. Violations raise `InvalidActionError` (`strict_action_masking=True` by default).
**Why:** §9.2 requires the agent "physically cannot select an invalid or unsafe phase". Masking padding slots makes that literally true on 2-green junctions. The min-green rule makes anti-flicker structural rather than advisory — §9.4's switch penalty only discourages flicker, masking forbids it. Raising rather than silently ignoring matters because MaskablePPO can never produce an invalid action anyway (masks are applied to logits pre-sampling); the exception exists so a hand-written controller, a §14 voice command, or a later-phase bug gets a hard failure instead of a silently-dropped action. The current phase is always legal, so every sub-space always has at least one valid action — MaskablePPO raises otherwise.
**Deviates from plan?** No.
**Verified:** Live, three ways. (1) J3 is 2-lane -> `mask=[1,1,0]`; action `[0,0,2]` -> `InvalidActionError: J3: phase slot 2 is masked at this step (valid slots: [0, 1])`. (2) After forcing a J1 switch, `green_age=2s` -> `mask=[0,1,0]`; action `[0,0,0]` -> `InvalidActionError: J1: phase slot 0 is masked (valid slots: [1]), time_since_switch=2.0s, min_green=10.0s`. (3) Control — after holding to `green_age=12s` the identical action was ACCEPTED with `switched_junctions=['J1']`, proving the rejection was the min-green rule and not a blanket refusal.

**Decision:** Yellow transitions are driven by the env, not by SUMO's static timer. On a switch the env runs the phase immediately AFTER the current green in program order, then sets the target green. Every `setPhase` is followed by `setPhaseDuration(HOLD_PHASE_S=1e5)`.
**Why:** The yellow that clears a given green is keyed off the CURRENT green, not the destination — that phase clears exactly the movements running now, so the rule stays correct for non-adjacent jumps (green 0 -> green 2). Without the `setPhaseDuration` hold, SUMO's static program would keep advancing phases on its own timer behind the agent's back, and the action would only intermittently be what actually ran.
**Deviates from plan?** No — implementation detail §9.2 left implicit.
**Verified:** Across a 718-step episode the observed `current_green_slot` always matched the last accepted action, and `time_since_switch_s` reset to 0 only on an accepted switch.

**Decision:** Reward weights: `starvation_knee=4.0`, `mean_weight=0.5`, `max_weight=1.0`, `w_starvation=1.0`, `w_throughput=0.25`, `w_emergency=20.0`, `w_switch=0.5`. Per-lane penalty `p = r^2 + 4*max(0, r-1)^2` where `r = wait / 90`. Per junction: `0.5*mean(p) + 1.0*max(p)`, summed across the corridor.
**Why:** The `max` term carries most of the weight deliberately. Pure `mean` would break §16's Stage 2 check ("consistent across all 3 lane-counts"): one starved lane among a 4-lane junction's 16 lanes is diluted 16x, among a 2-lane junction's 8 lanes only 8x, so identical physical starvation would score differently purely because of lane count — the agent would look "great on 4-lane, terrible on 2-lane", that checkpoint's exact red flag. `max` is lane-count invariant and is also the honest definition of starvation (§9.1 is about the worst-off lane). Throughput uses `simulation.getArrivedNumber()` rather than junction crossings so the training signal and §15.2's `total_throughput` evaluation metric are literally the same number. `w_emergency=20.0` dominates so the reward AGREES with §10's validator instead of the policy spending training fighting a gate it cannot win.
**Deviates from plan?** No — supplies the concrete formula §9.4 gives only as pseudocode.
**Verified:** `python -m env.reward` reproduces, as executable assertions, the hand-calculations signed off before build: balanced `+1.278`, one lane at the 90s threshold `+0.297`, one lane at 200s `-9.926` (strictly monotonic). Non-linearity: `penalty(100s)=1.284`, `penalty(200s)=10.914` -> **a 2x wait costs 8.5x the penalty**, satisfying §9.4's "2x wait != 2x penalty, much worse". Emergency: ignored `-18.722` vs prioritized `+0.778`, a 19.50 gap. Flicker: all-three-switch `-0.222` vs hold `+1.278`.

**Decision:** `reset()` is curriculum-parameterized via a `ScenarioConfig` dataclass (`randomize_lane_counts` / `randomize_density` / `spawn_emergencies`), defaulting to §16 Stage 1 (fixed 4/3/2, fixed density, no emergencies).
**Why:** §9.2 says reset "draws a fresh randomized topology/lane-count/density scenario" but §16 Stage 1 says "single topology, fixed moderate density", with lane-count variation only at Stage 2 and density at Stage 3. Those contradict unless reset is stage-parameterized. §9.2 now describes the config fully enabled; §16's stages are restrictions of it. Networks are generated once per lane-count combo and cached on disk as `corridor_{j1}{j2}{j3}.net.xml` — running netconvert at every reset would dominate episode setup cost once Stage 2 turns randomization on.
**Deviates from plan?** No — resolves a genuine internal contradiction between §9.2 and §16.
**Verified:** `reset()` returned `lane_counts=(4, 3, 2)` and the env auto-generated `corridor_432.net.xml` on first use.

**Decision:** Episode ends with `terminated = (simulation.getMinExpectedNumber() == 0)` and `truncated = (sim_time >= 3600)`. Scenario flows stop at `flows_end_s=3000` by default.
**Why:** §9.2's "vehicles-cleared target met" was undefined. `getMinExpectedNumber()` counts vehicles on the road PLUS those still to be inserted, so zero is exactly "all traffic cleared, nothing pending" — SUMO's own signal, no arbitrary threshold to tune. Flows must end before the horizon or the corridor never empties and the condition is unreachable by construction. The terminated/truncated split is not cosmetic: Gymnasium bootstraps the value function past a truncation but not past a termination, so swapping them would bias every value estimate while never surfacing as a crash.
**Deviates from plan?** No — makes §9.2's phrase concrete.
**Verified:** Full random-action episode ended `steps=718 sim_time=3600s terminated=False truncated=True` — correctly truncated on the time limit, not falsely terminated.

**Decision:** `--time-to-teleport 600` on every SUMO launch (constant `TIME_TO_TELEPORT_S` in `env/psychoflow_env.py`).
**Why:** SUMO's default is 300s, which sits inside the starvation regime the reward is built to measure — a badly starved lane would silently have its worst vehicle removed, erasing the exact signal §9.4 penalizes. 600s is clear of the 90-200s band the reward operates over while still leaving a deadlock escape hatch; disabling teleport entirely (`-1`) risks permanent gridlock at high density, which would hang an episode rather than score it badly. Same class of bug as the `--waiting-time-memory` issue caught in Phase 2.
**Deviates from plan?** No — the master plan does not mention teleporting.
**Verified:** Observed `worst single-vehicle wait = 793.0s` in a full episode, well past the 300s default, so vehicles are genuinely not being teleported out of the starvation measurement.

**Decision:** Built `sim/scenario_generator.py` (minimal) and `sim/run_env_smoke.py` (verification harness).
**Why:** `scenario_generator.py` is in §6's folder structure but not named in §18 Phase 3; `reset()` cannot produce varying density without it. Scope confirmed with the user before building and kept minimal — route/flow writing only, no incident scripting or weather schedules. `run_env_smoke.py` is Phase 3's done-bar harness, same category as Phase 2's `run_perception_episode.py`, not a runtime component.
**Deviates from plan?** Partly — `scenario_generator.py` is in §6 but was pulled into Phase 3 rather than left for Phase 6; `run_env_smoke.py` is outside §6 entirely.
**Verified:** Both exercised by the full-episode and masking runs above.

**Note (process failure worth not repeating — wrong Python interpreter):** the first pass of Phase 3 verification ran on the SYSTEM interpreter (`WindowsApps\PythonSoftwareFoundation.Python.3.11`), not the project venv, because CLAUDE.md §8 asserted the venv "is already active in the shell" and that was taken on trust rather than checked. On the system interpreter `pip list` showed only `numpy 1.26.4`, which led to a `pip install gymnasium` landing outside the venv and to a nearly-committed BUILD_LOG entry falsely "correcting" the Phase 1 note to claim §5.1's packages were never installed. They were: the venv has `gymnasium 1.3.0`, `stable_baselines3 2.9.0`, `sb3_contrib 2.9.0`, `torch 2.13.0`, `numpy 2.4.6`. `sumolib`/`traci` resolve from `SUMO_HOME/tools` rather than pip, which is why Phase 2's SUMO-only scripts ran fine on the wrong interpreter and gave no signal anything was off. All three Phase 3 verifications were re-run inside the venv under numpy 2.x and reproduced identically, so the recorded evidence stands. CLAUDE.md §8 corrected to require `python -c "import sys; print(sys.prefix)"` before any install or reportable run.

**Note (reward tail behaviour under a random agent — flagged for §16 Checkpoint 1, no change made):** the full random-action episode produced `mean_reward/step = -224.8`, with per-step rewards reaching `-765` late in the episode as the worst lane's wait climbed to 793s. That is the signed-off formula behaving as designed — `p(793s) = 8.81^2 + 4*7.81^2 = 321.6` per lane — and a random agent starving lanes on 624 of 718 steps is exactly the headroom §16's "beats random-action baseline" checkpoint needs. But the starvation term is unbounded, so early-training advantage estimates will be dominated by a few catastrophic steps and the value function has to span roughly `[-800, +3]`. If Checkpoint 1's reward curve comes back flat or unstable, capping `r` (e.g. at 5.0, giving a max lane penalty of ~89) is the first thing to try, before touching anything structural. Not changed now — the formula was verified and signed off, and this is a training-dynamics question Checkpoint 1 is the right place to answer.

**Note (build-order gate added to CLAUDE.md §3, not just here):** this env must NOT be used for a real training run until Phase 5 (§8.1 spillover) lands. Observation indices 10 and 11 of each junction's scalar block are zero-filled while `spillover_predictor is None`, and training against permanently-zero inputs teaches the policy those features carry no information — a lesson it will not unlearn when Phase 5 makes them live. Smoke tests and random-action rollouts are fine. §18 already orders Phase 5 before Phase 6; the gate is what makes that ordering load-bearing rather than incidental.

### Phase 3 close-out (same session)

**Decision:** Removed the stray system-Python packages left by the wrong-interpreter incident.
**Why:** The mistaken `pip install gymnasium` had landed in the user's general-purpose system Python (`WindowsApps\PythonSoftwareFoundation.Python.3.11`), which also hosts unrelated work (Django, FastAPI, pytest, pandas). Leaving a project dependency there is inconsistent state that could later mask exactly the same class of "which interpreter am I on" confusion.
**Deviates from plan?** No — cleanup, not a build decision.
**Verified:** `python -m pip uninstall -y gymnasium cloudpickle farama-notifications` against the system interpreter removed exactly the three packages that install had added (`Successfully uninstalled gymnasium-1.3.0 / cloudpickle-3.1.2 / Farama-Notifications-0.0.6`). Confirmed afterwards that the system Python retains its unrelated packages (Django 5.1.3, fastapi 0.115.0, numpy 1.26.4, pytest 8.3.3) and that the project venv is untouched (`gymnasium 1.3.0`, `cloudpickle 3.1.2`, `Farama-Notifications 0.0.6`, `numpy 2.4.6`, `stable_baselines3 2.9.0`, `sb3_contrib 2.9.0`, `torch 2.13.0`). `python -m env.reward` re-run in the venv afterwards: all §9.4 assertions still pass.

**Decision:** The reward-tail finding was PROMOTED from this log into master plan §16, directly under the verification-checkpoint table.
**Why:** BUILD_LOG is append-only and already 145 lines at the end of Phase 3; by Phase 6 this note would sit buried mid-file, and BUILD_LOG is not what anyone has open when a reward curve looks flat. §16's checkpoint table IS the document consulted at that moment, so the actionable guidance now lives there — stated as the concrete change to make (`r = min(r, 5.0)` in `lane_starvation_penalty()`, capping per-lane penalty at ~89 instead of 300+), with the reasoning, the §9.4 properties it must preserve, and the instruction to re-run `python -m env.reward` afterwards because those assertions encode the pre-signoff hand-scored scenarios and will catch a cap that breaks the ordering. Explicitly marked "don't pre-emptively cap before seeing a curve".
**Deviates from plan?** The master plan is edited in place, which §20 of that document explicitly invites ("Update it in place as decisions change during the build").
**Verified:** §16 now carries both the guidance and a measured random-action baseline table for Checkpoints 1 and 2 to compare against — 718 steps / 3600s / truncated, mean reward per step −224.8, 4604 vehicles arrived, worst single-vehicle wait 793s, starved lane on 624 of 718 steps (87%). Those are the actual figures from `python sim/run_env_smoke.py --full-episode`, so "beats random-action baseline" is now a number rather than a judgement call.

**§18 Phase 3 is complete.** Both done-bar clauses verified in the project venv: "random-action agent runs a full episode without crashing" (718 steps, terminated=False truncated=True) and "hand-scored test scenarios produce intuitively-correct rewards" (`python -m env.reward`, all assertions pass). Next phase is §18 Phase 4 — Tier 0 rule-based controller + safety validator (§9.1, §10) — which CLAUDE.md §4 marks as a "state your design plan and wait for confirmation before writing code" phase.

## 2026-08-12 — §18 Phase 4 (Tier 0 controller + Safety Validator, §9.1 / §10)

Design plan was stated and signed off before any code, per CLAUDE.md §4. Eleven decisions were put to the user; all eleven approved as recommended.

**Decision:** §10's precedence is EMERGENCY FIRST, then starvation ceiling — the opposite of §10's own pseudocode.
**Why:** §10's code block tests starvation first and returns, so a starved lane would deprioritize an ambulance. That directly contradicts §10's prose ("cannot be delayed/blocked/deprioritized by anything else") and §9.4's weights (`w_emergency=20.0` against a starvation term that only reaches ~20 at a 250s wait). Prose and reward agree with each other; the pseudocode ordering was incidental.
**Deviates from plan?** Yes — master plan §10's pseudocode was edited in place, with a comment recording why. §20 of that document invites in-place updates.
**Verified:** Unit scenario 7b — ambulance on east/west, 200s starved lane on north/south, proposal serving north/south → action rewritten to the ambulance's phase, `rule=emergency_override`, not `starvation_ceiling`.

**Decision:** An ambulance CLAIMS its junction — the emergency branch suppresses the ceiling entirely, not just when the ambulance is unserved.
**Why:** Found while writing the unit scenarios, and it is a hole in the design as originally signed off. The approved design said "if the resulting phase doesn't serve the ambulance, override to one that does". That is insufficient: with an ambulance on east/west and a 200s starved lane on north/south, a proposal *already* serving the ambulance passes the emergency check, falls through to the ceiling, and the ceiling drags the green off the ambulance mid-transit. The fix is that any ambulance present at a junction causes an early `continue`, suppressing the ceiling for that junction. This is the real content of §10's early-return structure.
**Deviates from plan?** No — strengthens §10 in the direction its prose already required. Flagged to the user and confirmed before proceeding.
**Verified:** Unit scenario 7a — same conflicting state, proposal already serving the ambulance → action unchanged, **zero** overrides. Under the originally-approved design this case would have produced a starvation override.

**Decision:** `STARVATION_CEILING_S = 120.0` (`safety/validator.py`).
**Why:** §10 requires a ceiling but never gives a value. It must sit above §0.1's 90s threshold — if the two were equal the ceiling would fire the instant a lane is flagged, §9.1's soft bonus would never get a band to act in, and "fairness-first rule-based controller" would reduce to "the validator drives". 120s leaves a 30s working band. A module-level `assert` pins the relationship so the two constants cannot silently converge.
**Deviates from plan?** No — supplies a value §10 left unspecified.
**Verified:** Unit scenario 4 — a lane at 110s (over the 90s threshold, under the 120s ceiling) produces no override, confirming the band belongs to Tier 0's bonus.

**Decision:** The validator lives INSIDE `PsychoFlowEnv.step()`, between the mask check and actuation — not a Gymnasium wrapper, not a caller-side call.
**Why:** §10 claims "nothing reaches the road without passing through here". A wrapper leaves `env.step()` directly callable and unshielded, making that a convention rather than a structural fact. Placing it immediately before the only code path reaching `traci.trafficlight.setPhase()` makes it literally true. Second reason, which matters more at Phase 6: this is a safety shield, and a shield inside the env means the policy trains in the same dynamics it deploys into. §9.4's `w_emergency=20.0` was chosen so the reward agrees with the gate rather than fighting it — that only pays off if the gate is present during training. Accepted consequence: SB3 will log the proposed action while the validated one executes (standard for shielded RL).
**Deviates from plan?** No — §10 specifies the gate's position in the pipeline, not its host.
**Verified:** `sim/run_env_smoke.py` still passes all three Phase 3 masking checks with the gate inserted, and B2/B3 show overrides genuinely changing what SUMO executed.

**Decision:** The validator is a pure function importing no `traci`; the env passes it the snapshot, the runtime, and a static phase→served-lane map.
**Why:** Honours §7.6's "no module outside perception queries TraCI", and makes the §10 rules unit-testable with no SUMO process — which is what turned an eight-scenario test suite into a sub-second check that can be re-run after every change.
**Deviates from plan?** No.
**Verified:** `python -m safety.validator` → all 11 scenarios pass, no SUMO started.

**Decision:** The validator judges against `self._snapshot` — the snapshot from the END of the previous step.
**Why:** That is exactly the snapshot `build_observation()` was called on, so the validator evaluates the action against the same reality the policy saw when it chose it. §7.6's single-pull guarantee extended one module further. Using a fresher snapshot would mean the gate and the policy reason about different instants.
**Deviates from plan?** No.
**Verified:** B2's measured latency from detection is 3.0s = exactly the yellow duration, i.e. the validator acts on the very next decision step after the ambulance appears in a snapshot. Zero decision latency is only possible because the snapshot it reads is the one the action was chosen against.

**Decision:** The emergency override bypasses `MIN_GREEN_S`; the starvation ceiling does NOT.
**Why:** §10 says the emergency cannot be delayed by anything. The ceiling is different: letting it break min-green reintroduces exactly the flicker §9.2's masking and §9.4's switch penalty exist to suppress — two mutually starved lanes would ping-pong every decision step. Deferring costs at most 10s against a wait already past 120s. Deferred overrides are still logged (`outcome="deferred_min_green"`) so §12.1 stays honest.
**Deviates from plan?** No — resolves an interaction §10 and §9.2 leave implicit.
**Verified:** Unit scenario 2b (ceiling wants a switch at green_age=4s → action unchanged, `outcome=deferred_min_green`, no bypass) and scenario 5 (ambulance at green_age=4s → override applied, `bypass_min_green={J1}`). Live in B2 variant (b): injected at green_age=2s, override fired, `bypass_min_green=['J2']`. B4 recorded 31 genuine deferrals across a full random episode.

**Decision:** A mid-yellow transition is RE-AIMED, never broken or shortened.
**Why:** Breaking a yellow releases conflicting movements before the previous ones have cleared — the exact hazard §10 exists to prevent. Rewriting `transition["target_slot"]` lets the yellow run to completion while changing what emerges from it, costing at most the yellow's remainder (~3s here). This is the fastest *safe* response.
**Deviates from plan?** No.
**Verified:** Unit scenario 8 — mid-yellow committed to slot 0, ambulance needs slot 1 → `outcome=retargeted_transition`, `retarget_transition={J1}`, yellow duration untouched. Applies to both rules, not just emergency: re-aiming an in-flight switch adds no flicker.

**Decision:** The emergency override is STATELESS — no latch, no hold timer, no release bookkeeping.
**Why:** `validate()` recomputes from the snapshot every step; when the ambulance leaves the approach lane the branch simply stops firing and normal masking resumes from the moment of the override switch (`_set_green` already zeroed `time_since_switch_s`). A latch is a state machine that can jam holding a green forever if its release condition is ever missed.
**Deviates from plan?** No.
**Verified:** B2 both variants — normal Tier 0/adversarial operation resumes immediately after the ambulance clears, with no explicit release step anywhere in the code.

**Decision:** Phase scoring SUMS the §9.1 lane scores of the lanes a phase serves; argmax among mask-valid slots; ties to the lowest slot index.
**Why:** Sum is total demand served. Mean would prefer one badly congested lane over four moderately congested ones, which is wrong for throughput. Known cost: sum favours phases serving more lanes, and the through-corridor phase serves more than a cross-street phase. Absorbed by the per-lane cubic bonus (one starved cross-street lane spikes above a broad-but-shallow corridor phase) and by §10's ceiling underneath.
**Deviates from plan?** No — §9.1 gives the per-lane formula, not the aggregation.
**Verified:** B1 — zero starved steps across a full 3145s episode and zero §10 overrides, so the feared cross-street starvation did not materialise on the 4/3/2 corridor. Revisit from data if it ever does; `mean` is a one-line change.

**Decision:** §9.1's `wait_time_current` is used literally, as TraCI reports it.
**Why:** `lane.getWaitingTime()` is a SUM over vehicles on the lane, so it runs O(0-1000) while `halted_count` runs O(0-20) — the 0.6/0.4 weights are nominal rather than a true blend, and the wait term dominates. Taken literally anyway: §9.1 is explicitly the SEED the RL agent deviates from (§9.3), not an optimum, and rewriting a stated formula for aesthetics is the scope creep CLAUDE.md §6 warns about. Flagged to the user with the normalized alternative; literal reading confirmed.
**Deviates from plan?** No.
**Verified:** B1's results are strong on the literal reading (worst wait 41s, zero starvation), so there is no evidence the blend needs correcting.

**Decision:** `starvation_bonus = 20.0 * min(r, 2.0)**3`, deliberately a DIFFERENT shape from §9.4's reward penalty (`r² + 4·max(0, r−1)²`), sharing only the 90s threshold constant.
**Why:** Different jobs. The bonus chooses among ≤3 phases now, so the range that matters is `[0, T)` — where starvation can still be prevented; above T, §10's ceiling has taken over. The reward scores an outcome after the fact, so its range is `[T, ∞)` and it must stay unbounded to keep discriminating among failures. Reusing the reward's shape as a bonus fails twice: its hinge is AT T so it is nearly flat below T (Tier 0 would react only once it was too late), and it is unbounded above so one catastrophic lane would swamp every other term and Tier 0 would tunnel-vision. Cubic so the bonus is negligible early and decisive late; capped at r=2 because past the ceiling the gate owns the decision.
**Deviates from plan?** No — supplies the function §9.1 describes only qualitatively.
**Verified:** Curve at the decision points that matter — 5.9 at 60s, 14.6 at 81s, 20.0 at the 90s flag, 47.4 at the 120s ceiling.

**Decision:** `starvation_bonus_scale = 20.0`, MEASURED rather than guessed.
**Why:** Same discipline as `MAX_PHASES`. `--measure-scale` runs Tier 0 with the bonus disabled and records per-phase base scores, so the bonus competes against a real distribution. **First attempt returned a degenerate median of 0.00** — the sample population was wrong: MIN_GREEN_S=10s against a 5s decision interval leaves two of every three steps locked to a single slot, and those non-decisions buried the distribution under zeros. Corrected to sample only decision points offering a real choice (2+ valid slots), and to record the statistic that actually matters — the highest competing base score at each choice point, i.e. the bar a starved lane's phase must clear.
**Deviates from plan?** No.
**Verified:** 1800s, corridor 4/3/2, seed 11 → 358 steps giving 367 real choice points and 707 min-green-locked. Bar to beat: min 0.00, p25 9.00, **median 13.00**, p75 17.60, p90 27.60, max 56.60. Calibration `scale = 13.00 / 0.9³ = 17.8 → 20`. Resulting ramp clears the median competitor at 81s (14.6), p75 at the 90s flag (20.0), and p90 nearly twice over at the 120s ceiling (47.4). Re-measure if `ScenarioConfig`'s density defaults change — this is calibrated against a distribution, not a physical constant.

**Decision:** `phase_served_lanes()` (static, per-episode) is a SEPARATE map from the env's existing `_green_lanes()` (live, per-step). Both retained.
**Why:** `_green_lanes()` reads the live RYG state, so mid-yellow it returns the yellow phase's greens — correct for §9.4's emergency term, which asks "is the ambulance moving right now". §9.1's phase scoring and §10's override targeting need "which lanes WOULD slot `s` green", which is static. Unifying them silently breaks the reward. Recorded as a standing rule in CLAUDE.md §8 because it looks like duplication.
**Deviates from plan?** No.
**Verified:** Both in use simultaneously across B1-B4 with the reward's emergency term and the validator's targeting agreeing (unit scenario 6: ambulance already green → validator emits no override, and §9.4 charges no penalty).

**Decision:** `PsychoFlowEnv(enable_safety_validator=False)` exists, restricted to test scaffolding.
**Why:** B3's A/B needs an unshielded run to prove the ceiling is what bounds the wait; without the contrast, "the ceiling works" is an assertion about code rather than a measurement. Restricted because it is a switch that turns off a §10 guarantee.
**Deviates from plan?** Yes — §10 implies no such switch. Recorded as a standing rule in CLAUDE.md §8: never reachable from `backend/`, `control_api.py` (§13.1) or §14's voice intents, constructible only from `sim/run_tier0_episode.py` and unit tests, with a §20 pre-event grep to confirm.
**Verified:** Only construction site outside unit tests is `b3()` in the harness.

**Decision:** §16's random-action baseline was RE-MEASURED with the validator on, and both rows are now recorded.
**Why:** The Phase 3 baseline predates the validator, but the trained agent runs inside the shield. Comparing a shielded agent against an unshielded baseline would flatter it by ~222 reward/step of pure shield effect and make Checkpoint 1 meaningless.
**Deviates from plan?** No — §16 invites its own tuning, and the master plan invites in-place updates.
**Verified:** B4, corridor 4/3/2 seed 7, validator on: 646 steps, 3240s, **terminated** (the unshielded run never cleared), mean reward/step **−2.4** (was −224.8), 4668 arrived, worst wait **141s** (was 793s), starved on 543/646 steps, 121 overrides (90 applied, 31 deferred). §16 now carries all three rows — no-validator, with-validator, and Tier 0 — with the with-validator row marked as the one Checkpoints 1 and 2 must use.

**Decision:** §10's absolute-ceiling language replaced with a measured bound (new §10.1).
**Why:** The section claimed no lane is ever allowed to wait past a safe maximum. That is false and falsifiable on a judge's screen — the ceiling bounds what the controller may DECIDE, not the observed wait. My own first draft of §10.1 then guessed "~140-160s"; the measurement disagrees at the low end.
**Deviates from plan?** Yes — §10 rewritten, §10.1 added. Same treatment as §17's centralized-execution boundary in Phase 3.
**Verified:** Measured overshoot is **124s (B3, adversarial) to 141s (B4, random)** against a 120s ceiling — tighter than the guess because the validator reads the snapshot the observation was built from and so acts on the very next step, leaving the 3s yellow plus discharge as the only real lag. §10.1 now quotes the range with both runs, and carries the genuine limit: a lane starved by DOWNSTREAM gridlock keeps climbing however green it is, so the ceiling guarantees the signal has stopped being the cause, not that the lane drains.

**Decision:** Built `sim/run_tier0_episode.py` (harness). §15.1's Greedy baseline deliberately NOT built.
**Why:** Harness is Phase 4's done-bar scaffolding, same category as `run_perception_episode.py` and `run_env_smoke.py`. Greedy sits in `agents/rule_based.py` per §6's file comment but §18 assigns it to Phase 12, and CLAUDE.md §3 forbids building ahead. The adversarial controller B3 needs is test scaffolding, not §15.1's baseline — kept separate and inside the harness.
**Deviates from plan?** Partly — the harness is outside §6.
**Verified:** `agents/rule_based.py` contains Tier0Controller and the calibration probe only; no Greedy.

**Note (two measurement bugs found and fixed during verification, worth not repeating):** both were cases where a test *ran green while proving nothing*, which is more dangerous than a failing test. (1) B2 variant (a) was supposed to be the ordinary non-bypass path but its injection condition did not require an old green, so (a) and (b) landed on the identical scenario; after fixing the condition, (a) then produced a **negative** latency (−2.0s) because Tier 0 had switched to the ambulance's phase on its own before detection — the override never fired and the "latency" was meaningless. Fixed properly by driving B2 with a controller that REFUSES the ambulance's phase at J2, so any green there is attributable solely to §10, plus an explicit check that fails the variant if a green appears without an override. (2) The SCALE measurement's first pass reported a median of 0.00 (see above). Both were caught only because the numbers looked wrong rather than because anything raised.

**§18 Phase 4 is complete.** Done bar: "rule-based controller runs live in SUMO, starvation ceiling and emergency override both verifiably trigger." Verified in the project venv (`sys.prefix` = `...\GitHub\Test\venv`): Tier 0 ran a full episode live in SUMO and cleared the corridor (B1, 627 steps, terminated); the emergency override fired with a measured 3.0s latency from detection in both the ordinary and min-green-bypass cases (B2); the starvation ceiling fired 13 times and cut the deliberately-starved lane's worst wait from 998s to 124s on an identical seed (B3). `python -m safety.validator` passes all 11 §10 scenarios, `python -m env.reward` still passes all §9.4 assertions, and `python sim/run_env_smoke.py` still passes all three Phase 3 masking checks with the gate inserted. Next phase is §18 Phase 5 — prediction (§8.1 spillover, §8.2 incident impact) — which is also the CLAUDE.md §3 hard gate on any real training run.

## 2026-08-12 — §18 Phase 5 (Prediction, §8.1 spillover / §8.2 incident impact)

Design plan was stated and signed off before any code, per CLAUDE.md §4. Three ambiguities were flagged and resolved before building; all three approved as recommended, plus one follow-up (the incident-impact aggregation) resolved with a worked example before code was written.

**Decision:** Spillover's "current outflow rate from J1" (§8.1's suggested heuristic) is proxied as the NET GROWTH RATE of the downstream junction's own corridor-facing queue between consecutive `forecast()` calls, not an estimated per-vehicle turn-routed outflow from the upstream junction.
**Why:** The predictor only ever receives the twin snapshot (`PsychoFlowEnv._spillover()` calls `predictor.forecast(self._snapshot)` — no runtime, no route data), so which fraction of J1's queued vehicles are actually headed toward J2 is unknowable from the inputs available. Net growth on the connecting lane group is a directly observed quantity that already nets J1's discharge against J2's own service of that lane — arguably the more honest signal for "spillover risk" than a guessed turn fraction would be.
**Deviates from plan?** No — §8 itself says "start with a heuristic (e.g. ...)"; this is a substitution within that latitude, confirmed with the user before building.
**Verified:** `python -m prediction.spillover` — 6 hand-scored assertions (cold start → delta=0.0/conf=0.5; flat queue → delta=0.0; queue 4→8 over 5s → delta=+48.0 exactly matching `(8-4)/5*60`; queue 8→2 over 5s → delta=-72.0, negative as expected; incident at the downstream junction → confidence drops from 0.85 to exactly 0.65; post-`reset()` → cold start again). All pass. Live integration (`sim/run_prediction_episode.py --c1`): withheld J2's west approach for 200 decision steps against real SUMO traffic — `describe(obs)["J2"]["spillover_delta"]` tracked the real queue's rises and falls (e.g. step 30 halted=26 → delta=+60.00; step 130 draining → delta=-100.00), confirming the mechanism responds to genuine simulator dynamics, not just the synthetic unit scenarios.

**Decision:** `LINK_APPROACH = "west"` — for both corridor pairs (J1→J2, J2→J3), the lane group that receives spillover from the upstream neighbor is the downstream junction's lanes tagged `approach == "west"`. Hardcoded as a module constant in `prediction/spillover.py`, not derived per-call.
**Why:** The digital twin's per-lane `approach` field is a compass direction (§7.1), not a source-junction id — nothing in the snapshot directly says "this lane comes from J1." But the corridor is locked linear W→E (§0.1) and both junctions lie on the same east-west axis, so this is geometrically deterministic: verified from Phase 2's `_compass_direction` math (J1(150,150)→J2(450,150) gives `dx<0` at J2 → "west"; same shape for J2→J3 at J3). Treated as the same category of locked assumption as `CORRIDOR_ADJACENCY` itself, not a general-purpose inference — flagged explicitly rather than silently assumed.
**Deviates from plan?** No — §8.1 doesn't specify which lanes to read; this supplies the missing detail.
**Verified:** `sim/run_prediction_episode.py --c1` printed `withholding J2 west lanes: ['J1_J2_0', 'J1_J2_1', 'J1_J2_2']` — confirms the "west" tag at J2 does resolve to the actual J1→J2 corridor edge's lanes, not some other approach.

**Decision:** Observation indices 10/11 (§9.2's one `(delta, confidence)` slot per junction row) are filled per junction as **`to_junction`** — i.e. each junction's slot reports the forecast for spillover arriving from its own upstream neighbor. J1 (no upstream neighbor in-corridor) always reads `(0.0, 0.0)`; J2 gets the J1→J2 forecast; J3 gets the J2→J3 forecast. J2's own downstream impact on J3 is reported on J3's row, not duplicated on J2's.
**Why:** §9.2's obs schema (built Phase 3) has exactly one spillover slot per row, not one per neighbor — a real ambiguity for J2, which has two neighbors. §8.1's own framing ("feeds the intervention layer a head start... before downstream congestion is observed") says the *downstream* junction is the one that needs the warning, which fixes the resolution: report incoming-from-upstream, not outgoing-to-downstream. For a 3-node linear chain this is an exact 1:1 fit (2 adjacency pairs, 2 non-trivial incoming slots), not a lossy compromise. Confirmed with the user before building; not reopening Phase 3's schema.
**Deviates from plan?** No — resolves an ambiguity §9.2 left implicit when it specified "the immediate downstream junction" singular without addressing a junction with two neighbors.
**Verified:** `prediction.spillover.as_junction_dict()` never emits a J1 key by construction (J1 is never a `to_junction` in `CORRIDOR_ADJACENCY`); `build_observation` already zero-fills any junction missing from the spillover dict (Phase 3 behavior, unchanged), so J1's slot is `(0.0, 0.0)` without special-casing. Confirmed live: `run_env_smoke.py`'s describe() dump shows J1/J2/J3 all `spillover=(0.0, 0.0)` when `spillover_predictor=None`; `run_prediction_episode.py --c1`'s trace shows J2's slot moving while wired to a real predictor.

**Decision:** `estimated_delay_increase_s` (§8.2) is the **SUM** of each affected junction's hop-decayed contribution, not the max. `contribution(hop) = BASE_DELAY_S(30.0) * SEVERITY_VALUE[severity] * len(affected_lanes) * DECAY_PER_HOP(0.5)**hop`, summed over the incident's own junction (hop 0) and every junction reachable downstream via `CORRIDOR_ADJACENCY`.
**Why:** Flagged by the user as underspecified in the initial design pass — §8.2 states a single scalar output but an incident can affect multiple junctions at different hop distances, and the design didn't say how those collapse to one number. Max was rejected: max always equals the origin's own hop-0 contribution regardless of how far the incident ripples (decay only ever makes farther hops smaller), which would make `DECAY_PER_HOP` and the `estimated_affected_junctions` list's length functionally irrelevant to the reported delay. Sum is the total added delay burden across the corridor, and it's the only aggregation where the hop-decay term actually does anything.
**Deviates from plan?** No — §8.2 gives the output shape, not the aggregation rule; this supplies it. Worked example (incident at J1, severity=high, 2 lanes) confirmed with the user before building: J1 hop0=60.0, J2 hop1=30.0, J3 hop2=15.0, sum=105.0s.
**Verified:** `python -m prediction.incident_impact` — reproduces the worked example exactly (105.00s for J1/high/2-lanes), confirms J3-origin incidents affect only `[J3]` (no downstream neighbor, so the sum degenerates to the single hop-0 term), confirms a mid-corridor origin (J2) sums to exactly 90.0s (60.0 + 30.0), and confirms severity scaling: high/low ratio = 3.03, matching `1.0/0.33` exactly. Live (`sim/run_prediction_episode.py --c2`): incident injected at J1 mid-episode via `twin.incidents.report(...)`, `active_incidents` visibly changes from `[]` to `['inc_0001']` after one step, and `predict_incident_impact()` on the live incident reproduces `estimated_affected_junctions=['J1','J2','J3']`, `estimated_delay_increase_s=105.00s` — the §18 Phase 5 done bar ("predictions visibly change when an incident is manually injected").

**Decision:** `SEVERITY_VALUE` (`{"low": 0.33, "medium": 0.67, "high": 1.0}`) relocated from `env/obs_action_spec.py` to `perception/incident_intake.py`, its §7.3 home; `obs_action_spec.py` now imports it rather than redefining it.
**Why:** `prediction/incident_impact.py` needs the identical severity→numeric mapping obs_action_spec already had for §9.2's `JS_INCIDENT_SEVERITY` feature. Duplicating a second copy would let the two silently drift; relocating to the module that owns the `SEVERITIES` enum gives one source of truth for both consumers.
**Deviates from plan?** No — internal refactor, no behavior change.
**Verified:** `python -m env.reward`, `python -m safety.validator`, and `python sim/run_env_smoke.py` all re-run clean after the move — no observation-layer regression from the import change.

**Decision:** `PsychoFlowEnv.reset()` now calls `self.spillover_predictor.reset()` (guarded by `is not None`) immediately after `self.twin.reset(0.0)`. `PsychoFlowEnv.__init__` emits a `UserWarning` when constructed with `spillover_predictor=None`, but does NOT raise — that stays a legal, non-erroring path.
**Why:** `SpilloverPredictor` is stateful (keeps the previous snapshot to compute a rate); without a reset hook, episode 2's first forecast would compute a rate against episode 1's last snapshot — a huge, meaningless `sim_time` jump. On the None-predictor question: CLAUDE.md §3's HARD GATE explicitly permits smoke tests, random-action rollouts and unit tests with the env in its predictor-less form (`run_env_smoke.py` and the §10 unit scenarios rely on exactly this), so raising inside `psychoflow_env.py` would break legitimate non-training uses. The user asked for a warning as a cheap, breaks-nothing tripwire instead, plus an explicit, named Phase 6 prerequisite (assert a non-None predictor before `model.learn()`) recorded in CLAUDE.md §3 now rather than left implicit.
**Deviates from plan?** No — CLAUDE.md updated in place per its own §9 ("this file should change as the project does"); the hard gate itself is unchanged, only made more visible and its enforcement point named explicitly.
**Verified:** `sim/run_prediction_episode.py --c2` (constructs `PsychoFlowEnv()` with no predictor) printed the `UserWarning` exactly once, at construction, with the expected message. `sim/run_env_smoke.py` still passes with the warning firing harmlessly. Reset-safety verified in `python -m prediction.spillover`'s last two assertions (`p.reset()` then a fresh cold-start forecast).

**Decision:** Built `sim/run_prediction_episode.py` (harness, `--c1`/`--c2`) — same category as `run_perception_episode.py`, `run_env_smoke.py`, `run_tier0_episode.py`. Not part of §6's folder structure.
**Why:** §18 Phase 5's done bar ("predictions visibly change when an incident is manually injected") needs a live SUMO run to verify, not just the standalone hand-scored modules. C1 additionally exercises the harder, more convincing claim — that the forecast tracks genuine, noisy, bidirectional queue dynamics (rising when a withheld approach backs up, going negative when it drains), not just a single manufactured direction.
**Deviates from plan?** Partly — outside §6, same as every other Phase's verification harness.
**Verified:** Both `--c1` and `--c2` run clean against live SUMO (see verification notes above); both explicitly assert/PASS rather than just printing numbers for eyeballing.

**§18 Phase 5 is complete.** Done bar: "predictions visibly change when an incident is manually injected" — verified live via `sim/run_prediction_episode.py --c2`. `prediction/spillover.py` and `prediction/incident_impact.py` both exist with passing hand-scored assertion suites (`python -m prediction.spillover`, `python -m prediction.incident_impact`), and `PsychoFlowEnv` now wires `spillover_predictor.forecast()` into observation indices 10/11 on every step (verified via `obs_action_spec.describe()` readback in `run_prediction_episode.py --c1`). Regression-checked clean: `python -m env.reward`, `python -m safety.validator`, `python sim/run_env_smoke.py`. CLAUDE.md §3 and §8 updated in place with the Phase 6 prerequisite (assert a non-None `spillover_predictor` before `model.learn()`) and five new standing rules (prediction commands, `SEVERITY_VALUE`'s canonical location, the predictor's statefulness/reset requirement, the `LINK_APPROACH` hardcoded corridor fact, and the one-slot-per-row resolution). CLAUDE.md §3's HARD GATE condition — `prediction/spillover.py` exists and `PsychoFlowEnv` is constructed with a non-None `spillover_predictor` — is now satisfiable; Phase 6 (PPO training, Stages 1-4) may proceed once its own `training/train.py` adds the named hard assert.

## 2026-08-12 — §18 Phase 6 (PPO training, Stage 1, §16)

**Decision:** Built `training/curriculum.py` (cumulative per-stage `ScenarioConfig` deltas + timestep targets), `training/train.py` (single-stage-per-invocation training entry point with the CLAUDE.md §3 hard assert), and `training/evaluate_stage.py` (checkpoint evaluation against §16's recorded baselines). Stage 1 trained in two bounded bursts rather than one uninterrupted run: Burst A (fresh model, 10k timesteps, actual `num_timesteps=10240` due to PPO's rollout-buffer overshoot) reviewed against §16's "~10k steps: reward trending up" checkpoint before Burst B (`MaskablePPO.load(..., env=env)` + `model.learn(reset_num_timesteps=False)`, resumed to `num_timesteps=50960`) was authorized.
**Why:** §16's own checkpoint table requires stopping to review a red flag before continuing, not letting a bad run continue unattended — a single uninterrupted call to the stage's full 50k-100k target would make that review impossible until after the budget was already spent.
**Deviates from plan?** No — this is the two-burst pattern proposed and confirmed before Burst A started.
**Verified:** Reward curve (`training/plot_reward.py`, concatenated `monitor.csv` + `monitor_burstB.csv.monitor.csv`, timestep-offset applied): raw per-episode `mean_reward_per_step` rises from −2.04 (episode 1) through episode ~55, then holds a genuine plateau for episodes 60-81 (raw values in `[1.2453, 1.3122]`, mean 1.2847, spread only 0.067 — confirmed as a real raw-episode pattern, not a rolling-mean artifact, by printing full-precision per-episode values directly). `evaluate_stage.py` (deterministic policy, corridor 4/3/2, validator ON) on the final checkpoint (`psychoflow_stage1_50960_steps_final.zip`): seed 7 gives mean reward +1.0, worst wait 122.0s, 7/631 (1%) starved steps, 1 starvation-ceiling override — beats the shielded-random baseline (−2.4, 141.0s, 84% starved) on every metric, does not yet beat Tier 0 (+1.2, 41.0s, 0 overrides, 0% starved). Confirmed seed-independent: seeds 1/3/42 all land in the same band (worst wait 93-124s, mean reward +1.0 to displayed precision) — seed 7 is not a hard draw.

**Decision:** `evaluate_stage.py` stays on `deterministic=True` as its default, despite a large, confirmed, reproducible gap where the deterministic (argmax) policy underperforms its own stochastic policy on this checkpoint.
**Why:** Investigated because the eval numbers (+1.0 mean reward) sat well below the training curve's converged plateau (~1.28-1.30). Ruled out, by reading code rather than guessing: (1) reward normalization/scaling — neither `Monitor` nor `MaskablePPO`'s construction applies any (`VecNormalize` is not used anywhere in the repo; `gamma`/`gae_lambda`/`normalize_advantage` are internal to the PPO loss and never touch the logged reward); (2) a computation-definition mismatch — `Monitor.step()`'s `r/l` and `run_episode()`'s `total_reward/steps` are verified identical (same undiscounted per-decision-step sum, same stop condition). Confirmed instead, via 10 genuinely-independent stochastic episodes (seed 7, drawn from ONE loaded model instance in a loop — see the methodology note below) against a bit-for-bit-reproducible deterministic rerun: stochastic mean **1.297183** (stdev 0.013514, range [1.274729, 1.317628], n=10) vs. deterministic **1.028848**, confirmed identical to 9 decimal places and identical per-step action trace across two independent fresh-process runs (rules out unseeded randomness elsewhere in the pipeline, e.g. V2X noise §7.5, as the cause). The gap (~0.25-0.29, roughly 20x the stochastic-to-stochastic spread) is real, seed-independent, and reproducible — not a bug in this checkpoint's measurement. Working hypothesis, explicitly NOT confirmed further: `MaskablePPO` trained with the SB3 default `ent_coef=0.0` (no entropy bonus), and `entropy_loss` stayed around −0.9 to −1.3 through the end of Stage 1 training — real residual stochasticity. The action space is `MultiDiscrete([3,3,3])`, three independent per-junction categorical heads; `deterministic=True` takes the argmax independently per head, which need not coincide with the jointly-best combination if the reward depends on cross-junction coordination and the per-head distributions haven't sharpened enough yet. Kept `deterministic=True` as the default anyway because that is what an actually-deployed or demoed controller would run — a traffic signal controller sampling actions randomly is not a real operating mode — so the eval script should report what deployment would actually produce, even though it is currently the worse number.
**Deviates from plan?** No — this is a measurement/evaluation-methodology finding, not a change to any locked decision or to the trained artifact.
**Verified:** See the numbers above. Methodology note worth recording: an earlier attempt to gather the 10 stochastic episodes by launching separate `evaluate_stage.py --stochastic` processes in parallel produced corrupted data (7 of 10 output files missing, the 3 that existed byte-for-byte identical) — traced NOT to a TraCI port race but to `MaskablePPO.load()`'s `_setup_model()` calling `self.set_random_seed(self.seed)`, where `self.seed` is restored from the checkpoint's saved training-time seed (7). Every fresh `.load()` of this checkpoint reseeds torch's global RNG to the same value, so a `deterministic=False` sample immediately after a fresh load is reproducible across separate process launches — the "stochastic" runs were silently deterministic. Fixed by loading the model once and drawing all 10 episodes from that single running instance, letting the RNG genuinely advance between episodes. Worth remembering for any future SB3 evaluation script in this project that wants independent stochastic samples across process launches.

**§18 Phase 6, Stage 1 is complete** by §16's own checkpoint bars: "~10k steps: reward trending up" (confirmed) and "beats random-action baseline on wait time" (confirmed, worst wait 122.0s vs. 141.0s, seed-independent). Does not yet beat Tier 0 — not a red flag per §16's stated criteria. CLAUDE.md updated with a standing watch-item to re-check the deterministic-vs-stochastic gap at every future stage checkpoint. Stage 2 (`+ randomize_lane_counts=True`) not yet started — awaiting go-ahead.

**Stage 1 checkpoint-comparison table** (same numbers as the `Verified:` field above, tabulated for reference — `psychoflow_stage1_50960_steps_final.zip`, deterministic policy, corridor 4/3/2, seed 7, validator ON):

| Metric | This checkpoint | Shielded-random baseline | Tier 0 |
|---|---|---|---|
| Mean reward / step | +1.0 | −2.4 | +1.2 |
| Vehicles arrived | 4668 | 4668 | 4668 |
| Worst single-vehicle wait | 122.0s | 141.0s | 41.0s |
| Steps with a starved lane | 7/631 (1%) | 543/646 (84%) | 0/627 (0%) |
| §10 overrides | 1 (starvation_ceiling, applied) | — | 0 |

Seed-independence check (deterministic, same checkpoint, corridor 4/3/2): seeds 1/3/42 all land in the same band (worst wait 93.0-124.0s, mean reward +1.0 to displayed precision) — confirms seed 7 above is not a hard draw.

Deterministic-vs-stochastic reproducibility (bit-for-bit check, requested before Stage 2 review): two fully independent process runs of the deterministic seed-7 episode produced identical results to 9 decimal places (`mean_reward=1.028847596` both runs) and an identical 631-step proposed/executed action trace (`diff` exit code 0) — confirms the gap logged above is genuine, reproducible policy behavior, not unseeded randomness sneaking in from elsewhere in the pipeline (V2X noise §7.5, vision mock §7.2, etc.).

## 2026-08-15 — §18 Phase 6 (PPO training, Stage 2, §16)

**Decision:** Stage 2 (`+ randomize_lane_counts=True`) followed the same two-burst pattern as Stage 1 (Burst A: fresh model, 10k timesteps, actual `num_timesteps=10240`; Burst B: resume with `reset_num_timesteps=False`, actual final `num_timesteps=51200`), plus two additions not needed in Stage 1: (1) all 27 lane-count combos pre-generated via `generate_corridor()` before Burst A, matching `PsychoFlowEnv._ensure_corridor()`'s exact naming convention (`corridor_{j1}{j2}{j3}`), so no mid-training `netconvert` calls; (2) `training/train.py`'s `Monitor(raw_env, filename=...)` call changed to `Monitor(raw_env, filename=..., info_keywords=("lane_counts",))`, applied starting Burst B only — Burst A's 16 episodes predate the fix and have no `lane_counts` record in `monitor.csv`, left blank rather than backfilled or re-run.
**Why:** With a fixed topology (Stage 1), a rising reward curve unambiguously means the policy improved. With `randomize_lane_counts=True`, each episode draws a different topology, and topology difficulty itself affects achievable reward independent of policy quality — a rising raw curve could just as easily mean "easier topologies happened to be drawn later." Logging `lane_counts` per episode (SB3's `Monitor` already supports this via `info_keywords`; `PsychoFlowEnv` already puts `lane_counts` in its `info` dict every step, confirmed by reading `env/psychoflow_env.py` directly) makes that confound checkable instead of assumed away. Not re-running Burst A: the fix is additive logging only, changes no training behavior, and discarding an already-completed, already-reviewed burst to backfill 16 rows of metadata was judged not worth the compute.
**Deviates from plan?** No — pre-generation was already in the approved Stage 2 design plan; the `info_keywords` logging fix was raised as a live confound-check requirement during Burst A review and approved before Burst B started.
**Verified:** `git show 0daf26f --stat` — 78 files (26 new combos × 3 file types; `corridor_432` already cached from Stage 1). `monitor_burstB.csv.monitor.csv` header confirmed as `r,l,t,lane_counts` (was `r,l,t` in Stage 1 and in this run's own Burst A file) with real per-episode values, e.g. `"(3, 2, 3)"`, `"(4, 4, 4)"`.

**Decision:** Stage 2's reward trend (raw `mean_reward_per_step` rising from −4.03 at episode 1 to a ~1.25-1.33 plateau by episode ~40, holding through episode 81) is accepted as substantially a genuine policy-improvement signal, not primarily a topology-luck artifact — with one honest caveat below, not swept under the rug.
**Why:** Checked directly rather than assumed. Pearson correlation between `total_lanes` (j1+j2+j3) and `mean_reward_per_step` across Burst B's 65 logged episodes: **r = 0.0791, r² = 0.0063** — total lane count explains under 1% of reward variance episode-to-episode. Stronger check: splitting Burst B's episodes into early/late halves *within* each fixed `total_lanes` bucket (holding topology-difficulty-by-lane-count constant) — **5 of 7 buckets improve late vs. early** (bucket 6: 1.150→1.247; bucket 7: 1.231→1.263; bucket 8: 0.939→1.252; bucket 9: 0.897→1.268; bucket 10: 1.056→1.270), **bucket 11 does not improve** (1.279→1.276, delta −0.0036), and bucket 12 has only 1 episode total (no early-half comparison possible). The correction on record: an earlier draft of this finding incorrectly stated "every bucket improves" — the accurate count is 5/7, with bucket 11 flat-to-slightly-negative. Caveat: buckets 8 and 9 have early-heavy sample splits (9/5 and 10/8), overlapping the period Stage 2 was still generally converging, so part of their large deltas likely reflects concurrent overall training progress rather than a pure per-topology effect — this is a total-lane-count aggregate check, not a per-exact-topology control.
**Deviates from plan?** No — this is the confound check the pre-generation/logging-fix work above exists to enable.
**Verified:** Correlation and bucket computation shown in full (not just the summary statistic) — `n=65`, `cov=0.042802`, `Pearson r=0.079095` (pandas `.corr()` cross-check identical), full per-bucket early/late table with sample counts.

**Decision:** Stage 2's consistency sweep (§16's "Stage 2: consistent across all 3 lane-counts" checkpoint; 5 combos × seeds `{1,7,42}` = 15 deterministic episodes against the final checkpoint `psychoflow_stage2_51200_steps_final.zip`) found 4 of 5 swept combos consistent (`(4,3,2)`, `(2,2,2)`, `(4,4,4)`, `(2,4,2)`: mean reward 0.934–1.326, starved% 0.0–6.3% mean) and **one clear outlier: `(4,2,4)`** (mean reward 0.456, starved% mean 19.3%, individual seeds ranging 8.4–37.5%, worst wait 123–126s vs. 65–121s for every other combo). Diagnosed rather than left as an unexplained anomaly, via three follow-up checks against three candidate explanations:
  1. *Topology-luck / confound* — already ruled out by the bucket analysis above; the checkpoint's general upward trend is not primarily topology-driven, so `(4,2,4)`'s poor score is not "it just never got a fair shot at improving."
  2. *Inherently hard topology, not a policy gap* — ruled out. Tier 0 (rule-based, `agents.rule_based.Tier0Controller`, no learning) on the identical `(4,2,4)` / seed set: mean reward **1.204**, worst wait **40.3s**, **0.0% starved on all 3 seeds**. `(4,2,4)` is solvable; the trained checkpoint specifically fails to solve it.
  3. *Undersampling during training* — not the primary cause, though the picture is more nuanced than first stated. `(4,2,4)` drew 2 of Burst B's 65 episodes — *below* the uniform expectation of 2.41/combo (an earlier draft of this finding incorrectly called 2 draws "at-or-above" the average; corrected here). However, `(4,3,2)` and `(4,4,4)` each drew only 1 episode — less than `(4,2,4)` — and both score far better in the sweep (1.220 and 0.934 mean reward respectively). Raw exposure count alone does not explain the gap. Caveat: 65 episodes spread across 27 combos is a thin per-combo sample generally (2 zero-draw combos: `(2,3,3)`, `(3,2,2)`), so this check has limited statistical power either way.
  4. *Mechanism, identified*: per-seed diagnostic trace (full `reward_breakdown` + executed-action capture, all 3 seeds) shows the checkpoint's J2 phase-time allocation (J2 is the 2-lane bottleneck in this combo) is **nearly seed-invariant** — executed phase-0/phase-1 split of {153,481} (seed 1), {156,476} (seed 7), {153,475} (seed 42) — despite the three seeds drawing genuinely different, and differently-*directioned*, demand skews at J2: worst-lane-hit north/south split is 39.7%N/60.3%S (seed 1), **88.1%N/11.9%S (seed 7)**, 30.9%N/69.1%S (seed 42). Seed 7 is not merely "a hard seed" — it is the most extreme demand skew of the three, in the opposite direction from seeds 1/42's mild south-skew, and the policy's fixed ~24%/76% phase-time split does not track that reversal. This shows up mechanically as `safety.validator`'s hard `starvation_ceiling` override firing **10 times on seed 7 vs. 1 time each on seeds 1 and 42** — the policy is not proactively adapting to which approach is loaded, and the safety gate is catching the resulting starvation late rather than the policy avoiding it.
**Why:** §16's Stage 2 checkpoint requires "consistent across all 3 lane-counts" — a single outlier combo needed a real explanation, not just a number, before deciding whether it blocks progression to Stage 3.
**Deviates from plan?** No — the consistency sweep itself was the approved Stage 2 design; the follow-up diagnostic chain was requested and performed in response to the sweep's own result, same "diagnose before deciding" discipline as every other checkpoint in this project.
**Verified:** Full 15-row sweep table, full 3-seed Tier 0 comparison table, full 27-combo draw-count table (all pasted as raw command output, not summarized, per this session's reporting discipline), full per-seed diagnostic trace (`reward_breakdown`, executed actions, override counts, worst-lane N/S split recomputed from raw hit counts after an initial mischaracterization — corrected in this entry, not silently fixed).

**Decision:** Stage 2 checkpoint accepted as passing §16's core bar — beats the recorded baselines, reasonably consistent across 4 of 5 swept combos — with the `(4,2,4)`-class narrow-middle/wide-ends topology under skewed demand logged as one specific, diagnosed exception. Proceeding to Stage 3 (`+ randomize_density=True`); **not** extending Stage 2 training to specifically target this gap.
**Why:** User's decision after reviewing the full diagnostic chain above. The diagnosis narrows what a Stage-2 extension would even be fixing: it is not an exposure/sampling gap (item 3 above — more random `randomize_lane_counts=True` draws is not a targeted fix for a policy that already saw `(4,2,4)`-comparable exposure and still failed to adapt its serving ratio), so more of the same Stage 2 training has no clear mechanism to close it. Logged as a known, diagnosed limitation to revisit rather than a blocking failure.
**Deviates from plan?** No locked decision (§2) is touched. §18's phase/stage ordering is unaffected — this is a within-Phase-6 stage-sequencing call, and CLAUDE.md does not lock Stage-to-Stage progression on every sub-metric being perfect, only on §16's stated checkpoint bars, which Stage 2 passes with one named exception now on record.
**Verified:** See the sweep/Tier0/exposure/mechanism verification above. CLAUDE.md updated with a new standing watch-item (narrow-middle-bottleneck adaptivity gap) to re-check at every future stage checkpoint (3/4/5) and flagged as possibly sharing a root cause with the existing determinism/per-head watch-item — both are, at heart, "policy behavior not sufficiently sensitive to actual state" (per-head argmax insensitive to cross-junction coordination; per-junction phase allocation insensitive to which approach is actually loaded).

**§18 Phase 6, Stage 2 is complete** by §16's own checkpoint bars: "~10k steps: reward trending up" (confirmed, Burst A), "consistent across all 3 lane-counts" (confirmed for 4 of 5 swept combos; `(4,2,4)` is a diagnosed, logged exception — not a silent gap). Topology-vs-reward confound checked and substantially ruled out (r²=0.63%, 5/7 buckets improve independent of topology). Stage 3 (`+ randomize_density=True`) not yet started — design plan to be stated and confirmed before any code, same discipline as every prior stage.

**Stage 2 consistency-sweep table** (`psychoflow_stage2_51200_steps_final.zip`, deterministic policy, validator ON, seeds `{1,7,42}` per combo):

| Combo | mean_reward (mean/min/max) | worst_wait_s (mean/min/max) | starved_pct (mean/min/max) |
|---|---|---|---|
| (4,3,2) | 1.220 / 1.214 / 1.224 | 88.3 / 79.0 / 103.0 | 0.2 / 0.0 / 0.6 |
| (2,2,2) | 1.224 / 1.193 / 1.240 | 88.0 / 66.0 / 125.0 | 0.4 / 0.0 / 1.1 |
| (4,4,4) | 0.934 / 0.916 / 0.953 | 114.0 / 103.0 / 121.0 | 5.8 / 5.1 / 6.3 |
| (2,4,2) | 1.326 / 1.323 / 1.331 | 66.0 / 65.0 / 67.0 | 0.0 / 0.0 / 0.0 |
| (4,2,4) | 0.456 / 0.189 / 0.615 | 124.7 / 123.0 / 126.0 | 19.3 / 8.4 / 37.5 |

`(4,2,4)` vs. Tier 0 on the identical combo/seeds — the "inherently hard vs. policy gap" test:

| Seed | RL mean_reward | RL worst_wait | RL starved% | Tier 0 mean_reward | Tier 0 worst_wait | Tier 0 starved% |
|---|---|---|---|---|---|---|
| 1 | 0.5652 | 125.0s | 12.0% | 1.1907 | 45.0s | 0.0% |
| 7 | 0.1891 | 126.0s | 37.5% | 1.2044 | 39.0s | 0.0% |
| 42 | 0.6147 | 123.0s | 8.4% | 1.2176 | 37.0s | 0.0% |

## 2026-08-15 — §18 Phase 6 (PPO training, Stage 3, §16)

**Decision:** Stage 3 (`+ randomize_density=True`) departed from Stage 1→2's pattern in one deliberate way: Burst A **resumed from Stage 2's final checkpoint** (`psychoflow_stage2_51200_steps_final.zip`, `reset_num_timesteps=False`) rather than starting a fresh model. This surfaced a finding worth recording on its own: Stage 1→2 was never actually a continuous curriculum — Stage 2's Burst A used a fresh randomly-initialized model (confirmed by re-reading `training/train.py`'s branch logic and the exact command used), so Stage 2's checkpoint never benefited from Stage 1's 50,960 steps of training. This was not a deliberate prior decision, just an artifact of copying Stage 1's command template forward. Stage 3 onward now resumes properly; Stage 1→2's gap was not retroactively fixed (re-running Stage 2 as a resume was considered and explicitly declined — cost not justified given Stage 2 already passed its checkpoint).
**Why:** User's explicit call after the fork was surfaced and both options (resume vs. fresh, vs. retroactively fixing Stage 2) were presented. Resuming is the standard meaning of curriculum learning and matches §16's framing of stages as cumulative deltas on one progression; not retrofitting Stage 2 avoids re-spending ~51k steps of compute on a stage that already passed its own checkpoint bar.
**Deviates from plan?** Yes, from what was *implicitly* being done (fresh-model-per-stage) — but that was never an explicit decision to begin with, so this is a correction, not a reversal of a locked call. No CLAUDE.md §2 locked decision is touched.
**Verified:** `stage_dir = CHECKPOINTS_ROOT / f"stage{stage}"` (`training/train.py:94`) confirmed by reading — derives from the `--stage` CLI flag only, independent of `--resume`'s path, so Stage 3's checkpoints correctly landed in `training/checkpoints/stage3/` despite resuming a `stage2/` checkpoint. Obs/action space confirmed identical across Stage 1/2/3 configs by constructing all three and printing: `Box(-10.0, 10.0, (3, 191), float32)` / `MultiDiscrete([3 3 3])` for all three, `obs match 1==2==3: True`, `action match 1==2==3: True`.

**Decision:** Added `density_mult_corridor`/`density_mult_cross` to `PsychoFlowEnv`'s `info` dict (both `reset()` and `step()`) and to `Monitor`'s `info_keywords` in `train.py`, mirroring Stage 2's `lane_counts` logging fix — applied proactively *before* Burst A this time (Stage 2's fix only started at its Burst B, leaving Burst A's 16 episodes unlogged). `sim/scenario_generator.py`'s `write_route_file()` now returns `(path, density_summary)` — `density_summary` is the mean of each route group's independently-drawn multiplier (`corridor_mean` over 2 flows, `cross_mean` over 6 flows), not the full 8-value per-flow draw, kept as a lightweight two-number summary.
**Why:** Stage 2 needed density-adjacent logging added mid-stream because the confound wasn't anticipated until Burst A's review; applying the same class of fix proactively for Stage 3's own new randomization axis (density) avoids repeating that gap.
**Deviates from plan?** No — this was item 4 of the approved Stage 3 design plan.
**Verified:** Live construction check — Stage 1 config gives `density_mult_corridor=1.0000 density_mult_cross=1.0000` exactly (as expected, `randomize_density=False`); Stage 3 config gives real, independently-varying, non-round values, e.g. `density_mult_corridor=0.9002 density_mult_cross=0.9797`. `monitor.csv` header confirmed live as `r,l,t,lane_counts,density_mult_corridor,density_mult_cross` with real per-episode values from the actual Burst A run. Full regression suite re-run clean after the change: `python -m env.reward`, `python -m safety.validator`, `python sim/run_env_smoke.py`.

**Decision:** Stage 3's reward curve is accepted as a genuine improvement under harder conditions, with one qualifier logged precisely rather than glossed as "resolves cleanly like Stage 1's Burst A did."
**Why:** Burst A (16 episodes, resumed to `num_timesteps=61,440`) showed raw reward oscillating in [0.92, 1.50] with a rolling mean that peaked at episode 5 (1.30) then declined to 1.12–1.22 by episode 16 — flagged as ambiguous (not collapse, but not a clean climb either) pending a confound check. That check (episode-level `total_lanes`/`density_mult_corridor`/`density_mult_cross` against reward) found no draw-luck explanation for the decline — if anything late episodes were mildly *easier* by lane-count and *harder* by corridor density, the opposite of what would explain lower reward via luck. Burst B (40k additional timesteps, resumed to `num_timesteps=102,400`) then showed the reward gain holding up under genuinely harder conditions: Burst B's late half scored higher mean reward (1.2607 vs. early half's 1.2276, delta +0.0331) while drawing *higher* corridor density on average (0.9981 vs. 0.9390, delta +0.0591) and materially unchanged lane-count (delta −0.07, negligible) — the improvement is not explained by an easier draw mix. Per-`total_lanes`-bucket, **4 of 6 comparable buckets improve late vs. early (6, 7, 8, 9 lanes); 2 decline (10, 11 lanes)** — bucket 12 has no early-half episodes, not comparable. The correction on record: an earlier draft of this finding said "4 of 5" and did not call out that bucket 10 — the single largest bucket in the whole set at n=23, nearly a third of all 65 Burst-B episodes — was one of the two that declined (delta −0.0763). This is a real qualifier, not a footnote: the checkpoint's improvement is broad-based across most topology-difficulty buckets but not universal, and the exception sits in the most heavily-sampled bucket. Variance narrowed monotonically across Burst B (raw std: Burst A 0.184 → Burst B first half 0.167 → second half 0.114 → last 10 episodes 0.112) but did **not** compress to anything like Stage 2's converged plateau (episodes 60–81: std 0.0313, range 0.1140) — Stage 3's last 10 episodes still show ~3.6× Stage 2's std and ~3.1× its range. Read as: genuinely adapting, correct direction, but density is a harder axis to converge on than lane-count was, at least within this stage's ~41k-step Stage-3-specific budget.
**Deviates from plan?** No — this is the confound check and variance report specified as post-Burst-A/B follow-ups, not a plan change.
**Verified:** Full 81-episode concatenated table (Burst A ep 1–16, Burst B ep 17–81) with `lane_counts`/`density_mult_corridor`/`density_mult_cross` per episode; early/late split computation shown in full (not just summary stats); per-bucket table with all 7 `total_lanes` buckets including 6 and 10 explicitly; variance table across four sub-windows plus the exact recomputed Stage 2 reference (not recalled from memory).

**Decision:** Stage 3's density-consistency sweep (5 combos × seeds `{1,7,42}` × density levels `{0.7, 1.0, 1.3}` = 45 deterministic episodes against `psychoflow_stage3_102400_steps_final.zip`) confirms `(4,2,4)`'s gap as **structural and density-independent**, and surfaces a **second, distinct, density-triggered gap in `(2,4,2)`**.
  - `(4,2,4)`: worst_wait elevated (87–95s mean) and starved_pct nonzero (0.4–0.5% mean) at **every** density level including the lowest (0.7×), where every other combo shows exactly 0.0% starved. Worst_wait is roughly flat-to-slightly-improving across density (94.7 → 88.0 → 87.3), not worsening with more traffic — the "density could compound the bottleneck gap" hypothesis from the Stage 3 design plan does not hold for this combo. This strengthens, not just repeats, the Stage 2 diagnosis: the same phase-invariant-serving-ratio mechanism identified there is consistent with a gap that shows up regardless of traffic volume, since the mechanism (fixed phase-time allocation not tracking which approach is loaded) has nothing to do with how much traffic is present.
  - `(2,4,2)`: **new finding, not previously flagged.** Clean at 0.7×/1.0× (worst_wait ~53–56s, 0.0% starved, in line with every other well-behaved combo) but degrades specifically at 1.3× (worst_wait mean 83.0s, max 99.0s, starved% mean 0.2%). Not deeply investigated — no per-seed diagnostic trace run for this combo the way `(4,2,4)` got in the Stage 2 entry. Logged as an open, separate item.
  - Caveat recorded explicitly: `mean_reward` is **not a valid axis for cross-density comparison** — `env/reward.py`'s `throughput_bonus` term scales with vehicles arrived, which itself scales with traffic density, so every combo's mean_reward rises predictably with density level regardless of policy quality (e.g. `(4,3,2)`: 0.698 → 1.192 → 1.730 across 0.7×/1.0×/1.3×, a near-uniform step for every combo). `(4,2,4)`'s mean_reward looked much less like an outlier in this sweep (0.733–1.806, close to peers) than in the Stage 2 sweep (0.456 vs. peers' 0.93–1.33) — but that narrowing is partly this density-reward scaling artifact, not proof the underlying gap closed. `worst_wait`/`starved_pct` remain the correct, density-normalized metrics, and by those `(4,2,4)` is unambiguously still the outlier at every level. This should have been anticipated before building the sweep, not caught after seeing the numbers — recorded as a standing gotcha in CLAUDE.md for any future cross-density eval work.
**Why:** Item 6 of the approved Stage 3 design plan — explicitly re-checking `(4,2,4)` under density variation, since density was flagged as a plausible compounding factor.
**Deviates from plan?** No — this is exactly the sweep the plan specified, deferred until after Burst B as agreed.
**Decision (Stage 3 status / next step):** Stage 3 checkpoint accepted — reward improved under genuinely harder conditions (majority of buckets, not draw-luck), `(4,2,4)`'s gap confirmed structural rather than density-driven, `(2,4,2)`'s new gap logged but not investigated further right now. Proceeding to Stage 4 (`+ spawn_emergencies=True`); **not** further investigating either `(4,2,4)` or `(2,4,2)` at this time.
**Verified:** Full 45-run raw sweep table (pasted, not summarized) plus the per-`(combo, density)` aggregate table (mean/min/max per group). CLAUDE.md updated with a third standing watch-item (`(2,4,2)` density-sensitive degradation) and the mean_reward cross-density-comparison gotcha.

**§18 Phase 6, Stage 3 is complete** by §16's own checkpoint bars — no explicit "after Stage 3" row exists in §16's table (it jumps from Stage 2 to Stage 4), so the applied bar was the generic "reward trending up, not collapsing" plus an extension of Stage 2's own consistency-sweep methodology to the new density axis. Both are satisfied, with the `(4,2,4)` and `(2,4,2)` exceptions logged precisely rather than smoothed over. Stage 4 (`+ spawn_emergencies=True`) not yet started — design plan to be stated and confirmed before any code, same discipline as every prior stage.

**Stage 3 density-consistency sweep table** (`psychoflow_stage3_102400_steps_final.zip`, deterministic policy, validator ON, seeds `{1,7,42}` per combo/density):

| Combo | Density | mean_reward (mean/min/max) | worst_wait_s (mean/min/max) | starved_pct (mean/min/max) |
|---|---|---|---|---|
| (4,3,2) | 0.7x | 0.698 / 0.688 / 0.715 | 33.0 / 31.0 / 35.0 | 0.0 / 0.0 / 0.0 |
| (4,3,2) | 1.0x | 1.192 / 1.185 / 1.202 | 39.7 / 39.0 / 40.0 | 0.0 / 0.0 / 0.0 |
| (4,3,2) | 1.3x | 1.730 / 1.653 / 1.775 | 52.7 / 40.0 / 68.0 | 0.0 / 0.0 / 0.0 |
| (2,2,2) | 0.7x | 0.755 / 0.740 / 0.768 | 55.0 / 43.0 / 71.0 | 0.0 / 0.0 / 0.0 |
| (2,2,2) | 1.0x | 1.309 / 1.294 / 1.322 | 58.7 / 52.0 / 68.0 | 0.0 / 0.0 / 0.0 |
| (2,2,2) | 1.3x | 1.872 / 1.860 / 1.887 | 43.3 / 41.0 / 47.0 | 0.0 / 0.0 / 0.0 |
| (4,4,4) | 0.7x | 0.836 / 0.826 / 0.848 | 43.3 / 40.0 / 48.0 | 0.0 / 0.0 / 0.0 |
| (4,4,4) | 1.0x | 1.364 / 1.362 / 1.365 | 40.7 / 39.0 / 42.0 | 0.0 / 0.0 / 0.0 |
| (4,4,4) | 1.3x | 1.918 / 1.911 / 1.922 | 41.3 / 40.0 / 43.0 | 0.0 / 0.0 / 0.0 |
| (2,4,2) | 0.7x | 0.845 / 0.840 / 0.855 | 54.7 / 53.0 / 58.0 | 0.0 / 0.0 / 0.0 |
| (2,4,2) | 1.0x | 1.349 / 1.341 / 1.363 | 52.3 / 50.0 / 56.0 | 0.0 / 0.0 / 0.0 |
| (2,4,2) | 1.3x | 1.835 / 1.794 / 1.869 | 83.0 / 57.0 / 99.0 | 0.2 / 0.0 / 0.3 |
| (4,2,4) | 0.7x | 0.733 / 0.701 / 0.754 | 94.7 / 73.0 / 115.0 | 0.4 / 0.0 / 0.8 |
| (4,2,4) | 1.0x | 1.253 / 1.202 / 1.278 | 88.0 / 45.0 / 125.0 | 0.4 / 0.0 / 1.1 |
| (4,2,4) | 1.3x | 1.806 / 1.767 / 1.841 | 87.3 / 38.0 / 121.0 | 0.5 / 0.0 / 1.1 |

## 2026-08-15 — §18 Phase 6 (PPO training, Stage 4, §16)

**Decision:** Stage 4 (`+ spawn_emergencies=True`) trained in the established two-burst pattern, resuming from Stage 3's final checkpoint (Burst A to `num_timesteps=112,640`; Burst B resumed to `num_timesteps=153,600`). Logging was extended proactively *before* Burst A (not mid-stage as in Stage 2): `sim/scenario_generator.py`'s `write_route_file()` now returns `(path, density_summary, emergency_info)`, and `PsychoFlowEnv` threads `emergency_route` / `emergency_route_type` (corridor vs. cross) / `emergency_depart_s` into `info` on both `reset()` and `step()`, with all three added to `Monitor`'s `info_keywords`.
**Why:** `spawn_emergencies=True` spawns exactly `n_emergencies=1` ambulance per episode on a route drawn uniformly from all 8 named routes — so the randomization axis is *which approach and when*, not presence/absence. A cross-street ambulance is a structurally different (and potentially harder-to-serve) draw than a corridor-through one, and neither was recoverable after the fact without logging it at draw time.
**Deviates from plan?** No — this was item 6 of the approved Stage 4 design plan, approved before Burst A.
**Verified:** Live printed `info` dict — a no-emergency config (Stage 1) gives `emergency_route='' emergency_route_type='' emergency_depart_s=nan`; Stage 4's config gives real values (`emergency_route='r_sn3' emergency_route_type='cross' emergency_depart_s=573.8878717787998`, correctly inside the configured `(300, 2400)` window). Live `monitor.csv` header from the actual Burst A run: `r,l,t,lane_counts,density_mult_corridor,density_mult_cross,emergency_route,emergency_route_type,emergency_depart_s`, with varying real per-episode values (`r_ew`/corridor, `r_ns1`/cross, `r_we`/corridor). Full regression suite clean after the change: `python -m env.reward`, `python -m safety.validator`, `python sim/run_env_smoke.py`.

**Decision:** Stage 4's reward trend is accepted as genuine improvement, not draw-luck — confirmed against all four randomized axes now logged.
**Why:** Burst B's late half scored +0.1364 higher mean reward than its early half (1.2692 vs. 1.1328) while drawing *slightly higher* density on both axes (corridor +0.0250, cross +0.0148) and only marginally fewer lanes (−0.56) — the improvement is not explained by an easier draw mix. Route type showed no clean signal (corridor n=11, mean 1.1427; cross n=53, mean 1.2131 — cross has the higher mean but also both catastrophic outliers, and the corridor sample is small). Departure timing showed effectively zero correlation with reward (Pearson r = −0.0395, n=64), which clears the "early ambulance arrival is systematically harder" hypothesis raised after Burst A's unexplained episode-11 dip — though the early-window bucket does carry the highest variance (std 0.271 vs ~0.20), so timing volatility is concentrated rather than absent. Variance narrowed across the burst (std: Burst A 0.2153 → Burst B first half 0.2744 → second half 0.1495 → last 10 episodes 0.1365), with the first half's figure inflated by one severe outlier rather than uniform noise.
**Deviates from plan?** No — this is the confound check specified as a post-Burst-B follow-up.
**Verified:** Full 80-episode concatenated table with all four axes per episode; early/late split computation shown in full; route-type and departure-window groupings shown with per-group n/mean/std/min/max.

**Decision — §16 CHECKPOINT FAILED. Stage 4's stated bar, "near-100% emergency priority in test episodes," was NOT met.** Measured result: §10's `emergency_override` fired in **15 of 15** sweep runs (5 combos × seeds `{1,7,42}`, `spawn_emergencies=True`, deterministic, validator ON, against `psychoflow_stage4_153600_steps_final.zip`). That is **0% proactive emergency handling** — the policy never once served an approaching ambulance on its own initiative across the entire sweep; the safety validator intervened every single time. Per-combo override-firing rate was 3/3 for every combo tested, with mean summed `emergency_penalty` ranging 13.333 (`(4,3,2)`, `(4,2,4)`) to 46.667 (`(2,4,2)`) and mean `emergency_blocked_junctions` events 0.67 to 2.33. For contrast, Phase 4's Tier 0 baseline (B1) required **zero** overrides across a full episode — the rule-based controller handles this correctly and the trained policy does not.
**Why this is logged as a failure, not a watch-item:** the naive metric ("was the ambulance served?") is structurally guaranteed to read 100% for *any* policy, including a random one, because §10's override makes leaving an ambulance unserved impossible by construction. The measure that actually reflects policy quality is how often the validator must intervene — and by that measure the result is unambiguous and uniform across every topology tested, not a marginal or topology-specific shortfall.
**Plausible but UNCONFIRMED explanation (hypothesis only, not diagnosed):** the training signal for emergency handling may simply be too sparse to learn from. Across ~80 Stage 4 episodes, each contained exactly one ambulance (`n_emergencies=1`), producing a one-off −20 `w_emergency` penalty set against hundreds of decision steps of ordinary throughput/starvation reward per episode. The gradient contribution from emergency events is therefore a very small fraction of total episode reward, and the policy may never have received enough signal to learn the behavior — as opposed to having learned it and failing to execute. **This has not been tested.** Distinguishing "insufficient signal" from "learned but not executed" would require something like raising `n_emergencies`, up-weighting `w_emergency`, or measuring the policy's pre-override proposed action specifically at ambulance-present steps; none of that was run.
**Known measurement limitation (logged, not fixed):** the sweep's detection-to-green latency figures are unreliable and several are negative (−42.0s to −2.0s). Cause: the harness tracks detection and green-onset at the *first* junction the ambulance is detected at, while `override_fired` is a whole-episode flag — on corridor-through routes (J1→J2→J3) the override can fire at a later junction than the one latency is measured at, and green-onset recovered via `sim_time - time_since_switch_s` can predate detection if that junction was already green for unrelated reasons. The binary `override_fired` result is unaffected (a direct read of `info["safety_overrides"]`, no timing arithmetic), so the 15/15 finding stands independently of this bug. Not fixed — deferred as a measurement-harness issue, not a system defect.
**Also noted:** because `lane_counts` and density are pinned (not randomized) in this sweep, the ambulance route/timing draw depends only on `seed`, so all 5 combos at a given seed receive an identical emergency scenario (seed 1: `corridor r_ew` @ 582.2s; seed 7: `cross r_ns1` @ 980.0s; seed 42: `corridor r_we` @ 1642.8s). This makes the 15/15 result *more* robust as a claim (three distinct emergency scenarios, override fires under all five topologies for each) but means route/timing were not independently varied per combo. The check for correlation with the flagged `(3,2,3)`/`(3,2,4)` combos returned "0 of 15" — **uninformative by construction**, since neither flagged combo is in the pre-approved 5-combo sweep matrix; this is not evidence of absence.
**Deviates from plan?** No — the sweep, its metric definition, and its matrix were all specified in the approved Stage 4 design plan (item 4/5) and deferred to after Burst B as agreed.
**Verified:** Full 15-run raw sweep output pasted (per-run override flag, summed penalty, blocked-event count, latency, route type, departure time), plus per-combo aggregate table.

**Decision:** Proceed to Stage 5 (MARL, §9.5) rather than remediating Stage 4's emergency-priority failure first.
**Why:** User's call after reviewing the failed checkpoint. Recorded explicitly so it is not mistaken later for an oversight: §16's Stage 4 bar is on record as FAILED and unremediated, and this is a known-accepted state going into Stage 5, not a passing checkpoint. Stage 5's own evaluation is expected to re-test emergency handling (§9.5's neighbor-aware attention is a plausible mechanism for it, since an ambulance approaching on a corridor route is precisely cross-junction state), which is the natural place for this to be revisited.
**Deviates from plan?** Yes, in the sense that §18's phase discipline ("don't start phase N+1 until phase N's done-bar has been verified") would ordinarily block progression on a failed checkpoint. Flagged explicitly rather than silently: this is a deliberate, user-authorized exception with the failure recorded above, not a done-bar quietly treated as met.
**Verified:** N/A — this is a scheduling decision, not a technical result.

**§18 Phase 6, Stage 4 status: trained and reward-verified, but its §16 emergency-priority checkpoint FAILED and is unremediated.** Reward trend confirmed genuine against all four randomized axes (lane counts, density, ambulance route type, ambulance timing). Bottleneck watch-item re-tested and substantially narrowed (see CLAUDE.md's updated Stage-2 watch-item: `(4,2,4)` resolved; a confirmed, repeatable `j1=3` vulnerability isolated to `(3,2,3)` and `(3,2,4)` specifically, verified causal via a same-seed test and verified not to be ceiling-masking). Stage 5 (MARL, §9.5) is next.

**Stage 4 emergency-priority sweep table** (`psychoflow_stage4_153600_steps_final.zip`, deterministic, validator ON, `spawn_emergencies=True`, seeds `{1,7,42}` per combo):

| Combo | override fired | mean summed emergency_penalty | mean emergency_blocked events |
|---|---|---|---|
| (4,3,2) | 3/3 | 13.333 | 0.67 |
| (2,2,2) | 3/3 | 20.000 | 1.00 |
| (4,4,4) | 3/3 | 20.000 | 1.00 |
| (2,4,2) | 3/3 | 46.667 | 2.33 |
| (4,2,4) | 3/3 | 13.333 | 0.67 |
| **TOTAL** | **15/15** | — | — |

## 2026-08-15 — §18 Phase 7 (MARL, §9.5) — gradient-flow verification + watch-item re-check harness

**Decision:** Added a gradient-flow verification for both §9.5 extractors, run against the Stage 5 `graph_attention` Burst A checkpoint (`psychoflow_stage5_10240_steps_final.zip`) and against a freshly-constructed `SharedPolicyExtractor` model — the latter deliberately checked BEFORE its own Burst A ever runs.
**Why this check mattered, stated plainly:** the extractor smoke test proved both modules compute the right thing in a forward pass; it proved nothing about whether their parameters actually *learn*. A custom feature extractor whose parameters are constructed but never registered with the optimizer trains **silently**: the rest of the network still learns, the loss falls, the reward curve rises, nothing raises, and the attention layer sits frozen at its initialisation for the entire run. There is no visible symptom to notice. That is the same failure class as the `--stage 5 --resume` bug caught in the previous round (where `MaskablePPO.load()` did NOT raise on an architecture mismatch and would have silently trained a `FlattenExtractor` under a MARL label) — both are cases where the wrong thing succeeds quietly, which CLAUDE.md §8 names as this repo's characteristic failure mode. Checking after 50k timesteps would have meant discovering it only by puzzling over why attention made no difference.
**Methodology — three properties, each independently falsifiable:** (1) **optimizer registration compared BY IDENTITY** — every extractor tensor's `id()` is checked for membership in the set of `id()`s collected from `policy.optimizer.param_groups`, *not* by name and *not* by presence in `named_parameters()`, because a module tree can legitimately contain parameters the optimizer never sees; (2) **gradient arrival** — grads are zeroed, a synthetic scalar loss depending on the full policy forward path (both value and latent-policy heads, so gradients must route back *through* the extractor) is backpropagated, and each watched parameter's `.grad` must be non-None and non-zero (a registered-but-disconnected parameter yields `grad=None`); (3) **weight movement** — after `optimizer.step()`, the tensor must actually differ from a pre-step clone, since a non-zero gradient with a zeroed LR or a frozen group would still leave it static.
**Deviates from plan?** No — §9.5 requires both extractors be "built and tested for basic functionality before large-scale training starts"; this is part of satisfying that, beyond the shape/mechanism smoke test already recorded.
**Verified — actual measured numbers, both extractors:**

| Extractor | State | Optimizer | Extractor params | All registered by `id()`? |
|---|---|---|---|---|
| `GraphAttentionExtractor` | trained, `num_timesteps=10240` | Adam, 1 group, 22 tensors | 10 | yes |
| `SharedPolicyExtractor` | untrained (pre-Burst-A) | Adam, 1 group, 16 tensors | 4 | yes |

| Watched parameter | grad_norm after backward | max abs weight change after `step()` |
|---|---|---|
| `attention.in_proj_weight` (192, 64) | 2.067585e+01 | 8.703226e-04 |
| `attention.out_proj.weight` (64, 64) | 5.670167e+00 | 8.654539e-04 |
| `encoder.0.weight` (64, 191) — shared mode | 4.594777e+00 | 2.999902e-04 |
| `encoder.2.weight` (64, 64) — shared mode | 2.911764e+00 | 2.999902e-04 |

All assertions passed for both modes. The attention layer in the trained checkpoint is genuinely learning, not decorative, and the shared-policy encoder is confirmed optimizer-tracked before any budget is spent on it.

**Decision:** Built the watch-item re-check harness as two flags on `training/evaluate_stage.py` (`--j1-recheck`, `--emergency-recheck`) rather than a separate script, keeping one evaluation entry point. Prepared in advance of Stage 5's Burst B checkpoint landing, not written reactively afterwards.
**Why:** Both re-checks will be run repeatedly — against `graph_attention`, against `shared_policy`, and against Stage 1-4 checkpoints for reference. Writing them ahead of the checkpoint avoids the temptation to shape a measurement around a number already seen.
**Design points worth recording:** (a) both functions take **only a checkpoint path** and are entirely coordination-mode-unaware — `MaskablePPO.load()` restores whichever extractor the checkpoint carries and `ppo_picker` only needs `model.predict()` — which is precisely what makes a like-for-like mode-vs-mode comparison possible; (b) `--j1-recheck` runs 12 deterministic episodes (`(3,2,3)`/`(3,2,4)` × seeds `{1,3,7,42}`, plus `j1=4` controls `(4,2,3)`/`(4,2,4)` × `{1,3}`) with density pinned at nominal and no emergency spawn, matching exactly how the Stage 4 reference numbers were measured, and prints each run beside its Stage 4 reference value and delta; (c) `--emergency-recheck` reuses Stage 4's exact 5×3 matrix and reports override-firing rate against that 15/15 baseline; (d) **detection-to-green latency is deliberately OMITTED** from the re-check rather than reported — the Stage 4 harness's latency figures were wrong (several negative, caused by tracking green-onset at the first junction the ambulance was detected at while the override could fire at a later junction on corridor routes), that bug is still unfixed, and emitting the number would propagate a known-bad metric instead of leaving a visible gap.
**Verified:** Syntax-checked (`ast.parse`), imported cleanly, both functions callable, run-plan counts confirmed (12 and 15), Stage 4 reference table confirmed to cover all 12 planned `--j1-recheck` runs, and both flags confirmed present in `--help`. **Neither sweep was executed** — Stage 5 Burst B was training concurrently and the harness is intentionally staged for the moment its checkpoint lands.

**CLAUDE.md updated:** the existing `(2,4,2)` density watch-item now records an independent corroboration from Stage 5's `graph_attention` cold-start Burst A — episode 8, the worst of that burst (mean_reward −3.6081), drew `(2,4,2)` at the highest corridor density of the run (`density_mult_corridor=1.1653` vs a 0.9298 rest-of-burst mean), the same combo and same high-density direction as the original Stage 3 finding, and not explained by the other candidates (not a flagged `j1=3` combo, not a narrow-middle bottleneck, not the earliest ambulance — that was episode 11, which scored +0.7337). Logged as n=1 and confounded with cold-start noise, explicitly NOT upgrading the item's status.

## 2026-08-15 — §18 Phase 7 (MARL, §9.5) — Stage 5 `graph_attention` Burst A + B

**Decision:** Stage 5's `graph_attention` mode trained to a final `num_timesteps=51624` (`psychoflow_stage5_51624_steps_final.zip`), fresh model, under Stage 4's full `ScenarioConfig` (all three randomisation axes on). Burst A ran 10,240; Burst B was launched for 40,000 more but was **interrupted at ~38,912 by a laptop battery power-cut** (hard power loss, not a code fault, not a graceful sleep); the last checkpoint written was 35,240, and training was resumed from there for a further 15,000, landing at 51,624.
**Why the resume rather than a restart:** the 35,240 checkpoint was verified intact before being trusted, on two axes chosen because a hard power-off threatens mid-write files more than a completed zip: (1) **file-size progression** — the series runs 867,685 / 868,607 / 868,608 / 869,578 / 870,486 / 871,358 / 872,236 / **873,112** bytes, i.e. monotonically increasing with 35,240 the LARGEST, which is the opposite of what a truncated write produces; the Stage 4 final is larger still (1,049,274) but that is expected, since its `FlattenExtractor` policy carries 82,442 parameters against graph-attention's 66,890; (2) **monitor CSV tail** — the final row is well-formed with a plausible `r/l/t` triple (628.422207 / 634 / 1742.552855 → 0.9912 reward/step), the file ends with a proper `\r\n`, and a raw comma-split showing 11 tokens rather than 9 is explained by `lane_counts` being a quoted tuple containing two commas, consistent across every row rather than evidence of truncation. The 46-episode / 29,195-step count therefore stands with no row dropped. Restarting Burst B from 10,240 would have discarded ~25k timesteps of verified-good training for no benefit.
**Deviates from plan?** No. The interruption was environmental. System sleep and hibernate timeouts were subsequently set to never (`powercfg /change standby-timeout-ac|dc 0`, `hibernate-timeout-ac|dc 0`; the prior standby value was 1200s / 20 min on both AC and DC — hibernate's prior value was not captured before the change) to remove the other interruption mode; note this does not protect against power loss.

**CAVEAT ON THE COMMITTED RECORD — the reward curve is not a clean continuous history, and should not be read as one.** The concatenated plot spans three monitor files (`monitor.csv`, `monitor_burstB.csv.monitor.csv`, `monitor_burstB_resumed.csv.monitor.csv`) totalling **86 episodes and 54,673 summed episode-steps**, while the model's actual `num_timesteps` is **51,624** — an overcount of **3,049**. The cause is real and worth stating rather than smoothing: when the power cut hit, training had reached ~38,912 but the newest checkpoint was 35,240, so roughly 3k timesteps were trained, logged as completed episodes, and then **discarded** when the resume rolled back to 35,240. Those episodes appear in the curve but their learned weights are not in the final model's lineage. The x-axis therefore slightly overstates the training that actually produced this checkpoint.

**Decision:** `graph_attention`'s actual total is **51,624**, not the 50,240 that was targeted, and CLAUDE.md's mode-comparison note has been corrected to record 51,624.
**Why:** PPO only stops on a rollout boundary — 35,240 + 8 × `n_steps`(2048) = 51,624, an overshoot of 1,384. This is a general trap, not a one-off: the `--timesteps` argument cannot be assumed to equal the final `num_timesteps`, so each run's actual figure must be read off rather than presumed. `shared_policy`'s own total will land on a different boundary, and a "50k vs 50k" claim would be wrong for both modes.
**Verified:** `Done. num_timesteps=51624`, exit code 0. Final checkpoint re-loaded and inspected: restores `GraphAttentionExtractor`, `num_timesteps=51624`, and the J1↔J3 attention mask survives the save/load round-trip intact (`attn_mask[0,2]` and `attn_mask[2,0]` both True = blocked). The `Resuming from ...` guard line itself was lost to a `| tail -45` pipe on the launching command and cannot be quoted for this run; the guard raises *before* `model.learn()`, so completion is sufficient proof it passed, but that is inference and the direct quote is not available.

**Decision:** Compared against Stage 4's converged plateau, `graph_attention` currently **falls short**, and this is recorded as an unequal comparison rather than a verdict on attention.
**Why / measured:** last 20 episodes — Stage 4 (single-agent `MlpPolicy`, `num_timesteps=153600`): mean **1.2771**, std 0.1290, range [1.0926, 1.5183]. Stage 5 `graph_attention` (`num_timesteps=51624`): mean **1.0949**, std 0.2280, range [0.7098, 1.5339]. Gap **−0.1822** with ~1.8× the variance. The comparison is NOT like-for-like: Stage 4 has ~3× the training budget, and Stage 5 necessarily started from scratch because the architecture change makes resuming a Stage 1-4 checkpoint impossible. The resumed segment alone averages 1.0601 with its last 10 episodes at 1.092, still trending up rather than plateaued. The decision-relevant comparison is `shared_policy` at a comparable budget, which isolates attention from the cold-start and budget differences that dominate this one.
**Deviates from plan?** No — §9.5's flip rule is judged on "a clean upward reward trend", which is present; it is not judged against Stage 4's absolute number.

**Checkpoint tracking:** `psychoflow_stage5_51624_steps_final.zip` is currently gitignored (`.gitignore:17`, `training/checkpoints/*`), consistent with every other checkpoint in the repo — zero are tracked, including Stage 4's. Deliberately NOT un-ignored yet: `.gitignore`'s own comment scopes tracking to "the FINAL model that powers the demo", and this is a candidate whose mode may still lose the §9.5 comparison. Revisit once the attention-vs-shared-policy decision is made.

## 2026-08-16 — §18 Phase 7 (MARL, §9.5) — Stage 5 COMPLETE, `graph_attention` KEPT

**DECISION: `COORDINATION_MODE` stays `"graph_attention"`. §9.5's flip trigger is NOT met and no flip is made.** §9.5's stated trigger is *"if graph-attention hasn't shown a clean upward reward trend by that checkpoint, flip to `shared_policy`"* — attention showed exactly that trend on every check run. The decision is additionally supported, decisively, by the two targeted cross-junction metrics below. `agents/config.py`'s checked-in default already reads `"graph_attention"`; no code change was required.
**Why the decision rests on targeted metrics, not aggregate reward:** aggregate reward made the two modes look **tied** — last-20-episode means of **1.0949** (`graph_attention`) vs **1.0862** (`shared_policy`), 0.009 apart, far inside noise. Had the call been made on reward alone it would have been a coin flip. The metrics §9.5 actually cares about — cross-junction demand skew and cross-junction emergency awareness — separate the two modes cleanly, which is precisely why item 8 of the approved Stage 5 design plan specified testing them directly rather than trusting reward.

**Training histories (both modes, fresh models, Stage 4's full `ScenarioConfig`, seed 7):**

| | `graph_attention` | `shared_policy` |
|---|---|---|
| Burst A | 10,240 | 10,240 |
| Burst B | interrupted at ~38,912 by battery power-cut; last checkpoint 35,240; resumed +15,000 | single run, `--timesteps 41000` |
| **Final `num_timesteps`** | **51,624** | **53,248** |
| Policy params | 66,890 | 50,122 |
| Extractor verified in final ckpt | `GraphAttentionExtractor`, attn_mask intact | `SharedPolicyExtractor`, `has_attention=False` |

**Budget-target arithmetic error, recorded rather than buried.** `shared_policy` was meant to land on 51,200 for near-parity. It landed on **53,248**. Cause: PPO rounds *up* to a whole rollout, and `ceil(41000/2048) = 21` rollouts = 43,008, so 10,240 + 43,008 = 53,248. Hitting 51,200 required 20 rollouts = 40,960, i.e. `--timesteps 40000`; the requested 41,000 was **40 steps past the boundary** and bought an entire extra rollout. Net effect: **`shared_policy` received +1,624 steps (+3.1%) MORE training than `graph_attention`** — the opposite direction from the −0.8% intended. This is the exact trap the CLAUDE.md mode-comparison note warns about, walked into while the warning was already written. It does not weaken the conclusion: `shared_policy` lost on both decisive metrics *despite* the budget advantage.

**Paired-comparison methodology (worth reusing).** Both modes' Burst A and Burst B construct a fresh env with seed 7, so episode *k* draws an identical scenario in both runs — verified, not assumed (`lane_counts` sequences compared element-wise, matched exactly over the full overlap). This makes the comparison paired rather than merely budget-matched, removing scenario-draw variance entirely. Over the 46-episode overlapping Burst-B window `graph_attention` led by **+0.404** mean reward/step (winning 31/46 episodes, ≈2.3 standard errors) — but that lead had vanished by end-state, which is consistent with attention's larger parameter count buying faster early gains while the smaller constrained architecture converges to a similar place given enough steps. That pattern is informative but is NOT itself a flip trigger.

**SELF-CORRECTION — an earlier read of `graph_attention`'s `--j1-recheck` was wrong.** It was reported as showing "uniform regression, gap currently unmeasurable". That conclusion was an artifact of the only available comparator at the time being **Stage 4** — a checkpoint with ~3× the training budget AND a different architecture (`FlattenExtractor`, 82,442 params). Against its actual budget-matched peer, `graph_attention` is not regressed at all; it is dramatically better. Two lessons recorded:
  1. **Comparing across both a budget gap and an architecture change simultaneously produces conclusions that cannot be attributed to either.** A budget-matched peer was required before any read was justified.
  2. **`worst_wait` is a poor discriminator for this comparison because §10's starvation ceiling caps it regardless of underlying policy quality.** The two modes differ by only ~4s on mean `worst_wait` (123.1s vs 127.1s) while differing by **53×** on `starved_pct`. The ceiling was masking an enormous behavioural difference underneath. `starved_pct` is the metric that actually separates them, and should be the primary axis in any future comparison of this kind.

**DECISIVE FINDING 1 — starvation, `--j1-recheck`, 12 paired combo+seed runs, `graph_attention` wins 12/12:**

| combo | seed | GA worst_wait | GA starved% | SP worst_wait | SP starved% |
|---|---|---|---|---|---|
| (3,2,3) | 1 | 125.0s | 1.26% | 128.0s | 89.86% |
| (3,2,3) | 3 | 124.0s | 1.57% | 126.0s | 88.89% |
| (3,2,3) | 7 | 125.0s | 3.54% | 127.0s | 89.35% |
| (3,2,3) | 42 | 122.0s | 1.11% | 126.0s | 84.00% |
| (3,2,4) | 1 | 121.0s | 1.43% | 129.0s | 85.10% |
| (3,2,4) | 3 | 123.0s | 2.20% | 129.0s | 87.92% |
| (3,2,4) | 7 | 121.0s | 1.11% | 125.0s | 88.84% |
| (3,2,4) | 42 | 125.0s | 1.75% | 128.0s | 88.69% |
| (4,2,3) | 1 | 121.0s | 1.11% | 127.0s | 85.80% |
| (4,2,3) | 3 | 123.0s | 1.57% | 126.0s | 86.76% |
| (4,2,4) | 1 | 123.0s | 1.89% | 127.0s | 82.73% |
| (4,2,4) | 3 | 124.0s | 1.11% | 127.0s | 81.23% |
| **mean** | | **123.1s** | **1.64%** | **127.1s** | **86.60%** |

`shared_policy` holds a lane above the 90s starvation threshold for ~87% of every episode on narrow-middle topologies; `graph_attention` holds it to ~2%. That is the §9.3 fairness claim, and it is the single clearest result of the whole comparison.

**DECISIVE FINDING 2 — emergency handling, `--emergency-recheck`, 5 combos × 3 seeds:** `graph_attention` **11/15** override-firing vs `shared_policy` **13/15** (lower is better — fewer forced §10 interventions), against Stage 4's 15/15. Confirmed-clean runs (no override, zero penalty, zero blocked events): `graph_attention` **2**, `shared_policy` **1**.

**Three-way summary:**

| Metric | Stage 4 single-agent (153,600) | `graph_attention` (51,624) | `shared_policy` (53,248) |
|---|---|---|---|
| mean worst_wait, 12 runs | 56–120s (varied) | 123.1s | 127.1s |
| **mean starved_pct, 12 runs** | 0.0% on controls | **1.64%** | 86.60% |
| **override-firing** | 15/15 | **11/15** | 13/15 |
| last-20 mean reward | 1.2771 | 1.0949 | 1.0862 |

Caveats stated rather than smoothed: Stage 4's spike-rate figures came from a different, larger seed set and are not directly comparable per-cell; both Stage 5 modes remain ~0.19 below Stage 4's reward at ~1/3 its budget; and `shared_policy` carried a 3.1% budget advantage into a comparison it lost on both decisive metrics.

**Deviates from plan?** No. §9.5 required both extractors built in parallel (done), attention attempted first (done), and the flag set to whichever path converges (done — attention). §18 Phase 7's done bar — *"Stage 5 checkpoint evaluated, flag set to whichever path is actually converging"* — is met.
**Verified:** Both final checkpoints re-loaded and architecture-confirmed. All sweep outputs pasted raw. Paired scenario alignment verified element-wise. Sweep results persisted to `training/checkpoints/_sweeps/j1_recheck_stage5.json` after the earlier `/tmp` outputs proved transient.

**§18 Phase 7 (MARL, §9.5) is COMPLETE.** Next per §18's build order is **Phase 8 — Coordinator + Explainability (§11, §12)**: clearance behaviour, responder messaging, decision log, narration templates, query interface. Its done bar is *"full decision log renders correctly for a rule-based test run before the RL agent is even wired in"* — note it is explicitly gated on Tier 0, not on any trained checkpoint.

## 2026-08-16 — §18 Phase 7 close-out addendum: four bounded diagnostics, and an IMPORTANT correction

Four cheap diagnostics were run against existing checkpoints to close loose threads. Three changed the picture materially. Raw output: `training/checkpoints/_sweeps/phase7_loose_ends.txt` / `.json`.

**CORRECTION 1 — the "attention wins" framing in the entries above was INCOMPLETE and needs the single-agent comparator stated.** The Stage 5 A/B compared `graph_attention` against `shared_policy` only. Running **Stage 4 single-agent on the exact same 12 (combo, seed) pairs** gives the true three-way ordering:

| | mean worst_wait | mean starved_pct |
|---|---|---|
| **Stage 4 single-agent** (153,600, `FlattenExtractor`) | **75.2s** | **0.24%** |
| `graph_attention` (51,624) | 123.1s | 1.64% |
| `shared_policy` (53,248) | 127.1s | 86.60% |

Per-pair, Stage 4 is clean (0.00% starved) on 8 of 12 and only reaches 0.95% on the four it does not. **Single-agent Stage 4 beats BOTH MARL modes on both metrics.** What stands and what does not:
  - **STANDS:** `graph_attention` beats `shared_policy` decisively (12/12 paired runs, ~53× on starved_pct). §9.5's flip decision is unaffected and `COORDINATION_MODE` correctly remains `"graph_attention"`.
  - **DOES NOT STAND:** any claim that MARL *solved* or *closed* the `j1=3` gap. The honest claim is that attention **meaningfully narrowed the gap between architectures, not the gap to single-agent performance.**
  - **Most likely explanation — PLAUSIBLE BUT UNCONFIRMED:** Stage 4 has ~3× the training budget (153,600 vs ~51-53k) and the MARL modes started from scratch because the architecture change forbids resuming. This has NOT been tested; doing so would require training a Stage 5 mode to ~153,600. Do not state it as established.

**CORRECTION 2 — folded into the `(2,4,2)` density watch-item.** Stage 3's density-sweep methodology ((2,4,2) × density {0.7, 1.0, 1.3} × seeds {1,7,42}, 9 runs per mode) against both Stage 5 finals:

| density | `graph_attention` wait / starved | `shared_policy` wait / starved |
|---|---|---|
| 0.7× | 106.3s / **0.80%** | 125.3s / 56.38% |
| 1.0× | 96.0s / **0.90%** | 127.0s / 80.54% |
| 1.3× | 124.0s / **1.16%** | 128.3s / 95.76% |

`graph_attention` shows only a mild load-triggered rise (0.80% → 1.16%); the Stage 3 watch-item's pattern is present but small. `shared_policy` degrades monotonically and severely with load, reaching **95.76%** starved at 1.3× — a starved lane for essentially the whole episode. This is a SECOND independent axis on which neighbour-aware attention helps, and it is a load-scaling effect, which is what §9.5 would predict. Status: **substantially mitigated for `graph_attention`; still open for `shared_policy`** — moot in practice since `shared_policy` is not the deployed mode, recorded for completeness.

**RESOLVED — the ceiling-engagement question is now CONFIRMED, not hypothesised.** An earlier note flagged as unverified that `graph_attention`'s 121-125s band might reflect §10's `starvation_ceiling` actually firing, unlike Stage 4's 119-120s band (which was verified to fire ZERO overrides via a shielded/unshielded A/B). Override counts were captured on all 8 `(3,2,3)`/`(3,2,4)` runs: **every single run fires 1-2 `starvation_ceiling` overrides.** So the two bands are genuinely different phenomena — Stage 4 stayed under the ceiling on its own; `graph_attention` is being caught by it. The validator is doing work Stage 4's policy did not need. Thread closed.

**RECORDED — the deterministic-vs-stochastic gap has INVERTED, and carries an unresolved tension.** Stage 1's watch-item recorded stochastic BEATING deterministic by +0.268335 (1.297183 vs 1.028848), hypothesised as `ent_coef=0.0` plus independent per-head argmax over `MultiDiscrete([3,3,3])`. Measured on `graph_attention`'s final checkpoint with Stage 1's exact methodology (corridor 4/3/2, seed 7, model loaded ONCE and looped — a fresh `.load()` reseeds torch's RNG and silently makes "stochastic" runs identical):

```
  deterministic        = 1.285947
  stochastic n=10 mean = 1.027080  (stdev 0.061626, range 0.946280-1.130255)
  GAP = -0.258867      (Stage 1 was +0.268335 — near-identical magnitude, OPPOSITE sign)
```

Deterministic is now the better policy by almost exactly the margin by which it was worse at Stage 1. For deployment this is the desired direction (§13.1 runs the greedy policy). **UNRESOLVED TENSION, logged deliberately without resolving it:** deterministic has the higher REWARD (1.286) but the WORSE tail behaviour — worst_wait 121.0s / 1.09% starved, versus stochastic episodes mostly at 75-103s / 0.00% starved. Reward and fairness disagree on which policy is better here. Not investigated; whoever picks this up should know it exists before treating mean reward as the sole quality signal.

**Also added this pass (housekeeping, no investigation):**
  - `training/train.py` gained `preflight_timesteps()` and a `PRE-FLIGHT:` line printed BEFORE `build_env()`, showing the actual `num_timesteps` a run will end on. Unit-checked against every real case from this session including the `--timesteps 41000 → 53248` error. This trap was explicitly flagged as a risk in CLAUDE.md and then walked into anyway; the fix makes it visible before compute is spent rather than after.
  - CLAUDE.md §8 gained a **PHASE 8 WARNING** that the Stage 4 emergency-latency measurement is known-broken (negative latencies on corridor routes, unfixed) and must NOT be inherited by §11.2's `clearance_time_s` without being fixed first — that value is shown to a human operator, so a wrong or negative number would be user-facing. Points at `run_tier0_episode.py --b2` as the correct single-junction implementation to generalise from.

## 2026-08-18 — CORRECTION to the Stage 4 §16 emergency-priority entry (§18 Phase 6 Stage 4, 2026-08-15)

**This entry does not overturn the Stage 4 checkpoint FAILURE. It corrects how strongly one specific sentence in that entry was worded, on the basis of a finer-grained measurement that did not exist when it was written.** Per this file's append-only rule the original entry is left intact; read the two together.

**Decision:** The phrasing **"That is 0% proactive emergency handling — the policy never once served an approaching ambulance on its own initiative"** is WITHDRAWN as stated. The measurement behind it (§10's `emergency_override` fired in 15 of 15 sweep runs) is correct and is not in dispute; the inference drawn from it was too strong. Replacement wording: *the safety validator had to intervene at least once in every one of the 15 runs.*

**Why — the metric is a per-episode BINARY, and cannot support a per-decision claim.** `override_fired` is true if §10 fired **at any point** in an episode of ~630 decision steps. It therefore cannot distinguish a policy that proposes correctly on 88% of ambulance decisions and lapses once, from one that proposes correctly on 50% and lapses once. Both read 15/15. "Never once served an ambulance on its own initiative" is a claim about every decision, and a metric with one bit per episode cannot license it. This is the same class of defect §0.3 was written about — the earlier finding was that the ORIGINAL metric (served-ambulance rate) was trivially satisfiable; this correction is that its REPLACEMENT, while a genuine improvement, is still too coarse for the sentence it was used to support.

**Measured, on the same 3 seeds and the same Stage 4 checkpoint** (`psychoflow_stage4_153600_steps_final.zip`), using the per-junction-step classifier in `training/scripts/phase0_baselines.py` (`served` / `blocked_avoidable` / `blocked_unavoidable`, quality = served/(served+blocked_avoidable), mask-locked steps excluded so the metric measures the policy and not the action mask):

| condition | served | avoidable | decidable | quality | chance | lift | ovr_emergency | ovr_starvation |
|---|---|---|---|---|---|---|---|---|
| **Stage 4 single-agent 153,600** | 23 | 3 | 26 | **0.885** | 0.583 | **+0.301** | **5** | **0** |
| `graph_attention` 154,024 | 21 | 6 | 27 | 0.778 | 0.623 | +0.154 | 7 | 98 |
| `graph_attention` 102,824 | 23 | 7 | 30 | 0.767 | 0.650 | +0.117 | 7 | 43 |
| **RANDOM** (mask-valid uniform, 3 reps × 3 seeds) | 71 | 61 | 132 | **0.538** | 0.538 | **+0.000** | 65 | 1440 |

Two-proportion z against the pooled random control: Stage 4 **z=+3.29, p=0.0010**; `graph_attention`@154,024 **z=+2.30, p=0.0214**; @102,824 **z=+2.29, p=0.0219**. **Both architectures propose ambulance-serving phases meaningfully above chance.** That is not compatible with "never once on its own initiative."

**The chance baseline is validated, not assumed.** The random control's measured quality (0.538) equals its own analytically-computed chance rate (0.538) to three decimals — lift +0.000 — which is exactly what a uniform picker must produce if the chance arithmetic is right. This matters because chance here is HIGH (~0.54-0.65): when an ambulance is present there are typically few mask-valid slots and often more than one serves it. Any raw proposal-quality figure must be quoted against that floor, never alone.

**Also corrected: the coarse metric INVERTS the ranking relative to the fine-grained one.** Override-firing put Stage 4 (15/15) behind `graph_attention` (11/15). Pooled proposal quality puts Stage 4 ahead (0.885 vs 0.778, roughly double the lift over chance), and the emergency-override COUNT agrees with the fine-grained view (Stage 4 **5**, `graph_attention` **7**, random **65**). So the 11/15-vs-15/15 comparison should not be cited as evidence that MARL handles emergencies better than single-agent without this caveat attached.

**What still STANDS, unchanged:** §16's Stage 4 emergency-priority bar ("near-100% emergency priority") remains **FAILED and unremediated**. The validator intervened in all 15 runs; a policy needing the safety gate in every episode has not met that bar on any reading. The sparse-signal hypothesis remains untested.

**HONEST LIMIT on this correction — it is underpowered and does NOT establish a winner between Stage 4 and `graph_attention`.** n=3 seeds, 26-30 decidable decisions per condition. Pooled quality favours Stage 4 (0.885 vs 0.778) but MEAN-OF-SEEDS reverses it (0.636 vs 0.695), because Stage 4's seed 42 drew only 2 decidable steps and missed both, scoring 0.000 with std 0.553 across seeds. Pooled and mean-of-seeds disagreeing that violently is a sample-size symptom. The claim supported at this n is only the one against RANDOM (n=132 control), which is what the p-values above cover. Widen the seed set before ranking the two policies.

**Deviates from plan?** No. This is a measurement-methodology correction of the kind §0.3 requires, applied to §0.3's own motivating example. No locked decision (CLAUDE.md §2) is touched, no reward or validator code changed, and the Stage 4 checkpoint's FAILED status is unaffected.

**Verified:** `training/scripts/phase0_baselines.py --selfcheck` reproduces the recorded `_sweeps/phase0_emergency.json` row (part2[110824] seed=1) EXACTLY on all 8 fields — steps=641, amb_visible=10, amb_junction=10, served=8, avoidable=0, unavoidable=2, quality=1.0, overrides=72 — confirming the rewritten harness is not silently disagreeing with the matrix it is compared against. Raw results persisted to `training/checkpoints/_sweeps/phase0_baselines.json` (now git-tracked).

## 2026-08-18 — CORRECTION (scope + record accuracy): every burst REPLAYS its scenario sequence; the Stage 4 vs Stage 5 cause is now OPEN

Appended, not edited — this file is append-only. This entry qualifies how several
earlier entries' reward-progression claims may be read. It does **not** retract any
measurement.

**Finding:** every training burst constructs a fresh env with `seed=7`, so
`reset(seed=...)` restarts at episode 1 and re-draws the identical scenarios in the
identical order. **A resumed run adds PASSES, not DATA.** Verified across every stage
that logs enough to test it: stage3 Burst A vs B **16/16 identical**; stage4 Burst A vs
B **16/16**; Stage 5 Burst C vs D **81/81**, Burst B vs C **46/46**. It holds ACROSS
STAGES sharing a config — **Stage 4 Burst B vs Stage 5 Burst C: 64/64 identical**, since
both use `STAGES[4]` and `seed=7`. There is no stage-specific seed-handling path; Stage 5
was not different.

Measured distinct-scenario counts (= the longest single burst, never the sum):
stage1/2/3 ≈ **65** each, stage4 ≈ **64**, Stage 5 `graph_attention` **81 distinct from
248 logged episodes (~3.1x each)**. Stage 5's Burst D — 51,200 timesteps — introduced
**ZERO** new scenarios.

Stage 1 and 2 could not be tested directly (their Burst A predates `lane_counts`
logging). Episode LENGTH was checked and is **not** a valid proxy — it varies with the
policy and differs across bursts in stage3/4 where scenarios are provably identical. The
mechanism is shared code, so they almost certainly replay too; for Stage 1 it is moot
(fixed config).

**CONFLATION — what this qualifies.** Every within-stage Burst A→B progression recorded
in this log (Stage 1's rise to its ~1.28 plateau, Stage 2's, Stage 3's, Stage 4's)
includes a component of **re-fitting to already-seen scenarios**: Burst B's first 16
episodes are re-runs of Burst A's 16. Those improvements are real — the policy did get
better — but "improved with more training" cannot be separated from "improved with more
exposure to the same ~65 scenarios." Any future explanation invoking "more budget" must
first check whether the extra steps were new scenarios or repeats.

**NOT affected, stated so the correction is not over-read:**
  - **Within-burst analyses stand.** Episodes inside one burst are all distinct (81/81
    for Stage 5 Burst C), so Stage 2's `total_lanes` bucket analysis and Stage 3's
    early/late-half splits compare genuinely different scenarios.
  - **The §9.5 `graph_attention` vs `shared_policy` A/B stands, and DEPENDS on this** —
    identical sequences are what make it paired. Both modes received the same scenarios.
    The flip decision is untouched.
  - **The 2026-08-18 measurements are uncontaminated.** `--j1-recheck` pins
    `randomize_density=False` and `spawn_emergencies=False`; measured over 162 Stage 5
    training episodes, **162/162 have an ambulance and 0/162 have density exactly 1.0**,
    so no eval episode can coincide with a training episode. Incidental, not designed —
    master plan §15.4 updated to require an explicit held-out set.

**THE STAGE 4 vs STAGE 5 DATA-DIVERSITY CONFOUND IS UNRESOLVED — the CAUSE of the
bottleneck gap is now genuinely OPEN.** Stated plainly rather than as a footnote:

  - **The MEASUREMENT remains VALID.** Stage 4 single-agent outperforms
    `graph_attention` on the flagged narrow-middle combos. Post-knee `graph_attention`
    averages **30.33% starved across 14 checkpoints** (168 episodes) against Stage 4's
    **0.24%**. That is measured on a structurally held-out eval set and nothing here
    weakens it.
  - **The CAUSE is now open between THREE candidates, not one.** Stage 4's policy
    inherited a lineage through four *different* configs (Stage 1 fixed → 2 lane-counts
    → 3 density → 4 emergencies), each generating its own sequence — roughly 259
    scenario-instances across four distributions. Stage 5 `graph_attention` trained from
    scratch and saw **81 scenarios from one distribution**. So the gap may be
    **architecture**, **training budget**, or **data diversity**, and the Burst D
    parity run controlled only for budget. This confound was never stated in the record
    before now.
  - **What this does and does not do to Track A.** Track A's conclusion — budget parity
    does not close the gap — **stands, and is strengthened**: Burst D added zero new
    scenarios, which is a concrete mechanism for why more budget could not help. But
    Track A's further framing that the gap is therefore "a structural reward/curriculum
    property" should be read as **curriculum-DIVERSITY property, cause still open**,
    pending D1 (the persistent-seed-counter run). Do not cite the gap as evidence
    against graph-attention as an ARCHITECTURE until D1 reports.

**Deviates from plan?** No. No locked decision (CLAUDE.md §2) touched, no code changed by
this entry, no measurement retracted. CLAUDE.md gained the standing rule; master plan
§15.4 gained the held-out-set requirement.

**Verified:** All comparisons above computed directly from the committed `monitor*.csv`
files; disjointness figures computed over the 162 Stage 5 Burst C+D episodes.

## 2026-08-28 — §18 Phase 9 (Backend, §13)

Design plan stated and approved before any code (CLAUDE.md §4). Built concurrently
with Phase 8 (separate session, not yet landed); Phase 9's decision-log / narration
output is built strictly against §11.1/§11.2/§12.1-12.3's documented JSON shapes as
a frozen contract, via a thin in-backend adapter that Phase 8's `explainability/`
modules replace without moving the wire schema. Every spot where Phase 8's contract
has the final say is marked `# PHASE 8 SEAM` in code.

**Files:** `backend/main.py` (FastAPI app, `/ws` §13.2 stream, `/control/*` §13.1
router, `/health`), `backend/sim_runner.py` (`SimRunner` — the SUMO/env loop on one
dedicated thread), `backend/control_api.py` (`ControlState` + the six §13.1
functions as plain, SUMO/torch-free functions so §14's voice agent imports them
verbatim), `sim/run_backend_smoke.py` (Phase 9 done-bar harness — outside §6, same
category as every prior phase's harness). `agents/rule_based.py` edited (additive
param, below). `backend/voice/` deliberately NOT created — Phase 11.

**Decision:** `Tier0Controller.act()` gained an optional `lane_weights:
dict[str, float] | None` parameter for §13.1's `set_lane_bias` — a per-lane
multiplier on that lane's whole §9.1 score contribution (halted + wait + bonus).
**Why:** put in `agents/rule_based.py`, the single place lane scoring is defined,
rather than a backend wrapper that would duplicate the scoring loop. `None`
reproduces the unbiased controller exactly, so every existing caller
(`run_tier0_episode.py`, `evaluate_stage.py` via `ppo_picker`) is untouched.
**Deviates from plan?** No — §13.1 specifies `set_lane_bias`'s behaviour ("multiply
that lane's score by weight"); this supplies where the multiply happens.
**Verified:** `python -m env.reward` and `python -m safety.validator` still pass
unchanged; `run_backend_smoke.py`'s no-SUMO check 3b shows `act(lane_weights=
{"J1_e0":10,"J1_w0":10})` flips J1's chosen phase 0→1 while the unbiased call is
bit-identical to before.

> **[SUPERSEDED — see the 2026-08-28 "4a CHECKPOINT BAKE-OFF" entry below.]** The
> checkpoint named in the Decision immediately following is no longer the deployed
> default; it is now `stage4/psychoflow_stage4_153600_steps_final.zip`. Pointer only —
> the original entry text is unedited and records what was decided at the time.

**Decision:** Auto-mode default checkpoint = `training/checkpoints/
stage5_graph_attention/psychoflow_stage5_51624_steps_final.zip`, `deterministic=
True`. **Why:** the §9.5 KEPT `graph_attention` Stage 5 decision checkpoint;
confirmed genuine local minimum on the flagged j1=3 bottleneck combos (1.64%
starved, corroborated, not ceiling-masked) vs the longer 154,024-step run's ~30%
average in the oscillating regime. Deterministic because deployment runs the
greedy policy (§16). **Flag:** re-evaluate once the D1 persistent-seed-counter run
completes. **Deviates from plan?** No.

**Decision:** `trigger_emergency(lane_id)` sets `env.forced_emergency_lanes` (the
hook Phase 3 left "Wired in Phase 9") and auto-clears after `EMERGENCY_HOLD_S =
20.0` simulated seconds — it has no natural release event since it is
operator-forced, not a real vehicle. **Deviates from plan?** No — §13.1 gives the
behaviour, not the lifetime. Revisit once Phase 10 lets an operator see/clear it.

**Decision:** `set_baseline_mode("greedy")` is fully plumbed (switch, state flag,
stream echo) but returns `{"applied": false, reason: "...Phase 12..."}` — the
Greedy controller is a Phase 12 deliverable (§18) and CLAUDE.md §3 forbids
building ahead (same precedent as Phase 4's Greedy note). `"psychoflow"` works.
**Deviates from plan?** No — the §13.1 endpoint is Phase 9's; the controller it
would point at is Phase 12's.

**Decision:** Threading model — one `SimRunner` thread owns the env and is the
only code touching TraCI. Control endpoints enqueue `Command`s drained between
decision steps; `get_stats()` reads a lock-protected cache; §13.2 frames fan out
to all WebSocket clients via `loop.call_soon_threadsafe` onto an `asyncio.Queue`
per client (bounded — slow clients miss frames, never back-pressure the sim).
Pacing: `realtime_factor` wall-clock sleep per decision step (default 0.3s),
`--fast` disables it. Episode end → auto-reset and continue (a demo runs
continuously); per-episode metric counters reset on the boundary.
**Deviates from plan?** No — §13 specifies the API and stream, not the host
process structure.

**Decision:** The §13.2 frame's `decision` is one §12.1-shaped entry for a single
junction each step. Emit-junction rule (approved, with the multi-switch tie-break):
a junction with a §10 override wins (emergency_override outranks
starvation_ceiling; ties → lowest corridor index J1<J2<J3) → else the
lowest-corridor-index junction that switched this step → else rotate J1→J2→J3 by
step index so the log stays live. `reason` resolution mirrors Phase 8's
reconciliation order exactly: `emergency_override` / `starvation_ceiling` (checking
BOTH, per Session 2's required fix — a starvation override under RL control would
otherwise be mislabeled) → Tier 0's own reason under manual mode → `"rl_policy"`
(Session 2's stated sixth value) for a no-override RL decision, marked
`# PHASE 8 SEAM: pending rl_policy confirmation`. §12.2's 4 narration templates
used verbatim; `starvation_ceiling` / `rl_policy` get placeholder wording (seam).
`score_breakdown` / `alternative_scores` are `{}` under RL control (seam — Phase
8's decision log owns that). §11.2 responder messaging is NOT in §13.2's frame and
is not emitted by Phase 9. **Deviates from plan?** No — resolves ambiguities
§13.2 leaves open (one decision per frame; which junction; the two reason values
§12.2 does not list).

**Decision:** CLAUDE.md §8 standing rule added — `backend/` is a hard
TraCI-single-thread boundary, and `enable_safety_validator` is not referenced
anywhere under `backend/` except the three comments citing this rule. Verified by
`grep -rn enable_safety_validator backend/` → only the rule-citation comments.
The env is always constructed with the default `enable_safety_validator=True`.

**§18 Phase 9 done bar — MET.** "dashboard-less test client can call every control
function and see the WebSocket stream update." `venv/Scripts/python.exe
sim/run_backend_smoke.py` → **21 passed, 0 failed** (project venv confirmed,
`sys.prefix` = `...\GitHub\Test\venv`). All six §13.1 functions called and their
effect observed on the live §13.2 stream: `set_mode` manual↔auto flips
`decision.reason` between Tier 0 reasons and `rl_policy`; `set_lane_bias` reaches
`get_stats().lane_bias` and auto-reverts after `duration_s`; `trigger_emergency`
produces a §10 `emergency_override` on the decision stream with the emergency
narration template; `set_topology("222")` rebuilds the network and every
junction's `lane_count` becomes 2 with the stream still flowing; `get_stats()`
returns the full §13.1 field set with per-lane wait/counts/starvation;
`set_baseline_mode` applies `psychoflow` and reports Phase 12 for `greedy`. The
graph_attention checkpoint loads and drives auto mode
(`MaskableActorCriticPolicy`). Regression: `python -m env.reward`,
`python -m safety.validator` still pass after the `rule_based.py` change.

> **[SUPERSEDED — see the 2026-08-28 "4a CHECKPOINT BAKE-OFF" entry below.]** "The
> graph_attention checkpoint loads and drives auto mode" was true when this was
> verified; auto mode now loads and drives the Stage 4 SINGLE-AGENT checkpoint
> (`FlattenExtractor`, not `GraphAttentionExtractor`). The 21/21 done-bar result
> above still stands — it was independently re-run against the new default and
> passed unchanged. Pointer only; the original entry text is unedited.

**Note (not mine):** the working tree also shows untracked `coordinator/`,
`explainability/`, `training/scripts/checkpoint_bakeoff.py` and a one-character
uncommitted typo in this file's 2026-08-18 entry ("t   his") — those are the
concurrent Phase 8 session's in-progress work, left untouched.

**Next per §18:** Phase 10 — Frontend (§6's component list). Phase 8 (Coordinator
+ Explainability) must land and its `explainability/` modules be reconciled
against this backend's PHASE 8 SEAM points before the frontend's Decision Log
panel can show anything richer than the adapter output.

## 2026-08-28 — Phase 0 close-out: the post-51k collapse curve, the worst_wait threshold artifact, and what the three-way confound has narrowed to

Appended, not edited. This entry records the INTERPRETATION of diagnostics whose raw
data was already committed (`_sweeps/reward_term_pre51k.json`,
`_sweeps/reward_term_replay.json`, `_sweeps/j1_curve_burstC.txt`,
`_sweeps/j1_curve_burstD.txt`, `_sweeps/phase0_baselines.json`). The numbers were on
disk; the reading of them was not, and would have been lost at the session boundary.

**Decision:** record three findings as settled, and stop treating `worst_wait` as a
quality signal anywhere in the project.

---

### 1. Training `graph_attention` past 51,624 does not plateau — it COLLAPSES.

Measured on the same 12 pinned (combo, seed) pairs throughout, deterministic,
validator ON, density pinned, no emergency:

| checkpoint | rew/step | starvation penalty/step | ovrS per run | starved% |
|---|---|---|---|---|
| 45,240 | +0.0711 | 1.4528 | 5.42 | 10.26 |
| **51,624 (kept final)** | **+1.2594** | **0.2778** | **1.42** | **1.64** |
| 56,624 | +1.1594 | 0.4275 | 2.08 | — |
| 61,624 | +0.7123 | 0.8733 | 14.83 | — |
| 144,824 | — | — | — | **27.8–80.5** |
| 150,824 | — | — | — | 1.7–41.3 |
| 154,024 (Burst D final) | — | — | 32.67 | — |

`ovrS` (starvation-ceiling overrides, split per the 2026-08-18 correction — never the
combined count) rises **1.42 → 14.83 → 32.67**. `phase0_baselines.json` confirms this
independently on a different harness and different seeds: Stage 4 @153,600 fires
**ovrS=0** across 3 seeds; `graph_attention` @154,024 fires **98**.

**51,624 is not merely the kept checkpoint — it is the best one on the curve, and the
run degrades monotonically after it.** Burst D's 51,200 additional timesteps made the
policy materially worse.

**Why this matters for Track A:** it is a second, independent mechanism for "budget
parity did not close the gap." Burst D added zero new scenarios (2026-08-18 entry), and
those zero-new-scenario steps did not merely fail to help — they actively destroyed the
policy. That is the signature of overfitting to a replayed ~81-scenario set, evaluated
against a structurally disjoint eval config.

### 2. THRESHOLD ARTIFACT CONFIRMED: `worst_wait` saturates and cannot rank policies.

`STARVATION_CEILING_S = 120` means §10 intervenes before any lane can go far past 120s.
So `worst_wait` — an episode-level MAX — collapses to a near-binary "did the ceiling
fire at all," landing in a 121–142s band regardless of how bad the policy is underneath.

The `--j1-recheck` harness's spike criterion (`worst_wait > 90s`) reports
**"4/4 spiked"** at BOTH:
  - ckpt 51,624 — `starved_pct` 1.11–3.54%, a GOOD policy, and
  - ckpt 144,824 — `starved_pct` 27.84–80.51%, a CATASTROPHIC one.

A metric that cannot separate 1.6% from 80% is not measuring policy quality. This also
**resolves the UNVERIFIED side-note** recorded in CLAUDE.md's Stage 5 section: Stage 4's
spikes sat at 119–120s firing zero overrides, while Stage 5's sit at 121–125s. The
`ovrS` column now confirms the ceiling is genuinely engaging at Stage 5 — the shift
across 120 was real, not coincidence.

**Consequence, applied in this session (4b):** `worst_wait` is retired from every
judge-facing metric and from checkpoint-selection decisions. `starved_pct`,
`starvation_events_count` and `wait_time_variance_across_lanes` replace it. The one
legitimate remaining use of `worst_wait` is demonstrating this saturation.

### 3. The three-way confound has NARROWED — but the architecture question is now moot.

The 2026-08-18 entry left the Stage 4 vs Stage 5 cause open between **architecture**,
**budget**, and **data diversity**. Finding 1 does not fully close it, but it does
remove budget as an explanation in the helpful direction: more budget on replayed
scenarios is not neutral, it is harmful. Remaining live candidates are **data
diversity** (D1 is the test, running at time of writing) and **architecture**.

**However — the practical decision no longer waits on that answer.** §9.5's flip
question ("does attention earn its complexity over shared_policy?") is ANSWERED and
untouched: attention wins 12/12. The separate question of which checkpoint to DEPLOY is
an empirical bake-off between Tier 0, Stage 4 single-agent, and the two
`graph_attention` finals — settled by measurement (4a, next entry), not by resolving
the cause. Recording this explicitly so a future session does not block deployment on a
research question that deployment does not depend on.

---

**Deviates from plan?** No. No locked decision (CLAUDE.md §2) reopened — §9.5's
`COORDINATION_MODE = "graph_attention"` stands as the MARL-architecture answer. No
measurement retracted; every number above was already committed as raw data.

**Verified:** All figures read directly from the committed `_sweeps/*.json` and
`_sweeps/*.txt` artifacts named above. No new runs were required for this entry.

## 2026-08-28 — 4a CHECKPOINT BAKE-OFF (§15.2, §20): the deployed policy is Stage 4 single-agent, not `graph_attention`

**Decision:** `backend/sim_runner.py`'s `DEFAULT_CHECKPOINT` becomes
`training/checkpoints/stage4/psychoflow_stage4_153600_steps_final.zip`. This SUPERSEDES
the `ga_51624` default recorded earlier the same day, which was chosen on the existing
diagnostic history before any head-to-head measurement existed.

**Why:** 48 episodes — 4 controllers x 4 topologies `{(4,3,2),(2,2,2),(4,4,4),(3,2,3)}`
x seeds `{1,7,42}`, deterministic, validator ON, density pinned 1.0, no emergency
(matching `evaluate_stage.py::_run_plain_episode` so the fairness comparison is not
confounded by the other randomisation axes). Harness:
`training/scripts/checkpoint_bakeoff.py`; raw data `_sweeps/checkpoint_bakeoff.json`.

| controller | starv_ev | wait_var | mean_wait_max | starved% | worst_wait | reward | ovrS | arrived |
|---|---|---|---|---|---|---|---|---|
| tier0 | **0.00** | **50.7** | **24.5** | **0.00** | 41.5 | 1.2011 | 0.00 | 4668 |
| **stage4_153600** | 0.08 | 72.2 | 26.7 | 0.08 | 59.7 | **1.3450** | **0.00** | 4668 |
| ga_51624 | 3.33 | 109.8 | 31.8 | 1.20 | 114.8 | 1.2347 | 1.08 | 4668 |
| ga_154024 | 132.75 | 592.9 | 71.0 | 25.82 | 126.4 | 0.2414 | 15.75 | 4668 |

Per-combo `starvation_events_count` / `starved_pct`:

| combo | tier0 | stage4_153600 | ga_51624 | ga_154024 |
|---|---|---|---|---|
| (4,3,2) demo | 0.0 / 0.00% | **0.0 / 0.00%** | 4.0 / 1.21% | 159.7 / 22.14% |
| (2,2,2) | 0.0 / 0.00% | 0.0 / 0.00% | 1.3 / 0.79% | 67.7 / 20.58% |
| (4,4,4) | 0.0 / 0.00% | 0.0 / 0.00% | 2.3 / 0.85% | 208.7 / 36.26% |
| (3,2,3) | 0.0 / 0.00% | 0.3 / 0.32% | 5.7 / 1.97% | 95.0 / 24.28% |

**On the demo corridor specifically** — the only topology §19 actually shows — Stage 4
is 0 starvation events / 0 overrides / 38-42s worst on all three seeds, where ga_51624
is 4 / 1 / 121-125s on all three. That is the whole decision.

**Budget is controlled, not a confound.** Read off the checkpoints, not assumed:
stage4 `num_timesteps=153600`, ga_154024 `num_timesteps=154024` — a 424-step
difference. Extractors self-describe on load (`FlattenExtractor` vs
`GraphAttentionExtractor`), so the harness is genuinely mode-unaware.

**Four findings worth keeping:**

  1. **Tier 0 still wins the pure fairness metrics.** 0.00 events, `wait_var` 50.7,
     `mean_wait_max` 24.5 — better than Stage 4 on all three. Stage 4 wins on reward
     (1.3450 vs 1.2011) at statistically indistinguishable starvation (0.08 vs 0.00
     events, i.e. one event across twelve episodes). The honest claim is **"the learned
     policy matches Tier 0's fairness while clearing traffic better,"** not "it beats
     the rule baseline on fairness." §15.1's demo comparison is against GREEDY, which
     both beat comfortably — this does not weaken the demo, it just bounds the claim.

  2. **The threshold artifact is reproduced inside this table.** `ga_51624` (3.33
     events) and `ga_154024` (132.75 events — a ~40x worse policy) sit at 114.8s vs
     126.4s `worst_wait`, essentially indistinguishable, while
     `starvation_events_count` separates them by two orders of magnitude. Independent
     confirmation of the same session's Phase 0 entry, on a different harness.

  3. **The post-51k collapse reproduces on a third harness.** `ga_154024` at 25.82%
     starved / 15.75 `ovrS` / reward 0.2414 — consistent with `reward_term_replay`'s
     61,624 figures and `phase0_baselines`' ovrS=98. Three independent measurements now
     agree that training `graph_attention` past 51,624 destroyed it.

  4. **Every controller clears the corridor.** `arrived=4668` and `terminated=12/12`
     for all four, episode lengths 628.8-633.5 steps. So `total_throughput` is a
     sanity check here, not a discriminator — recorded in §15.2 so the dashboard does
     not present it as one.

**Deviates from plan?** **No locked decision reopened, but one needs stating clearly.**
§9.5's `COORDINATION_MODE` stays `graph_attention` — that flag answers "which MARL
extractor," and attention won that A/B 12/12. Which trained CHECKPOINT the backend
serves is a separate axis, and CLAUDE.md's Stage 5 ESSENTIAL QUALIFIER already recorded
that single-agent beats both MARL modes. **Demo-honesty consequence (§17, §20): the
policy running in the live demo is SINGLE-AGENT PPO, not MARL.** The MARL work remains
a real, measured result (attention vs shared_policy) but must not be described as what
is driving the corridor on stage.

> **[CORRECTED 2026-08-28 — the two proposal-quality figures in the paragraph below are
> CONTAMINATED; do not cite them.]** `phase0_baselines.py` runs `STAGES[4]`, the TRAINING
> config, so eval seed 7 reproduces Stage 4 training episode 1 exactly and supplied 11 of
> the 26 decidable steps behind 0.885 (test-be's `stage4_contamination.py`). Re-measured
> held-out, 11 clean seeds of 12 run, contamination-screened, same methodology both sides:
> **Stage 4 = 39/47 = 0.8298** (lift +0.1915), **`graph_attention` @154,024 = 49/64 =
> 0.7656** (lift +0.1641); `graph_attention` @102,824 = 40/56 = 0.7143.
> **The gap is NOT statistically significant** — 0.0642, two-proportion z = +0.824,
> p = 0.410 (independently recomputed). Both still beat the matched random control
> (Stage 4 z = +3.388 p = 0.0007; GA154024 z = +2.904 p = 0.0037), so "both are
> meaningfully above chance" stands for both.
> **The direction survives; the magnitude drops ~40% and the ranking is not established.**
> Contamination measurably favoured STAGE 4, not `graph_attention`: inflation over
> held-out was **+0.2176 for Stage 4 vs +0.0264 for `graph_attention`**, ~8x. So the
> recorded gap was inflated in Stage 4's favour and cleaning it NARROWS the gap.
> This does not reopen 4a — the deployment case rests on the fairness grid, not on
> proposal quality — but "Stage 4 also leads emergency proposal quality" must be stated
> as a **non-significant directional edge, not a supporting pillar.** Raw data:
> `_sweeps/{stage4,ga154,ga102}_proposal.json`. Pointer only — the original text below is
> unedited and records what was believed at the time.

**Not measured here:** emergency behaviour (config pins `spawn_emergencies=False` to
isolate fairness). The existing split figures still favour Stage 4 — proposal quality
0.885 vs `graph_attention`'s 0.778, `ovrE` 5 vs 7 (`_sweeps/phase0_baselines.json`) —
so this does not cut against the decision, but it is a separate measurement and should
be cited as one.

**Re-evaluate when D1 finishes.** If the post-51k collapse was data-diversity-driven, a
re-trained MARL run may overtake Stage 4; the bake-off is a repeatable procedure, not a
one-off, and should be re-run against any new candidate.

**Verified:** `python -m training.scripts.checkpoint_bakeoff` — 48/48 episodes, 26.5 min.
Harness cross-checked against the existing record twice: it reproduces Tier 0's B1
baseline exactly (627 steps / 4668 arrived / 41.0s worst / 0 starved / reward 1.1947 vs
the logged 1.2) and `ga_51624`'s recorded (4,3,2) seed-7 row exactly (1.2859 / 121.0s /
1.09%). Post-change backend check: `python sim/run_backend_smoke.py` — **21/21 pass**
with Stage 4 loaded, auto mode emitting `reason='rl_policy'`.

## 2026-08-28 — §18 Phase 8 (Coordinator + Explainability, §11 / §12)

Design plan stated and approved before any code (CLAUDE.md §4); seven design
points confirmed, plus `rl_policy` added to the reason enum at Session 3's
request (Phase 9's RL auto mode has no rule-based justification to render).
Built concurrently with Session 3's Phase 9 (Backend) and Session 4's Phase 0 /
4a bake-off.

**Files (6 new). Nothing in `env/`, `env/reward.py` or `safety/validator.py`
was touched — Phase 8 consumes their outputs only. No `__init__.py` (matches the
repo's implicit-namespace-package convention).**
- `explainability/decision_log.py` (§12.1)
- `explainability/narrator.py` (§12.2)
- `explainability/query_interface.py` (§12.3)
- `coordinator/emergency_clearance.py` (§11.1)
- `coordinator/responder_messaging.py` (§11.2)
- `sim/run_explainability_episode.py` — done-bar harness, outside §6, same
  category as every prior phase's `run_*.py`.

**Decision:** the decision log is an AGENT-AGNOSTIC, caller-side recorder.
`DecisionLog.record_step(sim_time, decisions, info, snapshot, served_lanes)`
takes the `{junction_id: {phase_selected, score_breakdown, alternative_scores,
reason}}` dict `Tier0Controller.act()` already returns; Phase 9's RL runner
builds the same shape with `reason="rl_policy"` and possibly-empty breakdowns.
Never wired into `PsychoFlowEnv.step()` — the env's scope is frozen (CLAUDE.md
§3).
**Why:** the override reconciliation (controller proposal <-> §10 `OverrideRecord`
<-> what executed) is identical regardless of who produced the proposal, so one
recorder serves Tier 0 and RL with no branch. Fed caller-side, exactly as the
Phase 4 B-harnesses already thread `decisions` through `run_episode`.
**Deviates from plan?** No — §12.1 gives the entry schema, not the plumbing.
**Verified:** `python -m explainability.decision_log` — 6 hand-scored assertions
(triggering-lane selection for raw_count vs wait_time_threshold; emergency
override rewrites `reason` and executed phase; a deferred-min-green ceiling
leaves the executed phase unchanged; JSONL round-trip drops None fields;
`entries_for` / `latest` filters).

**Decision:** six reason values, single-sourced. `wait_time_threshold` /
`raw_count` imported from `agents.rule_based`; `emergency_override` /
`starvation_ceiling` imported from `safety.validator` (== `RULE_EMERGENCY` /
`RULE_STARVATION`); `voice_command` (§12.2, producer Phase 9/11); `rl_policy`
(producer Phase 9 auto mode). Module-level asserts pin the four imported string
literals so an upstream rename fails loudly here rather than silently dropping a
narrator template. **`rl_policy` is confirmed and final** — closes item (1) of
Session 3's Phase 9 seam note (CLAUDE.md §8).
**Deviates from plan?** No.
**Verified:** `python -m explainability.decision_log` asserts `len(REASONS) == 6`
and `REASON_RL_POLICY == "rl_policy"`.

**Decision:** narrator = §12.2's four templates verbatim + two confirmed
additions: a `starvation_ceiling` template (§12.2 lists four reasons; §10 has two
rules) and an `rl_policy` template; every template prefixed `Jx · ` (the §12.2
templates predate §0.1's 3-junction lock, and an operator needs the junction).
`{lane}` is the within-approach index of the lane the decision turned on
(`_triggering_lane`: longest-waiting served lane for `wait_time_threshold`,
most-halted otherwise; `OverrideRecord.lane_id` for an override). An unknown
reason raises `KeyError`, never a canned string (§12.3). Closes item (2) of
Session 3's seam note.
**Deviates from plan?** Two small deviations from §12.2's literal text, flagged
in the design plan and approved.
**Verified:** `python -m explainability.narrator` — all 6 templates render
non-empty; fragment assertions; unknown reason raises.

**Decision:** query interface = deterministic at-or-before lookup + render.
`why(sim_time=None, junction_id=None, lane_id=None)` returns `{sim_time,
junction_id, entry, narration}` — the real logged §12.1 entry rendered through
§12.2, never canned (§12.3), no LLM (§2). `sim_time` omitted -> most recent;
otherwise the latest entry at-or-before it (queries land between the 5s steps).
`lane_id` -> junction via the twin topology map, not string parsing. Misses raise
`DecisionNotFound`.
**Deviates from plan?** No.
**Verified:** `python -m explainability.query_interface` — at-or-before
semantics, latest, lane->junction resolution, real-entry identity check, three
miss cases raise.

**Decision:** §11.1's coordinator emits events, does NOT move vehicles.
`EmergencyClearanceCoordinator.observe(...)` tracks a clearance episode PER
JUNCTION and emits `EmergencyClearanceEvent` (first detection / override onset /
green onset / close). §11.1's visible "vehicles part to open a path" is a Phase
10 frontend animation fed by this event stream — moving vehicles via
`traci.vehicle.moveTo` / `changeLane` fights SUMO's car-following model and risks
the exact conflicts §10 exists to prevent.
**Why (per-junction attribution — the CLAUDE.md §8 PHASE 8 WARNING fix):** green
onset is recovered as `sim_time - time_since_switch_s` (B2's method) at the
junction the override actually fired at, so a corridor-route ambulance detected
at J1 then J3 produces two independent episodes with no cross-contamination. If
the phase was already green on arrival, `green_onset` predates detection;
`clearance_time_s` floors at 0.0 and `served_on_arrival` is set — the honest
reading, not a negative latency.
**Deviates from plan?** No — the animation is explicitly deferred to the frontend
in the confirmed design plan.
**Verified:** `python -m coordinator.emergency_clearance` — override case
(detect->override->green, 5.0s), corridor route (J1 served-on-arrival closes as
the ambulance reaches J3, J3 opens independently), `finalize()` closes
stragglers.

**Decision:** §11.2 `clearance_time_s` is real; `baseline_clearance_time_s` is a
LABELLED MODEL ESTIMATE. `clearance_time_s` = detection -> green at the firing
junction (from §11.1, per-junction — the known-broken Stage 4 sweep latency is
NOT inherited). `baseline_clearance_time_s` is a conservative signal-rotation
estimate (`estimate_baseline_clearance_s`: min-green owed on the current phase +
every other green phase held for min-green with a yellow between, target phase
assumed last in rotation) — a true per-event A/B is impossible live and is what
`run_tier0_episode.py --b3` does offline. Marked `baseline_is_estimate: true` in
the payload and in the summary text. §11.2's message gains `junction_id` and
`override_fired` beyond its literal schema.
**Deviates from plan?** Additions to §11.2's literal JSON schema, flagged in the
design plan; the estimate-not-measurement treatment was confirmed as design
point 2.
**Verified:** `python -m coordinator.responder_messaging` — baseline arithmetic
(2-phase/age0 = 28.0s, 3-phase/age0 = 42.0s), override message (8.0s vs 28.0s ->
71.4%), served-on-arrival message (0.0s -> 100%, "already clear"), unresolved
event raises.

> **[CORRECTED by commit `3496057` — see the 2026-08-29 entry "Commit `3496057`:
> five correctness fixes" below.]** Every baseline number in the Verified line
> immediately above is SUPERSEDED and must not be quoted. `estimate_baseline_
> clearance_s` was double-counting (it charged min-green + yellow for every
> phase including the target, plus a spurious trailing yellow), roughly doubling
> the estimate. Corrected values: **2-phase/age0 = 14.0s** (was 28.0),
> **3-phase/age0 = 28.0s** (was 42.0), **4-phase/age0 = 42.0s**; the override
> message is now **8.0s vs 14.0s -> 42.9%** (was 8.0 vs 28.0 -> 71.4%). The
> `71.4%` figure in particular is an OVERSTATED improvement claim against an
> inflated baseline, and §11.2's message is operator-facing — do not cite it.
> Pointer only; the original entry text is unedited per this file's append-only
> rule.

> **[ALSO CORRECTED by `3496057`.]** The decision_log Verified line further up
> this entry says "6 hand-scored assertions". `3496057` added a seventh
> (scenario 7, the auto-mode `decisions`-dict contract), so the current count is
> 7. Same for the §11.2 design note above: `baseline_clearance_time_s` is now
> labelled `baseline_is_worst_case` as well as `baseline_is_estimate`, and the
> summary text says "worst-case".

**§18 Phase 8 is COMPLETE.** Done bar — *"full decision log renders correctly for
a rule-based test run before the RL agent is even wired in"* — verified in the
project venv (`sys.prefix` = `...\GitHub\Test\venv`) via
`python sim/run_explainability_episode.py`, an 8-step check over two rule-based
segments (no trained checkpoint anywhere):

- **Segment 1** — Tier 0, corridor 4/3/2, seed 7, `spawn_emergencies=True`,
  validator ON, manual ambulance on J2 north @ t>=130s + `forced_emergency_lanes`
  (§13.1's operator-trigger path): 627 steps, 3145s, terminated, 18 §10 override
  records, 2 clearance episodes.
- **Segment 2** — adversarial rule-based controller starving J2 north, seed 31:
  238 steps, 1200s, 13 `starvation_ceiling` overrides (Tier 0 never trips the
  ceiling on 4/3/2, §16, so this segment sources real ceiling entries).
- **Step 3 (structural):** 1881 Tier-0 entries = 627 steps x 3 junctions, + 714
  adversarial — all carry the §12.1 schema, reasons in the enum, `sim_time`
  monotonic, phase keys consistent, reason <-> override-block agreement; 18/18
  overridden entries reconcile (proposed vs executed vs reason vs outcome);
  override-entry count == raw §10 record count (18); >=1 `emergency_override`
  (18) and >=1 `starvation_ceiling` (13); `to_jsonl` -> 1881 lines.
- **Step 4 (narration):** all 6 reasons render one operator line each (4 from
  real Tier-0 / adversarial entries, `voice_command` via the real `record_voice`
  API, `rl_policy` synthetic — no rule-based run produces it); unknown reason
  raises.
- **Step 5 (query):** mid-episode at-or-before, latest, emergency-window
  (asserted `entry == logged.to_dict()` — real, not canned), lane->junction
  resolution.
- **Step 6 (responder):** 2 messages — J2 `served_on_arrival` (0.0s,
  `override_fired: true`), J1 proactive (3.0s, `override_fired: false`, the
  `spawn_emergencies` ambulance Tier 0 served on its own); `improvement_pct`
  arithmetic re-checked.
- **Step 7:** 5/5 module self-tests pass.
- **Step 8 (regression):** `python -m env.reward`, `python -m safety.validator`
  (11/11), `python sim/run_env_smoke.py` all pass unchanged.

**Three caveats, stated rather than smoothed:**
1. **The J2 probe resolved `served_on_arrival` (0.0s), not a measured positive
   override latency** — `forced_emergency_lanes` + Tier 0 already holding J2 north
   meant the lane was green before the ambulance was physically sensed. The §11.2
   message reports this truthfully. A measured positive override latency (~3s)
   already exists in `run_tier0_episode.py --b2`; Phase 8 did not re-derive it.
   The per-junction attribution + negative-latency flooring the PHASE 8 WARNING
   actually asked for is verified in `emergency_clearance._selftest`
   (corridor-route case).
2. **`baseline_clearance_time_s` is a conservative model estimate, not a measured
   counterfactual** — flagged in-payload (`baseline_is_estimate: true`) and in
   the summary text.
3. **This BUILD_LOG entry and the CLAUDE.md §8 bullet were appended, but the doc
   files were NOT included in the Phase 8 commit.** At commit time
   `docs/BUILD_LOG.md` (+294), `CLAUDE.md` (+102), `docs/PsychoFlow_Master_Plan.md`
   and `agents/rule_based.py` all carried uncommitted Session 3/4 (Phase 9
   backend / Phase 0 / 4a bake-off) work — HEAD was still `eba9f7a`. `git add`-ing
   the doc files would have committed ~396 lines of another session's in-flight,
   unreviewed work under a Phase 8 message. The Phase 8 commit therefore contains
   only the 6 new code files; these doc appends are left in the working tree for
   a coordinated doc commit once Session 3/4's changes land.

Session 3's Phase 9 seam note (CLAUDE.md §8) listed three items handed to Phase 8
— all now closed: (1) `rl_policy` string confirmed / finalised; (2)
`starvation_ceiling` and `rl_policy` narration templates finalised; (3) empty
`score_breakdown` / `alternative_scores` under RL mode accepted by `record_step`
and exempted from the phase-key structural check. Override classification order
(emergency outranks starvation, then lowest corridor index) matches Phase 9's
`overrides_by_j` reconciliation.

## 2026-08-28 — STAGE 4 ADVERSARIAL AUDIT: the `j1=3` vulnerability is a sampling artifact, and every `phase0_baselines` proposal-quality figure is partly a memorisation score

Stage 4 @153,600 became the deployed checkpoint (4a) on a 4-combo, density-pinned,
no-emergency grid, having never received the scrutiny `graph_attention` got. This
entry records that audit: **290 episodes across 9 sweeps**. Harnesses and raw data
committed in `d91c308` (`training/scripts/stage4_{scrutiny,proposal,contamination}.py`,
`_sweeps/stage4_*.json`, `_sweeps/ga{154,102}_proposal.json`). Measurement only —
env / reward / validator imported read-only, `LaneMetricProbe` imported from
`checkpoint_bakeoff` rather than reimplemented.

**Harness trust established BEFORE any claim was drawn**, because a rewritten harness
that silently disagrees would invalidate every comparison while printing plausible
numbers (CLAUDE.md §8's named failure mode). Three exact cross-checks: the 4a
`stage4_153600 (4,3,2)` seed-7 row reproduces **bit-for-bit on all 9 fields**
(`mean_reward=1.369126205607078`, `wait_var=35.71695758757162`); **all 8**
`STAGE4_J1_REFERENCE` `worst_wait` values reproduce with **delta +0.0**; and
`phase0_baselines`' Stage 4 seed-1 row reproduces (13/13, chance 0.692).

---

### 1. The `j1=3` vulnerability is a SAMPLING ARTIFACT. Measurements stand; the category does not.

**Decision:** downgrade the Stage-2 watch-item's "CONFIRMED REPEATABLE `j1=3`
vulnerability" to a mild, unconfirmed `j3` gradient.
**Why:** a sweep of **all 27 lane-count combos x seeds {1,7,42} = 81 episodes** —
the first measurement with the coverage to answer the question. Episodes containing
at least one starvation event, by `j1`: **`j1=2` -> 4/27, `j1=3` -> 3/27,
`j1=4` -> 2/27**. `j1=3` is not the worst; `j1=2` is. The earlier reading failed
structurally, not arithmetically: every prior test ran through
`evaluate_stage.py --j1-recheck`, whose four-combo matrix was chosen *because
`graph_attention` struggled on it* and contains no `j1=2` combo at all, so it could
not have detected a `j1=2` effect however strong. test-d0's same-day docs audit
independently flagged the same coverage gap from the other end.

What is actually present is a **mild gradient in `j3`**: mean p99 of the across-lane
max wait 41.7 / 54.1 / 63.0s for `j3` = 2/3/4, event-episodes 1/3/5. All 9
event-episodes have `j3 >= j1` and 0/27 `j3 < j1` episodes have any (Fisher
p=0.0257) — but that split is **not** quoted as a clean interaction: stratifying
within each `j3` level shows the `j1` effect holds at `j3` in {2,3} and vanishes at
`j3`=4, so it is largely the `j3` effect repackaged, at n=9 per cell.

**Magnitude, stated with the finding:** worst combo `(4,3,4)` is 0.67
events/episode, 0.58% starved, p99 75.7s against a 90s threshold; **81/81 episodes
terminate with all 4668 vehicles arrived**; only 3/81 fire any §10 override.
Status: a mild directional gradient, NOT a confirmed hard finding.
**Deviates from plan?** No. Corrects an interpretation, not a measurement or a
locked decision.

### 2. Tier 0 is strictly fairer on six combos — a bounded caveat on 4a, not a reopening.

Tier 0 on the six combos where Stage 4 showed most starvation, identical scenarios:
**Tier 0 0/18 event-episodes, Stage 4 7/18**; mean p99 35.4s vs 67.0s; mean reward
1.2042 vs **1.3450**. So on `(2,3,2)`, `(2,3,3)`, `(2,4,3)`, `(3,2,4)`, `(3,3,4)`,
`(4,3,4)` Tier 0 is strictly fairer and Stage 4 strictly faster. This also settles
"inherently hard topology vs policy gap" the way Stage 2 settled `(4,2,4)`: Tier 0
solves all six cleanly, so it is a real (mild) policy gap.
4a's "matches Tier 0's fairness while clearing traffic better" is **true on 4a's own
four combos and too strong outside them**. Does not reopen the deployment decision —
Stage 4 wins reward on all six, Tier 0 was already fairer on 4a's own dispersion
metrics, and the demo corridor is 0.00 events for Stage 4. Tier 0 remains an
extremely strong fairness floor.

### 3. `phase0_baselines.py` evaluates on TRAINING scenarios. Both checkpoints affected.

**Decision:** every proposal-quality figure that harness produced is partly a
memorisation score, and the Stage-4-vs-`graph_attention` gap does not survive
cleaning.
**Why:** `phase0_baselines.py` runs `STAGES[4]` — the full TRAINING config — and
calls `reset(seed=s)`, which sets `self._rng = random.Random(s)`. Training used
`--seed 7` with the same config, so **eval seed 7 reproduces TRAINING EPISODE 1
exactly** (lane counts, both density multipliers to 16 digits, ambulance route and
depart time). Verified without SUMO — `_draw_scenario()` is rng-pure. Both Stage 4
and `graph_attention` are hit on the same seed, since they share a scenario sequence
(2026-08-18 burst-replay entry). Seed 7 supplied **11 of the 26 decidable steps
(42%)** behind the recorded 0.885.

Re-measured on 12 seeds, screened, held-out only, matched random control:

| checkpoint | recorded (contaminated, 3 seeds) | held-out (11 seeds) | lift over own chance |
|---|---|---|---|
| Stage 4 @153,600 | 0.885 (26 decidable) | **0.8298** (47) | +0.1915 |
| `graph_attention` @154,024 | 0.778 (27) | **0.7656** (64) | +0.1641 |
| `graph_attention` @102,824 | 0.767 | **0.7143** (56) | +0.0893 |

**Survives:** both beat the random control (Stage 4 z=+3.388 p=0.0007;
`graph_attention` z=+2.904 p=0.0037), and the control's own lift is -0.0155 ~ 0,
revalidating the analytic chance baseline.
**Does not survive — the GAP.** Recorded +0.107; clean **+0.0642, z=+0.824,
p=0.4099, NOT significant.** "0.885 vs 0.778" must stop being cited as separating
the two checkpoints.
**Contamination was not symmetric in effect.** Lift on the contaminated seed minus
lift held-out: Stage 4 **+0.2176**, `graph_attention` **+0.0264** — it inflated
Stage 4 ~8x more, so the recorded gap was inflated *in Stage 4's favour* and
cleaning it NARROWS the gap. An earlier cross-session framing that a surviving Stage
4 lead would therefore be "conservative" is **refuted by this measurement** and must
not be used. The containment premise under it IS separately confirmed by exact tuple
comparison — Stage 4's 64 training scenarios are a strict subset of
`graph_attention`'s 81 — but containment says nothing about which policy benefits
more, and measurement says Stage 4 did.
**Deviates from plan?** No. 4a's deployment decision is untouched: it rested on the
fairness grid, not on this metric.

**Also superseded, flagged here rather than by editing that entry (append-only):**
the 2026-08-18 "CORRECTION to the Stage 4 §16 emergency-priority entry" cites 0.885
in three places — its results table, its "coarse metric INVERTS the ranking"
paragraph, and its "HONEST LIMIT" paragraph. All three are contaminated figures and
should be read against the held-out table above. That entry's own closing
instruction was *"the claim supported at this n is only the one against RANDOM...
widen the seed set before ranking the two policies."* **This audit did exactly that,
and the result vindicates the caution: widened to 12 screened seeds, the Stage 4 vs
`graph_attention` ranking is NOT established (p=0.410), while both remain
significant against RANDOM.** The self-limit was correct and is now discharged.

### 4. Clean bill on everything else that was checked

  - **Density** {0.7,1.0,1.3}: 0.00/0.11/0.44 events per episode, p99 44.2/46.4/53.7s,
    **ovrS=0 at every level**, 27/27 terminate. Demo corridor 0.00 events at all three.
  - **Emergencies ON** (4a pinned them off): 11/12 cells zero events, 12/12 terminate.
  - **No post-peak collapse** — unlike `graph_attention` after 51,624. Stage 4's own
    curve 107,400 -> 153,600 trends UP in reward (1.226 -> 1.354). A full 81-episode
    paired re-run of `137640` vs the deployed `153600` favours 137640 on
    `starved_pct`/p90/p99/`worst_wait`/reward and not on event count, **paired sign
    test p=0.58 — not significant**. No evidence the deployment choice is wrong and no
    established better alternative.
  - **Deterministic beats stochastic on BOTH reward and tails** (1.358 vs 1.258; p99
    33.7s vs 45.5s). **Stage 4 has no reward/fairness tension** — the opposite of the
    `graph_attention` det/stoch finding. `deterministic=True` is correct on both axes.
  - **Not a knife-edge.** The inverse threshold artifact was specifically hunted — a
    policy scoring a clean 0.00 only because its waits sit just under 90s. Median p99
    is 50s, only 8/81 episodes reach p99 >= 80s, and 0.106% of steps fall in the
    70-90s band. The near-zero counts reflect genuine margin.

### 5. Process failure recorded: this audit killed the D1 training run.

The concurrent multi-SUMO sweeps caused D1 to die at 17:09:55 with
`FatalTraCIError: Could not connect` from `psychoflow_env.py:457`, at
`num_timesteps` 132,640 of 155,168. **Cost: ~2,640 steps.** D1 was subsequently
restarted by another operator, resumed from its 130,000 checkpoint and **COMPLETED
at `num_timesteps=156,624`** (final checkpoint 17:59:54, intact), so the
data-diversity hypothesis remains testable and no result was lost.

*Both this session and test-d0 initially reported this as "~37k steps lost, last
checkpoint 95,000" — the identical error, made independently: `ls` sorts
`psychoflow_stage5_100000_steps.zip` BEFORE `..._50000_steps.zip`, so an
alphabetically-sorted listing ends at 95,000 while the run had actually reached
130,000. Verified here numerically (`sort -n`): checkpoints run continuously
5,000 -> 156,624. **Never read a numbered checkpoint series from a default
directory listing** — sort numerically or the most recent work is invisible.*
I did check for a running trainer first and **misread the evidence twice**: the
process check showed a live SUMO process at 7.97 CPU which I recorded as "a stray
sumo process", and I had separately noticed `d1_resume_train.log` with a live mtime
and explicitly wondered whether D1 was running before talking myself out of it.
**Rule going forward: a live `sumo` process is evidence of a running trainer until
proven otherwise; check `_sweeps/*_train.log` mtimes, and announce multi-SUMO sweeps
cross-session before launching.** Not restarting D1 — that is the user's call.

**Verified:** all figures from the committed `_sweeps/stage4_*.json` and
`_sweeps/ga*_proposal.json`. Harness cross-checks above are exact, not approximate.
CLAUDE.md §8 updated in place (its own §9 invites this; BUILD_LOG stays append-only):
the `j1=3` bullet downgraded with the superseded text retained, plus three new
bullets — the Stage 4 audit summary, the Tier 0 fairness caveat, and the
contamination correction. The three existing "0.885" citations (4a's "Not measured
here" paragraph, CLAUDE.md's backend-checkpoint bullet, `backend/sim_runner.py`'s
comment block) were deliberately NOT edited here — test-d0 owns that text and is
correcting them.

## 2026-08-29 — CONSOLIDATING AUDIT ENTRY: deployment decision, two 08-28 corrections, the D1 saga (one conclusion written and withdrawn), the beacon gap now closed

Appended, not edited (append-only). Consolidates four threads settled or advanced
across the parallel sessions of 2026-08-27/28 and a 2026-08-29 forensic audit.
Sections 1–3 point at existing entries and state the current reading crisply
rather than re-deriving them; sections 4–7 add new material — the full D1
timeline, `keep_awake.py`'s corrected role, the `train.py` beacon gap and its
fix, and a 3-point re-check of the D1 checkpoints.

---

### 1. The deployed policy is Stage 4 single-agent PPO (4a bake-off). §9.5 is NOT reopened.

Settled in the 2026-08-28 "4a CHECKPOINT BAKE-OFF" entry; restated because it is
the single most consequential current fact. `backend/sim_runner.py`'s
`DEFAULT_CHECKPOINT` = `training/checkpoints/stage4/psychoflow_stage4_153600_steps_final.zip`,
deterministic. 48 episodes, 4 controllers × 4 topologies × 3 seeds, pinned config:

| controller | starv_ev | wait_var | starved% | reward | ovrS |
|---|---|---|---|---|---|
| tier0 | 0.00 | 50.7 | 0.00 | 1.2011 | 0.00 |
| **stage4_153600** | **0.08** | 72.2 | **0.08** | **1.3450** | **0.00** |
| ga_51624 | 3.33 | 109.8 | 1.20 | 1.2347 | 1.08 |
| ga_154024 | 132.75 | 592.9 | 25.82 | 0.2414 | 15.75 |

On the demo corridor (4,3,2) Stage 4 is 0 events / 0 overrides / 38–42s worst on
all three seeds. **Why this does not reopen §9.5:** `COORDINATION_MODE =
"graph_attention"` answers *which MARL extractor* — attention beat `shared_policy`
12/12 on `starved_pct` (1.64% vs 86.60%), and that A/B is untouched. *Which
trained checkpoint the backend serves* is a separate axis, and single-agent Stage
4 measures better than both MARL modes on it. **Demo-honesty consequence (§17,
§20): the live demo runs SINGLE-AGENT PPO — say so out loud, do not call it
multi-agent.** The MARL result stands as a measured architecture comparison; it
is not what drives the corridor on stage.

### 2. The `j1=3` vulnerability is a sampling artifact — WITHDRAWN as a category.

Settled in the 2026-08-28 "STAGE 4 ADVERSARIAL AUDIT" entry §1. A 27-combo sweep ×
seeds {1,7,42} = 81 episodes against `psychoflow_stage4_153600_steps_final.zip`.
Episodes with ≥1 starvation event, by j1: **j1=2 → 4/27, j1=3 → 3/27,
j1=4 → 2/27**. j1=3 is not the worst; j1=2 is. The earlier reading failed
structurally: every prior test ran through `evaluate_stage.py --j1-recheck`,
whose four-combo matrix ((3,2,3)/(3,2,4) + j1=4 controls) contains no j1=2 combo,
so it could not have detected a j1=2 effect however strong. What is actually
present is a **mild j3 gradient** (mean p99 of across-lane max wait 41.7 / 54.1 /
63.0s for j3 = 2/3/4; event-episodes 1/3/5), ~1 event/episode, thin per-cell n —
**NOT a confirmed hard finding.** Worst combo (4,3,4) = 0.67 events/episode,
0.58% starved, p99 75.7s against a 90s threshold; 81/81 episodes terminate with
all 4668 vehicles arrived; 3/81 fire any §10 override. All 8
`STAGE4_J1_REFERENCE` `worst_wait` values reproduce with delta +0.0 — the
correction is to the interpretation, not the data.

### 3. `phase0_baselines.py` evaluated on training scenarios. The proposal-quality gap does not survive cleaning.

Settled in the 2026-08-28 "STAGE 4 ADVERSARIAL AUDIT" entry §3.
`phase0_baselines.py` runs `STAGES[4]` (the training config) and calls
`reset(seed=s)`; training used `--seed 7` with the same config, so **eval seed 7
reproduces training episode 1 exactly** and supplied 11 of the 26 decidable steps
(42%) behind the recorded 0.885. Re-measured held-out on 11 clean screened seeds:

| checkpoint | recorded (contaminated) | held-out (11 seeds) |
|---|---|---|
| Stage 4 @153,600 | 0.885 | **0.8298** |
| `graph_attention` @154,024 | 0.778 | **0.7656** |
| `graph_attention` @102,824 | 0.767 | **0.7143** |

Both still beat the matched random control (z=+3.388 p=0.0007; z=+2.904
p=0.0037). **The GAP does not survive:** +0.107 → +0.0642, z=+0.824, p=0.4099 —
NOT significant. Contamination was asymmetric — it inflated Stage 4 ~8× more than
`graph_attention` (+0.2176 vs +0.0264), so cleaning it NARROWS the gap. 4a's
deployment decision is unaffected (it rested on the fairness grid, not this
metric). "0.885 vs 0.778" must stop being cited as separating the two
checkpoints; the three prior citations are corrected in CLAUDE.md and
`backend/sim_runner.py` (commit `83dbefd`), with the append-only BUILD_LOG
originals left in place under correction pointers.

### 4. The D1 saga — one clean timeline.

D1 is the persistent-seed-counter Stage 5 `graph_attention` run, testing whether
the post-51k collapse is data-diversity-driven. It ran in four legs, three of
which died:

| leg | log / pid | start | end | num_timesteps | exit |
|---|---|---|---|---|---|
| 1 | `d1_train.log` / 15372 | 2026-08-18 10:53:29 | 2026-08-18 11:16:30 | 0 → 20,480 | `Socket reset by peer` |
| 2 | `d1_resume_train.log` / 18188 | 2026-08-28 15:18:49 | 2026-08-28 17:09:55 | 20,000 → 132,640 | `FatalTraCIError: Could not connect` |
| 3 | `d1_resume2_train.log` / 21820 | 2026-08-28 17:25:13 | 2026-08-28 17:59:54 | 130,000 → **156,624** | ✅ COMPLETED |
| 4 | `d1_resume3_train.log` / 25340 | 2026-08-28 17:35:45 | 2026-08-28 18:02:42 | 130,000 → 150,480 | `Socket reset by peer` |

Times from each Monitor CSV's `t_start`, cross-checked against four TensorBoard
event files (one per `train.py` process) and the four logs.

**Leg 2 died from the concurrent multi-SUMO Stage 4 adversarial-audit sweeps** —
recorded in the 2026-08-28 audit's §5. Cost: ~2,640 steps (130,000 checkpoint
intact).

**Legs 3 and 4 are the "leg3/leg4 collision" `sim/sumo_activity.py` references.**
Byte-identical PRE-FLIGHT lines — same `--timesteps 25168`, same source
checkpoint `psychoflow_stage5_130000_steps.zip`, same target 156,624, same TB dir
`tb/MaskablePPO_1` — and **overlapped from 17:35:45 to 17:59:38 (~24 minutes)**,
two `train.py` processes driving SUMO at once (leg 3 fps 18→15, leg 4 fps 11→9).
Trigger, from file timestamps: `keep_awake.py` written 17:33:56 → launched
17:34:36 → **leg 4 started 17:35:45, 69 seconds later** — a session diagnosed the
sleep problem, armed the workaround, and relaunched the D1 resume without knowing
leg 3 had already been running ten minutes. Leg 3 won (wrote the 156,624 final at
17:59:54); leg 4 died at 150,480 three minutes later.

**The checkpoint directory is confusing but NOT corrupted.** Legs 3 and 4 resumed
from the same checkpoint with the same seed and — scenario sequences being pinned
per burst — computed bit-identical updates: `approx_kl` agrees to 9 decimal
places across all 9 paired rollouts, `value_loss` and `entropy_loss` identical.
Leg 4 wrote `135000/140000/145000/150000` on top of leg 3's identical files; only
`150000` (written 18:01:31, after leg 3's `155000` @ 17:57:49 and the final @
17:59:54) trips an mtime-ordering check. Read-back of every checkpoint ≥ 130,000:
`num_timesteps` == filename in all seven, seed 7, `GraphAttentionExtractor`
throughout. `psychoflow_stage5_130000_steps.zip` and
`psychoflow_stage5_156624_steps_final.zip` specifically: both load clean, both
`num_timesteps` == filename, both `GraphAttentionExtractor`, neither mtime touched
after finalisation (130,000 written by leg 2 at 17:00:29, before either colliding
leg started; the final written by leg 3 at 17:59:54, and leg 4 never reached
156,624). The final survived on timing luck — leg 4 was targeting the identical
filename and died 6,144 steps short.

**The beacon design gap, now closed.** `training/train.py` EMITS a SUMO-activity
beacon (via `_SumoBeaconCallback`) but historically never CHECKED for one — all
14 `require_free()` call sites are sweeps/harnesses, none a trainer. Two
concurrent `train.py` invocations both pass straight through with no check to
race. **Fix (this session, commit `1d974a2`) — two halves, both needed:**

  1. `require_free('train.py (stage training run)')` as the first statement
     inside `train.py`'s `if __name__ == "__main__":` guard, matching the
     14-harness convention exactly (local import inside the guard).
  2. `_sumo_beat()` moved EARLIER — ownership is claimed immediately after the
     pre-flight print and **before `build_env()` and `MaskablePPO.load()`**.
     Half 1 alone would have left a real TOCTOU window: `require_free()` only
     READS the beacon, and the first WRITE previously came from
     `_SumoBeaconCallback` at `_on_training_start`, so for the several seconds
     to a minute of env construction plus checkpoint load, neither of two
     trainers had claimed anything and both would pass. That is the same
     failure shape as the bug being fixed, just narrower — and worth closing
     while the code was open rather than documenting as residual.

`beat()`/`clear()` semantics unchanged; `_SumoBeaconCallback` still refreshes
against `STALE_AFTER_S=300`, and `beat()` preserves `started` for the same pid so
elapsed time stays accurate from process start.
`PSYCHOFLOW_IGNORE_SUMO_BEACON=1` still overrides for deliberate parallelism.
**Verified:** parses and imports; `require_free` raises `SystemExit` with the
standard refusal against a planted live beacon (driven from Python via
`subprocess.Popen(...).pid`, per CLAUDE.md's note that Git Bash's `$!` is an MSYS
pid `psutil` cannot see); and with `build_env` monkeypatched to inspect the
beacon at its own call site, the beacon is present and owned by this pid before
`build_env()` runs. **Remaining window** is now the few imports between the guard
and the claim — not closable without a real lock, which Tier 2 deliberately
defers.

### 5. `keep_awake.py`'s corrected role.

`training/scripts/keep_awake.py` is a standalone companion process (NOT a repo
module): it holds `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED|ES_AWAYMODE_REQUIRED)`
to keep Windows Modern Standby from idle-suspending the machine during a
genuinely-solo long run, then sleeps re-arming until killed. Touches no repo
code, no SUMO, no TraCI. Written 2026-08-28 17:33:56; committed 2026-08-29 as the
one-word `3203c14 "scripts"`.

**Docstring corrected this session (commit `7f430ed`).** It had asserted that
Kernel-Power Id=506 "Modern Standby, Idle Timeout" events killed two D1 resumes
("leg 2 at ~17:00–17:05, leg 3 at ~17:10:56"). The record refutes that: two SUMO
sweeps (`_sweeps/detstoch.log` ending 17:08:47 "wall clock: 9.9 min",
`_sweeps/emerg.log` ending 17:09:27 "10.5 min") ran to completion straight
through that window — a machine actually in Modern Standby could not have let them
finish. Those D1 legs died from concurrent multi-SUMO TraCI-port contention (§4),
not sleep. The "leg 2 / leg 3" numbering in that docstring also maps onto no
log/monitor/tb artifact in the repo. The tool is kept — idle suspend on a
genuinely-solo overnight run is a real risk — but the causal claim is removed.

### 6. `evaluation/heldout.py` — the §15.4 held-out set exists as infrastructure.

Committed 2026-08-28 as `45dbccc`: `evaluation/heldout.py` (399 lines) +
`evaluation/heldout_manifest.json` (90 scenario TUPLES from 30 frozen seeds = the
first 30 primes ≥ 100; `BURNED_SEEDS` = the 12 already spent, including training
seed 7). The manifest is drawn offline under `STAGES[4]` — `_draw_scenario()` is
rng-pure and touches no TraCI — so the module never launches SUMO and is
deliberately runnable during a live training run (and deliberately carries no
`require_free` guard). `--verify <ckpt_dir>` screens the manifest against exactly
that directory's `monitor*.csv` training scenarios, asserts zero collisions PER
CHECKPOINT (not globally — stage4 trained on 64 scenarios, stage5 graph_attention
on 81, a strict superset), and runs a gate self-check that re-derives seed-7
episode-1's key and confirms it IS in the training set — proving the check can
catch contamination and guarding against key-format drift vs `stage4_proposal.py`.

Verified this session:

```
=== training\checkpoints\stage4 ===                   64 training scenarios, 0 collisions, self-check PASS, GATE PASS
=== training\checkpoints\stage5_graph_attention ===   81 training scenarios, 0 collisions, self-check PASS, GATE PASS
=== training\checkpoints\stage5_graph_attention_d1 == 179 training scenarios, 0 collisions, self-check PASS, GATE PASS
```

**Does it satisfy §15.4?** §15.4's three stated requirements — (1) held-out set
defined explicitly (reserved seeds / disjoint index range) recorded in the repo;
(2) disjointness asserted programmatically, not by inspection; (3) training-set
distinct-scenario count stated alongside any generalization claim — are all met
as **infrastructure**: `HELDOUT_SEEDS` is frozen, the manifest committed,
`verify()`/`assert_disjoint_at_runtime()` are the programmatic gate, and
`training_set_size()` is exported and printed by `--verify`. What does NOT yet
exist: any eval harness that USES it. Wiring `HELDOUT_SEEDS` into the eval
harnesses and running `--verify` before publishing a generalization number is
Phase 12's job, and the generalization evaluation §15.4 ultimately calls for is
still unbuilt. The mechanism §15.4 requires exists and is verified; the
evaluation is not done.

### 7. D1 final status: two evaluation passes, the first withdrawn.

**D1 finished training** at `num_timesteps=156,624` on 2026-08-28 17:59:54
(`training/checkpoints/stage5_graph_attention_d1/psychoflow_stage5_156624_steps_final.zip`,
`GraphAttentionExtractor`, seed 7). No background run is live. Its training set is
**179 distinct `STAGES[4]` scenarios** — 2.2× the standard `graph_attention`
run's 81 — so the persistent-seed-counter mechanism did produce more scenario
diversity. Per-burst: leg 1 contributed 33, leg 2's burstB brought the union to
179, and **legs 3 and 4 (burstC, burstD) each added ZERO new scenarios** — the
collision legs contributed only passes, no diversity.

**D1 was evaluated in two passes this session, and the FIRST PASS'S CONCLUSION IS
WITHDRAWN. It is recorded here in full rather than deleted, because the way it
failed is the same way the `j1=3` and `0.885` claims failed and is the reusable
lesson.**

**Pass 1 — 3 checkpoints (20,000 / 130,000 / 156,624), 12 pinned (combo, seed)
pairs each, deterministic, validator ON, no emergency, density pinned 1.0:**

| checkpoint | mean starved% | worst_wait band |
|---|---|---|
| 20,000 | 87.2% | 126–129s |
| 130,000 | 38.0% | 125–135s |
| 156,624 (final) | 31.5% | 123–171s |

**What was written from that, and is now WITHDRAWN:** *"D1's final checkpoint is
in the same collapsed regime as standard `graph_attention` @154,024 ... more
scenario diversity did not prevent the post-51k collapse."*

**Why it was wrong — a coverage gap, not an arithmetic error.** A "collapse"
claim requires exhibiting a peak that was fallen from. The standard
`graph_attention` run peaks at **51,624**. Of the three points sampled, none lies
within ±25,000 of that; the draft compared D1's *late* checkpoints against the
standard run's *peak* and read the difference as a collapse. This is structurally
the `--j1-recheck` mistake a third time: a sampling matrix that could not contain
the answer being read as though it had. It is also refuted by its own three
numbers, which run 87.2% → 38.0% → 31.5%, i.e. monotonically *improving* — the
opposite shape from the standard run's 1.6% → 31%.

**Pass 2 — the peak region actually sampled.** Five more checkpoints across
45,000–65,000 (the region containing the standard run's peak), same 12 pairs,
same harness, **60 further episodes**. Raw data:
`_sweeps/d1_peak_region.json`.

| ckpt | mean starved% | mean reward | mean ovrS | mean worst_wait | flagged (j1=3) | controls (j1=4) |
|---|---|---|---|---|---|---|
| 45,000 | 19.71% | +0.4271 | 7.8 | 125.0s | 20.16% | 18.81% |
| 50,000 | 47.70% | −0.3207 | 33.9 | 126.2s | 50.13% | 42.82% |
| 55,000 | 45.91% | −0.2925 | 29.2 | 127.0s | 45.77% | 46.18% |
| **60,000** | **7.42%** | **+0.9360** | **4.3** | 126.7s | 5.76% | 10.74% |
| 65,000 | 46.78% | −0.1922 | 34.0 | 125.8s | 46.76% | 46.83% |

60/60 episodes terminate with all 4668 vehicles arrived.

**THE ACTUAL FINDING — D1 is VIOLENTLY UNSTABLE ACROSS CHECKPOINTS, and neither
"collapse" nor "steady improvement" describes it.** The full eight-point series
is 87.2 / 19.7 / 47.7 / 45.9 / **7.4** / 46.8 / 38.0 / 31.5. Adjacent checkpoints
**5,000 steps apart differ by up to 39 percentage points** (60,000 = 7.42% vs
65,000 = 46.78%), and 45,000 → 50,000 jumps 19.7% → 47.7% in the other
direction. There is no trend; the three pass-1 points were three draws from an
oscillating run, not samples of a curve.

**The load-bearing consequence: any single-checkpoint claim about D1 is close to
meaningless** — including the one pass 1 built on 156,624. The between-neighbour
variance exceeds the difference between the readings the two passes disagreed
over. Eight of ~31 available checkpoints are now sampled.

**What this does to the data-diversity hypothesis — the direction survives, the
reasoning does not.** D1's *best sampled* point (60,000: 7.42% starved, reward
+0.9360, ovrS 4.3) is still worse than standard `graph_attention`'s recorded peak
on these same 12 pinned pairs (51,624: **1.64%** starved, reward **+1.2594**,
ovrS 1.42). So 2.2× the scenario diversity did not produce a better policy — but
the mechanism is **"never got stably good," not "peaked then collapsed."** Two
honest limits: with 23 checkpoints unsampled on a run that swings 39 points
between neighbours, 7.42% is a **floor on D1's best, not a measurement of it**;
and no same-session A/B against standard `graph_attention` on identical pairs was
run, so the cross-run comparison leans on the recorded 51,624 figures.

**The j1=4 control asymmetry at 156,624 is NOT a finding — the peak sweep is what
shows why.** At 156,624 the controls read 2.70%/3.02% against 15–61% for the
j1=3 combos, which in isolation looks like a real split. Across the sweep the
flagged-vs-control gap **flips sign**: controls better at 45,000 and 50,000,
tied at 55,000 and 65,000, and **worse** at 60,000 (10.74% vs 5.76%). It is one
draw from an unstable run, n=2 on the control arm, with neighbouring checkpoints
disagreeing. Recorded explicitly so it is not later promoted to a pattern —
that promotion is exactly what happened to `j1=3`.

**Incidental third confirmation of the `worst_wait` saturation rule.** Across all
60 peak-region episodes `worst_wait` sits in a 122–139s band while `starved_pct`
ranges **3.97%–68.84%**. A metric that cannot separate 4% from 69% is not
measuring policy quality — independent reproduction, on a fourth harness, of the
2026-08-28 Phase 0 finding and §15.2's ban.

**The two long-wait episodes (150s, 171s) are DOCUMENTED §10.1 BEHAVIOUR, not a
new defect.** Both exceed the 121–142s band recorded anywhere else in this
project, so the override log was pulled (`_sweeps/d1_override_pull.json`):

| | (3,2,4) seed 42 | (3,2,4) seed 1 |
|---|---|---|
| worst_wait | 171.0s | 150.0s |
| overrides | 85 | 217 |
| by rule | 100% `starvation_ceiling` | 100% `starvation_ceiling` |
| applied / deferred | 61 / 24 (28.2%) | 166 / 51 (23.5%) |
| override `wait_s` max | 144.0s | 150.0s |
| fired above the 120s ceiling | 85/85 | 217/217 |
| episode | 628 steps, 4668 arrived, terminated | 640 steps, 4668 arrived, terminated |

Zero emergency overrides (config pins `spawn_emergencies=False`), so this is
purely the starvation ceiling. Three things settle it as known behaviour.
(1) Every override fires above the 120s ceiling — the gate is working, not
missing. (2) The deferral fraction (23.5–28.2%, `outcome=deferred_min_green`) is
the documented `MIN_GREEN_S` deferral §10.1 specifies, not an unhandled path.
(3) Decisively, seed 1's records show the ceiling firing on the **same lanes
repeatedly with the wait not falling** — `J2_J1_1` at t=2870/2875/2880 all
`applied` at 150.0s, then `J2_J1_0` at t=2890/2895/2900/2910 `applied` at
147–150s. Green was granted and the queue did not drain. That is verbatim
§10.1's stated genuine limit: *"if a lane is starved because of downstream
gridlock rather than its own signal, giving it green does not discharge it and
the wait keeps climbing. The ceiling guarantees the signal has stopped being the
cause of that lane's starvation. It does not guarantee the lane drains."*
Seed 42 makes the same point from the other side: its peak (171.0s on `J2_J1_2`
at t=1110) is **27s above the highest wait any override ever fired at** (144.0s),
i.e. at the peak instant J1's ceiling did not need to fire — the served phase
already covered that lane.

**How hard the shield is actually working, quantified** (computed from
`_sweeps/d1_override_pull.json`, no new runs). Against Phase 8's adversarial
rule-based Segment 2 — a controller *deliberately built to starve J2 north*,
which produced 13 `starvation_ceiling` overrides across 238 steps =
**0.055 overrides/step**:

| episode | overrides / steps | per step | vs adversarial baseline |
|---|---|---|---|
| (3,2,4) seed 42 | 85 / 628 | **0.135** | **~2.5×** |
| (3,2,4) seed 1 | 217 / 640 | **0.339** | **~6.2×** |

So at this checkpoint the policy makes §10's ceiling fire between two and six
times as often as a controller written on purpose to starve a lane. That is a
measure of how much work the shield is doing to keep the corridor moving, and it
is **consistent with — not an additional finding beyond — "the policy never got
stably good"** (§7's pass-2 result). It is **not** a validator defect: every one
of those overrides fired above the 120s ceiling, and both episodes still
terminate with the full **4668 vehicles arrived**. Stated because the raw
override counts (85, 217) mean little without a reference point, and the
adversarial segment is the only comparable one on record.

**One record update this does force, small but real:** §10.1 quotes a measured
overshoot of **124–141s** against the 120s ceiling, from Phase 4's B3/B4 on
corridor 4/3/2. These runs measure **up to 171s** on (3,2,4) under a policy
starving 46–61% of steps. That is not a contradiction — §10.1 already says *"the
overshoot is scenario-dependent and will grow under heavier load"* — but the
quoted range is now known to be a 4/3/2-specific figure, not a corridor-wide
bound, and should be cited that way. **No change to §10, the validator, or the
ceiling constant is warranted:** every override fired correctly, and both
episodes still cleared all 4668 vehicles.

**Net status of the data-diversity thread.** The three "re-evaluate once D1
completes" flags (CLAUDE.md's backend-checkpoint bullet,
`backend/sim_runner.py`, BUILD_LOG's 4a entry) are answered to the extent this
audit can answer them: **D1 does not produce a checkpoint that would displace
Stage 4 as the deployed policy** — its best sampled point is worse than standard
`graph_attention`'s peak, which the 4a bake-off already placed well behind
Stage 4 (0.08% starved, reward 1.3450). 4a is not reopened. What is NOT settled
is *why* more diverse data failed, and the instability finding is the more
interesting lead — a run that swings 39 points between adjacent checkpoints at
fixed hyperparameters points at optimisation dynamics, not data. **Per the
scope set for this pass, the D1 thread is CLOSED here.** Reopening it needs a
specific reason, and if it is reopened the first move is checkpoint-density
sampling (all ~31 points, not 8), because that is the axis this audit showed is
load-bearing and under-measured.

**Deviates from plan?** No locked decision (CLAUDE.md §2) touched. `train.py`
gained a `require_free` guard at startup AND an early `_sumo_beat()` claim before
`build_env()` (§4, commit `1d974a2`); `keep_awake.py`'s docstring corrected (§5,
commit `7f430ed`); CLAUDE.md's CURRENT STATUS bullet updated to reflect D1
completion (one factual line, no separate entry). No reward, validator, or env
code changed. No training run started. §10.1's quoted overshoot range is flagged
in §7 as 4/3/2-specific rather than corridor-wide; the master plan is not edited
here, since the correction is a scoping note on an existing measured figure.

**METHOD NOTE — this entry contains a conclusion this same session wrote and then
withdrew (§7, pass 1 vs pass 2).** That is the third time in this project a
confident interim reading has been overturned by re-examining coverage rather
than arithmetic: `j1=3` (a matrix with no `j1=2` combo), `0.885 vs 0.778`
(seeds that replayed training episodes), and now D1 "collapse" (three points
none of which sampled the peak region). All three shared one shape — **the
measurement could not have detected the alternative, and that was not checked
before the conclusion was written.** The cheap defence is to ask, before writing
any comparative claim, which regions the sampling would have had to cover for the
opposite conclusion to be visible.

**Verified:** every figure is read from a committed artifact (`_sweeps/*.log`,
Monitor CSVs, TensorBoard event files, checkpoint zips) or produced this session
and named inline. `train.py` beacon fix: parse + import + positive refusal test.
`keep_awake.py`: parse + commit `7f430ed`. `heldout.py`: `--verify` on all three
stage dirs, GATE PASS + self-check PASS. `--j1-recheck`:
`training/evaluate_stage.py --j1-recheck` on the three D1 checkpoints, exit 0,
raw output retained. Peak-region sweep: 60 episodes via
`evaluate_stage._run_plain_episode` + `ppo_picker` (the same harness the recorded
j1 rows came from, not a re-implementation), persisted to
`_sweeps/d1_peak_region.json`. Override pull: 2 episodes with a `run_episode`
`on_step` hook tagging each `OverrideRecord` with `sim_time`, persisted to
`_sweeps/d1_override_pull.json`.

**Two harness errors of my own, recorded because this file's standing rule is
that a run which passes while proving nothing is the failure mode to watch.**
(1) The first override-pull script indexed `OverrideRecord` fields that do not
exist (`sim_time`, `trigger_wait_s`; the real fields are `junction_id, rule,
from_slot, to_slot, lane_id, wait_s, outcome`, `safety/validator.py:93`). It
raised `TypeError` on the print AFTER producing seed 42's aggregate, so seed 1
never ran — a loud failure, correctly. Rewritten and both episodes re-run.
(2) That crash sat upstream of the peak-region JSON write, so those 60 episodes
were briefly held only in stdout; recovered by parsing the captured output rather
than re-running, and the parsed table was checked against the raw lines before
being written.

## 2026-08-29 — Commit `3496057`: five correctness fixes (§11.2 / §12.1 / §12.2 / §13.2)

**Backfilled record.** `3496057` landed 2026-08-28 17:39 as an adversarial-audit
follow-up to Phase 8, and never got a BUILD_LOG entry — so the Phase 8 entry's
recorded §11.2 baseline arithmetic sat wrong-and-uncorrected in this file for a
day, with no pointer telling a reader it had been superseded. Two correction
pointers have now been inserted into that entry; this entry is the target they
point at. Written from the commit diff and message, not from memory of the
session that made the change.

**Fix 1 — `DecisionLog.record_step` now RAISES on an override at a junction
absent from `decisions` (was: dropped silently).**
**Why:** the loop iterates `decisions.items()`, so a §10 `OverrideRecord` for a
junction the caller did not score was discarded without a sound. The deployed
policy is RL, and `sim_runner._pick_action` returned an EMPTY decisions dict in
auto mode — so every shield firing under the deployed configuration would have
vanished from the log, and a §15 "how often did the safety gate have to fire"
metric computed from it would have read ~100% proactive for a policy doing
nothing proactive. That is a textbook §0.3 done-bar-integrity failure, the same
shape as the invalid "near-100% emergency priority" metric §0.3's footnote is
built around. `sim_runner._pick_action` correspondingly now emits a full
per-junction dict with `reason="rl_policy"`.
**Deviates from plan?** No — enforces §12.1's contract rather than changing it.
**Verified:** `decision_log._selftest` scenario 7 (auto-mode override reconciles;
`decisions={}` and a partial dict both raise `ValueError` naming the junction),
plus `run_backend_smoke.py` checks 1b/1c.

**Fix 2 — `estimate_baseline_clearance_s` traversal math corrected; it was
roughly DOUBLE-counting.**
**Why:** it charged `min_green + yellow` for every phase including the target and
added a trailing yellow. The correct worst-case traversal is the remaining
min-green on the current phase + `(n-1)` yellows + `(n-2)` intervening
min-greens; the target's own min-green is not waited, since service begins the
instant it goes green. 3-green baseline **42.0 -> 28.0s**; 2-green **28.0 ->
14.0s**. **This is the one number in the system shown to a human operator as
decision support (§11.2, and master plan §11.2's own blocker note), so an
inflated baseline directly inflates the advertised `improvement_pct`** — the
Phase 8 entry's `71.4%` becomes `42.9%`.
**Deviates from plan?** No — §11.2 gives the field, not the model behind it.
**Verified:** the self-test now DERIVES its expectations from first principles in
the test body rather than asserting the function's own output back at it (closing
a "a wrong model passes its own test" gap), plus hand-computed anchors at
MIN_GREEN=10 / YELLOW=4 that fail deliberately if `MIN_GREEN_S` ever moves.

**Fix 3 — `responder_messaging._MIN_GREEN_S` imports `env.psychoflow_env.
MIN_GREEN_S` instead of re-typing the literal.** Drift risk; CLAUDE.md §8
single-source discipline, same class as `WAITING_TIME_MEMORY_S` and
`TIME_TO_TELEPORT_S`.

**Fix 4 — the `rl_policy` narration template frames the busiest served lane as
CONTEXT, not as the stated cause.**
**Why:** the previous wording read like "Lane N — selected", attributing a
rationale to an opaque trained policy that the system cannot attest. Applied in
both `explainability/narrator.py` and `sim_runner._NARRATION`.
**Deviates from plan?** No — §12.2's honesty requirement; §17's boundary
language.

**Fix 5 — the payload gained `baseline_is_worst_case: true`** alongside the
existing `baseline_is_estimate: true`, and the summary text says "worst-case".
The estimate assumes the target phase is last in rotation, which is a
worst-case, not a typical case — saying only "estimate" understated how
conservative it is.

**Deviates from plan?** No locked decision (CLAUDE.md §2) touched. Nothing in
`env/`, `env/reward.py` or `safety/validator.py` changed.
**Verified (at the time, per the commit message):** all module self-tests +
`python -m env.reward` + `python -m safety.validator` green; Phase 8 harness and
Phase 9 smoke (23/23 at that commit) pass live. **Re-verified 2026-08-29** in the
project venv: 8/8 module baselines green and `sim/run_backend_smoke.py` **24/24
pass** (the count reached 24 via `f3d5908`'s §15.2 metrics check, after this
commit's 1b/1c took it from 21 to 23 — CLAUDE.md §8 had this stale at 21/21 and
is now corrected).

**Note — what this commit did NOT do, and is the reason it reads as incomplete.**
Fix 1 patched `sim_runner._pick_action` to satisfy `decision_log`'s contract, but
`backend/sim_runner.py` still runs its OWN hand-rolled `_decision_entry` /
`_narrate` adapter rather than calling `DecisionLog` / `narrate` at all — so the
raise it added protects a call site the backend does not yet make. The
adapter->Phase 8 swap remains open, and is the actual remaining work at the
PHASE 8 SEAM.

## 2026-08-29 — PHASE 8 SEAM CLOSED: DecisionLog monotonicity guard, operator-emergency provenance, and the adapter swap (§11 / §12 / §13.2)

Three separately committed, separately verified steps, in order. The
cross-phase scope (Phase 8 modules + Phase 9 backend in one pass) was
approved on the condition that they land and be verified one at a time —
recorded here because CLAUDE.md §6 otherwise treats a multi-phase change as
a stop-and-ask.

### Commit B — `DecisionLog` refuses a backwards `sim_time`.

**Decision:** `record_step` and `record_voice` raise `ValueError` on a
`sim_time` earlier than the highest already recorded. Equal is legal (one
entry per junction per step; a voice entry may share an instant).
**Why:** `entries_for` / `latest` / §12.3's `why()` read the deque
POSITIONALLY and take the last match — they never sort. So an out-of-order
entry raises nothing and instead makes every later at-or-before query answer
with the wrong decision: this repo's named failure mode, a run that passes
while proving nothing. The realistic route in is an episode boundary, since
`env.reset()` sends sim_time back to ~0. The guard is what makes commit D's
per-episode log lifecycle load-bearing rather than a convention.
**Deviates from plan?** No. Nothing in `env/`, `env/reward.py` or
`safety/validator.py` touched.
**Verified:** `python -m explainability.decision_log` — scenarios 1-7
unchanged plus a new scenario 8 (equal/increasing accepted; both recorders
reject t=150 after t=200; the rejected calls append nothing and do not move
the watermark; a FRESH log accepts t=0.0). `narrator` and `query_interface`
self-tests re-run green.

**The guard's first catch was in-repo, and is recorded rather than quietly
fixed:** `sim/run_explainability_episode.py` recorded its §12.2 voice entry
at a hardcoded `t=1234.0` into a Segment-1 log already at t~3145. It now
records at the log's own watermark (`log.latest().sim_time`). One line, no
measured claim moved.

### Commit C — operator-vs-detected provenance on §11.1 / §11.2.

**Decision:** `EmergencyClearanceCoordinator.observe(..., *,
forced_emergency_lanes: frozenset[str] = frozenset())` — the same name, type
and default as `safety.validator.validate()` — unioned per junction with the
sensed-ambulance set. `EmergencyClearanceEvent.source` is `"detected"` |
`"operator"`; detection wins on overlap; the field is fixed when the episode
opens and never mutated. §11.2's payload gains `trigger_source` and its
summary text follows it.
**Why:** §10's emergency branch already fired on either trigger, but §11.1
only ever saw a sensed ambulance — so an operator-forced clearance produced
no clearance episode and no §11.2 message at all. And the messages it did
produce said "cleared for emergency vehicle" regardless, asserting an
observation the system never made, in the one place a sentence is shown to a
HUMAN as decision support. `source` is fixed at open because the field is the
provenance of the TRIGGER: an ambulance turning up later at an
operator-opened junction does not make the trigger a detection.
**Deviates from plan?** Additive to §11.2's literal schema, and master plan
§11.2 was updated in the same commit with the field, its enum and the
fixed-at-open rule. Nothing in `env/`, `env/reward.py` or
`safety/validator.py` touched.
**Verified:** `python -m coordinator.emergency_clearance` (9 scenarios — the
3 originals plus operator-only trigger, detected+forced simultaneously at one
junction, same-lane overlap, source-fixed-at-open, cross-junction forced-lane
attribution, and default-equivalence) and
`python -m coordinator.responder_messaging`.
**Backward compatibility MEASURED, not asserted:**
`sim/run_explainability_episode.py` does not pass the new kwarg and
reproduces the recorded Phase 8 figures exactly — 627 steps / 1881 entries /
18 §10 override records / 13 `starvation_ceiling` / 2 clearance episodes both
resolved (J2 `served_on_arrival` 0.0s `override_fired: true`, J1 3.0s
`override_fired: false`). The self-test pins it directly too: a coordinator
with the kwarg omitted and one passed `frozenset()` produce byte-identical
`to_dict()` output.

### Commit D — the adapter swap.

**Decision:** `backend/sim_runner.py`'s hand-rolled `_NARRATION`,
`_decision_entry()`, `_reason_for()`, `_representative_lane()` and
`_narrate()` are DELETED. The §13.2 frame's `decision` is
`DecisionLogEntry.to_dict()` and its `narration` is
`explainability.narrator.narrate(entry)`. `_emit_junction()` survives as a
pure selector over the entries `record_step` returns, with the precedence it
always had (emergency override > starvation ceiling > lowest switched
corridor index > rotate by step index), so the wire schema did not move.
§11.2 responder messages ride the frame as `responder_messages`, ADDITIVE
and only when non-empty. `QueryInterface` is wired from
`from_twin_topology(...)`.
**Why:** the seam's whole point. The adapter reimplemented §12.1/§12.2 from
the same inputs and drifted from them — see the regression below.
**Deviates from plan?** No. Nothing in `env/`, `env/reward.py` or
`safety/validator.py` touched; no locked decision (CLAUDE.md §2) reopened.

**Two snapshots, and the order is load-bearing.** `env.step()` rebinds the
twin's snapshot (`twin.update()` builds a NEW dict), so the loop holds two
distinct objects. The PRE-step snapshot goes to `record_step()` — it is what
§10's validator judged the action against (`psychoflow_env.py` step 2b) and
what the observation was built from. The POST-step snapshot is the frame's
`digital_twin` field and what §11.1 observes, matching the Phase 8 harness.
**Swapping them fails SILENTLY** — lane ids exist in both, so every field
still populates while describing the wrong instant.

**Per-episode lifecycle:** `_reset_counters()` REPLACES `self._log`,
`self._query` and `self._coord` rather than clearing them, and runs after
BOTH the natural episode end and a `set_topology` rebuild. Commit B's guard
is what turns a mistake here into a loud failure.

**THE LIVE REGRESSION THE SWAP FIXES, and why the first version of the check
proved nothing.** The deleted `_representative_lane` named the busiest lane
of the EXECUTED PHASE's served set. Right after an emergency override that
set is *tied at zero wait across BOTH approaches the phase serves*, so
`max()` returned an arbitrary tie-break and the compass direction printed
had nothing to do with the forced lane. Demonstrated on the captured live
lane set (J1 phase 0, 8 lanes, 4 north + 4 south, all `wait_max=0.0`,
forced lane `N1_J1_2`):

| iteration order | deleted rule names | direction correct? |
|---|---|---|
| sorted | `N1_J1_0` (north) | yes, by luck |
| reversed | **`S1_J1_3` (south)** | **NO** |
| frozenset as seen | `N1_J1_1` (north) | yes, by luck |

`explainability/decision_log` takes the lane from the §10 `OverrideRecord`
instead, so entry and narration both name the lane the operator forced.
**Worth recording as method:** the first version of smoke check 4 forced *the
busiest lane*, which is usually a lane already being served — where the old
and new rules agree and the check passes without discriminating. Check 4a now
picks a lane OUTSIDE the junction's current green set whose approach differs
from every lane that green serves, and asserts it did so, before 4b/4c run.

**Verified:** `sim/run_backend_smoke.py` — **37 passed, 0 failed** (project
venv, deployed Stage 4 checkpoint; was 24). 13 new checks, each pinning a
specific regression: 1d/1e/1f (`_reset_counters` replaces the log; the
rebuilt `QueryInterface` binds the new one; a REUSED log rejects the
post-reset sim_time while the fresh one accepts it — no SUMO), 2b
(`{lane}` renders a within-approach INDEX, not a raw SUMO lane id), 4a
(forcing outside the current green set), 4b/4c (entry and narration name the
FORCED lane and its real direction — `J1 slot 0 serves ['north','south'];
forcing J2_J1_0 (east)` -> `J1 · Emergency override — East cleared for
ambulance.`), 4d (`proposed` AND `override` both present), 4e (a §11.2
message arrives after `EMERGENCY_HOLD_S`, `trigger_source='operator'`), 5c
(sim_time crosses a live reset backwards, 105.0 -> 25.0, the sim thread
survives it and decisions are monotonic again).
`sim/run_explainability_episode.py` re-run green and unchanged. All 5 Phase 8
module self-tests plus `python -m env.reward` and `python -m safety.validator`
(11/11) green.

### OPEN ITEM, NOT FIXED — `served_on_arrival` can be reported for a lane that was not already clear.

Found while verifying commit D; **not** introduced by it. **The call on how
to fix it is the user's (CLAUDE.md §6), and the numbers below are traced, not
inferred.**

§11.1's `observe()` is called with `info["sim_time"]` (POST-step), so
`first_detection_sim_time` is stamped on the 5s decision grid, while
`green_onset` is recovered as `sim_time - time_since_switch_s` at true 1s
resolution. When §10 clears the lane INSIDE the same decision step, green
onset lands BEFORE detection, `served_on_arrival` fires and
`clearance_time_s` floors to 0.0. Live backend, operator trigger on
`N1_J1_0` at J1:

```
t=90.0  forced=['N1_J1_0']  ovr=[('J1','emergency_override',1,0,'applied')]
        J1 open: lane=N1_J1_0 src=operator det=90.0 green_onset=88.0
                 cur_slot=0 green_age=2.0 serves=True clearance=0.0 on_arrival=True
```

The lane went green at t=88, ~2s after the request — it was **not** already
clear. The operator-facing summary nonetheless reads "was already clear when
the clearance was requested". §11.2 is the one place in this system where a
number and a sentence go to a human as decision support (master plan §11.2's
own blocker note), which is why this is logged rather than left as a detail.

**Pre-existing:** `run_explainability_episode.py` calls
`observe(info["sim_time"], ...)` the same way, and the 2026-08-28 Phase 8
entry records the same 0.0s `served_on_arrival` result as its caveat 1. The
swap is what put it on an operator-facing wire.

**Candidate fixes, neither applied:** stamp detection at the PRE-step
sim_time (the boundary from which the request was actually live), or floor
`green_onset` at the previous decision boundary. Either changes §11.1's
recorded numbers, so `python -m coordinator.emergency_clearance` and
`sim/run_explainability_episode.py` must be re-run and the Phase 8 figures
re-recorded.

**Next per §18 is still Phase 10 — Frontend.** Phases 1-9 remain complete;
this pass closed the last outstanding item at the Phase 8 / Phase 9 seam.

## 2026-08-29 — §16 STAGE 4 EMERGENCY CHECKPOINT: the sparse-signal hypothesis is REFUTED as the primary mechanism; the cause is OBSERVABILITY. "15/15 = 0% proactive" withdrawn a second time.

Appended, not edited. **DIAGNOSIS ONLY — no fix, no training run, no code
changed.** CLAUDE.md §2's locked decisions untouched; nothing in `env/`,
`env/reward.py`, `safety/validator.py` or `backend/` modified. Phases 10/11/12
not started. Every figure below comes from committed `_sweeps/*.json`, the
Stage 4 Monitor CSVs, TensorBoard event files, or `evaluation/heldout.
drawn_scenarios` (rng-pure, starts no SUMO). **No SUMO process was launched.**

CLAUDE.md has carried, since 2026-08-15, an explicit and explicitly UNTESTED
hypothesis for why §16's Stage 4 emergency bar failed: *"the training signal for
emergency handling may simply be too sparse to learn from."* This entry tests it.

---

### 1. Sparsity, quantified — "sparse in scenarios" is FALSE, "sparse in steps" is TRUE

**Decision:** record the sparsity as a measured number rather than an adjective.
**Why:** the hypothesis was never given a magnitude, and 5%-of-steps and
0.1%-of-steps are different problems with different fixes.
**Measured:**

  - **64 / 64** distinct Stage 4 training scenarios contain exactly one ambulance
    (`n_emergencies=1`; both Monitor CSVs, 80 episodes, zero blank
    `emergency_route`). Scenario-level sparsity is **zero**.
  - The ambulance exists in the observation only while on a *sensed* lane, and
    `twin/digital_twin.py` senses INCOMING lanes only. Measured across 138
    committed eval episodes (57 policy-driven, 81 random control), route
    recovered per seed offline:

    | route type | n | ambulance-visible steps | penalty-firing junction-steps |
    |---|---|---|---|
    | corridor (`r_we`/`r_ew`) | 22 | **12.82** | 2.36 |
    | cross (6 routes) | 35 | **2.46** | 1.06 |

  - Stage 4's actual training mix is **14 corridor / 66 cross** (17.5% corridor).
    Route-weighted onto the real run:

    | | count | of Stage 4's 50,449 steps | of the 153,600-step lifetime |
    |---|---|---|---|
    | ambulance VISIBLE | ~342 | **0.68%** | 0.22% |
    | penalty FIRING | ~103 | **0.20%** | 0.067% |

  - The lifetime column matters: Stages 1-3 ran `spawn_emergencies=False`, so
    **two thirds of the policy's training contained no ambulance at all.** And
    per the burst-replay rule those ~342 steps are ~274 *distinct* situations.

**Answer to the magnitude question: 0.68% visible, 0.20% firing — the 0.1% end,
not the 5% end.**
**Deviates from plan?** No.

### 2. The reward term is NOT drowned out — it is the loudest signal in the function

**Decision:** rule out "structurally too small to produce a gradient."
**Why / measured:** `w_emergency = 20.0` per blocked junction per step.

  - At a firing step the reward goes **+1.34 -> -18.66**.
  - GAE with SB3 defaults (`gamma=0.99`, `gae_lambda=0.95`; `training/train.py`
    passes neither) gives `gamma*lambda = 0.9405`, a credit horizon of **49 steps
    (~244 simulated seconds)** at advantage >= 1.0 — far longer than the 2.5
    (cross) / 12.8 (corridor) step visibility window. **Credit assignment is not
    the bottleneck.**
  - Share of reward mass, computed from the Stage 4 TRAINING LOG via the identity
    `total = throughput - starvation - emergency - switch`:

    ```
    nominal inserted = 2x1000vph + 6x600vph over 3000s = 4666.7  (recorded arrived @1.0 = 4668)
    80 episodes, 50,449 steps, 0 truncations (max 644 vs 720)
      sum episode returns (Monitor 'r')      =  60,030.8
      estimated arrived                      = 367,139.5
      sum throughput_bonus = 0.25 x arrived  =  91,784.9
      => sum(starvation+emergency+switch)    =  31,754.0
      summed |emergency| = 103 x 20          =   2,060
      emergency share of VARIABLE terms      = 6.49%
      emergency share of ALL reward mass     = 1.67%
    ```
    Sensitivity on the firing-step numerator (70/103/140/181): 4.4%-11.4%.

**CORRECTION MADE WITHIN THIS PASS, recorded rather than quietly fixed.** The
first version of this calculation quoted **5.87% / 1.62%**, using per-step terms
(`throughput 1.82, starvation 0.33, switch 0.325`) taken from
`_sweeps/reward_term_pre51k.json` and `_sweeps/det_stoch_diag.json` — **both are
`graph_attention` checkpoints, not Stage 4.** **No Stage-4 per-term reward
decomposition exists anywhere in this repo** (`stage4_*.json` and
`checkpoint_bakeoff.json` carry `mean_reward`/`ovrE`/`ovrS` only). The figures
above replace them and are derived from Stage 4's own training log. The
conclusion is unchanged and marginally strengthened.
**Deviates from plan?** No.

### 3. Critic exposure — thin, and the large majority of it is IRREDUCIBLE by construction

> **[CORRECTED 2026-08-30 — the single "81%" figure is replaced by a RANGE of
> 81–92%, and the vis/none bucket table shown below in this section was
> ILLUSTRATIVE CONTEXT, not the literal input to the 17.1 / 19.0 / 81.0 split.]**
> A reviewer recomputed `Var_B(E[C|B])` directly from the two printed bucket
> stats (`p_vis=0.0064175, mean_vis=10.2179, var_vis=108.5088`; `p_none`,
> `mean_none=0.5733, var_none=6.3094`; `E[C]=0.6352`) and got **0.593**, i.e.
> **7.85% reducible / 92.15% irreducible** — not the 1.2895 / 17.1% / 81.0% this
> section states. The total reproduces (0.593 + 6.965 = 7.558), so the two-bucket
> table is internally consistent; it just is not what produced the split.
>
> **What actually produced 1.2895:** a FINER partition — `vis` states split by
> `(steps-into-transit k, corridor-route flag)` into 15 sub-buckets, `none` as a
> single bucket (16 buckets total). Recomputed and confirmed this session:
> two-bucket {vis,none} → `Var_B = 0.5931` (**7.85%** reducible); + position/route
> sub-buckets → `0.5931 → 1.2895` (**17.06%**); + an exact `sim_time` (50s bins,
> `none` states only) → `1.4344` (**18.98%**). Totals close to 7.5584 in all three.
>
> **The honest reading — reducible is a RANGE, 7.85%–19.0%; irreducible 81%–92%.**
> The 7.85% lower bound (vis/none only) is the *faithful* "what a perfect critic
> conditioned on the observation could remove," because the observation's only
> ambulance channel is a per-lane count — it shows presence and location but not
> `k` directly, and `sim_time` is absent entirely (`env/obs_action_spec.py`). The
> 17–19% figures over-credit the critic with transit-position / clock information
> it does not have. So the vis/none lower bound makes this section's point
> *stronger*, not weaker: **92% irreducible, not 81%.**
>
> **Does this change the verdict? NO.** §6's "no fix, no retrain" and §4's
> observability root cause rest on `env/obs_action_spec.py`'s channel list and the
> route-sensing geometry — both read directly from code, neither touched by this
> arithmetic. The direction (an ambulance is visible in 0.64% of states; the rest
> of the emergency variance in the value target is unpredictable from the obs) is
> unchanged and, at the 92% end, blunter. This is the third correction in the same
> diagnostic pass; the two below were made inline before commit `585b666`, this
> one after, so it takes the blockquote-pointer form used elsewhere in this file.

**Decision:** record the observability decomposition as the finding, and WITHDRAW
the value_loss comparison that was first offered as its support.
**Why:** no per-state value predictions are logged anywhere in this repo. What
does exist: TensorBoard scalars for Stage 3 and Stage 4 — **filed under
`training/checkpoints/stage2/tb/`, because `MaskablePPO.load()` restores
`tensorboard_log` from the checkpoint and Stages 3/4 resumed the Stage 2 lineage.**
Identified by matching each event file's timestamp to the Monitor `t_start`.
Stage 3 -> Stage 4 differ by ONLY `spawn_emergencies` (`training/curriculum.py`),
so this is a clean natural experiment:

  | | explained_variance | value_loss |
  |---|---|---|
  | Stage 3 (emergencies OFF) | 0.7864 | 127.9 |
  | Stage 4 (emergencies ON) | **0.7453** | **135.3** |

**The load-bearing finding — a law-of-total-variance decomposition of the
emergency component of the lambda-return,** `Var(C) = E_B[Var(C|B)] +
Var_B(E[C|B])`, where `B` is what the observation actually carries about the
ambulance (`('vis', k, corridor?)` while on a sensed lane, one single `('none',)`
bucket otherwise):

```
E[C] = 0.6352     Var(C) total = 7.5584
 'vis'  bucket: n=  2,567 ( 0.64%)  mean=10.2179  var=108.5088
 'none' bucket: n=397,433 (99.36%)  mean= 0.5733  var=  6.3094
 Var_B(E[C|B])                = 1.2895 = 17.1%   reducible by a perfect critic
 Var_B(E[C|B]) + exact clock  = 1.4344 = 19.0%   reducible, UPPER BOUND
 E_B[Var(C|B)]                = 6.1240 = 81.0%   IRREDUCIBLE
 check: 1.4344 + 6.1240 = 7.5584 vs 7.5584 -> OK
```

Stress-tested across 12 variants (RNG seed, `p(corridor)` 0.10-0.25, firing
counts, visibility windows, episode length, `lambda=1.0`): irreducible share
**75.3%-83.9%**. The clock variant is an upper bound only — **`sim_time` is NOT
an observation feature** (`env/obs_action_spec.py`).

**TWO CLAIMS FROM THIS PASS'S FIRST REPORT ARE WITHDRAWN:**
  1. The 17.1 / 19.0 / 81.0 percentages were **never derived from the
     explained_variance / value_loss deltas.** They come entirely from the model
     above plus the observation channel list. The first report placed the two
     adjacently and implied a derivation that does not exist. Presentation
     defect, not an arithmetic one — but it would have misled a reader about what
     the figure rests on.
  2. **"The observed +7.4 value_loss rise is attributable to the emergency term"
     is WITHDRAWN as non-informative.** Total modelled variance spans **6.6-20.0**
     over the same stress box, so +7.4 is merely inside the range. Worse: "critic
     learned the reducible 19%" predicts ~6.1 and "critic learned nothing"
     predicts ~7.5 — 1.4 apart against many units of model uncertainty. **The
     measurement cannot resolve what the critic learned.** The EV/value_loss
     delta is real and directionally consistent; it is not evidence.

**What stands:** an ambulance is visible in **0.68% of states**, and in the other
99.3% the observation contains *nothing* that predicts one. **More emergency data
cannot remove 81% of that variance.**
**Deviates from plan?** No.

### 4. ROOT CAUSE — observability, not sparsity. The reward is not purely reactive; the OBSERVATION is.

**Decision:** the primary mechanism is that no anticipatory state exists, and the
measured sparsity is a *symptom* of the same fact rather than an independent cause.
**Why / verified directly from code:**

  - `env/reward.py`'s emergency term fires on EVERY step an ambulance sits on a
    sensed lane that is not green — **not only at the override moment.** So the
    reward is not structurally reactive-at-arrival. That candidate explanation is
    disconfirmed on its own terms.
  - But the window it can fire in is fixed by route geometry
    (`sim/scenario_generator.py::ROUTES` x incoming-lanes-only sensing):

    ```
    r_ns1  N1_J1 J1_S1              sensed = ['N1_J1']                    <- ONE edge
    r_sn1  S1_J1 J1_N1              sensed = ['S1_J1']                    <- ONE edge
    ... all 6 cross routes identical in shape
    r_we   W1_J1 J1_J2 J2_J3 J3_E3  sensed = ['W1_J1','J1_J2','J2_J3']
    ```

    For the **6 cross routes the single sensed edge IS the target junction's own
    approach arm** — the ambulance's first appearance anywhere in the observation
    is already at the junction that must serve it. **There is no anticipatory
    state. Not a sparse one: none.** For corridor routes a junction still never
    sees the ambulance before its own approach, though the flattened `(3,191)`
    observation lets J2's head read J1's row (~2 steps of real lead).
  - **82.5% of Stage 4's training episodes were cross routes.** For those,
    "proactive" was not a behaviour the policy failed to learn — it was undefined.
  - The only ambulance channel in the observation is `LF_TYPE_START+4`, a per-lane
    count that is zero until the vehicle is physically on that junction's approach
    lane (`env/obs_action_spec.py`). There is no clock and no upstream lookahead.

**Deviates from plan?** No. CLAUDE.md's sparse-signal hypothesis is answered:
sparsity is real and now measured, but it is downstream of the sensing geometry,
not an independent cause — which is why up-weighting `w_emergency` or oversampling
emergencies would supply more of a signal the policy already responds to, at
states where the information it would need is absent.

### 5. "15/15 = 0% proactive emergency handling" — WITHDRAWN A SECOND TIME, on COVERAGE

**Decision:** the 15/15 figure is 3 distinct emergency draws, not 15 trials, and
its route mix over-samples the hardest case 3.8x. Replacement: **10/12 distinct
draws (~83%), or ~79% re-weighted to the training route mix.**
**Why:** `run_emergency_recheck` runs `ScenarioConfig(lane_counts=combo,
randomize_lane_counts=False, randomize_density=False, spawn_emergencies=True)`.
With both randomisation axes off, the emergency draw depends only on `seed`.
Reproduced offline (rng-pure, no SUMO):

```
 all 5 combos x seed  1 -> r_ew  @ 582.2s    BUILD_LOG 2026-08-15 recorded r_ew  @582.2   MATCH
 all 5 combos x seed  7 -> r_ns1 @ 980.0s    recorded r_ns1 @980.0                        MATCH
 all 5 combos x seed 42 -> r_we  @1642.8s    recorded r_we  @1642.8                       MATCH
 DISTINCT (route, depart) pairs across all 15 cells: 3
```

**2 of the 3 draws are corridor routes (67%) against Stage 4's training mix of
17.5% — a 3.8x over-sample of the long-exposure case,** which is precisely the
case where the shield is most certain to be needed.

Re-measured on `stage4_proposal.py`'s 12 DISTINCT draws (`STAGES[4]`,
`reset(seed=sd)`), same checkpoint, **metric matched exactly** (`ovrE >= 1`,
i.e. §10's `emergency_override` fired — the same event the 15/15 counted):

| seed | route | type | topology | ambJ | srv | blkA | blkU | ovrE | fired |
|---|---|---|---|---|---|---|---|---|---|
| 1 | r_we | corridor | (2,4,2) | 14 | 13 | 0 | 1 | 1 | YES |
| 2 | r_ns3 | cross | (2,2,2) | 2 | 1 | 0 | 1 | 1 | YES |
| 3 | r_sn3 | cross | (2,4,4) | 2 | 2 | 0 | 0 | 0 | **no** |
| 5 | r_ns1 | cross | (4,3,4) | 2 | 1 | 0 | 1 | 1 | YES |
| 7 | r_ew | corridor | (3,2,3) | 12 | 10 | 1 | 1 | 2 | YES |
| 10 | r_sn2 | cross | (4,2,3) | 2 | 0 | 2 | 0 | 2 | YES |
| 13 | r_sn1 | cross | (3,3,4) | 2 | 2 | 0 | 0 | 0 | **no** |
| 21 | r_we | corridor | (2,3,4) | 10 | 9 | 0 | 1 | 1 | YES |
| 42 | r_sn1 | cross | (4,2,2) | 2 | 0 | 2 | 0 | 2 | YES |
| 99 | r_sn1 | cross | (3,3,2) | 2 | 1 | 1 | 0 | 1 | YES |
| 123 | r_sn2 | cross | (2,3,2) | 2 | 1 | 1 | 0 | 1 | YES |
| 777 | r_ew | corridor | (2,3,3) | 12 | 9 | 2 | 1 | 3 | YES |

`ovrE>=1` -> **10/12**; `blocked>=1` -> **10/12**, agreeing on **all 12 seeds**
(zero disagreements), so the metric is genuinely matched and not a proxy. By
route: **corridor 4/4 fire, cross 6/8.** Re-weighted to the training mix:
`0.175x1.0 + 0.825x0.75 = 0.794` -> **~79%**. Wilson 95% CI on 10/12:
**[0.55, 0.95]**. This also EXPLAINS 15/15 rather than merely disputing it: two
corridor draws (4/4 firing) plus one cross draw that fired, each counted 5x.

**NAMED PARALLEL — this is the `j1=3` failure shape for the third time, and the
D1-"collapse" shape for the fourth.** Each was a matrix that *could not have
detected the alternative*, read as though it had: `--j1-recheck`'s four combos
contained no `j1=2` cell; `phase0_baselines`' seeds replayed training episodes;
D1's three checkpoints missed the peak region; and here, three emergency draws
were reported as fifteen, with the route mix skewed 3.8x toward the case that
fires most reliably. The cheap defence remains the one recorded on 2026-08-29:
**before writing a comparative claim, ask which regions the sampling would have
had to cover for the opposite conclusion to be visible.**

What the policy actually does, Stage 4 @153,600 on held-out-screened seeds:
**49/64 = 76.6%** of ambulance junction-steps served by its own proposal;
held-out proposal quality **0.8298 vs chance 0.6383, z=+3.388, p=0.0007**;
**16.9%** of blocked steps were `blocked_unavoidable` (the action mask left no
slot that serves it). §16's bar remains FAILED — the shield is still needed in
~8 of 10 episodes — but "0% proactive" was withdrawn on 2026-08-18 and this pass
confirms the withdrawal with a properly-powered matrix.
**Deviates from plan?** No. §16's Stage 4 FAILED status is unchanged.

### 6. VERDICT — no fix, no retrain. Recommendation accepted as scope for this pass.

**Decision:** attempt no remediation before the Sep 5 deadline.
**Why:** the only fix addressing the mechanism is an OBSERVATION-SPACE change (an
upstream-ambulance / ETA channel, or `sim_time`). That changes
`Box(-10,10,(3,191))`, which invalidates every checkpoint including the deployed
`psychoflow_stage4_153600_steps_final.zip` and forces a retrain of Stages 1-5.
With ~4 days left and D1 having demonstrated 39-percentage-point swings between
checkpoints 5,000 steps apart at fixed hyperparameters (2026-08-29 entry §7), a
retrain has no reliable landing point. Reward reweighting and emergency
oversampling — the two cheap fixes the sparse-signal hypothesis would have
implied — are specifically NOT recommended: §2 shows the term is already the
loudest in the function and §4 shows the missing ingredient is state, not signal.
**Deviates from plan?** No. No locked decision reopened; deployment unchanged.

### 7. Corrected fallback wording for §17 / §19 / §20

The wording carried into this session — *"the policy doesn't proactively handle
emergencies yet, but the safety validator catches every one by design, 15/15"* —
has two factually wrong components. "Doesn't proactively handle" is measurably
too harsh (76.6% self-served, p=0.0007 above chance) and "15/15" is 3 draws.
"Catches every one by design" is TRUE and structurally so — §10 sits inside
`env.step()` ahead of the only `setPhase` call. Replacement, judge-facing:

> "The learned policy serves an approaching ambulance on its own initiative on
> about three quarters of the decision steps where one is present — measurably
> better than chance (0.83 vs 0.64, p=0.0007, on held-out scenarios). It can't
> anticipate one: the sensors don't register an ambulance until it's already on
> the junction's own approach lane, so on most routes there is no earlier moment
> at which it could have acted. The safety validator sits underneath as a hard
> gate — it has to fire in roughly 8 of 10 episodes, and it catches every
> ambulance, by construction rather than by training."

**Verified (this entry):** Stage 4 Monitor CSVs (80 episodes, 64 distinct
scenarios, 14/66 route split, 0 truncations); 138 committed eval episode rows
across `phase0_baselines.json`, `phase0_emergency.json`, `stage4_proposal.json`,
`ga154_proposal.json`, `ga102_proposal.json`; TB scalars for Stage 3/4 read from
`stage2/tb` by timestamp match; offline scenario reproduction of the 15-cell
matrix against BUILD_LOG's three recorded (route, depart) values — **exact match
on all three**; law-of-total-variance decomposition with its own consistency
check and a 12-variant stress test; reward-mass identity closed against the
training log. `python -m env.reward` and `python -m safety.validator` NOT re-run —
no code was touched. **No SUMO process launched; the Tier 1 beacon was never
required because nothing in this pass calls `env.reset()`.**

**Next per §18 is still Phase 10 — Frontend.** Phases 1-9 remain complete.

## 2026-08-30 — Pre-event completions of Phase 8/9 modules (§17 / §12.2 / §13.2 / §13.1 / §15.2)

Five separable, individually-verified commits. **Not Phase 10/11/12 work** —
each finishes an already-built Phase 8/9 module against a phrase in the problem
statement it did not yet fully meet. Nothing in `env/`, `env/reward.py` or
`safety/validator.py` touched; no locked decision (CLAUDE.md §2) reopened. All
verification in the project venv (`sys.prefix` ends `...\GitHub\Test\venv`).

**Commit 1 — §17: lane closures are out of scope.**
**Decision:** the problem statement's parenthetical reads "signal timing changes,
lane closures, emergency corridors"; PsychoFlow's interventions are signal-phase
control (§9) + emergency-corridor clearance (§10/§11) only. Lane closures are a
system output it does NOT emit.
**Why:** same honesty discipline V2X / vision mock / Y-merge already get — named
once explicitly rather than silently implied. The system detects a blockage
(§7.3) and predicts its impact (§8.2), then re-times around it; commanding a
closure is a physical/authority action.
**Deviates from plan?** No — new §17 bullet, same section that already scopes out
the other modelled-not-built pieces. Code: `backend/control_api.py` docstring
(HONEST BOUNDARY note — no `close_lane` by design) and
`coordinator/responder_messaging.py` (sibling line where "not a real dispatch
system (§17)" already sits).
**Verified:** `python -m coordinator.responder_messaging` green; `control_api`
imports clean.

**Commit 2 — `explainability/narrator`: a public register.**
**Decision:** `narrate(entry, *, register="operator")`. `"operator"` (default) is
§12.2's control-room wording, unchanged. `"public"` renders the same decision in
plain language — drops the lane INDEX and every mechanism term ("phase", "slot",
"ceiling", "threshold", "override", raw lane id) for a public information
channel. `REGISTERS` / `REGISTER_OPERATOR` / `REGISTER_PUBLIC` exported; unknown
register -> `ValueError`.
**Why:** the problem statement asks for "operator/public-ready explanations" and
the module's own docstring said it produced operator-only language.
**Deviates from plan?** Additive to §12.2; the four operator templates are
untouched and stay the default, so `backend/sim_runner.py` and
`explainability/query_interface.py` (both call `narrate` positionally) are
unaffected.
**Verified:** `python -m explainability.narrator` — 6 reasons x 2 registers, the
operator fragments asserted unchanged, the public lines asserted jargon-free and
different from operator, unknown register raises. `python -m
explainability.query_interface` green. `python sim/run_explainability_episode.py`
— all checks passed.

**Commit 3 — §13.2 frame: `predictions` (§8.1 spillover + §8.2 incident impact).**
**Decision:** new ADDITIVE top-level key, same contract as `responder_messages` —
omitted unless material. `predictions.spillover` is §8.1's list shape filtered to
pairs whose forecast moves >= `_SPILLOVER_MIN_DELTA` (1.0) queued vehicles over
the 60s horizon; `predictions.incident_impact` is §8.2's shape, one per active
§7.3 incident. Reference: `backend/sim_runner.py::_predictions`.
**Why:** the forecasts existed (Phase 5) and fed the observation but never
reached the dashboard; §13.2 is where the frontend will read them.
**Deviates from plan?** No — master plan §13.2 updated in the same commit (frame
example + a `predictions` paragraph + explicit "frozen five-key core + two
additive keys" framing).
**Key implementation choice:** spillover is computed by a SECOND, read-side
`SpilloverPredictor` (`SimRunner._spillover_view`, `.reset()` in
`_reset_counters()`), NOT the env's. `SpilloverPredictor.forecast()` is stateful
(stores the previous snapshot for the rate calc); calling the env's from the
frame path would double-advance it and corrupt the next observation. Fed the same
post-step snapshots at the same 5s cadence, so it yields the same numbers obs
indices 10/11 carry. `env/` is frozen (task constraint) so stashing the env's
forecast was not an option anyway.
**Verified:** `sim/run_backend_smoke.py` 41/41 (was 37). New no-SUMO P1/P2/P3
(cold-start empty; a 2->12 queue over 5s yields a §8.1 spillover entry with
`predicted_queue_delta` 120.0; an active incident yields a §8.2 entry) and live
1g (109 frames carried a well-formed `predictions` object). The frame-keys check
was loosened from `== {5}` to `{5} <= keys <= {5} | {responder_messages,
predictions}`.

**Commit 4 — §13.1 `inject_incident` endpoint.**
**Decision:** `inject_incident(state, junction_id, affected_lanes, *,
incident_type="lane_blocked", severity="high", lane_id=None,
estimated_duration_s=600.0)` in `backend/control_api.py`; `POST
/control/inject_incident` in `backend/main.py`;
`_apply_command`'s `inject_incident` branch calls
`env.twin.incidents.report(...)` between decision steps.
**Why:** without it `digital_twin.active_incidents` is always empty in a live run
and "detects incidents" has no trigger. `perception/incident_intake.py`'s own
docstring already anticipated "backend control API ... later".
**Deviates from plan?** Additive §13.1 endpoint (7th), table row added to the
master plan. Validates junction against `_CORRIDOR_JUNCTIONS` and
type/severity against `INCIDENT_TYPES`/`SEVERITIES` imported from
`perception.incident_intake` (pure, no SUMO import — keeps `control_api`
voice-context-importable). `report()` is a registry write on the sim thread, not
a TraCI call, so §13's single-thread boundary is intact.
**§17:** this REPORTS a blockage; it does not command a closure. There is no
`close_lane`.
**Verified:** `sim/run_backend_smoke.py` 45/45 (was 41). New check 8: an
unknown `junction_id` is rejected; an accepted injection rides
`digital_twin.active_incidents` (`inc_0001` at J1, lane on the affected list) and
`predictions.incident_impact` (`estimated_delay_increase_s` 52.5, affected
`[J1,J2,J3]`) within ~200 frames.

**Commit 5 — §15.2 `emergency_clearance_time_s` definition.**
**Decision:** point the metric at `EmergencyClearanceEvent.clearance_time_s`
(`coordinator/emergency_clearance.py`) — first detection -> green onset at the
junction the §10 override fired at, 1s resolution, 0.0 floor with
`served_on_arrival`. The §15.2 row was "BLOCKED — see §11.2. Do not populate
from the Stage 4 harness."
**Why:** Phase 8 (commit `9cf19af`) built exactly the per-junction fix §11.2's
blocker demanded; §15.2 had not been updated and openly contradicted the shipped
§13.2 `responder_messages`.
**Deviates from plan?** Definition-level only — no eval harness built (that
aggregation is Phase 12). §11.2's PHASE 8 BLOCKER text keeps a RESOLVED pointer
at its head; the Stage 4 harness prohibition stands.
**Verified:** `python -m coordinator.emergency_clearance` green (docstring-only
code change — added the §15.2 / §11.2 cross-ref to the `clearance_time_s`
property).

**Next per §18 is still Phase 10 — Frontend.** Phases 1-9 remain complete.

## 2026-08-30 — §13.2 `_SPILLOVER_MIN_DELTA` provenance clarification (verification pass)

**Decision:** Recorded plainly, in `backend/sim_runner.py` and here, that the
`_SPILLOVER_MIN_DELTA = 1.0` veh materiality threshold on the §13.2
`predictions.spillover` stream is a **chosen default for the 2026-08-30 streaming
commit, not a §8.1 spec value.** §8.1 defines only the forecast heuristic and no
streaming/materiality threshold; master plan §13.2 names the constant but did not
characterise it either way.
**Why:** a verification pass asked whether the constant reads as a spec number.
It does not gate anything load-bearing — obs indices 10/11 carry the full
unfiltered forecast from the env's own predictor; the constant only decides
whether a near-zero-delta spillover pair rides the WebSocket frame. Leaving its
origin implicit risked a future reader treating 1.0 as spec-mandated.
**Deviates from plan?** No — comment/log clarification only, no behaviour change.
**Verified:**
- Read side tracks state correctly. `SimRunner._spillover_view` (the read-side
  `SpilloverPredictor`) is `.forecast(post-step snapshot)`-ed **every decision
  step** via `_assemble_frame -> _predictions`, and `.reset()`-ed every episode in
  `_reset_counters()` — same cadence and reset discipline as the env's stateful
  predictor feeding obs 10/11. `forecast()` unconditionally rebinds `self._prev`,
  so the rate calc always sees dt = one 5s interval. No cold/unstepped instance.
- `python sim/run_backend_smoke.py` → **45/45 pass** (project venv, deployed
  Stage 4 checkpoint). Check 1g: 104 live frames carried a well-formed
  `predictions` object this run (was 109 when the commit-3 entry was written —
  the count is scenario/timing-dependent, not fixed).
- Spillover IS being populated live, not empty. A 400-frame clean window (no
  incident injected) split: **328 `spillover`-only frames, 0 `incident_impact`,
  0 both, 72 with the key omitted** (near-zero delta). Sample live deltas swing
  +48 / +12 / −48 / −12 / +24 veh on the J1→J2 link — genuine queue growth/drain,
  not a stuck value. The commit-3 "109 frames" were therefore all non-empty
  `spillover`; `incident_impact` only appears after `inject_incident`
  (smoke check 8), which runs after the 1g window.

**Next per §18 is still Phase 10 — Frontend.** Phases 1-9 remain complete.
