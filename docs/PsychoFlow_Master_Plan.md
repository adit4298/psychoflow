# PsychoFlow — Master Project Plan (Full Build Specification)
### An Agentic, Multi-Modal Traffic Flow Optimization & Road-Safety Response System
**Status:** Repo is currently empty. This is the complete, from-zero build spec and the single source of truth for the project — everything Claude Code needs is in this one file. Paste this into `docs/PsychoFlow_Master_Plan.md` before starting Claude Code.
**How to use this with Claude Code:** every section below is written to be handed to Claude Code section-by-section, in the order given in §18 (Build Sequence). Don't paste the whole file and say "build this" — work through §18's phases; each phase prompt references the section number it needs.

---

## 0. Key Scoping Decisions (read this first)

These are the calls that shape everything downstream — stated once here, applied consistently in every section that follows.

| Decision point | Locked decision | Why |
|---|---|---|
| V2X (connected-vehicle data) | Build V2X-*shaped* data only (§7.5) — reformatted TraCI vehicle state with a synthetic noise model. No real network-layer V2X simulator (e.g. Veins/OMNeT++). | Integrating two independently-versioned simulators is a toolchain/binary-compatibility risk, not a logic problem — and the result is visually indistinguishable from the lightweight version on a judge's screen. All risk, no visible payoff. |
| Prediction/forecasting | In scope, required (§8) — spillover forecasting + incident impact prediction. | Directly required by the "predicts spillover impact" bullet in the problem statement (§1). |
| Incident intake / weather / vision | All in scope as perception modules (§7). Vision is a simulated mock (§7.2), not a real detection model. | Required by the "multi-modal signals" bullet. A real vision model adds real engineering risk (data, inference latency, integration) for zero visible demo difference versus a mock emitting the same output shape. |
| Voice layer | Required (Tier 3, §14), implemented entirely against a local model — Web Speech API + Ollama/Gemma. No Claude API calls anywhere in the runtime path. | Hard budget constraint — no further spend beyond the existing Claude Pro subscription, which covers coding assistance only, not runtime inference calls. |
| Y-merge topology | Downgraded to true stretch, last-resourced (§3). | Doesn't map to any bullet in the problem statement — pure generalization flex the grading rubric doesn't ask for. |
| MARL coordination | Required. Graph-attention primary, shared-policy PPO fallback, config-flag swappable (§9.5). | This is the actual mechanism that makes "multi-agent" true rather than three independent single-agent controllers. The one place genuine engineering uncertainty is worth carrying. |
| Tech stack | SUMO/TraCI, Stable-Baselines3 PPO, FastAPI, React — all free/local (§5). | No paid infrastructure needed anywhere in the build. |

---

## 0.1 Corridor Topology, Cost Boundary, and Task Split (read second — resolves the remaining open gaps)

**Corridor size — finalized.** Lane count per junction (2/3/4 per approach) and corridor size (how many junctions are connected) are two different things — the corridor size was never pinned down until now. "Corridor-wide coordination" (§9.5) and spillover forecasting (§8.1) are meaningless with a single junction — they need real neighbors. **Locked: a 3-junction linear corridor, `J1 → J2 → J3`.** Each junction independently configurable to 2/3/4 lanes per approach — build this as one parametrized network generator (§6), not four static files, so lane count is genuinely a runtime choice per junction rather than a fixed set. Three junctions is the minimum that makes "multi-agent, neighbor-aware" literally true without inflating build/train time.

**Cost boundary — confirmed zero-spend.** Every tool in this stack (SUMO, PyTorch/SB3, FastAPI, React, Ollama, Gemma, browser Web Speech API) is free and local. The only real cost anywhere is your own machine's CPU time for RL training — wall-clock, not money. If local training is too slow, the one legitimate free overflow is Google Colab's free tier for training runs specifically (optional, not required — nothing else in the stack needs it).

**Starvation threshold (90s) and episode length (3600s) — confirmed as starting defaults**, meant to be tuned after Checkpoint 1 (§16) as the agent trains, not fixed values. No change made.

**MARL approach — confirmed.** Graph-attention primary, shared-policy PPO fallback, config-flag swappable (§9.5). This is the one place genuine engineering uncertainty remains, and it's intentional — it's the actual mechanism that makes "multi-agent" true. Everything else in this build is deterministic engineering work.

**Task split — what Claude Code builds vs. what stays manual:**

| Claude Code builds, end to end | Stays with you |
|---|---|
| Corridor network generator, all perception modules, digital twin, prediction, env/reward, Tier 0 controller, safety validator, both MARL extractors + config flag, training scripts, backend, frontend, voice intent agent, decision log/narration, evaluation suite. Can also run install/setup commands via terminal (SUMO, Ollama, `pip install`, `ollama pull gemma3`) if given shell access. | Reading a reward curve at a checkpoint and deciding to flip the MARL config flag (§9.5); physical/sensory checks (mic in a noisy room, screen legibility on the actual presentation setup); any OS-level installer GUI click-through; scope-cut calls if time runs short (§18 gives the priority order, but stopping early is your call); hackathon logistics — submission, organizer compliance, rehearsal. |

---

## 0.2 Model & Effort Allocation — Claude Pro Budget Plan (Aug 11 → Sep 5 hackathon deadline)

You have one Claude Pro seat, subscription runs to Sep 11, but the real deadline is the **hackathon end on Sep 5** — roughly 3.5 working weeks from tonight. Pro has a weekly usage cap, not a monthly one, so the goal isn't "spend less overall," it's **don't blow one week's cap on work that didn't need the expensive model.** The rule throughout: use Opus 4.8 only where a design mistake would be expensive to discover later (mid-training, mid-demo); use Sonnet 5 for everything with a precise spec and low ambiguity.

**Weekly build phases → model → effort**

| Week | Dates | §18 phases | Model | Effort | Why |
|---|---|---|---|---|---|
| 1 | Aug 11–17 | 1 (corridor generator), 2 (perception), 3 (env + reward), 4 (Tier 0 + safety validator) | **Opus 5** for phases 2–4; Sonnet 5 for phase 1 | High/extended thinking on phases 2–4 | Corridor generator (phase 1) is boilerplate SUMO XML — Sonnet is fine. Reward function and safety validator (§9.4, §10) are the two places a subtle bug becomes invisible until hours into training — worth Opus's slower, more careful reasoning here, and cheap relative to the training time it saves. |
| 2 | Aug 18–24 | 5 (prediction), 6 (PPO training Stages 1–4), 7 (MARL, both extractors + config flag) | **Sonnet 5** for phase 5 and routine training-script work; **Opus 5** for phase 7 (both MARL extractors) and any Stage 1–2 checkpoint failure | High thinking only for MARL extractor design and checkpoint debugging | This week is compute-heavy, not chat-heavy — most of your week is your CPU training in the background, not you spending credits. Reserve Opus specifically for the graph-attention/shared-policy extractor pair (§9.5), since a wrong design there is the single most expensive mistake to catch late. |
| 3 | Aug 25–31 | 5 continued (MARL Stage 5 checkpoint decision), 8 (coordinator + explainability), 9 (backend), 10 (frontend) | **Sonnet 5** for 8–10; **Opus 5** only for the MARL checkpoint call itself (deciding attention vs. shared-policy fallback, §9.5/§16) | High thinking only for the checkpoint decision; default elsewhere | Coordinator, backend, and frontend are all precisely spec'd in §11–13 — high-volume, low-ambiguity work, exactly where Sonnet is both cheaper and fast enough to matter. |
| 4 | Sep 1–5 | 11 (voice), 12 (evaluation suite), rehearsal, bug fixes only | **Sonnet 5** for everything, no new Opus work planned | Default effort | No new architecture decisions should be happening this late — if one comes up, that's a sign scope crept past what §18 intended. Keep this week's usage light on purpose; you want headroom in the weekly cap for last-minute fixes, not none left when something breaks two days before the deadline. |

**Credit conservation rules, in order of importance:**
1. **Never use Opus for anything in §6 (folder structure) that's pure boilerplate** — XML generation, React components, FastAPI route stubs, JSON schema implementations. These are Sonnet's whole use case.
2. **Front-load Opus usage into Weeks 1 and 2.** The foundation (env, reward, safety validator, MARL extractors) is where mistakes compound; everything built in Weeks 3–4 sits on top of it. If Week 1–2 foundations are solid, Weeks 3–4 genuinely don't need Opus much at all.
3. **Keep a deliberate reserve, don't plan to use the full weekly cap every week.** Training runs fail in ways that aren't predictable from this plan (a flat reward curve, a masking bug that only shows up at Stage 3) — that debugging is exactly when you want Opus available and haven't already spent the week's allowance on routine frontend work.
4. **When a training checkpoint (§16) fails, diagnose with Opus, fix with Sonnet.** Use Opus to reason about *why* the reward curve is flat or the MARL policy isn't converging (paste the curve/logs, ask for the likely cause) — that's a small, high-value exchange. Once you know the fix, hand the actual code change to Sonnet.
5. **If you're ever unsure which model a task needs, default to Sonnet and escalate only if it visibly struggles** — that's cheaper than guessing Opus-first on something that turns out to be routine.

---

## 0.3 Done-Bar Integrity Principle

Any phase "done bar" or §16 checkpoint metric must be checked for whether it can be satisfied trivially or structurally, regardless of actual system/policy quality — for instance, an outcome that a downstream safety or validation mechanism already guarantees by construction, independent of what the layer under test actually does. A metric that would pass for any implementation, including a deliberately bad one, is not measuring what it claims to measure. If a done-bar check turns out to have this shape, redefine the metric to isolate the underlying behavior it was meant to verify — before building or training against it, not after.[^1]

[^1]: Motivating example: §16's Stage 4 checkpoint bar was originally read as "near-100% emergency priority in test episodes," measured as the served-ambulance rate. But §10's safety validator makes an unserved ambulance impossible by construction — the emergency override always fires if the policy doesn't proactively serve it — so this metric would read 100% for any policy whatsoever, including a random one. It measured the validator, not the policy. The corrected metric — validator override-firing rate, i.e. how often the safety gate had to intervene rather than the policy handling it proactively — showed the real result: 15/15 overrides fired, meaning 0% proactive emergency handling, which the naive metric would have reported as a full pass. See `docs/BUILD_LOG.md`'s 2026-08-15 §18 Phase 6 Stage 4 entry.

---

## 1. Governing Problem Statement

> **Autonomous Traffic Flow Optimization & Road-Safety Response Agents (V2X + Multi-Modal Signals)**
> Urban corridors suffer from congestion, slow emergency response, and inconsistent road-safety enforcement because traffic operations rely on siloed signals (CCTV, signal timing plans, incident hotlines, weather feeds) and manual interventions — leading to delayed clearance, secondary accidents, and avoidable emissions. What's missing is an agentic traffic-control system that interprets multi-modal inputs, generates safe intervention plans (signal timing changes, lane closures, emergency corridors), coordinates with responders, and produces explainable "why this action" narratives for operators and public communication.
>
> **Challenge:** Design a multi-agent AI system that:
> - Detects incidents and predicts spillover impact using multi-modal signals
> - Proposes and validates interventions under safety and policy constraints
> - Coordinates responder actions and generates operator/public-ready explanations
>
> **Impact:** Reduced congestion and clearance times, fewer secondary incidents, improved emergency response, and lower emissions.

**Mapping — every module traces to one of these three bullets. If it doesn't, it's not in scope:**

| Bullet | Modules |
|---|---|
| Detect incidents + predict spillover, multi-modal | §7 Perception + §8 Prediction |
| Propose/validate interventions under constraints | §9 Core Intervention + §10 Safety & Policy Validator |
| Coordinate responders + explain decisions | §11 Coordinator + §12 Explainability/Narrator |
| Impact claims | §15 Evaluation & Comparison |

---

## 2. Locked Scope & Tiers

| Tier | What it is | Priority |
|---|---|---|
| **Tier 0** | Rule-based fairness-first controller live in SUMO, full dashboard, ambulance override | **Build first. Guaranteed floor — a complete, demoable project on its own.** |
| **Tier 1** | PPO agent beating Tier 0, across 2/3/4-lane four-way intersections, full pipeline wired (Perception→Prediction→Intervention→Validator→Coordinator→Explainability) | **Primary deliverable.** |
| **Tier 1.5** | Graph-attention MARL corridor coordination + shared-policy PPO fallback, config-flag swappable | **Required attempt — build both paths from day one, not sequentially.** |
| **Tier 2** | Y-merge topology generalization | **True stretch. Do not schedule time against this until Tiers 0/1/1.5/3 are demo-ready and rehearsed.** |
| **Tier 3** | Voice layer: officer speaks → local STT → local LLM intent → control API | **Required, local-model-only implementation.** |

---

## 3. Why Y-Merge Is Deprioritized (stated once, not revisited)

The problem statement asks for incident detection, safe intervention under constraints, and responder coordination/explanation — it never asks for topology generalization. Y-merge was relevant to the original self-directed pitch, not to this grading rubric. Time spent there buys nothing against any of the three challenge bullets. Build it only if Tiers 0, 1, 1.5, and 3 are fully working and rehearsed with real time left over.

---

## 4. System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         BROWSER (React Frontend)                          │
│  Live Intersection View │ Metrics/Graphs │ Decision Log │ Voice Control    │
└──────────────────────────────────┬────────────────────────────────────────┘
                                    │ WebSocket (live state stream, §13.2)
┌──────────────────────────────────▼────────────────────────────────────────┐
│                          FastAPI Backend (Python)                         │
│                                                                            │
│  PERCEPTION LAYER (§7)                                                    │
│   per-lane sensing │ vision(mock) │ incident intake │ weather │ V2X-shaped │
│                              │                                            │
│                              ▼                                            │
│                     DIGITAL TWIN (§7.6, shared live corridor state)       │
│                              │                                            │
│              ┌───────────────┴────────────────┐                          │
│              ▼                                 ▼                          │
│        PREDICTION (§8)                CORE INTERVENTION (§9)              │
│   spillover forecast          fairness score → learned deviation          │
│   incident impact             (PPO + graph-attention MARL,                │
│                                fallback: shared-policy PPO)                │
│              │                                 │                          │
│              └───────────────┬─────────────────┘                          │
│                               ▼                                           │
│               SAFETY & POLICY VALIDATOR (§10)                             │
│         starvation ceiling │ emergency override │ pre-execution check      │
│                               │                                           │
│              ┌────────────────┴───────────────┐                          │
│              ▼                                 ▼                          │
│         COORDINATOR (§11)              EXPLAINABILITY / NARRATOR (§12)    │
│   emergency clearance          technical log │ plain-language │ Q&A        │
│   responder messaging                                                     │
│                               │                                           │
│                    VOICE INTENT HANDLER (§14)                             │
│              Web Speech API → local Gemma (Ollama) → control API          │
└────────────────────────────────────────────────────────────────────────────┘
                                    ▲
                          (trained offline, background)
┌──────────────────────────────────────────────────────────────────────────┐
│    Training Pipeline (§16): Scenario Randomizer → SUMO → Gymnasium Env    │
│    → PPO(+graph-attention) Agent → Reward → Policy Update → repeat        │
└────────────────────────────────────────────────────────────────────────────┘
```

**Plain-English flow:** training runs offline, produces a checkpoint. The backend loads it and drives a live SUMO instance via TraCI, streaming state to the browser over WebSocket. Voice is a separate input channel into the same control API the dashboard buttons use.

---

## 5. Tech Stack

- **Simulation:** SUMO + TraCI
- **RL framework:** Stable-Baselines3 (PPO), `sb3-contrib` for `MaskablePPO`
- **Environment:** Gymnasium, padded obs/action space (`MAX_LANES = 4`), masking
- **Backend:** FastAPI + WebSockets, Python 3.10/3.11
- **Frontend:** React, Node.js LTS
- **Voice:** Web Speech API (browser STT, free) + Ollama + Gemma (local intent parsing) — **no Claude API anywhere in the runtime path**
- **ML for prediction:** lightweight — heuristic or small model (e.g. scikit-learn / a small PyTorch MLP), not a new framework

### 5.1 Install checklist (run once, verify each)
| Tool | Verify command | Notes |
|---|---|---|
| Git | `git --version` | |
| Python 3.11 | `python --version` | 3.10/3.11 only — SB3 and SUMO bindings are least reliable on newer versions |
| SUMO | `sumo --version` | Set `SUMO_HOME` env var — critical, TraCI won't import without it |
| Node.js LTS | `node --version`, `npm --version` | |
| Claude Code CLI | `claude` opens a session | |
| Ollama | `ollama --version` | Then `ollama pull gemma3` (or latest tag) |
| Python packages | — | `pip install stable-baselines3[extra] sb3-contrib gymnasium sumolib traci numpy matplotlib fastapi uvicorn websockets pydantic ollama` |

**Compute note:** all training runs locally on CPU (SUMO is CPU-bound, not GPU-bound) — no cost beyond wall-clock time. If your machine is too slow to get through the curriculum (§16) in the time you have, Google Colab's free tier is an acceptable overflow for training runs specifically — nothing else in this stack needs it, and it doesn't change any other part of the plan.

---

## 6. Repo / Folder Structure (complete — build exactly this)

```
psychoflow/
├── docs/
│   └── PsychoFlow_Master_Plan.md        # this file
├── sim/
│   ├── networks/
│   │   ├── generate_corridor.py           # parametrized: builds J1→J2→J3 linear corridor, lane count 2/3/4 per junction, independently settable (§0.1)
│   │   ├── generated/                     # .net.xml output lands here, not hand-authored
│   │   └── ymerge.net.xml                 # deprioritized, build last if at all
│   ├── routes/                            # generated per-scenario route files
│   └── scenario_generator.py              # NumPy-based randomized scenario creation
├── perception/
│   ├── lane_sensor.py                     # per-lane count/type/wait/starvation (§7.1)
│   ├── vision_mock.py                     # simulated CCTV → count/type output (§7.2)
│   ├── incident_intake.py                 # structured incident event schema (§7.3)
│   ├── weather.py                         # weather state + SUMO behavior hooks (§7.4)
│   └── v2x.py                             # TraCI state → V2X-shaped noisy messages (§7.5)
├── twin/
│   └── digital_twin.py                    # unified corridor state model (§7.6)
├── prediction/
│   ├── spillover.py                       # queue propagation forecast (§8.1)
│   └── incident_impact.py                 # disruption ripple estimate (§8.2)
├── env/
│   ├── psychoflow_env.py                  # Gymnasium wrapper around SUMO/TraCI
│   ├── reward.py                          # reward function (§9.4)
│   └── obs_action_spec.py                 # padding + masking (§9.2)
├── agents/
│   ├── rule_based.py                      # Tier 0 fairness-first + Greedy baseline (§9.1, §15.1)
│   ├── policy_extractor_attention.py      # graph-attention feature extractor (§10.3)
│   ├── policy_extractor_shared.py         # shared-policy fallback extractor (§10.3)
│   └── config.py                          # single flag switching between the two above
├── safety/
│   └── validator.py                       # starvation ceiling, emergency override, pre-exec check (§10)
├── coordinator/
│   ├── emergency_clearance.py             # vehicle-clearing behavior (§11.1)
│   └── responder_messaging.py             # structured message generator (§11.2)
├── explainability/
│   ├── decision_log.py                    # technical log (§12.1)
│   ├── narrator.py                        # plain-language templates (§12.2)
│   └── query_interface.py                 # "why did you do that" handler (§12.3)
├── training/
│   ├── train.py                           # PPO entry point
│   ├── curriculum.py                      # stage definitions (§16)
│   └── checkpoints/                       # saved models land here
├── backend/
│   ├── main.py                            # FastAPI app + WebSocket server
│   ├── sim_runner.py                      # live SUMO instance driven by trained agent
│   ├── control_api.py                     # set_mode/set_lane_bias/get_stats/trigger_emergency/set_topology (§13.1)
│   └── voice/
│       ├── stt.py                         # Web Speech API bridge
│       └── intent_agent.py                # Ollama/Gemma function-calling (§14)
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── IntersectionView.jsx
│       │   ├── MetricsPanel.jsx
│       │   ├── DecisionLog.jsx
│       │   └── VoiceControl.jsx
│       └── App.jsx
├── evaluation/
│   ├── metrics.py                         # wait-balance, starvation count, throughput, clearance time (§15.2)
│   ├── emissions.py                       # emissions estimate from sim physics (§15.3)
│   └── generalization_test.py             # held-out scenario evaluation (§15.4)
└── README.md
```

---

## 7. Perception Layer — Full Data Contracts

### 7.1 Per-lane traffic sensing
Read continuously via TraCI. Output schema (per lane, per simulation step):
```json
{
  "lane_id": "north_approach_0",
  "vehicle_count": 7,
  "halted_count": 4,
  "type_composition": {"bike": 2, "auto": 1, "car": 3, "truck": 1, "ambulance": 0},
  "wait_time_current": 42.5,
  "wait_time_max_single_vehicle": 61.0,
  "starvation_flag": false
}
```
- `vehicle_count` = TraCI `lane.getLastStepVehicleNumber`
- `halted_count` = TraCI `lane.getLastStepHaltingNumber` (this is what reward/scoring use, not raw count)
- `wait_time_current` = TraCI `lane.getWaitingTime`
- `starvation_flag` = true when any individual vehicle's accumulated wait exceeds the threshold (start 90s, tune at Checkpoint 1)

### 7.2 Vision input — simulated mock (confirmed approach, §0)
`vision_mock.py` does **not** run a real detection model. It reads the same TraCI ground truth as §7.1 and re-emits it through a "vision pipeline" shaped interface — same output schema as §7.1, plus an optional `confidence` field (e.g. 0.85-0.98, randomized) to simulate the fact real vision has detection uncertainty. This keeps the core system genuinely agnostic to input source (§4), since it only ever consumes this shape, while avoiding the toolchain/latency risk of a real detector for zero visible demo difference.
```json
{ "lane_id": "north_approach_0", "vehicle_count": 7, "type_composition": {...}, "confidence": 0.91, "source": "vision_mock" }
```

### 7.3 Incident intake
```json
{
  "incident_id": "inc_0007",
  "type": "lane_blocked",
  "location": {"junction_id": "J2", "lane_id": "east_approach_1"},
  "severity": "high",
  "affected_lanes": ["east_approach_1", "east_approach_2"],
  "reported_at_sim_time": 1840,
  "estimated_duration_s": 600
}
```
`type` enum: `lane_blocked`, `accident`, `roadworks`. Same shape a human hotline operator would log — structured, not free text.

### 7.4 Weather awareness
```json
{ "state": "heavy_rain", "changed_at_sim_time": 900 }
```
`state` enum: `clear`, `rain`, `heavy_rain`. On change, apply SUMO `vType` parameter adjustments (increase `tau` follow-time gap, reduce `maxSpeed`, increase `sigma` driver imperfection) so behavior genuinely shifts, not just a label.

### 7.5 V2X-style connected-vehicle data (confirmed lightweight approach, §0)
Read TraCI vehicle state, reformat, apply noise. ~30-line module, not a new toolchain, no Veins/OMNeT++.
```json
{
  "vehicle_id": "veh_1123",
  "position": {"x": 412.3, "y": 88.1},
  "speed": 11.4,
  "heading": 87.2,
  "timestamp": 1840.3,
  "delay_ms": 120,
  "dropped": false
}
```
Noise model: `delay_ms` randomized (e.g. 0-300ms), `dropped` true ~2-5% of messages (drop the message entirely, don't emit malformed data), `position` jittered by a small random offset (e.g. ±0.5-1.5m).

### 7.6 Digital Twin
One object updated every simulation step, merging all of §7.1-7.5 into a single corridor-wide state every downstream module reads from — no module queries SUMO/TraCI directly except the perception modules themselves. This is what guarantees every decision is made against the same reality. Corridor is the 3-junction linear layout locked in §0.1.
```json
{
  "sim_time": 1840,
  "corridor_adjacency": [["J1", "J2"], ["J2", "J3"]],
  "junctions": {
    "J1": {"lanes": {...per §7.1 shape...}, "current_phase": 2, "lane_count": 4},
    "J2": {"lanes": {...}, "current_phase": 0, "lane_count": 3},
    "J3": {"lanes": {...}, "current_phase": 1, "lane_count": 2}
  },
  "active_incidents": [ ...§7.3 objects... ],
  "weather": { ...§7.4 object... },
  "v2x_messages_recent": [ ...§7.5 objects, last N... ]
}
```
`lane_count` per junction is independently set at scenario-generation time (§6's `generate_corridor.py`) — this is what "customizable 2/3/4 lanes" means concretely: each of J1/J2/J3 can be a different lane count in the same corridor, not just the corridor as a whole.

---

## 8. Prediction

### 8.1 Spillover forecasting (implemented, Phase 5 — `prediction/spillover.py`)
Input: digital twin state only + `corridor_adjacency` (§7.6: `J1→J2`, `J2→J3`) — `PsychoFlowEnv._spillover()` calls `predictor.forecast(self._snapshot)`, no runtime/route data. Output: per downstream-junction, a predicted queue-length delta over the next N seconds (N=60s, `DEFAULT_HORIZON_S`).
```json
{ "from_junction": "J1", "to_junction": "J2", "horizon_s": 60, "predicted_queue_delta": 4.2, "confidence": 0.78 }
{ "from_junction": "J2", "to_junction": "J3", "horizon_s": 60, "predicted_queue_delta": 1.9, "confidence": 0.81 }
```
**Implemented heuristic:** net growth rate of the downstream junction's own corridor-facing queue (`halted_count` summed over lanes tagged `approach == "west"` — on this locked linear W→E corridor, always the lane group fed by the upstream neighbor, hardcoded as `LINK_APPROACH = "west"`), extrapolated forward over the horizon:
```
queue_now  = sum(halted_count for lanes at downstream junction where approach == "west")
rate       = (queue_now - queue_prev) / dt        # dt = sim_time - prev_sim_time
predicted_queue_delta = rate * horizon_s
```
This proxies "current outflow rate from J1" as the connecting lane group's own net growth rather than an unmeasurable per-vehicle turn-routed outflow (no route/destination data exists in the twin snapshot to isolate "vehicles at J1 headed for J2"). Net growth already nets J1's discharge against J2's own service of it.

Confidence is a fixed baseline, not a learned uncertainty estimate:
```
confidence = 0.5                          # cold start (no previous snapshot): delta forced to 0.0
confidence = 0.85                         # otherwise (CONFIDENCE_BASE)
confidence = max(0.5, confidence - 0.2)   # if an incident is active at the downstream junction
```
The predictor is stateful (keeps the previous snapshot to compute the rate) and is reset every episode by `PsychoFlowEnv.reset()`.

**Obs-slot resolution:** each junction's §9.2 spillover slot (indices 10/11) reports the forecast where THAT junction is `to_junction` — i.e. "spillover about to arrive here from upstream." J1 has no upstream neighbor on this corridor and is never a `to_junction`, so its slot is always `(0.0, 0.0)` by omission. J2's own downstream impact on J3 is reported on J3's row, not duplicated on J2's.

### 8.2 Incident impact prediction (implemented, Phase 5 — `prediction/incident_impact.py`)
Input: an incident event (§7.3) + digital twin state. Output: estimated ripple.
```json
{ "incident_id": "inc_0007", "estimated_affected_junctions": ["J2", "J3"], "estimated_delay_increase_s": 85, "horizon_s": 300 }
```
Feeds the intervention layer a head start on compensating before downstream congestion is actually observed.

**Implemented formula:** `estimated_affected_junctions` is the incident's own junction (hop 0) plus every junction reachable downstream via `corridor_adjacency` (a forward walk — congestion propagates downstream on this linear corridor). `estimated_delay_increase_s` is the SUM (not max — max would always equal the hop-0 term regardless of ripple distance, making the decay term inert) of each affected junction's hop-decayed contribution:
```
contribution(hop) = BASE_DELAY_S(30.0) * SEVERITY_VALUE[severity] * len(affected_lanes) * DECAY_PER_HOP(0.5) ** hop
estimated_delay_increase_s = sum(contribution(hop) for each affected junction, hop = 0, 1, 2, ...)
horizon_s = min(MAX_HORIZON_S(300.0), incident["estimated_duration_s"])
```
`SEVERITY_VALUE = {"low": 0.33, "medium": 0.67, "high": 1.0}` (`perception/incident_intake.py`, §7.3's canonical home — also used by §9.2's `JS_INCIDENT_SEVERITY` feature).

Worked example (incident at J1, severity=high, 2 affected lanes):
```
J1 (hop 0): 30.0 * 1.0 * 2 * 0.5^0 = 60.0
J2 (hop 1): 30.0 * 1.0 * 2 * 0.5^1 = 30.0
J3 (hop 2): 30.0 * 1.0 * 2 * 0.5^2 = 15.0
sum = 105.0s
```
Reproduced exactly by `python -m prediction.incident_impact`.

---

## 9. Core Intervention

### 9.1 Fairness-first priority scoring (seed formula — Tier 0 baseline)
```
score(lane) = 0.6 * halted_count + 0.4 * wait_time_current + starvation_bonus(lane)
starvation_bonus(lane) grows non-linearly as wait_time_max_single_vehicle approaches the starvation threshold
```
This is Tier 0's entire decision rule, and it is also the seed the RL agent starts from and learns to deviate from — not replaced by RL, extended by it.

### 9.2 Observation / action space
- **Observation:** padded to `MAX_LANES = 4` per approach; zero-fill + mask extra slots for smaller junctions. Includes: per-lane data (§7.1), topology-type one-hot flag, active weather state, active incident flags, spillover prediction (§8.1) for immediate downstream junction.
- **Action:** padded to `MAX_PHASES` (largest valid phase count across all topologies). `MaskablePPO` masks out phases invalid for the current topology/lane-count — the agent physically cannot select an invalid or unsafe phase combination.
- **Episode:** 3600 simulated seconds or vehicles-cleared target met, whichever first. `env.reset()` draws a fresh randomized topology/lane-count/density scenario.

### 9.3 Learned adaptive behavior
PPO learns when to deviate from §9.1's formula: faster rotation in rush-hour density so no lane crosses the starvation line; longer greens off-peak; sustained (not just momentary) extra frequency for a permanently busier approach without ever fully starving the quiet one; absorb sudden surges without abandoning every other lane.

### 9.4 Reward function
```
reward = -penalty_starvation(lane_wait_times)      # non-linear: 2x wait ≠ 2x penalty, much worse
         + bonus_throughput(vehicles_cleared_this_step)
         - large_penalty_if(emergency_vehicle_not_prioritized)
         - small_penalty_if(phase_switched_this_step)   # discourages flickery signals
```
Print reward for hand-built test scenarios before training — an intentionally-starved lane must produce a clearly worse reward than a balanced one, or the formula is wrong before any training time is spent.

### 9.5 Corridor-wide coordination — full MARL spec

**Corridor graph (locked, §0.1):** 3 junctions, linear adjacency `J1—J2—J3` (from `corridor_adjacency` in §7.6). J2 has two neighbors (J1 and J3); J1 and J3 each have one. This is the minimum shape that makes "neighbor-aware" a real, testable claim rather than a single-junction system relabeled.

**Primary attempt: graph-attention.**
A graph-attention layer wraps the PPO policy network as a custom `sb3-contrib` feature extractor (PyTorch). Each junction's node embedding attends over its direct neighbors' current state (from the digital twin, per `corridor_adjacency`) before the policy head selects a phase — so a junction can hold or shorten a green specifically because of what a neighbor is doing, not just its own local state.

**Fallback: shared-policy PPO, no attention.**
One policy network, same weights, applied independently at every junction. No explicit neighbor state in the observation beyond what's already local. Coordination emerges only implicitly, through the shared reward signal across the corridor. This is still a real multi-agent system — the demo claim changes from "junctions explicitly model each other" to "junctions share a learned policy and a corridor-wide reward," which is honest and still defensible.

**Config flag (build this structurally from day one, not as a later refactor):**
```python
# agents/config.py
COORDINATION_MODE = "graph_attention"  # or "shared_policy"

def get_feature_extractor(mode: str):
    if mode == "graph_attention":
        return policy_extractor_attention.GraphAttentionExtractor
    return policy_extractor_shared.SharedPolicyExtractor
```
Both extractors must be built and tested for basic functionality before large-scale training starts. Do not write the shared-policy fallback only if attention fails — write it in parallel so switching the flag never requires new code, only a re-run.

**Decision rule for when to flip the flag:** set an internal training checkpoint (see §16). If graph-attention hasn't shown a clean upward reward trend by that checkpoint, flip to `shared_policy` and continue — this is treated as expected/normal, not a failure requiring root-cause debugging under time pressure.

---

## 10. Safety & Policy Validator

Runs as a mandatory gate between §9 (intervention proposal) and the actual TraCI signal-set call. Nothing reaches the road without passing through here.

```python
def validate(proposed_phase, digital_twin_state) -> ValidatedAction:
    # PRECEDENCE CORRECTED (2026-08-12, Phase 4): emergency is tested FIRST.
    # The original draft of this block tested starvation first and returned,
    # which meant a starved lane could deprioritize an ambulance — directly
    # contradicting this section's own prose ("cannot be ... deprioritized by
    # anything else") and §9.4's weights (w_emergency=20.0 vs a starvation term
    # that only reaches ~20 at a 250s wait).
    if any(lane.type_composition.get("ambulance", 0) > 0 for lane in ...):
        return emergency_override(ambulance_lane)                # bypasses RL entirely, cannot be delayed
    if any(lane.wait_time_max_single_vehicle > STARVATION_CEILING for lane in ...):
        return override_to_fix_starvation(digital_twin_state)   # rule wins, independent of what RL proposed
    return proposed_phase   # only reached if both checks pass
```
- **Starvation ceiling:** enforced as a rule, independent of the learned layer. `STARVATION_CEILING_S = 120.0` (`safety/validator.py`). When any lane's `wait_time_max_single_vehicle` crosses it, the validator overrides the proposed phase to one that actually serves that lane, before it reaches the road.
- **Emergency override:** ambulance detected in any lane → that lane green, all conflicting signals red (automatic — netconvert's phases are conflict-free by construction), cannot be delayed/blocked/deprioritized by anything else, bypasses the learned decision-maker entirely.
- **This is what makes "validates interventions under safety and policy constraints" literally true** — not a training-time hope, a structural gate every single decision passes through.

### 10.1 What the ceiling actually guarantees (honest boundary — see §17)

The ceiling is a bound on **what the signal controller is allowed to decide**, not a bound on observed wait time. Stating it as "no lane is ever allowed to wait past a defined safe maximum" would be false, and falsifiable on a judge's screen by reading the metrics panel. Three unavoidable lags sit between the ceiling triggering and the queue actually moving:

| Source of lag | Size | Why it can't be removed |
|---|---|---|
| Detection granularity | ≤ 5s | The agent decides once per `DECISION_INTERVAL_S`; the twin snapshot is pulled once per decision |
| Yellow clearance | ~3-4s | The override never breaks or shortens a running yellow — doing so releases conflicting movements before the previous ones have cleared, which is the exact hazard this validator exists to prevent |
| Physical discharge | variable | Green is permission to move, not motion. The vehicle at the head of a long queue still has to accelerate away |

The ceiling also defers to `MIN_GREEN_S` (10s): unlike the emergency override, the starvation ceiling does **not** bypass min-green, because letting it do so reintroduces exactly the flicker §9.4's switch penalty and §9.2's masking exist to suppress — two mutually starved lanes would ping-pong every decision step. Such an override is logged with `outcome="deferred_min_green"` and applies on the next eligible step.

**So the honest claim: the ceiling triggers at 120s; the measured worst-case wait is 124-141s.**

Measured 2026-08-12 (corridor 4/3/2), not estimated — an earlier draft of this section guessed ~140-160s, and the real overshoot is tighter than that guess at the low end:

| Run | Controller | Worst wait, ceiling OFF | Worst wait, ceiling ON | Overshoot |
|---|---|---|---|---|
| B3 | adversarial (deliberately starves J2 north) | **998s** | **124s** | +4s |
| B4 | random masked actions | 793s | **141s** | +21s |

The overshoot is small because detection costs nothing on average — the validator reads the snapshot the observation was built from, so it acts on the very next decision step — leaving the yellow (3s here) plus discharge as the only real lag. B4 overshoots more than B3 because random actions leave more of the corridor congested, so a lane takes longer to drain once it finally goes green.

Quote **the range, not a single number**: the overshoot is scenario-dependent and will grow under heavier load. B3's 87.6% reduction (998s → 124s) is the demo-day figure — it isolates the ceiling as the cause, since both runs use an identical seed, scenario and controller and differ only in `enable_safety_validator`.

**One genuine limit, state it rather than hide it:** if a lane is starved because of *downstream* gridlock rather than its own signal, giving it green does not discharge it and the wait keeps climbing. The ceiling guarantees the signal has stopped being the cause of that lane's starvation. It does not guarantee the lane drains. Spillover-driven starvation is §8.1's problem, not §10's.

---

## 11. Coordinator

### 11.1 Emergency corridor clearance
On emergency override trigger: vehicles already in the intersection visibly move to the sides (animate, don't teleport), queued vehicles in the affected lane split to open a path, the emergency vehicle proceeds at max speed, normal operation resumes the instant it's clear.

### 11.2 Responder coordination messaging
```json
{
  "event": "emergency_clearance",
  "lane_id": "east_approach_1",
  "sim_time": 1840,
  "clearance_time_s": 4.2,
  "baseline_clearance_time_s": 18.6,
  "improvement_pct": 77.4,
  "trigger_source": "detected",
  "summary": "Lane cleared for emergency vehicle in 4.2s (vs. ~18.6s baseline) — corridor resumed normal operation immediately after."
}
```
Decision-support output in the spirit of what a real dispatch coordinator would want to see — not wired into an actual emergency-services system (§17).

**`trigger_source` (added 2026-08-29) — `"detected"` or `"operator"`, required, never omitted.** §10's emergency branch fires on *either* a sensed ambulance in a lane *or* that lane appearing in `forced_emergency_lanes` — §13.1's `trigger_emergency(lane_id)`, an operator forcing the same override by hand. Those are different facts and this message is read by a human, so they must not render identically: an operator-forced clearance may have no vehicle behind it at all, and a payload that said "cleared for emergency vehicle" regardless would be asserting something the system did not observe. The field carries `EmergencyClearanceEvent.source` (§11.1) verbatim, and the `summary` text changes with it ("cleared for operator-requested emergency clearance …" rather than "… for emergency vehicle …").

Provenance is fixed when the clearance episode OPENS and is never rewritten: `"detected"` wins if a real ambulance is sensed at that junction on the opening step, otherwise `"operator"`. A real ambulance arriving later at an operator-opened junction does **not** retroactively relabel the trigger — the field describes what caused the clearance, not what turned up during it. `EmergencyClearanceCoordinator.observe()` takes `forced_emergency_lanes` with the same name, type and default (`frozenset()`) as `safety.validator.validate()`, and callers must pass the *same tracked set* they hand the validator rather than maintaining a second copy; the two triggers are unioned per junction so one junction yields one clearance episode however it was raised.

✅ **RESOLVED 2026-08-29 (commit `9cf19af`, seam closed `ad9e4df`).** `coordinator/emergency_clearance.py`'s `EmergencyClearanceEvent.clearance_time_s` now implements exactly the per-junction fix this blocker demanded — detection → green onset at the junction the override fired at, floored at 0.0 with `served_on_arrival` — and `baseline_clearance_time_s` is a labelled worst-case model estimate (`baseline_is_estimate` / `baseline_is_worst_case`), not the broken sweep. The message ships (§13.2 `responder_messages`). The blocker text below is kept as the rationale for that design and as the standing prohibition on reusing the Stage 4 harness. One known imperfection remains open — `served_on_arrival` can fire for a lane the shield had to clear inside the same decision step; see `docs/BUILD_LOG.md`'s 2026-08-29 seam-closed entry.

🚧 **PHASE 8 BLOCKER — `clearance_time_s` and `baseline_clearance_time_s` have NO working implementation in this repo. Do not ship this message until one exists.** The Stage 4 emergency-latency harness is KNOWN BROKEN and UNFIXED: it produced NEGATIVE latencies (−42.0s to −2.0s) because it tracked detection and green-onset at the FIRST junction the ambulance was seen at, while §10's override can fire at a LATER junction on a corridor-through route (J1→J2→J3) — and green-onset recovered as `sim_time - time_since_switch_s` can predate detection when that junction was already green for unrelated reasons. `training/evaluate_stage.py`'s `--emergency-recheck` deliberately OMITS latency rather than emit a known-bad number.

This field is the one place in the whole system where a wrong number is shown to a **human operator as decision support**, so inheriting the broken measurement silently is the worst available outcome — a negative clearance time on screen is both wrong and obviously wrong.

**Fix before populating:** track detection and green-onset PER JUNCTION and attribute the override to the junction it actually fired at. `sim/run_tier0_episode.py --b2` has a correct SINGLE-junction implementation to model it on (it measures from first detection at a known junction and recovers green onset exactly); only the multi-junction generalisation is missing. Until then, omit the fields or render them as "not measured" — never as 0.0 or a placeholder that reads as real.

---

## 12. Explainability / Narrator

### 12.1 Technical decision log (structured, every decision)
```json
{ "sim_time": 1840, "junction_id": "J2", "phase_selected": 2, "score_breakdown": {"halted_count": 3, "wait_time": 41.2, "starvation_bonus": 0.0}, "alternative_scores": {"phase_0": 2.1, "phase_1": 3.4}, "reason": "wait_time_threshold" }
```

### 12.2 Plain-language narration (template-based, not per-step LLM — too slow for a live per-cycle log)
```
if reason == "wait_time_threshold": f"Lane {lane}, {direction} — selected. Wait threshold crossed."
if reason == "raw_count": f"Lane {lane}, {direction} — selected. Highest vehicle count."
if reason == "emergency_override": f"Emergency override — {direction} cleared for ambulance."
if reason == "voice_command": f"Voice command received: '{transcript}' → {action_taken}."
```
Voice-triggered actions must post to this same log with visible on-screen confirmation, not just a spoken response.

### 12.3 Interactive querying
Answers "why did you do that?" by pulling the actual decision log entry (§12.1) for the referenced time/lane and rendering it through the same templates (§12.2) — never a canned or generic response.

---

## 13. Backend (FastAPI + WebSockets)

### 13.1 Control API (called by both dashboard buttons and voice)
| Function | Signature | Behavior |
|---|---|---|
| `set_mode` | `(mode: "manual" \| "auto")` | manual → Tier 0 rule-based (§9.1) takes over, RL paused; auto → RL resumes. (There is no fixed-timer controller — see §17.) |
| `set_lane_bias` | `(lane_id, weight, duration_s)` | multiplies that lane's score by `weight` for `duration_s`, auto-reverts. `weight` ∈ [0.1, 10.0], `duration_s` ∈ [10, 900], both finite (checked in `backend/control_api.py`, 2026-08-31). |
| `get_stats` | `()` | returns current per-lane wait times, counts, starvation counter |
| `trigger_emergency` | `(lane_id)` | manually forces the same override §10 triggers automatically |
| `set_topology` | `(topology_id)` | swaps live SUMO network file, restarts sim with same trained agent. No-op if the combo already matches; rate-limited to one rebuild per 10 s on the sim thread (2026-08-31). |
| `set_baseline_mode` | `("psychoflow" \| "greedy")` | swaps controller live, no restart — Greedy always picks highest raw `vehicle_count`, no fairness/wait consideration |
| `inject_incident` | `(junction_id, affected_lanes, incident_type="lane_blocked", severity="high", lane_id=None, estimated_duration_s=600)` | reports a §7.3 incident into perception — the live trigger for "detects incidents". From the next step it rides `digital_twin.active_incidents`, §8.2's `incident_impact` and §8.1's confidence penalty. Added 2026-08-30. `estimated_duration_s` ∈ [1, 7200] and finite; `affected_lanes` capped at 16 and de-duped; at most 32 operator incidents active at once (2026-08-31). Not a lane closure (§17): the system re-times around the reported blockage, it never actuates one — there is no `close_lane`. |
| `force_phase` | `(junction_id, phase)` | pins a junction to a chosen green `phase` at the next decision step. DEFERRED (goes through `env.step()`, so §10 still validates and an emergency / ceiling override still outranks it) and MASK-CHECKED against the live topology (`action_masks()` + `phase_served_lanes()`); an invalid pin is dropped. The decision-log entry carries `reason="voice_command"` (§12.2). Added 2026-08-31. |
| `clear_override` | `(junction_id=None)` | cancels a `force_phase` pin (`None` = all). Does not touch §10's automatic overrides or a `trigger_emergency` force. Added 2026-08-31. |

### 13.2 WebSocket stream (pushed every simulation step)
```json
{
  "sim_time": 1840,
  "digital_twin": { ...§7.6 shape... },
  "decision": { ...§12.1 shape... },
  "narration": "Lane 3, North — selected. Wait threshold crossed.",
  "metrics_snapshot": { "wait_time_variance_across_lanes": 41.2, "mean_wait_max": 33.6, "starvation_events_total": 1, "throughput_total": 340 },
  "predictions": {                                   // ADDITIVE, key omitted entirely when nothing is material
    "spillover": [ { ...§8.1 shape... } ],           //   sub-key present only when a forecast is material
    "incident_impact": [ { ...§8.2 shape... } ]      //   sub-key present only when an incident is active
  },
  "responder_messages": [ { ...§11.2 shape... } ],  // ADDITIVE, key omitted entirely when empty
  "shadow_advisor": {                                // ADDITIVE, key omitted entirely when the advisor is off
    "advisory_only": true, "drives_the_road": false, //   READ-ONLY — this never reaches the road
    "coordination_mode": "graph_attention", "checkpoint": "psychoflow_stage5_51624_steps_final.zip",
    "recommended_phase": {"J1": 0, "J2": 2, "J3": 1},        // the MARL policy's PRE-SHIELD proposal
    "deployed_proposed_phase": {"J1": 0, "J2": 1, "J3": 1},  // the deployed policy's PRE-SHIELD proposal
    "executed_phase": {"J1": 0, "J2": 1, "J3": 1},           // post-§10, context only
    "agrees_with_deployed": {"J1": true, "J2": false, "J3": true},
    "agreement_count": 2, "n_junctions": 3,
    "episode_agreement_rate": 0.83, "inference_ms": 2.4
  }
}
```

The frame has a **frozen five-key core** (`sim_time`, `digital_twin`, `decision`, `narration`, `metrics_snapshot`), plus **three additive keys** — `predictions`, `responder_messages` and `shadow_advisor` — each omitted entirely unless it carries something material, so a consumer that only handles the five never sees an empty list or empty object.

**`responder_messages` added 2026-08-29 — live since `ad9e4df`, previously undocumented here.** A list of §11.2 responder-coordination message payloads, one per emergency-clearance episode that resolved on this step. **ADDITIVE and present only when non-empty** — on the vast majority of frames the key is omitted entirely, so the frozen five-key shape above is unchanged and no consumer has to handle an empty list. `backend/sim_runner.py::_responder_messages` is the reference implementation.

**`predictions` added 2026-08-30 — the §8.1 spillover forecast and §8.2 incident-impact estimate on the wire.** An object with up to two sub-keys:
- `spillover` — §8.1's list shape (`from_junction` / `to_junction` / `horizon_s` / `predicted_queue_delta` / `confidence`), filtered to the adjacency pairs whose forecast moves at least `_SPILLOVER_MIN_DELTA` (1.0) queued vehicles over the 60s horizon. The near-zero-delta common case is not streamed.
- `incident_impact` — §8.2's shape (`incident_id` / `estimated_affected_junctions` / `estimated_delay_increase_s` / `horizon_s`), one entry per currently-active incident in `digital_twin.active_incidents`.

**ADDITIVE and present only when material** — the whole `predictions` key is omitted when neither sub-key has content, and each sub-key is omitted independently. The spillover numbers are computed by a **read-side** `SpilloverPredictor` in `backend/sim_runner.py` that is separate from the one feeding observation indices 10/11 (that one is stateful; sharing it would corrupt the next observation), fed the same post-step snapshots so it produces the same forecast the policy sees. `backend/sim_runner.py::_predictions` is the reference implementation.

**`shadow_advisor` added 2026-08-30 — the §9.5 MARL checkpoint's own recommendation, READ-ONLY, riding every frame.** `graph_attention` (`psychoflow_stage5_51624_steps_final.zip`) runs its forward pass on the **same pre-step observation and action mask** the deployed policy just used, every decision step, and its recommendation is published for comparison. **It never touches `env.step()` and never influences the deployed control path — Stage 4 single-agent drives the corridor unconditionally.** Default ON when the checkpoint file exists; `--no-shadow` disables it; a missing file is not an error, the key is simply never emitted. `backend/sim_runner.py::_shadow_advice` is the reference implementation and `sim/run_shadow_advisor_check.py` (S1-S6) is its verification harness.

⚠️ **STATE THIS WHEREVER THE FIELD IS SHOWN — the shadow is the WORSE policy, not a better idea being ignored.** On the 4a bake-off's demo corridor (4,3,2), the only topology §19 shows: Stage 4 scores **0** starvation events / **0** §10 overrides / **38-42s** worst wait, against `ga_51624`'s **4 / 1 / 121-125s**; across the full 48-episode grid, `starved_pct` 0.08% vs 1.20% and reward 1.3450 vs 1.2347. The field exists to make §9.5's measured architecture result (attention beat shared-policy 12/12) visible alongside §20's requirement to say out loud that the demo runs SINGLE-AGENT PPO. A disagreement is **not** evidence the deployed policy erred. Do not label this "recommended" or "suggested" in any UI without that context attached.

Both `recommended_phase` and `deployed_proposed_phase` are **PRE-SHIELD proposals**. Agreement is deliberately *not* computed against `executed_phase`, which is post-§10: comparing a proposal to a shielded action would conflate a policy disagreement with the validator's own intervention. `episode_agreement_rate` is cumulative agreeing junction-slots over compared junction-slots **within the current episode**, and resets on the episode boundary with every other per-episode counter. Any exception from the advisor disables it for the rest of the process (logged once) and the key stops being emitted — a broken advisor cannot affect the sim thread or the road.

**`metrics_snapshot` field set updated 2026-08-28 to match §15.2's pinned definitions.** `avg_wait` was **removed**: it was computed from `wait_time_current` (TraCI `lane.getWaitingTime()`, a SUM over the vehicles on a lane — see `agents/rule_based.py`'s "§9.1's UNITS" note), so it scaled with occupancy and lane count rather than fairness, and would confound the §19 Greedy-vs-PsychoFlow side-by-side. `wait_time_variance_across_lanes` and `mean_wait_max` are computed from `wait_time_max_single_vehicle`, verbatim per `training/scripts/checkpoint_bakeoff.py::LaneMetricProbe`, so the live dashboard, the eval suite and the bake-off share one implementation. `backend/sim_runner.py::_update_metrics` is the reference for the live stream; `get_stats()` (§13.1) carries the same field set.

---

## 14. Voice Layer — Full Build Spec (Tier 3, assigned to Claude Code)

**Architecture (local-only, confirmed final — no Claude API anywhere in this feature, ever):**
```
Officer speaks → Web Speech API (STT, browser) → Text → Local Gemma via Ollama (intent + function-calling) → Control API (§13.1) → Dashboard updates
```

- **STT:** browser's built-in Web Speech API — free, zero setup, default choice. Only fall back to a local Whisper install if Web Speech proves unreliable in rehearsal with real background noise.
- **Intent parsing:** local Gemma via Ollama (`ollama pull gemma3` or latest tag). Any local, open-source, constrained-JSON-capable model is an acceptable substitute if Ollama setup causes friction.
- **Function-calling prompt (use as-is):**
  > "You control a traffic signal dashboard. Given a spoken command, output ONLY a JSON function call from this list: `set_mode`, `set_lane_bias`, `get_stats`, `trigger_emergency`. Example: 'switch to manual' → `{"function": "set_mode", "args": {"mode": "manual"}}`"
- **Required demo commands:**
  - *"Switch to manual mode"* → `set_mode(manual)`
  - *"Give lane 3 more priority for the next five minutes"* → `set_lane_bias(lane=3, weight=high, duration=300s)`
  - *"What's the current wait time?"* → `get_stats()`
  - *"Emergency vehicle on lane 2"* → `trigger_emergency(lane=2)`
- **Fallback behavior:** invalid/unparseable JSON, or a function not in the known list → do not guess, do not apply a random action. Display "Command not understood, please try again," take no action, log the miss separately for rehearsal review.
- **"Done" bar:** speak one of the four commands, dashboard visibly reacts within ~2 seconds.

**Explicit instruction for Claude Code:** `backend/voice/intent_agent.py` is built against Ollama + Gemma from the first line of code. There is no earlier "Claude API version" to migrate away from in this build — start local, stay local.

---

## 15. Evaluation & Comparison

### 15.1 Baseline comparison mode
Two controllers runnable on identical live traffic, swappable via `set_baseline_mode` without restarting the sim: PsychoFlow (fairness-first, §9) vs. Greedy (highest raw `vehicle_count`, no fairness/wait consideration — the naive behavior most real systems default to).

### 15.2 Tracked metrics
```json
{ "wait_time_variance_across_lanes": 4.1, "starvation_events_count": 0, "total_throughput": 512, "emergency_clearance_time_s": 4.2 }
```
Comparable across scenarios, seeds, and modes — this is what makes the side-by-side demo moment (§19) show a number, not just a vibe.

⚠️ **`worst_wait` is NOT on this list, and must not be added to it (recorded 2026-08-28, Phase 0 close-out).** It is a SATURATED statistic, not a quality signal: §10's ceiling (`STARVATION_CEILING_S = 120`) intervenes before any lane can run far past 120s, so an episode-level max collapses to a near-binary "did the ceiling fire," landing in a 121–142s band no matter how bad the policy underneath is. Measured: the `--j1-recheck` spike criterion (`worst_wait > 90s`) reports **4/4 spiked** at both ckpt 51,624 (`starved_pct` 1.11–3.54%, good) and ckpt 144,824 (`starved_pct` 27.84–80.51%, catastrophic). Putting it on the dashboard would show judges a number that moves for the wrong reasons — and would make the shield's success look like the policy's failure. See `docs/BUILD_LOG.md`'s 2026-08-28 entry.

**Definitions (the metric names above are not self-defining — pinned here so the dashboard, the eval suite and the bake-off cannot drift apart):**

| metric | definition |
|---|---|
| `starvation_events_count` | RISING-EDGE count, per lane, of `wait_time_max_single_vehicle` crossing `DEFAULT_STARVATION_THRESHOLD_S` (90s) from below. A lane already starved does not re-count until it drops back under. An EVENT count — distinct from `starved_pct`, the fraction of *steps* with any lane over the line. |
| `wait_time_variance_across_lanes` | Population variance ACROSS LANES of `wait_time_max_single_vehicle` at each step, averaged over steps (s²). Deliberately **not** `wait_time_current`, which is a SUM over the vehicles on a lane (see `agents/rule_based.py`'s "§9.1's UNITS" note) and whose variance therefore tracks occupancy and lane count rather than fairness. Comparable BETWEEN CONTROLLERS on one (topology, seed); **not** comparable across topologies with different lane counts. |
| `total_throughput` | Vehicles arrived. Note this is near-constant across controllers on a pinned scenario (all clear the corridor), so it is a sanity check, not a discriminator. |
| `emergency_clearance_time_s` | Per emergency-clearance episode: `EmergencyClearanceEvent.clearance_time_s` (`coordinator/emergency_clearance.py`) — first detection → green onset **at the junction the §10 override fired at**, recovered as `sim_time - time_since_switch_s` (1s resolution), floored at 0.0 with `served_on_arrival` when the phase was already green on arrival. This is the §11.2 field and it ships live on §13.2's `responder_messages`. **NOT the Stage 4 emergency-latency sweep** — that is KNOWN BROKEN (negative latencies; see §11.2) and must never be reused. The eval-suite metric that aggregates it across a scenario set is Phase 12 (§18) and not yet built. |

Reference implementation of the first three: `training/scripts/checkpoint_bakeoff.py`.

### 15.3 Emissions estimate
Derived from the sim's actual vehicle physics (idling time, stop-start frequency per vehicle) — a genuine output of smoother flow, not an assumed constant.

### 15.4 Generalization testing
Evaluate the trained agent on scenarios never seen during training (different density draws, different vehicle-mix seeds) to confirm it learned general principles rather than memorizing training scenarios.

**REQUIRED (added 2026-08-18): an EXPLICIT held-out evaluation scenario set. Phase 12 must not rely on the current protection, which is incidental.**

Training draws are far less diverse than the timestep counts suggest. Every training burst constructs a fresh env with the same seed, so the scenario sequence **restarts at episode 1 on every resume** — a resumed run adds passes over the same scenarios, not new ones. Measured 2026-08-18: Stage 5 `graph_attention` saw **81 distinct scenarios across 248 logged episodes** (~3.1× each), and its final 51,200-step burst introduced **zero** new scenarios. Stages 1–4 sit at ~64–65 distinct scenarios each. Stage 4 and Stage 5 trained on the *same* sequence, since both use `STAGES[4]` with `seed=7`.

Against that, "scenarios never seen during training" is a much stronger requirement than it looks, and it is currently satisfied only by accident. `evaluate_stage.py`'s sweeps happen to pin `randomize_density=False` and `spawn_emergencies=False`, while every training episode has an ambulance and a non-1.0 density multiplier (measured: 162/162 and 0/162 respectively) — so the two sets cannot overlap. That is a *by-product of a config mismatch*, not a designed guarantee.

**The failure mode this creates:** any future evaluation that matches the training config — which is the natural thing to do when measuring "realistic" performance, and exactly what a §15.4 generalization test would reach for — silently evaluates on scenarios the policy trained on, and reports memorization as generalization. Nothing raises.

Phase 12 must therefore:
1. Define a held-out set explicitly (reserved seeds, or a disjoint scenario-index range), recorded in the repo rather than implied.
2. Assert disjointness from the training draw programmatically, not by inspection.
3. State the distinct-scenario count of the training set alongside any generalization claim, since a policy trained on ~81 scenarios generalizing to held-out ones is a materially weaker claim than the raw timestep count implies.

---

## 16. Training Curriculum & Checkpoints

| Stage | Adds | Target timesteps (starting point) |
|---|---|---|
| 1 | Single topology — the locked 4/3/2 corridor (§0.1), `randomize_lane_counts=False`, fixed moderate density | 50k-100k |
| 2 | + lane-count variation (2/3/4-lane, `randomize_lane_counts=True`) | 50k-100k |
| 3 | + density/traffic-mix randomization (`randomize_density=True`) | 50k-100k |
| 4 | + emergency-vehicle events (`spawn_emergencies=True`) | 50k-100k |
| 5 | MARL coordination (attention or fallback per §9.5's checkpoint rule) | 50k-100k+ — ⚠️ **this target range brackets a known collapse zone; see the warning below the table** |
| 6 *(only with slack, §3)* | Y-merge topology | — |

⚠️ **STAGE 5's TARGET RANGE IS A TRAP — "50k-100k+" brackets a measured collapse zone (recorded 2026-08-28).** `graph_attention` PEAKS at `num_timesteps=51,624` and degrades from there: by 61,624 its starvation penalty has tripled and `ovrS` has gone 1.4 → 14.8 per run; by 154,024 it is at 25.82% starved / `ovrS` 15.75 / reward 0.2414, against 1.20% / 1.08 / 1.2347 at the peak. **Following this row's stated target and training to 100k lands you in the degraded regime.** The mechanism is that every burst replays the same ~81 scenarios (§15.4), so extra timesteps are extra passes, not extra data. Do not read the target range as "more is better" — read the curve, keep the peak checkpoint, and see `docs/BUILD_LOG.md`'s 2026-08-28 Phase 0 close-out.

**Stage 1 fixed by `docs/BUILD_LOG.md`'s Phase 3 entry** ("`reset()` is curriculum-parameterized via a `ScenarioConfig` dataclass... defaulting to §16 Stage 1 (fixed 4/3/2, fixed density, no emergencies)") to mean the corridor's own locked 4/3/2 lane-count combination held fixed, not a uniform 4-lane topology — the original wording here ("4-way, 4-lane") predated that decision and was never updated to match it. Stages 2-4 are cumulative additions on top of Stage 1's `ScenarioConfig`, not independent configurations.

**Verification checkpoints — stop and debug on any red flag, don't let a bad run continue unattended:**
| Checkpoint | Check | Red flag |
|---|---|---|
| After Stage 1, ~10k steps | Reward trending up (plot it) | Flat or declining |
| After Stage 1 complete | Beats random-action baseline on wait time | No improvement |
| After Stage 2 | Consistent across all 3 lane-counts | Great on 4-lane, terrible on 2-lane |
| After Stage 4 | ⚠️ **"Near-100% emergency priority" IS AN INVALID METRIC — see footnote 1.** It measures §10's validator, not the policy, and reads ~100% for *any* agent including a random one. Use the **validator override-firing rate** (`evaluate_stage.py --emergency-recheck`) instead. | Agent sometimes ignores emergencies |
| After Stage 5 (MARL checkpoint) | ✅ **THIS DECISION IS ALREADY MADE AND CLOSED (2026-08-16) — DO NOT RE-TRIGGER THIS RULE.** `graph_attention` was kept; it beat `shared_policy` 12/12 on `starved_pct` (1.64% vs 86.60%). `COORDINATION_MODE` is settled. | ⚠️ **Do NOT apply the flip rule to the post-51,624 curve.** `graph_attention`'s reward *does* go flat and then collapse after 51,624 — a reader following the original rule mechanically would flip to `shared_policy`, which is **measurably far worse** (86.60% starved). The collapse is a data-diversity/overfitting artifact, not an architecture verdict. |

**Measured baselines (corridor 4/3/2, seed 7).** All actual numbers, not estimates.

⚠️ **Use the WITH-VALIDATOR row for Checkpoints 1 and 2.** §10's validator lives inside `env.step()` (Phase 4), so the trained agent runs in a shielded MDP. Comparing a shielded agent against the unshielded Phase 3 baseline would flatter it by ~222 reward/step of pure shield effect. The no-validator row is retained only because Phase 3's evidence was recorded against it.

| Metric | Random, **no validator** (Phase 3) | Random, **validator ON** (Phase 4) | **Tier 0** (Phase 4) |
|---|---|---|---|
| Source | `run_env_smoke.py --full-episode` | `run_tier0_episode.py --b4` | `run_tier0_episode.py --b1` |
| Episode | 718 steps, 3600s, `truncated` | 646 steps, 3240s, **`terminated`** | 627 steps, 3145s, **`terminated`** |
| Mean reward / step | −224.8 | **−2.4** | **+1.2** |
| Vehicles arrived | 4604 | 4668 | 4668 |
| Worst single-vehicle wait | 793s | 141s | **41s** |
| Steps with a starved lane | 624 / 718 (87%) | 543 / 646 (84%) | **0 / 627 (0%)** |
| §10 overrides | — | 121 (90 applied, 31 deferred) | **0** |

Three things to read off this table:

- **Checkpoint 1's bar is −2.4, not −224.8**, and Checkpoint 2's real target is Tier 0's **+1.2**. Beating random is now a low bar; beating the rule-based floor is the claim worth making (§15.1).
- **The shield alone clears the corridor.** Both Phase 4 rows `terminate` (every vehicle arrived, nothing pending) where the unshielded run never did. The starvation ceiling is what converts a gridlocked corridor into one that drains.
- **Tier 0 never triggers a single override.** That is the intended result, not a gap in coverage: the §9.1 bonus keeps every lane under the 120s ceiling, so the hard gate never has to fire. It also means B1 alone does **not** verify §10 — which is exactly why B2 (emergency) and B3 (ceiling, adversarial A/B) exist.

**If Checkpoint 1's reward curve comes back flat or unstable, try this FIRST** (recorded 2026-08-12, §18 Phase 3 — before touching anything structural):

The §9.4 starvation term is **unbounded**. Per-lane penalty is `p = r² + 4·max(0, r−1)²` with `r = wait / 90`, so a lane at 793s scores `p = 8.81² + 4·7.81² = 321.6` — and per-step reward reaches −765 late in a bad episode. The value function therefore has to span roughly `[−800, +3]`, and early-training advantage estimates get dominated by a handful of catastrophic steps rather than by the ordinary decisions the policy needs to learn.

**The fix to try first:** clamp `r` in `env/reward.py`'s `lane_starvation_penalty()` — e.g. `r = min(r, 5.0)`, capping the per-lane penalty at ~89 instead of letting it run to 300+. This preserves everything §9.4 requires (a 2× wait still costs far more than 2× penalty across the whole operating range; balanced still beats starved; the emergency term still dominates) while pulling the reward into a range PPO's value head can fit. Re-run `python -m env.reward` afterwards — its assertions encode the hand-scored scenarios that were verified before Phase 3 was signed off, so they will catch it if a cap breaks the ordering.

This was deliberately **not** applied during Phase 3: the uncapped formula was hand-verified and signed off, and whether the tail actually hurts learning is a training-dynamics question only this checkpoint can answer. Don't pre-emptively cap it before seeing a curve.

**Update (2026-08-12, Phase 4) — the tail is largely gone, and the clamp is probably now unnecessary.** The numbers above (793s, per-step −765, a `[−800, +3]` value range) were measured *without* §10's validator. With the ceiling in place the worst observed wait falls to **141s** under random actions, so the worst per-lane penalty falls from `p(793s) = 321.6` to `p(141s) = 3.74`, and mean reward/step goes from −224.8 to **−2.4** — a value range of roughly `[−25, +3]`. That is comfortably inside what PPO's value head fits, which was the entire concern.

So: still don't pre-emptively cap. But if Checkpoint 1 *does* come back flat, the clamp is now much less likely to be the cause, and the diagnosis should start elsewhere. Keep the clamp as a second resort rather than the first thing to try.

---

## 17. Honest Scope Boundaries

- No real traffic cameras, weather services, or emergency dispatch integration — all simulated with realistic structure, not live-integrated.
- "V2X" means structured, realistically-imperfect connected-vehicle-shaped data generated from the simulation — not a full network-layer V2X communications simulation.
- Coordinator/responder messaging is decision-support output, not an actual dispatch system.
- **Interventions are signal-phase control (§9) and emergency-corridor clearance (§10/§11) only.** The problem statement's own parenthetical reads "signal timing changes, lane closures, emergency corridors" — **lane closures are out of scope as a system output.** PsychoFlow *detects* a lane blockage (§7.3 incident intake) and *predicts* its downstream impact (§8.2), then re-times signals around it — but it never issues a closure order. Commanding a closure is a physical / authority action (crews, cones, police), not a control signal this system emits, and there is deliberately no `close_lane` in the §13.1 control API. Same treatment as V2X, the vision mock and Y-merge: named once here, not silently implied to be built.
- Everything is validated inside a traffic simulator, not on real roads — metrics are simulation-derived, not field-measured.
- The vision/CCTV input is a simulated mock producing realistic per-lane data, not a live camera pipeline.
- Coordination is achieved through one shared policy attending across all three junctions in a single forward pass (centralized execution), not three independently-executing agents. The corridor is modeled as three junction agents whose phase decisions come from a single neighbor-aware network (§9.5) and a single corridor-wide reward — not three separate processes negotiating at runtime. This is what makes the graph-attention/shared-policy config flag a one-line swap rather than a rewrite, and it is the claim to make on demo day.
- **Emergency handling is validator-guaranteed, not learned-proactive (diagnosed 2026-08-29, see `docs/BUILD_LOG.md`).** The honest statement: *"The learned policy serves an approaching ambulance on its own initiative on about three quarters of the decision steps where one is present — measurably better than chance (0.83 vs 0.64, p=0.0007, held-out). It can't anticipate one: the sensors don't register an ambulance until it's already on the junction's own approach lane, so on most routes there is no earlier moment at which it could have acted. The safety validator sits underneath as a hard gate — it has to fire in roughly 8 of 10 episodes, and it catches every ambulance, by construction rather than by training."* Do **not** say "0% proactive" or "15/15" — both were withdrawn (the 15/15 sweep was 3 distinct emergency scenarios, not 15). Do **not** claim the policy anticipates emergencies. §16's Stage 4 emergency checkpoint is on record as FAILED; the safety guarantee is structural (§10 sits inside `env.step()` ahead of the only `setPhase` call) and does not depend on that checkpoint passing.
- **The §13 control API is UNAUTHENTICATED, and is a local demo surface, not a hardened service (recorded 2026-08-31).** Every §13.1 function — `set_mode`, `set_lane_bias`, `trigger_emergency`, `set_topology`, `set_baseline_mode`, `inject_incident`, `force_phase`, `clear_override` — is callable by anyone who can reach the port, with no token, login, or per-caller check. This is acceptable **only** because the backend binds loopback (`127.0.0.1`) by default: `backend/main.py` refuses a non-loopback `--host` unless `--allow-lan` is also passed, and prints a standing warning banner when it is. The hardening that exists — operator-input range checks in `backend/control_api.py`, a server-side function allowlist (`dispatch()`), a per-iteration guard on the sim thread, an origin-scoped credential-less CORS policy, and `/health` disclosing only a boolean and an exception class — is damage limitation for a surface that is not meant to be exposed, **not** a substitute for authentication. If this is ever deployed anywhere reachable, an auth layer is a prerequisite, not an enhancement.
- **There is no round-robin / fixed-timer controller in the build — it is the §19 hook illustration only.** §19's opening beat ("fixed-timer signals, empty-lane problem") and the §13.1 `set_mode` row's "or fixed timer" phrasing predate the settled controller set. The only controllers that actually run are Tier 0 (fairness-first, §9.1), the trained PPO policy (auto mode), and — landing in Phase 12 — Greedy (§15.1, currently stubbed). `set_mode("manual")` hands over to Tier 0, never to a cyclic timer. A fixed-timer baseline could be shown as a static "before" picture in the hook, but it is not a `set_baseline_mode` option and nothing swaps to it live.
- **The voice layer (§14) is "no paid inference", not "fully local".** The hard constraint is *no Claude API call and no paid model call anywhere in the runtime path* (§0, §2). Intent parsing (local Gemma via Ollama) genuinely is local. **Browser STT via the Web Speech API is not** — in Chrome it streams audio to Google's speech service (free, no key, but off-device). Describe it as "free, local-model intent parsing with browser speech-to-text", and if a judge asks specifically, say the STT step can call a cloud service unless the optional local-Whisper fallback (§14) is used. Do not call the whole feature "local-only".

State these explicitly to judges and keep them in code comments — this is what keeps the team from accidentally overstating what's built vs. modeled.

---

## 18. Build Sequence for Claude Code (repo is empty — follow this order)

Each phase below is a self-contained Claude Code session. Don't start phase N+1 until phase N's "done" bar is met.

1. **Environment + SUMO networks (§5.1, §0.1, §6, §7.1's fields):** install checklist, build `generate_corridor.py` producing the 3-junction `J1→J2→J3` corridor with independently-settable 2/3/4 lane counts per junction, define vehicle types. Done bar: `sumo-gui` on a generated corridor (e.g. J1=4-lane, J2=3-lane, J3=2-lane) renders an empty, connected 3-junction network.
2. **Perception layer (§7):** all five modules, each independently testable against a running SUMO instance. Done bar: digital twin (§7.6) populates correctly for one manually-run episode.
3. **Environment wrapper + reward (§9.2, §9.4):** Gymnasium env, obs/action padding + masking, reward function. Done bar: random-action agent runs a full episode without crashing; hand-scored test scenarios produce intuitively-correct rewards.
4. **Tier 0 rule-based controller + Safety Validator (§9.1, §10):** get this live end-to-end first — it's the guaranteed floor. Done bar: rule-based controller runs live in SUMO, starvation ceiling and emergency override both verifiably trigger.
5. **Prediction (§8):** spillover + incident-impact, wired to read from the digital twin. Done bar: predictions visibly change when an incident is manually injected.
6. **PPO training, Stages 1-4 (§16):** single-agent first, no MARL yet. Done bar: all Stage 1/2/4 checkpoints pass.
7. **MARL coordination (§9.5):** both extractors built together, config flag wired, attention attempted first. Done bar: Stage 5 checkpoint evaluated, flag set to whichever path is actually converging.
8. **Coordinator + Explainability (§11, §12):** clearance behavior, messaging, decision log, narration templates, query interface. Done bar: full decision log renders correctly for a rule-based test run before the RL agent is even wired in.
9. **Backend (§13):** FastAPI, WebSocket stream, control API, baseline-swap. Done bar: dashboard-less test client can call every control function and see the WebSocket stream update.
10. **Frontend (§6's component list):** intersection view, metrics, decision log, voice panel, Greedy/PsychoFlow toggle. Done bar: full live demo runs end-to-end from browser.
11. **Voice layer (§14):** built against the now-working control API. Done bar: all four demo commands work with realistic background noise.
12. **Evaluation suite (§15):** metrics, emissions, generalization test. Done bar: side-by-side comparison produces the actual numbers you'll show judges.
13. **(Only with slack) Y-merge (§3, §16 Stage 6).**

---

## 19. Demo Script

1. **Hook (30s):** fixed-timer signals, empty-lane problem.
2. **Live intersection (30s):** realistic vehicle mix, animated movement.
3. **Side-by-side (60-90s), strongest beat:** Greedy vs. PsychoFlow on identical live traffic — fairness/starvation diverge in real time (§15.1, §15.2).
4. **Emergency override (20s):** spawn ambulance, instant corridor clearance (§11.1).
5. **Voice control (30-40s), differentiator:** speak a live command, dashboard reacts (§14).
6. **Honest close (20s):** state what's rule-based vs. learned vs. simulated-mock (§17), note pre-training is standard practice.

---

## 20. Final Pre-Event Checklist

- [ ] No Claude API calls anywhere in `backend/voice/` or any other per-decision runtime path
- [ ] Tier 0 confirmed working standalone (§18 phase 4)
- [ ] Full pipeline wired end-to-end (§4), not standalone scripts
- [ ] **The demo runs SINGLE-AGENT PPO — say so out loud, do not call it multi-agent.** The deployed policy is `psychoflow_stage4_153600_steps_final.zip` (Stage 4 single-agent), selected by measurement in the 4a bake-off. `COORDINATION_MODE = "graph_attention"` remains the answer to §9.5's *architecture* question (attention beat shared-policy 12/12), but that is **not** what is driving the corridor on stage. Both statements are true and both are defensible; conflating them is not.
- [ ] MARL config flag confirmed swappable (relevant to §9.5's result, not to what the demo runs)
- [ ] Trained checkpoint(s) saved and loading correctly in the backend
- [ ] All §16 checkpoints passed and recorded — **except Stage 4's emergency-priority bar, which is on record as FAILED (diagnosed 2026-08-29). Everyone can state the §17 emergency-handling wording out loud: "~¾ served proactively, better than chance; can't anticipate because sensing is approach-lane-only; the validator is the hard guarantee, ~8/10 episodes." Nobody says "0% proactive" or "15/15".**
- [ ] Voice layer tested with real background noise; fallback message confirmed to trigger
- [ ] Greedy-vs-PsychoFlow toggle and emergency override rehearsed live, multiple times
- [ ] Backup demo video recorded
- [ ] Everyone on the team can state, out loud, what's rule-based vs. RL-learned vs. simulated-mock

---

*This document is the single source of truth. Update it in place as decisions change during the build.*
