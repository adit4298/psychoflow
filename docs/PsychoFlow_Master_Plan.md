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
| 1 | Aug 11–17 | 1 (corridor generator), 2 (perception), 3 (env + reward), 4 (Tier 0 + safety validator) | **Opus 4.8** for phases 2–4; Sonnet 5 for phase 1 | High/extended thinking on phases 2–4 | Corridor generator (phase 1) is boilerplate SUMO XML — Sonnet is fine. Reward function and safety validator (§9.4, §10) are the two places a subtle bug becomes invisible until hours into training — worth Opus's slower, more careful reasoning here, and cheap relative to the training time it saves. |
| 2 | Aug 18–24 | 5 (prediction), 6 (PPO training Stages 1–4), 7 (MARL, both extractors + config flag) | **Sonnet 5** for phase 5 and routine training-script work; **Opus 4.8** for phase 7 (both MARL extractors) and any Stage 1–2 checkpoint failure | High thinking only for MARL extractor design and checkpoint debugging | This week is compute-heavy, not chat-heavy — most of your week is your CPU training in the background, not you spending credits. Reserve Opus specifically for the graph-attention/shared-policy extractor pair (§9.5), since a wrong design there is the single most expensive mistake to catch late. |
| 3 | Aug 25–31 | 5 continued (MARL Stage 5 checkpoint decision), 8 (coordinator + explainability), 9 (backend), 10 (frontend) | **Sonnet 5** for 8–10; **Opus 4.8** only for the MARL checkpoint call itself (deciding attention vs. shared-policy fallback, §9.5/§16) | High thinking only for the checkpoint decision; default elsewhere | Coordinator, backend, and frontend are all precisely spec'd in §11–13 — high-volume, low-ambiguity work, exactly where Sonnet is both cheaper and fast enough to matter. |
| 4 | Sep 1–5 | 11 (voice), 12 (evaluation suite), rehearsal, bug fixes only | **Sonnet 5** for everything, no new Opus work planned | Default effort | No new architecture decisions should be happening this late — if one comes up, that's a sign scope crept past what §18 intended. Keep this week's usage light on purpose; you want headroom in the weekly cap for last-minute fixes, not none left when something breaks two days before the deadline. |

**Credit conservation rules, in order of importance:**
1. **Never use Opus for anything in §6 (folder structure) that's pure boilerplate** — XML generation, React components, FastAPI route stubs, JSON schema implementations. These are Sonnet's whole use case.
2. **Front-load Opus usage into Weeks 1 and 2.** The foundation (env, reward, safety validator, MARL extractors) is where mistakes compound; everything built in Weeks 3–4 sits on top of it. If Week 1–2 foundations are solid, Weeks 3–4 genuinely don't need Opus much at all.
3. **Keep a deliberate reserve, don't plan to use the full weekly cap every week.** Training runs fail in ways that aren't predictable from this plan (a flat reward curve, a masking bug that only shows up at Stage 3) — that debugging is exactly when you want Opus available and haven't already spent the week's allowance on routine frontend work.
4. **When a training checkpoint (§16) fails, diagnose with Opus, fix with Sonnet.** Use Opus to reason about *why* the reward curve is flat or the MARL policy isn't converging (paste the curve/logs, ask for the likely cause) — that's a small, high-value exchange. Once you know the fix, hand the actual code change to Sonnet.
5. **If you're ever unsure which model a task needs, default to Sonnet and escalate only if it visibly struggles** — that's cheaper than guessing Opus-first on something that turns out to be routine.

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

### 8.1 Spillover forecasting
Input: digital twin state + `corridor_adjacency` (§7.6: `J1→J2`, `J2→J3`). Output: per downstream-junction, a predicted queue-length delta over the next N seconds (start N=60s).
```json
{ "from_junction": "J1", "to_junction": "J2", "horizon_s": 60, "predicted_queue_delta": 4.2, "confidence": 0.78 }
{ "from_junction": "J2", "to_junction": "J3", "horizon_s": 60, "predicted_queue_delta": 1.9, "confidence": 0.81 }
```
Start with a heuristic (e.g. current outflow rate from J1 vs. current capacity headroom at J2) before reaching for a learned model — this is a lightweight addition on the existing stack, not a new ML project.

### 8.2 Incident impact prediction
Input: an incident event (§7.3) + digital twin state. Output: estimated ripple.
```json
{ "incident_id": "inc_0007", "estimated_affected_junctions": ["J2", "J3"], "estimated_delay_increase_s": 85, "horizon_s": 300 }
```
Feeds the intervention layer a head start on compensating before downstream congestion is actually observed.

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
    if any(lane.wait_time_max_single_vehicle > STARVATION_CEILING for lane in ...):
        return override_to_fix_starvation(digital_twin_state)   # rule wins, independent of what RL proposed
    if any(lane.type_composition.get("ambulance", 0) > 0 for lane in ...):
        return emergency_override(ambulance_lane)                # bypasses RL entirely, cannot be delayed
    return proposed_phase   # only reached if both checks pass
```
- **Hard starvation ceiling:** enforced as a rule, independent of the learned layer. If a proposed action would let a lane cross the safe max, it's overridden before reaching the road.
- **Emergency override:** ambulance detected in any lane → that lane green immediately, all conflicting signals red, cannot be delayed/blocked/deprioritized by anything else, bypasses the learned decision-maker entirely.
- **This is what makes "validates interventions under safety and policy constraints" literally true** — not a training-time hope, a structural gate every single decision passes through.

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
  "summary": "Lane cleared for emergency vehicle in 4.2s (vs. ~18.6s baseline) — corridor resumed normal operation immediately after."
}
```
Decision-support output in the spirit of what a real dispatch coordinator would want to see — not wired into an actual emergency-services system (§17).

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
| `set_mode` | `(mode: "manual" \| "auto")` | manual → rule-based (§9.1) or fixed timer takes over, RL paused; auto → RL resumes |
| `set_lane_bias` | `(lane_id, weight, duration_s)` | multiplies that lane's score by `weight` for `duration_s`, auto-reverts |
| `get_stats` | `()` | returns current per-lane wait times, counts, starvation counter |
| `trigger_emergency` | `(lane_id)` | manually forces the same override §10 triggers automatically |
| `set_topology` | `(topology_id)` | swaps live SUMO network file, restarts sim with same trained agent |
| `set_baseline_mode` | `("psychoflow" \| "greedy")` | swaps controller live, no restart — Greedy always picks highest raw `vehicle_count`, no fairness/wait consideration |

### 13.2 WebSocket stream (pushed every simulation step)
```json
{
  "sim_time": 1840,
  "digital_twin": { ...§7.6 shape... },
  "decision": { ...§12.1 shape... },
  "narration": "Lane 3, North — selected. Wait threshold crossed.",
  "metrics_snapshot": { "avg_wait": 22.4, "starvation_events_total": 1, "throughput_total": 340 }
}
```

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

### 15.3 Emissions estimate
Derived from the sim's actual vehicle physics (idling time, stop-start frequency per vehicle) — a genuine output of smoother flow, not an assumed constant.

### 15.4 Generalization testing
Evaluate the trained agent on scenarios never seen during training (different density draws, different vehicle-mix seeds) to confirm it learned general principles rather than memorizing training scenarios.

---

## 16. Training Curriculum & Checkpoints

| Stage | Adds | Target timesteps (starting point) |
|---|---|---|
| 1 | Single topology (4-way, 4-lane), fixed moderate density | 50k-100k |
| 2 | Lane-count variation (2/3/4-lane) | 50k-100k |
| 3 | Density/traffic-mix randomization | 50k-100k |
| 4 | Emergency-vehicle events | 50k-100k |
| 5 | MARL coordination (attention or fallback per §9.5's checkpoint rule) | 50k-100k+ |
| 6 *(only with slack, §3)* | Y-merge topology | — |

**Verification checkpoints — stop and debug on any red flag, don't let a bad run continue unattended:**
| Checkpoint | Check | Red flag |
|---|---|---|
| After Stage 1, ~10k steps | Reward trending up (plot it) | Flat or declining |
| After Stage 1 complete | Beats random-action baseline on wait time | No improvement |
| After Stage 2 | Consistent across all 3 lane-counts | Great on 4-lane, terrible on 2-lane |
| After Stage 4 | Near-100% emergency priority in test episodes | Agent sometimes ignores emergencies |
| After Stage 5 (MARL checkpoint) | Graph-attention reward trending up cleanly | Flat/unstable → flip the config flag to shared-policy (§9.5), don't debug further under time pressure |

**Measured random-action baseline (recorded 2026-08-12, §18 Phase 3, corridor 4/3/2, seed 7).** Checkpoints 1 and 2 compare against this — these are actual numbers from `python sim/run_env_smoke.py --full-episode`, not estimates:

| Metric | Random masked actions |
|---|---|
| Episode length | 718 decision steps, 3600s, `truncated` (never cleared early) |
| Mean reward / step | **−224.8** |
| Vehicles arrived | 4604 |
| Worst single-vehicle wait | 793s |
| Steps with a starved lane | 624 / 718 (87%) |

**If Checkpoint 1's reward curve comes back flat or unstable, try this FIRST** (recorded 2026-08-12, §18 Phase 3 — before touching anything structural):

The §9.4 starvation term is **unbounded**. Per-lane penalty is `p = r² + 4·max(0, r−1)²` with `r = wait / 90`, so a lane at 793s scores `p = 8.81² + 4·7.81² = 321.6` — and per-step reward reaches −765 late in a bad episode. The value function therefore has to span roughly `[−800, +3]`, and early-training advantage estimates get dominated by a handful of catastrophic steps rather than by the ordinary decisions the policy needs to learn.

**The fix to try first:** clamp `r` in `env/reward.py`'s `lane_starvation_penalty()` — e.g. `r = min(r, 5.0)`, capping the per-lane penalty at ~89 instead of letting it run to 300+. This preserves everything §9.4 requires (a 2× wait still costs far more than 2× penalty across the whole operating range; balanced still beats starved; the emergency term still dominates) while pulling the reward into a range PPO's value head can fit. Re-run `python -m env.reward` afterwards — its assertions encode the hand-scored scenarios that were verified before Phase 3 was signed off, so they will catch it if a cap breaks the ordering.

This was deliberately **not** applied during Phase 3: the uncapped formula was hand-verified and signed off, and whether the tail actually hurts learning is a training-dynamics question only this checkpoint can answer. Don't pre-emptively cap it before seeing a curve.

---

## 17. Honest Scope Boundaries

- No real traffic cameras, weather services, or emergency dispatch integration — all simulated with realistic structure, not live-integrated.
- "V2X" means structured, realistically-imperfect connected-vehicle-shaped data generated from the simulation — not a full network-layer V2X communications simulation.
- Coordinator/responder messaging is decision-support output, not an actual dispatch system.
- Everything is validated inside a traffic simulator, not on real roads — metrics are simulation-derived, not field-measured.
- The vision/CCTV input is a simulated mock producing realistic per-lane data, not a live camera pipeline.
- Coordination is achieved through one shared policy attending across all three junctions in a single forward pass (centralized execution), not three independently-executing agents. The corridor is modeled as three junction agents whose phase decisions come from a single neighbor-aware network (§9.5) and a single corridor-wide reward — not three separate processes negotiating at runtime. This is what makes the graph-attention/shared-policy config flag a one-line swap rather than a rewrite, and it is the claim to make on demo day.

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
- [ ] MARL config flag confirmed swappable; know which mode you're actually demoing
- [ ] Trained checkpoint(s) saved and loading correctly in the backend
- [ ] All §16 checkpoints passed and recorded
- [ ] Voice layer tested with real background noise; fallback message confirmed to trigger
- [ ] Greedy-vs-PsychoFlow toggle and emergency override rehearsed live, multiple times
- [ ] Backup demo video recorded
- [ ] Everyone on the team can state, out loud, what's rule-based vs. RL-learned vs. simulated-mock

---

*This document is the single source of truth. Update it in place as decisions change during the build.*
