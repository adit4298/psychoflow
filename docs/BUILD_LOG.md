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
