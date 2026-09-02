\# PsychoFlow — Project Memory



This file is read automatically at the start of every session. It governs

\*how\* work gets done. What gets built is specified in

`docs/PsychoFlow\_Master\_Plan.md` — read that in full before touching any

code, every session, even if you've read it before in an earlier session.



\---



\## 1. Source of truth and read order



1\. This file (CLAUDE.md) — behavioral rules, read first.

2\. `docs/PsychoFlow\_Master\_Plan.md` — the actual spec. Read in full, not

&#x20;  just the section you think is relevant — sections cross-reference each

&#x20;  other (e.g. the reward function references the safety validator).

3\. `docs/BUILD\_LOG.md` — what's actually been decided/built in past

&#x20;  sessions, which may have deviated from the original plan for a good

&#x20;  reason. If BUILD\_LOG.md and the master plan conflict, BUILD\_LOG.md

&#x20;  wins for anything already built — it's the record of what's real.



\## 2. Locked decisions — do not revisit without asking



These are settled. If a task seems to require reopening one of these,

stop and ask instead of working around it silently.



\- V2X = shaped data only (§7.5). No Veins/OMNeT++, ever.

\- Vision input = simulated mock (§7.2). No real detection model.

\- Voice: intent parsing is local (Ollama/Gemma, §14); browser STT (Web

&#x20; Speech API) is NOT local — in Chrome it streams audio to Google's

&#x20; speech service (free, no key, but off-device). The hard rule is \*\*no

&#x20; Claude API call and no paid inference anywhere in the runtime path\*\* —

&#x20; a budget constraint, not a style preference. Say "free local-model

&#x20; intent parsing with browser speech-to-text", not "local-only" (§17

&#x20; corrected this 2026-08-31). Truly-local STT is the optional Whisper

&#x20; fallback in §14.

\- Corridor = 3 junctions, linear, J1→J2→J3 (§0.1). Lane count 2/3/4

&#x20; independently configurable per junction via the generator, not static

&#x20; files.

\- MARL = graph-attention primary + shared-policy PPO fallback, one

&#x20; config flag (§9.5) — build both extractors together, not sequentially.

\- Y-merge = deprioritized (§3). Do not write, extend, or "just quickly

&#x20; fix" Y-merge code until Tiers 0, 1, 1.5, and 3 are all built and their

&#x20; §18 done-bars have passed.



\## 3. Build order discipline



Follow `docs/PsychoFlow\_Master\_Plan.md` §18 phase by phase, in order.



\*\*HARD GATE — no real training before Phase 5 (prediction, §8) lands.\*\*

&#x20; `env/psychoflow\_env.py` may be used for smoke tests, random-action

&#x20; rollouts, reward sanity checks and unit tests at any time. It must NOT

&#x20; be used for an actual PPO training run — any run whose checkpoint is

&#x20; kept, or whose reward curve is read as a §16 checkpoint — until §8.1's

&#x20; spillover predictor exists and is wired into the observation.

&#x20; \*\*Why:\*\* observation indices 10 and 11 of each junction's scalar block

&#x20; (`spillover\_delta`, `spillover\_confidence`, §9.2) are zero-filled until

&#x20; Phase 5. Training against permanently-zero inputs teaches the policy

&#x20; that those two features carry no information; when Phase 5 makes them

&#x20; live, the policy has already learned to ignore them and will not

&#x20; recover without retraining from scratch. §18 already orders Phase 5

&#x20; before Phase 6 — this gate is what makes that ordering load-bearing

&#x20; rather than incidental.

&#x20; \*\*Check before any training run:\*\* `prediction/spillover.py` exists and

&#x20; `PsychoFlowEnv` is constructed with a non-None `spillover\_predictor`.

&#x20; If either is false, stop and say so — do not start the run.
&#x20; \*\*Phase 6 prerequisite (named explicitly, not just implied):\*\* `training/train.py` MUST assert
&#x20; `env.spillover_predictor is not None` before calling `model.learn()`. psychoflow\_env.py
&#x20; itself deliberately does NOT raise on a None predictor (smoke tests, random-action
&#x20; rollouts and unit tests are legitimate with it absent) — it only warns (see §8's
&#x20; SpilloverPredictor entry). The hard assert is Phase 6's job, not Phase 5's.



\- Do not start phase N+1 until phase N's stated done-bar has actually

&#x20; been verified (§4 below), not just "looks right."

\- Do not combine phases to save a round-trip, even if it seems efficient

&#x20; — later phases depend on earlier ones being correct, and combining

&#x20; them makes it harder to tell which phase introduced a bug.

\- Do not build ahead of the current phase "while you're in there" (e.g.

&#x20; don't start on the frontend during the reward-function phase because

&#x20; it seemed related). If you notice something in a later phase that the

&#x20; current phase's design should account for, say so and ask — don't

&#x20; silently expand scope.



\## 4. Verification protocol — what "done" actually means



Before reporting any phase or task as complete:



1\. State the exact done-bar from §18 (or the relevant module section)

&#x20;  you're checking against, verbatim.

2\. Actually run the check — execute the command, run the test, load the

&#x20;  network in sumo-gui, whatever the done-bar specifies. Don't infer

&#x20;  correctness from reading the code back.

3\. Show the actual output (not a paraphrase of what you expect it to

&#x20;  say) as evidence.

4\. If it doesn't pass, say so plainly and propose a fix — don't soften

&#x20;  a failure into "should work" or silently patch and move on without

&#x20;  flagging what broke.



For anything touching the RL environment, reward function, or safety

validator specifically: before writing code, state your design plan and

wait for confirmation. These are the places a wrong assumption is

expensive to discover late (§0.2) — a quick check now is cheaper than

hours of training on a broken reward.



\## 5. Decision log — append after every session with a real decision



Any time you make a call the master plan didn't fully specify — a

parameter value, a library choice, an interpretation of an ambiguous

schema, a workaround for something that didn't work as expected — append

an entry to `docs/BUILD\_LOG.md` in this format:



&#x20;   ## \[date] — \[§ section / phase]

&#x20;   \*\*Decision:\*\* what was chosen

&#x20;   \*\*Why:\*\* the reasoning, in one or two sentences

&#x20;   \*\*Deviates from plan?\*\* yes/no — if yes, what changed and why

&#x20;   \*\*Verified:\*\* what you actually ran to confirm it works



Read the existing log before starting a new session's work — don't

re-decide something that's already been settled and recorded, and don't

contradict a past entry without explicitly flagging that you're

overriding it and why.



\## 6. Stop-and-ask conditions



Pause and ask the user rather than proceeding when:



\- A locked decision (§2) seems to need reopening.

\- A checkpoint in §16 of the master plan fails (e.g. a flat reward

&#x20; curve) — diagnose and explain the likely cause, but the call on how to

&#x20; proceed is the user's, not yours to make silently.

\- You're about to make a change that touches more than one phase's worth

&#x20; of code at once.

\- Something in the master plan is genuinely ambiguous and the two

&#x20; reasonable interpretations would lead to materially different work —

&#x20; pick the safer/cheaper one to \*propose\*, but confirm before building

&#x20; it out fully.

\- You're tempted to add something not in the spec because it "would be

&#x20; better" — scope creep is a real cost against a fixed deadline (Sep 5)

&#x20; and a capped budget; flag the idea, don't just build it.



\## 7. Efficiency discipline (budget is real — see master plan §0.2)



\- Don't regenerate or rewrite files that already pass their done-bar

&#x20; "for cleanliness." Working code that meets spec is left alone.

\- Don't produce large explanatory essays before routine, low-ambiguity

&#x20; work (boilerplate schemas, CRUD endpoints, React components) — just

&#x20; build it and report what was done.

\- Do slow down and reason explicitly before code for anything in §4's

&#x20; "state your design plan first" list.



\## 8. Repo/environment facts (update as they're established)



\- Venv activate: `source venv/bin/activate`

\- SUMO check: `sumo --version` (requires `SUMO\_HOME` set)

\- Render a generated network: `sumo-gui -n sim/networks/generated/<file>.net.xml`

\- Venv on this machine is Windows-layout: `venv/Scripts/`, not `venv/bin/`.
&#x20; \*\*Do not assume it is active — confirm it.\*\* Run
&#x20; `python -c "import sys; print(sys.prefix)"` before any pip install or any
&#x20; verification run whose output you intend to report. It must print
&#x20; `...\\GitHub\\Test\\venv`. If it prints a `WindowsApps\\PythonSoftwareFoundation`
&#x20; path you are on the system interpreter, which has a different (older)
&#x20; numpy and none of the RL stack — installs will land in the wrong place
&#x20; and any result you report will have been produced by the wrong Python.
&#x20; This actually happened in the Phase 3 session.

\- The venv has the full §5.1 stack: `gymnasium`, `stable_baselines3`,
&#x20; `sb3_contrib`, `torch`, `numpy` 2.x. `sumolib`/`traci` resolve from
&#x20; `SUMO\_HOME/tools`, not from pip — which is why a SUMO-only script can
&#x20; run fine on the system interpreter and mislead you into thinking the
&#x20; environment is correct.

\- Generate the corridor (Phase 1): `python sim/networks/generate_corridor.py --j1 4 --j2 3 --j3 2`

\- Run the perception episode (Phase 2 verification): `python sim/run_perception_episode.py`

\- Reward hand-scored scenarios (§9.4): `python -m env.reward` — reproduces the
&#x20; signed-off numbers as assertions. Run this after ANY reward change.

\- Env done-bar checks (Phase 3): `python sim/run_env_smoke.py` (spaces, obs
&#x20; readback, masking rejections) and `python sim/run_env_smoke.py --full-episode`
&#x20; (§18's "random-action agent runs a full episode without crashing").

\- \*\*`MAX_PHASES = 3` is a measured value, not a guess\*\* (`env/obs\_action\_spec.py`).
&#x20; Phase count is not a function of a junction's own lane count — it depends on
&#x20; whether its approach lane count matches its outgoing edge. Symmetric junction
&#x20; = 4 phases / 2 green; asymmetric = 6 / 3. Measured across all 27 lane-count
&#x20; combos. The action space indexes GREEN phases only; when counting greens,
&#x20; test `"y" not in state` — testing for `G`/`g` over-counts, because netconvert's
&#x20; yellow phases keep a permissive `g` on minor movements.

\- \*\*Before acting on any §16 training checkpoint, read the notes under §16's
&#x20; checkpoint table in the master plan\*\* — not just the table rows. It carries
&#x20; three measured baselines and the guidance for a flat Checkpoint 1 curve.

\- \*\*Checkpoint 1's bar is −2.4, NOT −224.8.\*\* §16 records three baselines;
&#x20; use the WITH-VALIDATOR row. Since Phase 4, §10's validator lives inside
&#x20; `env.step()`, so a trained agent always runs in a shielded MDP — comparing
&#x20; it against the Phase 3 unshielded number would flatter it by ~222
&#x20; reward/step of pure shield effect and make the checkpoint meaningless.
&#x20; Measured, corridor 4/3/2 seed 7:
&#x20; | | random, no validator | random, validator ON | \*\*Tier 0\*\* |
&#x20; |---|---|---|---|
&#x20; | mean reward/step | −224.8 | \*\*−2.4\*\* | \*\*+1.2\*\* |
&#x20; | worst wait | 793s | 141s | 41s |
&#x20; | starved steps | 624/718 | 543/646 | 0/627 |
&#x20; | episode | truncated | terminated | terminated |
&#x20; Beating random is now a low bar. \*\*The claim worth making is beating Tier 0's
&#x20; +1.2\*\* (§15.1) — and note both Phase 4 rows TERMINATE, i.e. clear the
&#x20; corridor, where the unshielded run never did.

\- \*\*The §9.4 reward-tail clamp is now a SECOND resort, not the first thing to
&#x20; try.\*\* With the ceiling active the worst observed wait drops from 793s to
&#x20; 141s, so the worst per-lane penalty falls from `p=321.6` to `p=3.74` and the
&#x20; value range from ~`[−800,+3]` to ~`[−25,+3]` — which was the entire concern
&#x20; motivating the clamp. Still do not pre-emptively cap `r` in
&#x20; `env/reward.py`'s `lane\_starvation\_penalty()`; if Checkpoint 1 comes back
&#x20; flat, start the diagnosis elsewhere.

\- \*\*Standing rule — every SUMO launch must also pass `--time-to-teleport 600`.\*\*
&#x20; SUMO's 300s default sits inside the starvation regime §9.4 measures, so a
&#x20; badly starved lane would have its worst vehicle silently removed. The value
&#x20; lives as `TIME\_TO\_TELEPORT\_S` in `env/psychoflow\_env.py` — import it.

\- \*\*Standing rule — every SUMO launch must pass `--waiting-time-memory 1000`.\*\*
&#x20; SUMO's default is 100s, which saturates against the 90s starvation
&#x20; threshold (§0.1) and destroys the magnitude signal §9.1's
&#x20; `starvation\_bonus` and §9.4's starvation penalty depend on. The value
&#x20; lives as `WAITING\_TIME\_MEMORY\_S` in `perception/lane\_sensor.py` — import
&#x20; it, don't retype the literal.

\- \*\*Never hardcode junction coordinates from `generate\_corridor.py`'s
&#x20; parameters.\*\* netconvert normalises networks to non-negative coords and
&#x20; shifted ours by `[150, 150]` — J1 authored at (0,0) is actually
&#x20; (150,150), J2 (450,150), J3 (750,150). It's a rigid translation, so
&#x20; distances and all count/wait-based logic (§8.1, §9.2, §9.4, §10) are
&#x20; unaffected; only absolute-x/y consumers are — §7.5 V2X `position`
&#x20; fields, and §6's `IntersectionView.jsx`, which would render every
&#x20; vehicle 150m off on both axes if built against the authored frame.
&#x20; Read positions from the net file instead:
&#x20; `sumolib.net.readNet(...).getNode(jid).getCoord()`, or the shift itself
&#x20; via `net.getLocationOffset()`. `twin/digital\_twin.py` already does this.

\- §7.6's corridor adjacency is `CORRIDOR\_ADJACENCY` in
&#x20; `twin/digital\_twin.py`, exposed in the snapshot under the exact key
&#x20; `corridor\_adjacency` as `[["J1","J2"],["J2","J3"]]`. §8.1 and §9.5
&#x20; must import that constant, not re-declare the pairs.

\- \*\*Standing rule — `enable\_safety\_validator=False` is TEST-HARNESS ONLY.\*\*
&#x20; `PsychoFlowEnv(enable\_safety\_validator=False)` disables §10's gate
&#x20; entirely: no starvation ceiling, no emergency override. It exists for
&#x20; exactly two reasons — the same-seed A/B contrast in
&#x20; `sim/run\_tier0\_episode.py` that PROVES the ceiling is what bounds the
&#x20; wait, and reproducing Phase 3's pre-validator numbers. \*\*It must never
&#x20; be reachable from any backend or control-API code path\*\* — not
&#x20; `backend/main.py`, not `backend/sim\_runner.py`, not `control\_api.py`
&#x20; (§13.1), not §14's voice intents, not a config file or env var those
&#x20; read. There is no operator-facing reason to turn off the safety
&#x20; validator, and §10's guarantee ("nothing reaches the road without
&#x20; passing through here") is only true if the off-switch is unreachable
&#x20; from anything that drives a real sim. Constructible from
&#x20; `sim/run\_tier0\_episode.py` and unit tests, nowhere else. Add to the
&#x20; §20 pre-event checklist: grep `enable\_safety\_validator` before the
&#x20; demo and confirm every hit is test scaffolding.

\- Tier 0 done-bar checks (Phase 4): `python -m safety.validator` (8 §10 unit
&#x20; scenarios / 11 assertions, no SUMO process needed — run after ANY validator
&#x20; change) and `python sim/run\_tier0\_episode.py` (B1 standalone episode, B2
&#x20; emergency latency, B3 same-seed ceiling A/B, B4 re-measured random
&#x20; baseline). Sub-runs are individually selectable: `--b1 --b2 --b3 --b4`.

\- \*\*`Tier0Config.starvation\_bonus\_scale = 20.0` is calibrated against a
&#x20; DISTRIBUTION, not a physical constant — re-measure it if `ScenarioConfig`'s
&#x20; density defaults change.\*\* `python sim/run\_tier0\_episode.py --measure-scale`
&#x20; reproduces it. Two traps that already bit once: sample only decision points
&#x20; with 2+ valid slots (MIN\_GREEN\_S=10s against a 5s interval leaves ~2 of every
&#x20; 3 steps locked to one slot, and those non-decisions drive the median to
&#x20; 0.00), and calibrate against the MAX competing score per choice point — the
&#x20; bar a starved lane's phase must actually clear — not the mean of all slots.

\- \*\*A verification run that passes while proving nothing is the failure mode
&#x20; to watch in this repo.\*\* Phase 4 hit it twice: B2's two variants silently
&#x20; collapsed onto the same scenario (and then reported a NEGATIVE latency when
&#x20; the controller happened to serve the ambulance on its own before the
&#x20; override could fire), and the SCALE measurement returned a clean-looking
&#x20; median of 0.00. Neither raised. When a test targets a specific mechanism,
&#x20; drive it with a controller that REFUSES to produce the outcome by any other
&#x20; route, and assert the mechanism actually fired — not just that the outcome
&#x20; appeared.

\- \*\*Three starvation constants, three distinct roles — do not collapse them.\*\*
&#x20; `DEFAULT\_STARVATION\_THRESHOLD\_S` = 90 (`perception/lane\_sensor.py`) is the
&#x20; SOFT line: sets `starvation\_flag`, and is the denominator `r` in both
&#x20; §9.1's Tier 0 bonus and §9.4's reward penalty. `STARVATION\_CEILING\_S` = 120
&#x20; (`safety/validator.py`) is the HARD line where §10 overrides regardless of
&#x20; what was proposed. `MIN\_GREEN\_S` = 10 (`env/psychoflow\_env.py`) is
&#x20; anti-flicker. The ceiling MUST stay above the threshold — if they were
&#x20; equal the ceiling would fire constantly and Tier 0's soft bonus would never
&#x20; get a band to act in, which is the whole "fairness-first" claim.

\- \*\*§10 precedence is emergency FIRST, then starvation ceiling\*\* — this is the
&#x20; OPPOSITE of the order in §10's pseudocode, which returns on starvation
&#x20; before ever testing for an ambulance. §10's prose ("cannot be
&#x20; delayed/blocked/deprioritized by anything else") and §9.4's weights
&#x20; (`w\_emergency=20.0` vs a starvation term reaching ~20 only at a 250s wait)
&#x20; both say emergency wins. Unit scenario 7 in `safety/validator.py` pins this
&#x20; as an assertion — if it ever fails, someone has re-read the pseudocode
&#x20; literally.

\- \*\*The emergency override is STATELESS — never add a latch or hold timer.\*\*
&#x20; `validate()` recomputes from the snapshot every step; when the ambulance
&#x20; leaves the approach lane the branch simply stops firing and normal masking
&#x20; resumes. A latch is a state machine that can jam holding a green forever if
&#x20; its release condition is missed. §11.1's clearance animation (Phase 8) does
&#x20; not change this.

\- \*\*`_green\_lanes()` and `phase\_served\_lanes()` are NOT the same map — keep both.\*\*
&#x20; `PsychoFlowEnv.\_green\_lanes()` reads the LIVE RYG state each step, so
&#x20; mid-yellow it returns the yellow phase's greens — correct for §9.4's
&#x20; emergency term, which asks "is the ambulance moving right now". 
&#x20; `phase\_served\_lanes()` is the STATIC per-episode map of which lanes slot `s`
&#x20; WOULD green — what §9.1's phase scoring and §10's override target
&#x20; resolution need. Unifying them silently breaks the reward.

\- \*\*Prediction (Phase 5, §8) commands\*\*: `python -m prediction.spillover`
&#x20; (8 hand-scored §8.1 assertions, no SUMO needed) and
&#x20; `python -m prediction.incident_impact` (4 hand-scored §8.2 assertions, no SUMO
&#x20; needed) — run after ANY change to either module. Live integration check:
&#x20; `python sim/run_prediction_episode.py` (`--c1` spillover responds to a genuinely
&#x20; growing/draining queue, read back through obs indices 10/11 via
&#x20; `obs_action_spec.describe()`; `--c2` incident impact changes when an incident is
&#x20; manually injected — the §18 Phase 5 done bar).

\- \*\*`SEVERITY_VALUE` (severity —> numeric) lives in `perception/incident_intake.py`,
&#x20; not `env/obs_action_spec.py`.\*\* Was duplicated there until Phase 5, when
&#x20; `prediction/incident_impact.py` needed the same mapping; relocated to its §7.3
&#x20; home and both modules import it from there. Import from
&#x20; `perception.incident_intake`, don't redefine it a third time.

\- \*\*`prediction/spillover.py`'s `SpilloverPredictor` is STATEFUL and MUST be reset
&#x20; every episode.\*\* It keeps the previous twin snapshot to compute a queue growth
&#x20; rate (§8.1's "current outflow rate" proxy). `PsychoFlowEnv.reset()` calls
&#x20; `spillover_predictor.reset()` right after `twin.reset()` — if you ever construct
&#x20; a `SpilloverPredictor` outside the env (a standalone script, a notebook), reset it
&#x20; yourself at the start of every new episode or its first forecast will compute a
&#x20; rate against a different episode's last snapshot.

\- \*\*`LINK_APPROACH = "west"` in `prediction/spillover.py` is a hardcoded fact of the
&#x20; locked linear W—>E corridor (§0.1), not a general direction inference.\*\* On
&#x20; this corridor, the lane group fed by a junction's upstream neighbor is always
&#x20; tagged `approach == "west"` at both J2 (from J1) and J3 (from J2) — verified
&#x20; geometrically in Phase 2. If the corridor topology ever changes, this breaks
&#x20; silently; same risk class as `CORRIDOR_ADJACENCY` itself.

\- \*\*Observation indices 10/11 (§9.2's `JS_SPILLOVER_DELTA` / `JS_SPILLOVER_CONFIDENCE`)
&#x20; hold exactly ONE forecast per junction row, keyed by that junction as
&#x20; `to_junction`.\*\* J1 has no upstream neighbor on this corridor and is never a
&#x20; `to_junction`, so its slot is always `(0.0, 0.0)` by omission — not a bug. J2's
&#x20; own downstream impact on J3 is reported on J3's row, not duplicated on J2's. See
&#x20; `prediction/spillover.py`'s module docstring for the full resolution.

\- \*\*`PsychoFlowEnv(spillover_predictor=None)` is legal, not an error — it warns.\*\*
&#x20; Smoke tests, random-action rollouts and unit tests may construct the env without
&#x20; a predictor (§3's HARD GATE only restricts KEPT training runs). The constructor
&#x20; emits a `UserWarning` when this happens, as a free tripwire; the actual
&#x20; enforcement for training is the Phase 6 prerequisite recorded under §3 above.

\- \*\*CLOSED (Phase 6/7, opened Stage 1, RESOLVED 2026-08-17): deterministic-vs-
&#x20; stochastic reward gap — diagnosed, mechanism identified, track closed. No
&#x20; further investigation needed.\*\* Do NOT reopen this as a watch-item; the
&#x20; original `ent_coef=0.0` / per-head-argmax hypothesis was never confirmed and
&#x20; is now superseded by a measured mechanism. History: Stage 1 recorded
&#x20; stochastic BEATING deterministic (1.297 vs 1.029, gap +0.268); at Stage 5 the
&#x20; sign inverted. Diagnosed against `psychoflow_stage5_51624_steps_final.zip`
&#x20; (`graph_attention`) with \*\*74 episodes across 4 seeds\*\* — seed 7 n=40, seeds
&#x20; 1/3/42 n=10 each. Raw data: `training/checkpoints/_sweeps/det_stoch_diag.json`.
&#x20; \*\*(1) The gap is ROBUST and the mechanism is SWITCH FREQUENCY.\*\* Deterministic
&#x20; wins all 4 seeds — gaps +0.2372 / +0.2678 / +0.3627 / +0.3359, \*\*mean
&#x20; +0.3009\*\*. Deterministic switches \*\*18-19% less\*\* (0.642-0.657 vs 0.793-0.796
&#x20; switches/step, extremely tight across seeds). Fewer switches = less green time
&#x20; lost to yellow, which roughly \*\*halves the integrated starvation penalty\*\*
&#x20; (0.177-0.221 vs 0.400-0.445 per step) and separately saves switch penalty
&#x20; (0.321-0.328 vs 0.396-0.398). Attribution of the gap: starvation \*\*67-80%\*\*,
&#x20; switch \*\*21-30%\*\*, throughput −10% to +9%. Term decomposition reconstructs
&#x20; each mean reward exactly, so this is arithmetic, not inference.
&#x20; \*\*(2) The throughput hypothesis was TESTED and RULED OUT.\*\* All 74 episodes
&#x20; clear \*\*exactly 4668\*\* vehicles, so `throughput_bonus` summed is identical in
&#x20; every run; it only enters per-step reward via episode length, contributes
&#x20; near-zero, and \*\*changes sign across seeds\*\*. On seed 7 it works AGAINST
&#x20; deterministic. It is not a source of the gap.
&#x20; \*\*(3) WHY reward and `worst_wait` disagree — the wait distributions CROSS.\*\*
&#x20; Deterministic wins the body, stochastic wins the extreme tail:
&#x20; | percentile | DET | STO | winner |
&#x20; |---|---|---|---|
&#x20; | p50 | \*\*25.0s\*\* | 37.7s | DET, −34% |
&#x20; | p90 | \*\*36.8s\*\* | 57.2s | DET, −36% |
&#x20; | p99 | 95.3s | \*\*77.8s\*\* | STO |
&#x20; | max | 123.3s | \*\*87.3s\*\* | STO |
&#x20; The crossover sits between p90 and p99. §9.4's reward INTEGRATES the body
&#x20; (its `max` term is a max across lanes WITHIN a step, then averaged over
&#x20; ~640 steps); `worst_wait`/`starved_pct` read only the episode-level EXTREME.
&#x20; They are different statistics of one distribution, not contradictory quality
&#x20; signals — and a rare excursion is diluted to near-invisibility in the mean
&#x20; (p(121s)≈2.28 vs p(87s)≈0.94 on ~7 of 640 steps ≈ 0.015 reward/step, against
&#x20; the 0.189/step deterministic saves on typical waits — a ~12:1 payoff, so the
&#x20; policy is correctly optimising the reward as written).
&#x20; \*\*(4) CORRECTION — this is SCENARIO-DEPENDENT, not universal. The earlier
&#x20; "unresolved tension" framing overstated it.\*\* On 3 of 4 seeds (7/1/42)
&#x20; deterministic crosses `STARVATION_CEILING_S` exactly \*\*once per episode\*\*
&#x20; (worst_wait 121/124/125s, 1.09-1.27% starved, exactly \*\*1\*\*
&#x20; `starvation_ceiling` override each). On \*\*seed 3 the disagreement REVERSES\*\*:
&#x20; deterministic wins BOTH metrics cleanly — worst_wait \*\*54.0s / 0.00% starved
&#x20; / ZERO overrides\*\* (p99 only 47.0s, no excursion at all) versus stochastic's
&#x20; 89.5s / 0.82%. So the "worse tail" is a single once-per-episode excursion on
&#x20; SOME scenarios, not a property of the greedy policy. Do not state it as one.
&#x20; \*\*(5) SCOPE LIMIT — says NOTHING about emergency behaviour.\*\* The test ran
&#x20; `ScenarioConfig(lane_counts=(4,3,2))`, i.e. \*\*`spawn_emergencies=False`\*\*;
&#x20; `emergency_per_step` was \*\*0.0000 in all 74 episodes\*\*. Nothing here bears on
&#x20; §16's failed Stage 4 emergency-priority checkpoint, which remains open and
&#x20; unremediated on its own terms.
&#x20; \*\*Methodology worth reusing\*\* (both guarantees verified, not assumed): model
&#x20; loaded ONCE and looped (a fresh `.load()` reseeds torch's RNG and silently
&#x20; makes "stochastic" runs identical), and the scenario PINNED per seed via
&#x20; `env.reset(seed=s)` so action sampling is the only variable — without the pin
&#x20; a stochastic sample mixes policy noise with scenario-draw noise and no
&#x20; per-term attribution is possible. Seed 7's deterministic run reproduced the
&#x20; recorded 1.285947 / 121.0s / 1.09% EXACTLY, and two deterministic reps were
&#x20; bit-identical, confirming the harness measures the originally-logged
&#x20; phenomenon rather than a similar-looking different draw.

\- \*\*CORRECTION (Phase 0, 2026-08-18) — the `ovr` column in the recovered
&#x20; 12-checkpoint matrix (`training/checkpoints/_sweeps/phase0_emergency.json`)
&#x20; is dominated by `starvation_ceiling` overrides, NOT `emergency_override`,
&#x20; and must NOT be read as an emergency-handling signal.\*\*
&#x20; `training/scripts/phase0_emergency.py` counted `len(info["safety_overrides"])`,
&#x20; which lumps §10's two rules together. That the column is starvation-dominated
&#x20; is visible in the recorded numbers themselves: `ovr` ranges 22-383 per
&#x20; 3-seed triple while `ambJstep` (the hard ceiling on how many emergency
&#x20; overrides are even possible, since at most one can fire per junction per
&#x20; ambulance-present step) ranges only 23-34 in the same triples. So on
&#x20; every row above ~34 the majority of counted overrides provably cannot be
&#x20; emergency ones — 349+ of the 383 row, for instance. The one row where
&#x20; the column could in principle be emergency-dominated is 51624 (ovr=22,
&#x20; ambJstep=25); everywhere else the arithmetic settles it.
&#x20; `training/scripts/phase0_baselines.py` splits them via the `rule` key of
&#x20; `OverrideRecord.to_dict()` (`RULE_EMERGENCY` / `RULE_STARVATION`, both in
&#x20; `safety/validator.py`) and reports `ovrE`/`ovrS` separately. \*\*Any future
&#x20; emergency-quality claim must cite the SPLIT figures, not the combined
&#x20; count\*\* — and note this is a different measurement again from
&#x20; `evaluate_stage.py --emergency-recheck`'s per-episode binary
&#x20; override-fired rate (the 15/15, 11/15, 13/15 figures), which counts
&#x20; EPISODES not overrides. Three distinct numbers, easily conflated.

\- \*\*WATCH-ITEM (Phase 6, added Stage 2): narrow-middle-bottleneck adaptivity
&#x20; gap.\*\* Stage 2's consistency sweep (5 combos × seeds `{1,7,42}` against
&#x20; `psychoflow_stage2_51200_steps_final.zip`) found `(4,2,4)` as a clear
&#x20; outlier (mean reward 0.46 vs. 0.93-1.33 for the other 4 combos; starved%
&#x20; up to 37.5% vs. 0-6.3%) — see BUILD_LOG.md's Phase 6 Stage 2 entry for the
&#x20; full diagnostic chain. Ruled out: topology-luck (checkpoint's general
&#x20; upward trend is not topology-driven, r²=0.63%), inherent difficulty
&#x20; (Tier 0 solves the identical combo/seeds with 0% starved on all 3 seeds),
&#x20; and undersampling as primary cause (`(4,2,4)` had MORE Burst-B exposure
&#x20; than `(4,3,2)`/`(4,4,4)`, both of which score far better). Identified
&#x20; mechanism instead: the policy's per-junction phase-time serving ratio at
&#x20; the 2-lane bottleneck junction is nearly seed-invariant (~24%/76% split
&#x20; regardless of seed) even though the three seeds draw genuinely different
&#x20; and oppositely-skewed demand (seed 7: 88%N/12%S vs. seeds 1/42's mild
&#x20; south-skew) — the policy does not adapt its serving ratio to which
&#x20; approach is actually loaded. Re-check at every future stage checkpoint
&#x20; (Stage 3/4/5), specifically on 2-lane-bottleneck-shaped topologies under
&#x20; skewed demand. Flagged as POSSIBLY sharing a root cause with the
&#x20; determinism/per-head WATCH-ITEM above — both are, at heart, "policy
&#x20; behavior not sufficiently sensitive to actual state" (per-head argmax
&#x20; insensitive to cross-junction coordination; per-junction allocation
&#x20; insensitive to which approach is loaded). Escalate before the Stage 5
&#x20; MARL checkpoint if either gap persists.
&#x20; \*\*UPDATE (Stage 4, 2026-08-15) — `(4,2,4)` itself has resolved; the gap has
&#x20; NARROWED to a confirmed `j1=3`-specific vulnerability, not a shape-wide
&#x20; effect.\*\* Full diagnostic chain against `psychoflow_stage4_153600_steps_final.zip`:
&#x20; (1) `(4,2,4)` now scores worst_wait 58.7s mean, 0.0% starved on ALL 3 seeds
&#x20; — down from Stage 3's 88.0s/0.4% and Stage 2's original 88.0s/19.3%; the
&#x20; originally-flagged example no longer shows the gap. (2) A 6-combo sweep
&#x20; (`(4,2,4)`, `(3,2,3)`, `(3,2,4)`, `(4,2,3)` + controls `(2,2,2)`/`(4,4,4)`,
&#x20; seeds `{1,7,42}`) found `(3,2,3)` and `(3,2,4)` spiking on seed 1 (119.0s/
&#x20; 120.0s) while `(4,2,4)`/`(4,2,3)` stayed normal — split along `j1=3` vs
&#x20; `j1=4`. (3) 5 more seeds each on `(3,2,3)`/`(3,2,4)` (2,3,5,10,13):
&#x20; `(3,2,3)` seed 3 REPEATED the spike almost exactly (119.0s/0.95%, vs
&#x20; seed 1's 119.0s/1.0%) — 2/8 seeds now, confirmed repeatable, not a one-off;
&#x20; `(3,2,4)`'s other 4 new seeds all stayed normal (61-68s) — still only 1/8
&#x20; seeds for that combo. (4) Ceiling-masking hypothesis TESTED AND REJECTED:
&#x20; re-ran the three spike cases (`(3,2,3)` seeds 1/3, `(3,2,4)` seed 1) with
&#x20; `enable_safety_validator=False` — worst_wait identical to the shielded
&#x20; runs in all three (119.0/119.0/120.0s, delta +0.0s), and ZERO §10
&#x20; overrides fired in any shielded run. The 119-120s figures are the
&#x20; policy's genuine, natural worst case, not a ceiling capping something
&#x20; worse. (5) TARGETED same-seed test, not blind sampling: ran `(4,2,3)`/
&#x20; `(4,2,4)` on the SAME seeds 1 and 3 that spike `(3,2,3)` — both stayed
&#x20; firmly normal (56-61s, 0% starved). This confirms the `j1=3` vs `j1=4`
&#x20; split causally, not just correlationally: identical demand draws produce
&#x20; opposite outcomes depending on J1's own lane count.
&#x20; \*\*DOWNGRADED 2026-08-28 — the `j1=3` framing was a SAMPLING ARTIFACT.
&#x20; Read the correction at the end of this bullet BEFORE acting on the
&#x20; "FINAL READ" paragraph below, which is retained for history but is no
&#x20; longer the current reading.\*\*
&#x20; \*\*FINAL READ \[SUPERSEDED\]: CONFIRMED, narrowly scoped to `(3,2,3)` and
&#x20; `(3,2,4)` specifically — do NOT generalize to "`j1=3` combos" as a category.\*\*
&#x20; The same-seed causal test (identical demand draw, opposite outcome on
&#x20; `j1=3` vs `j1=4`) confirms this is a genuine j1-capacity effect for
&#x20; THESE TWO combos, not coincidence, not an isolated seed-1 draw, and not
&#x20; ceiling-masking (§10's validator fires zero times in the affected runs —
&#x20; the policy's own behavior produces the ~119-120s wait unassisted). Only
&#x20; `(3,2,3)` and `(3,2,4)` have been sampled at `j1=3`; no other `j1=3`
&#x20; combo has been tested, so this is NOT evidence of a `j1=3`-wide pattern
&#x20; — treat it as two specific data points, not a category. Within those
&#x20; two: `(3,2,3)` is CONFIRMED REPEATABLE (2/8 seeds, ~25% rate, both
&#x20; landing at ~119s); `(3,2,4)` is NOT YET CONFIRMED repeatable (1/8 seeds
&#x20; — could still be an isolated draw). `(4,2,4)`'s original Stage 2 finding
&#x20; has RESOLVED under this checkpoint — 58.7s/0.0% starved on all 3 seeds,
&#x20; down from Stage 2's 88.0s/19.3% — a real update, not residual
&#x20; background; `(4,2,3)` was never actually a problem. Mechanism still not
&#x20; identified beyond "J1's own capacity matters for these two combos" —
&#x20; likely the same class of issue as the original Stage 2 diagnosis (fixed
&#x20; serving ratio not adapting to skewed demand), narrowed to specific
&#x20; topology/seed pairs rather than one combo or a whole category.
&#x20; \*\*RESOLVED-IN-PART (Stage 5, 2026-08-16) — measurably improved under
&#x20; `graph_attention`, but NOT fully closed.\*\* The Stage 5 A/B re-check settles
&#x20; what this item was flagged to test. Measured on the 12 paired combo+seed
&#x20; runs (`(3,2,3)`/`(3,2,4)` × seeds `{1,3,7,42}` + `j1=4` controls):
&#x20; `graph_attention` mean `starved_pct` \*\*1.64%\*\* (range 1.11-3.54%) vs
&#x20; `shared_policy` \*\*86.60%\*\* (range 81.23-89.86%) — attention better on
&#x20; \*\*12/12\*\* runs, a ~53x difference, with `shared_policy` holding a 3.1%
&#x20; budget advantage. So neighbour-aware attention DOES address the
&#x20; cross-junction demand-skew failure this item describes, which is the
&#x20; §9.5 claim it was meant to test. NOT fully closed, for two honest
&#x20; reasons: (1) `worst_wait` is still elevated on these combos (123.1s mean,
&#x20; above Stage 4's 56-120s range) — though note §10's ceiling caps
&#x20; `worst_wait` regardless of policy quality, which is exactly why it
&#x20; separated the modes by only ~4s while `starved_pct` separated them by
&#x20; 53x; \*\*use `starved_pct`, not `worst_wait`, for this comparison\*\*;
&#x20; (2) Stage 5 has only ~1/3 of Stage 4's budget, so the absolute numbers
&#x20; are not yet a fair test against the single-agent baseline. Re-measure if
&#x20; Stage 5 is ever trained to Stage 4's budget. \*\*Correction on record:\*\* an
&#x20; earlier read called `graph_attention`'s j1-recheck a "uniform regression";
&#x20; that was an artifact of comparing against Stage 4's mismatched budget AND
&#x20; architecture simultaneously, not a real finding.
&#x20; \*\*ESSENTIAL QUALIFIER (2026-08-16) — the gap to SINGLE-AGENT is NOT
&#x20; closed. Do not read the 12/12 result as "MARL solved this".\*\* Stage 4
&#x20; single-agent was subsequently run on the EXACT same 12 (combo, seed)
&#x20; pairs. Three-way means: \*\*Stage 4 = 75.2s / 0.24% starved\*\*,
&#x20; `graph_attention` = 123.1s / 1.64%, `shared_policy` = 127.1s / 86.60%.
&#x20; Stage 4 is clean (0.00% starved) on 8 of the 12 pairs. \*\*Single-agent
&#x20; beats BOTH MARL modes on both metrics.\*\* What stands: attention beats
&#x20; shared_policy decisively, so §9.5's flip decision is unaffected. What
&#x20; does NOT stand: any claim the `j1=3` gap is solved. The honest framing is
&#x20; \*\*"attention meaningfully narrowed the gap between ARCHITECTURES, not the
&#x20; gap to single-agent performance."\*\*
&#x20; \*\*BUDGET HYPOTHESIS — TESTED AND REFUTED (2026-08-28). This line previously
&#x20; read "testing it needs a Stage 5 mode trained to ~153,600, which has not
&#x20; been done. Do not assert it." That test HAS been done and the hypothesis is
&#x20; dead.\*\* `graph_attention` WAS trained to 154,024 (budget-matched to Stage
&#x20; 4's 153,600 within 424 steps). It did not close the gap — it made things
&#x20; dramatically worse: 25.82% starved vs Stage 4's 0.08% on the 4a bake-off,
&#x20; `ovrS` 15.75 vs 0.00. So the residual gap is NOT explained by training
&#x20; budget; more budget on a replayed scenario set is actively harmful. The
&#x20; live candidate is DATA DIVERSITY (D1 tests it) — see BUILD_LOG's
&#x20; 2026-08-18 burst-replay entry and its 2026-08-28 Phase 0 close-out.
&#x20; \*\*Note this line was already stale on 2026-08-18\*\*, when the Burst D
&#x20; parity run was recorded; it went uncorrected for ten days.
&#x20; Original follow-up note, superseded by the above:
&#x20; re-check `(3,2,3)`/`(3,2,4)` specifically at the Stage 5 MARL checkpoint —
&#x20; this is exactly the kind of localized, demand-skew-sensitive gap
&#x20; graph-attention (§9.5, neighbor-aware) should plausibly help with over
&#x20; the shared-policy fallback, making it a real test of whether attention
&#x20; earns its complexity, not just a box to check. Not investigating further
&#x20; before Stage 5 — bounded to the two checks above by design.
&#x20; \*\*CORRECTION (2026-08-28, Stage 4 audit) — the `j1=3` FRAMING IS A
&#x20; SAMPLING ARTIFACT AND IS WITHDRAWN. The measurements above all stand;
&#x20; the CATEGORY they were read as evidence for does not.\*\*
&#x20; What the record said: "`(3,2,3)` is CONFIRMED REPEATABLE (2/8 seeds)"
&#x20; and "a confirmed `j1=3`-specific vulnerability". What refutes it: a
&#x20; sweep of \*\*ALL 27 lane-count combos × seeds {1,7,42} = 81 episodes\*\*
&#x20; against `psychoflow_stage4_153600_steps_final.zip`
&#x20; (`training/scripts/stage4_scrutiny.py --topology`, raw data
&#x20; `_sweeps/stage4_topology_*.json`). Episodes containing at least one
&#x20; starvation event, split by `j1`:
&#x20; | `j1`=2 | `j1`=3 | `j1`=4 |
&#x20; |---|---|---|
&#x20; | \*\*4/27\*\* | 3/27 | 2/27 |
&#x20; \*\*`j1=3` is not the worst — `j1=2` is.\*\* Why the earlier reading went
&#x20; wrong is structural, not arithmetic: every prior test of this item ran
&#x20; through `evaluate_stage.py --j1-recheck`, whose matrix is four combos
&#x20; (`(3,2,3)`/`(3,2,4)` + `j1=4` controls) chosen because `graph_attention`
&#x20; struggled on them. That matrix contains no `j1=2` combo at all, so it
&#x20; \*\*could not have detected a `j1=2` effect\*\* however strong. The
&#x20; 27-combo sweep is the first measurement with the coverage to answer the
&#x20; question. Independently corroborated by test-d0's 2026-08-28 docs audit,
&#x20; which flagged the same coverage gap in the harness from the other end.
&#x20; \*\*What is actually there instead — a MILD gradient in `j3`, not `j1`:\*\*
&#x20; mean p99 of the across-lane max wait runs 41.7s / 54.1s / 63.0s for
&#x20; `j3` = 2/3/4, and event-episodes 1/3/5. All 9 event-episodes have
&#x20; `j3 >= j1` and 0/27 `j3 < j1` episodes have any (Fisher p=0.0257) — but
&#x20; \*\*do not quote that split as a clean interaction\*\*: stratifying within
&#x20; each `j3` level shows the `j1` effect holds at `j3`∈{2,3} and vanishes
&#x20; at `j3`=4, so it is largely the `j3` effect repackaged, at n=9 per cell.
&#x20; \*\*Magnitude is small and must be stated with the finding\*\*: worst combo
&#x20; `(4,3,4)` is 0.67 events/episode, 0.58% starved, p99 75.7s against a 90s
&#x20; threshold; 81/81 episodes terminate with all 4668 vehicles arrived; only
&#x20; 3/81 fire any §10 override. \*\*Status: NOT a confirmed hard finding —
&#x20; a mild directional gradient, ~1 event/episode, thin per-cell n.\*\* Do not
&#x20; re-promote it to a "confirmed vulnerability" without a wider seed set.
&#x20; \*\*The harness reproduces the historical record exactly\*\*, so this
&#x20; corrects the INTERPRETATION and not the data: all 8
&#x20; `STAGE4_J1_REFERENCE` `worst_wait` values reproduce with delta +0.0, and
&#x20; the 4a `stage4_153600 (4,3,2)` seed-7 row reproduces bit-for-bit on all
&#x20; 9 fields.

\- \*\*Stage 4 @153,600 has NO weak topology shape, does NOT collapse late,
&#x20; and deterministic is the right deployment setting (2026-08-28 audit,
&#x20; 290 episodes).\*\* Ran because Stage 4 became the deployed checkpoint on a
&#x20; 4-combo, density-pinned, no-emergency grid and had never been stressed.
&#x20; Commands: `python -m training.scripts.stage4_scrutiny --topology |
&#x20; --tier0 | --curve | --density | --emergency | --detstoch` (shardable via
&#x20; `--shard i/n`; `--ckpt` swaps the checkpoint under test).
&#x20; \*\*(a) Topology\*\*: all 27 combos clean — see the correction above.
&#x20; \*\*(b) Density\*\* {0.7,1.0,1.3}: events 0.00/0.11/0.44 per episode, p99
&#x20; 44.2/46.4/53.7s, \*\*ovrS=0 at every level\*\*, 27/27 terminate. The demo
&#x20; corridor `(4,3,2)` is 0.00 events at ALL three densities.
&#x20; \*\*(c) Emergencies ON\*\* (`spawn_emergencies=True`, which 4a pinned off):
&#x20; 11/12 cells zero events, 12/12 terminate. Fairness does not degrade when
&#x20; emergencies are enabled.
&#x20; \*\*(d) NO post-peak collapse\*\* — unlike `graph_attention` after 51,624.
&#x20; Stage 4's own curve 107,400 -> 153,600 trends UP in reward (1.226 ->
&#x20; 1.354). A full 81-episode paired re-run of `137640` against the deployed
&#x20; `153600` gives 137640 better on `starved_pct`/p90/p99/`worst_wait`/reward
&#x20; and worse on mean event count, \*\*paired sign test p=0.58 — not
&#x20; significant\*\*. No evidence the deployment choice is wrong; no
&#x20; established better alternative. Do not swap on this data alone.
&#x20; \*\*(e) Deterministic beats stochastic on BOTH reward AND tails\*\* (reward
&#x20; 1.358 vs 1.258; p99 33.7s vs 45.5s, model loaded once and looped). So
&#x20; \*\*Stage 4 has no reward/fairness tension\*\* — the opposite of the
&#x20; `graph_attention` det/stoch entry, where the two disagreed. The
&#x20; `deterministic=True` deployment setting is correct on both axes.
&#x20; \*\*(f) NOT a knife-edge\*\* — the inverse of the `worst_wait` threshold
&#x20; artifact was specifically hunted (a policy scoring a clean 0.00 only
&#x20; because its waits sit just UNDER 90s). Median p99 is 50s, only 8/81
&#x20; episodes reach p99 >= 80s, and \*\*0.106% of steps\*\* fall in the 70-90s
&#x20; approach band. The near-zero starvation counts reflect genuine margin,
&#x20; not luck. `RichProbe` in `stage4_scrutiny.py` carries this
&#x20; instrumentation; keep reporting it alongside any threshold metric.

\- \*\*BOUNDED CAVEAT on 4a's "matches Tier 0's fairness while clearing
&#x20; traffic better" — TRUE on 4a's four combos, FALSE on six others. Does
&#x20; NOT reopen the deployment decision.\*\* Tier 0 was run on the six combos
&#x20; where Stage 4 showed the most starvation, same seeds, identical
&#x20; scenarios (`--tier0`, raw data `_sweeps/stage4_tier0_control.json`):
&#x20; | | Stage 4 | Tier 0 |
&#x20; |---|---|---|
&#x20; | episodes with an event | \*\*7/18\*\* | \*\*0/18\*\* |
&#x20; | mean events/episode | 0.50 | \*\*0.00\*\* |
&#x20; | mean p99 wait | 67.0s | \*\*35.4s\*\* |
&#x20; | mean reward | \*\*1.3450\*\* | 1.2042 |
&#x20; So on `(2,3,2)`, `(2,3,3)`, `(2,4,3)`, `(3,2,4)`, `(3,3,4)`, `(4,3,4)`
&#x20; \*\*Tier 0 is strictly fairer and Stage 4 strictly faster\*\* — the two
&#x20; metrics genuinely disagree, and "matches Tier 0's fairness" is too
&#x20; strong outside 4a's own matrix. This ALSO settles the "inherently hard
&#x20; topology vs policy gap" question the same way Stage 2 settled `(4,2,4)`:
&#x20; Tier 0 solves all six cleanly, so these are \*\*not\*\* hard topologies —
&#x20; it is a real, mild policy gap. \*\*Why it does not reopen 4a:\*\* Stage 4
&#x20; still wins reward on all six, Tier 0 was already fairer than Stage 4 on
&#x20; 4a's own dispersion metrics (0.00 events, `wait_var` 50.7 vs 72.2), and
&#x20; the deployment case rests on the demo corridor where Stage 4 is 0.00
&#x20; events. \*\*Tier 0 remains an extremely strong fairness floor\*\* — say so
&#x20; plainly rather than implying the learned policy dominates it.

\- \*\*CONTAMINATION: `phase0_baselines.py` EVALUATES ON TRAINING SCENARIOS.
&#x20; Every proposal-quality figure it produced is partly a memorisation
&#x20; score. Corrected 2026-08-28; this is the real-world proof that §15.4's
&#x20; held-out set must actually be BUILT before Phase 12.\*\*
&#x20; \*\*The mechanism:\*\* `phase0_baselines.py` runs `STAGES[4]` — the full
&#x20; TRAINING config — and calls `reset(seed=s)`, which does
&#x20; `self._rng = random.Random(s)`. Training used `--seed 7` with the same
&#x20; config, so \*\*eval seed 7 reproduces TRAINING EPISODE 1 exactly\*\* — same
&#x20; lane counts, both density multipliers to 16 digits, same ambulance route
&#x20; and depart time. Verified without SUMO (`_draw_scenario()` is rng-pure)
&#x20; by `python -m training.scripts.stage4_contamination`. \*\*Both Stage 4 and
&#x20; `graph_attention` are hit, on the same seed\*\* — they share a scenario
&#x20; sequence (the 2026-08-18 burst-replay entry). Seed 7 supplied \*\*11 of
&#x20; the 26 decidable steps (42%)\*\* behind the recorded 0.885.
&#x20; \*\*Re-measured on 12 seeds, screened, held-out only\*\*
&#x20; (`python -m training.scripts.stage4_proposal --checkpoint ... --monitor-dir ...`;
&#x20; raw data `_sweeps/stage4_proposal.json`, `ga154_proposal.json`,
&#x20; `ga102_proposal.json`):
&#x20; | checkpoint | recorded (contaminated, 3 seeds) | \*\*held-out (11 seeds)\*\* | lift over own chance |
&#x20; |---|---|---|---|
&#x20; | Stage 4 @153,600 | 0.885 (26 decidable) | \*\*0.8298\*\* (47) | +0.1915 |
&#x20; | `graph_attention` @154,024 | 0.778 (27) | \*\*0.7656\*\* (64) | +0.1641 |
&#x20; | `graph_attention` @102,824 | 0.767 | \*\*0.7143\*\* (56) | +0.0893 |
&#x20; \*\*What survives:\*\* both beat the matched random control — Stage 4
&#x20; z=+3.388 p=0.0007, `graph_attention` z=+2.904 p=0.0037 — so "meaningfully
&#x20; above chance" holds for both, and the random control's own lift is
&#x20; −0.0155 ≈ 0, revalidating the analytic chance baseline.
&#x20; \*\*What does NOT survive — the Stage-4-vs-`graph_attention` GAP.\*\*
&#x20; Recorded +0.107; clean \*\*+0.0642, z=+0.824, p=0.4099 — NOT
&#x20; SIGNIFICANT.\*\* \*\*Stop citing "0.885 vs 0.778" as though it separates the
&#x20; two checkpoints\*\*; at this n it cannot. 4a's deployment decision is
&#x20; unaffected — it rested on the fairness grid, not on this metric.
&#x20; \*\*Contamination was NOT symmetric in effect, so do not call the residual
&#x20; gap "conservative".\*\* Lift on the contaminated seed minus lift held-out:
&#x20; Stage 4 \*\*+0.2176\*\*, `graph_attention` \*\*+0.0264\*\* — contamination
&#x20; inflated Stage 4 roughly 8x more, i.e. the recorded gap was inflated in
&#x20; Stage 4's favour. (The containment premise itself IS confirmed by exact
&#x20; tuple comparison — Stage 4's 64 training scenarios are a strict subset of
&#x20; `graph_attention`'s 81 — but containment says nothing about which policy
&#x20; benefits more, and measurement says Stage 4 did.)
&#x20; \*\*Second-order caveat, worth keeping:\*\* `proposal_quality`'s denominator
&#x20; is ENDOGENOUS — a policy that clears an ambulance fast collects FEWER
&#x20; decidable steps. Stage 4 drew 1-14 per episode where the random control
&#x20; drew up to 36 on identical scenarios, so pooled quality weights episodes
&#x20; by how badly the policy did. The analytic `chance_quality` handles this
&#x20; (it is computed at the policy's own visited states); raw pooled
&#x20; comparisons across conditions do not. Report pooled AND mean-of-seeds;
&#x20; violent disagreement between them is a sample-size symptom.

\- \*\*WATCH-ITEM (Phase 6, added Stage 3): `(2,4,2)` density-sensitive
&#x20; degradation at high load.\*\* Distinct from the `(4,2,4)` bottleneck item
&#x20; above — different topology shape (`(2,4,2)` is wide-middle/narrow-ends,
&#x20; not narrow-middle/wide-ends), and only triggers at the highest swept
&#x20; density level. Stage 3's density sweep (5 combos × seeds `{1,7,42}` ×
&#x20; density `{0.7,1.0,1.3}` against `psychoflow_stage3_102400_steps_final.zip`)
&#x20; found `(2,4,2)` clean at 0.7×/1.0× (worst_wait ~53-56s, 0.0% starved, in
&#x20; line with every other well-behaved combo) but degrading specifically at
&#x20; 1.3× (worst_wait mean 83.0s, max 99.0s, starved% mean 0.2%). NOT yet
&#x20; diagnosed — no per-seed mechanism trace has been run for this combo the
&#x20; way `(4,2,4)` got in Stage 2. Re-check at Stage 4/5 checkpoints alongside
&#x20; the other two watch-items; escalate before Stage 5 MARL if it persists
&#x20; or if further combos show the same high-density-only pattern.
&#x20; \*\*RE-MEASURED at Stage 5 (2026-08-16) — SUBSTANTIALLY MITIGATED under
&#x20; `graph_attention`, catastrophic under `shared_policy`.\*\* Stage 3's own
&#x20; density-sweep methodology ((2,4,2) × density `{0.7,1.0,1.3}` × seeds
&#x20; `{1,7,42}`, 9 runs per mode) against both Stage 5 finals:
&#x20; `graph_attention` \*\*0.80% / 0.90% / 1.16%\*\* starved (worst_wait 106.3 /
&#x20; 96.0 / 124.0s) — the load-triggered pattern is still visible but small;
&#x20; `shared_policy` \*\*56.38% / 80.54% / 95.76%\*\* (worst_wait 125.3 / 127.0 /
&#x20; 128.3s) — monotonic with load, and at 1.3x it has a starved lane for
&#x20; essentially the entire episode. This is a SECOND independent axis on
&#x20; which neighbour-aware attention helps, and being a load-SCALING effect
&#x20; it is what §9.5 would predict. Status: substantially mitigated for
&#x20; `graph_attention` (the deployed mode); still open for `shared_policy`,
&#x20; which is moot in practice but recorded for completeness.
&#x20; \*\*Observed again (Stage 5, graph_attention cold-start Burst A, episode 8):\*\*
&#x20; the worst episode of that burst (mean_reward −3.6081) drew `(2,4,2)` at the
&#x20; HIGHEST corridor density of the run (`density_mult_corridor=1.1653` vs a
&#x20; 0.9298 rest-of-burst mean) — same combo, same high-density direction as the
&#x20; original Stage 3 finding, and NOT explained by the other candidates (not a
&#x20; flagged `j1=3` combo, not a narrow-middle bottleneck, not the earliest
&#x20; ambulance — that was episode 11, which scored +0.7337). Independent
&#x20; corroboration, but n=1 and confounded with cold-start noise, so it does not
&#x20; upgrade the item's status on its own. Logged, not investigated further.

\- \*\*STANDING GOTCHA: `mean_reward` is NOT a valid axis for CROSS-DENSITY
&#x20; comparison — use `starved_pct` / `starvation_events_count` instead.\*\*
&#x20; \*\*AMENDED 2026-08-28, two ways — read both before applying this rule.\*\*
&#x20; (i) This bullet originally said "always use `worst_wait`/`starved_pct`
&#x20; instead". \*\*`worst_wait` is no longer an acceptable alternative\*\* — it is
&#x20; itself a saturated statistic (see the `worst_wait` standing rule below, and
&#x20; master plan §15.2). Recommending it here was a contradiction introduced by
&#x20; the 4b metric change; `starved_pct` and `starvation_events_count` are the
&#x20; valid alternatives.
&#x20; (ii) \*\*The prohibition is CROSS-DENSITY specifically, not blanket.\*\* When
&#x20; density is PINNED and vehicles-arrived is identical across the compared
&#x20; runs, `throughput_bonus` is held constant and `mean_reward` IS a legitimate
&#x20; discriminator. That is exactly the 4a bake-off's condition — density pinned
&#x20; at 1.0 and `arrived = 4668` in all 48 episodes — so its reward column is
&#x20; valid and this gotcha does not invalidate it. Check the condition before
&#x20; invoking the rule, rather than discarding every reward comparison.
&#x20; `env/reward.py`'s
&#x20; `throughput_bonus` term scales with vehicles arrived, which itself scales
&#x20; with traffic density, so EVERY combo's mean_reward rises predictably with
&#x20; density level regardless of policy quality (Stage 3's sweep: `(4,3,2)`
&#x20; alone went 0.698 → 1.192 → 1.730 across 0.7×/1.0×/1.3× — a near-uniform
&#x20; step, same shape for every combo). This makes an outlier's mean_reward
&#x20; LOOK like it's closing across density levels even when the underlying
&#x20; problem (elevated worst_wait, nonzero starved_pct) hasn't moved at all —
&#x20; exactly what happened comparing `(4,2,4)`'s Stage 2 sweep (mean_reward
&#x20; 0.456, starkly below peers) against its Stage 3 density sweep (0.733-1.806,
&#x20; much closer to peers) while `worst_wait`/`starved_pct` stayed elevated at
&#x20; every density level in both. Caught only after building and running the
&#x20; Stage 3 density sweep, not anticipated beforehand — should have been
&#x20; caught at design time. Apply this to any future eval work that varies
&#x20; density (Stage 4/5's own checkpoints included).

\- \*\*CURRENT STATUS (2026-09-02) — READ THIS BULLET ALONE AND YOU KNOW WHERE
&#x20; THE PROJECT STANDS. Everything below it in this file is supporting detail
&#x20; and history; nothing below it overrides it.\*\*

&#x20; \*\*THE SINGLE MOST IMPORTANT NEXT ACTION: a human must watch `sumo-gui` and
&#x20; sign off the mixed-traffic driving model. Nothing else is blocking. No test
&#x20; suite can substitute for it, and until it happens the driving-realism work
&#x20; is NOT DONE and stays uncommitted.\*\* The exact command is in the
&#x20; "PENDING — BEFORE THE EVENT" section further down.

&#x20; \*\*Phases 1-9: COMPLETE and hardened.\*\* Unchanged. Corridor generator,
&#x20; perception, env+reward, Tier 0 + safety validator, prediction, PPO training
&#x20; (Stages 1-5), MARL A/B, coordinator + explainability, backend — all
&#x20; done-bars verified and recorded in `docs/BUILD_LOG.md`.
&#x20; \*\*The deployed policy is Stage 4 SINGLE-AGENT PPO\*\*
&#x20; (`training/checkpoints/stage4/psychoflow_stage4_153600_steps_final.zip`),
&#x20; chosen by the 4a bake-off, run `deterministic=True`. `COORDINATION_MODE`
&#x20; stays `graph_attention` as the answer to §9.5's ARCHITECTURE question
&#x20; (attention beat shared_policy 12/12) — a separate axis from which
&#x20; checkpoint the backend serves. \*\*Say "single-agent PPO" out loud on demo
&#x20; day\*\* (§20).

&#x20; \*\*Three post-Phase-9 bodies of work — all BUILT, all VERIFIED, all
&#x20; COMMITTED ON `main`:\*\*
&#x20; (1) \*\*Phase 8/9 seam CLOSED\*\* (commit `ad9e4df`). The adapter in
&#x20; `backend/sim_runner.py` is DELETED; the §13.2 frame's `decision` is
&#x20; `DecisionLogEntry.to_dict()` and its `narration` is
&#x20; `explainability.narrator.narrate(entry)`. §11.2 responder messages ride the
&#x20; wire, additive. One known imperfection stays OPEN: `served_on_arrival` can
&#x20; fire for a lane §10 had to clear inside the same decision step — see its
&#x20; own bullet below; the fix is a §11.1 semantic change and the call is the
&#x20; user's.
&#x20; (2) \*\*§13.2 `shadow_advisor`\*\* (commits `ae51049`, `3c4f4e3`). The
&#x20; `graph_attention` checkpoint runs a READ-ONLY forward pass alongside the
&#x20; deployed policy and its recommendation rides every frame as an additive
&#x20; key. \*\*It never drives the road, and it is the WORSE policy\*\* — carry the
&#x20; honesty note wherever the field is displayed.
&#x20; `sim/run_shadow_advisor_check.py` -> \*\*35/35\*\*.
&#x20; (3) \*\*Backend security hardening + §13.1 `force_phase` / `clear_override`\*\*
&#x20; (commits `e8b2e46` .. `803afbc`). Loopback-by-default host guard,
&#x20; operator-input range checks, `dispatch()` function allowlist, per-iteration
&#x20; sim-thread guard, CORS allowlist, non-leaky `/health`. \*\*The §13 control
&#x20; API is still UNAUTHENTICATED by design and is a LOCAL DEMO SURFACE\*\*
&#x20; (§17). `sim/run_backend_security_check.py` -> \*\*62/62\*\*;
&#x20; `sim/run_backend_smoke.py` -> \*\*50/50\*\*.
&#x20; \*\*Branch state — checked, not assumed:\*\* `backend-security-hardening` WAS
&#x20; merged into `main` on 2026-08-31 12:03 as a FAST-FORWARD (reflog:
&#x20; `merge backend-security-hardening: Fast-forward`). `main`, `origin/main`
&#x20; and that branch all sit at `803afbc`; `git log --merges main` is empty and
&#x20; history is 67 linear commits. The branch ref is now a redundant pointer at
&#x20; `main`'s tip — deleting it is safe. \*\*BUILD_LOG claimed "NOT merged" for
&#x20; three days; corrected 2026-09-02.\*\*

&#x20; \*\*MIXED-TRAFFIC DRIVING REALISM — BUILT and MEASURED, but NOT SIGNED OFF
&#x20; and NOT COMMITTED.\*\* Demo-only; never reachable from training or
&#x20; evaluation.
&#x20; \*\*BUILT:\*\* SUMO's SL2015 sublane model behind two default-off
&#x20; `PsychoFlowEnv` kwargs (`vtype_file`, `lateral_resolution`) — with both
&#x20; omitted the `traci.start()` argv is byte-identical to pre-STEP-1 HEAD;
&#x20; `sim/networks/vehicle_types_demo.add.xml` carrying per-type gap and `tau`
&#x20; tuning, a `jm*` junction-model group gated to the AGGRESSIVE TIER ONLY, and
&#x20; driver heterogeneity as nested `<vTypeDistribution>` tiers (bike 20/45/35,
&#x20; auto 25/50/25, car 0/90/10 cautious/normal/aggressive; truck and ambulance
&#x20; deliberately untiered); two consequent perception fixes
&#x20; (`lane_sensor.base_vtype()`, `weather.WeatherModel._resolve_members()`),
&#x20; both provably inert on the default file; a `training/train.py` assert that
&#x20; refuses both kwargs before `model.learn()`; and `backend/main.py
&#x20; --demo-driving` (default OFF).
&#x20; \*\*MEASURED\*\* — all raw SUMO, one pinned route file, corridor 4/3/2, seed 7,
&#x20; 1200s, 1868 vehicles; full tables in `docs/MIXED_TRAFFIC_RESEARCH.md` §6:
&#x20; queue-front filtering `bike_over_car` \*\*5.61% baseline -> 31.73% tiered\*\*
&#x20; while `car_over_bike` falls 26.47% -> 10.81%, so the bike/car ratio goes
&#x20; 0.21x -> \*\*2.94x\*\* and the ordering INVERTS; bike mean queue advancement
&#x20; −1.699 -> \*\*+1.717\*\* places, per tier aggressive \*\*2.500\*\* / normal 1.462
&#x20; / cautious 0.889, so the tiers are genuinely distinguishable on screen;
&#x20; strict same-lane sharing bike \*\*51.38%\*\* against research §1.3's ~62%
&#x20; target (\*\*~10.6 points short — flagged, not fixed\*\*), with the LC2013
&#x20; baseline control correctly reading \*\*0.00%\*\* as it must by construction;
&#x20; red-eligible population \*\*17.18% observed vs 16.50% computed\*\*;
&#x20; collisions \*\*0.00%\*\* after the truck lateral-recentring fix
&#x20; (root-caused 2026-09-01, independently re-verified 2026-09-02).
&#x20; \*\*NOT DONE — THE BLOCKING ITEM: THE HUMAN GUI WATCH.\*\* STEP 1 and its
&#x20; refinement share ONE combined done-bar, and neither is closed until a human
&#x20; has watched `sumo-gui` coloured by type and confirmed the behaviour
&#x20; actually looks right. Two decisions ride on that same watch: whether the
&#x20; `bike_over_car` 36.29% -> 31.73% drop (tiering working as designed, but a
&#x20; drop) is accepted, and whether bike lane-sharing at 51.38% against a ~62%
&#x20; target is close enough.
&#x20; \*\*UNCOMMITTED, deliberately — 9 MODIFIED + 38 UNTRACKED = \*\*47 FILES\*\*

&#x20; (counted 2026-09-02, not estimated; reproduce with `git diff HEAD

&#x20; --name-only` and `git ls-files --others --exclude-standard`).\*\*

&#x20; \*\*Modified (9):\*\* `CLAUDE.md`, `backend/main.py`,

&#x20; `backend/sim_runner.py`, `docs/BUILD_LOG.md`,

&#x20; `docs/PsychoFlow_Master_Plan.md`, `env/psychoflow_env.py`,

&#x20; `perception/lane_sensor.py`, `perception/weather.py`,

&#x20; `training/train.py`.

&#x20; \*\*Untracked (38):\*\* `docs/MIXED_TRAFFIC_RESEARCH.md`;

&#x20; `sim/networks/vehicle_types_demo.add.xml`;

&#x20; `sim/networks/demo_gui_settings.xml`; `sim/run_demo_gui.py`;

&#x20; `sim/routes/demo_gui_432_seed7.rou.xml`; and \*\*34 files under

&#x20; `sim/mixed_traffic/`\*\* (15 harness scripts + `README.md` + the pinned

&#x20; `measure.rou.xml` / `measure3000.rou.xml` + 2 probe fixtures + 4

&#x20; comparison-arm vType tables + 9 raw JSONs under `data/`).

&#x20; \*\*The count was 14 before the 2026-09-02 harness relocation\*\* — the jump

&#x20; is the 34 relocated files, nothing else changed hands.

&#x20; Committing the generated route file follows existing convention:

&#x20; `sim/networks/generated/` already tracks 84 generated network files.

&#x20; \*\*A fresh clone, a `git checkout .` or a `git stash` DESTROYS ALL OF IT.\*\*

&#x20; Commit it once the GUI watch passes.

&#x20; \*\*Phase 11 (VOICE): design APPROVED, pipeline UNBUILT — correctly reserved
&#x20; for the event.\*\* Settled and recorded: browser Web Speech API for STT
&#x20; (\*\*NOT local\*\* — §2; in Chrome it streams to Google's speech service),
&#x20; local Gemma via Ollama for intent parsing, \*\*no Claude API and no paid
&#x20; inference anywhere in the runtime path\*\*; scope is exactly the
&#x20; `control_api.CONTROL_FUNCTIONS` allowlist reached only through
&#x20; `dispatch()`, which is the safety argument — the server rejects any name
&#x20; not on the allowlist before argument binding, so a mis-parsed intent cannot
&#x20; reach an unintended function; fail-closed on an unparsed intent (display
&#x20; "Command not understood, please try again", log the miss, take NO action,
&#x20; never guess a function or an argument); and the 0-based-vs-1-based
&#x20; lane-numbering mismatch must be reconciled explicitly rather than assumed
&#x20; to match. Full design in the "APPROVED VOICE DESIGN" bullet below.
&#x20; \*\*LOST BENCHMARK DATA — caveat, not a blocker.\*\* The model-selection and
&#x20; latency numbers behind the Gemma choice were lost and are reproduced
&#x20; nowhere in this repo. Treat any remembered figure as \*\*UNVERIFIED\*\*: do a
&#x20; quick sanity pass (one timed `ollama run` on the actual demo machine)
&#x20; before trusting it. \*\*Do NOT re-derive the design from scratch\*\* — the
&#x20; decisions are sound and settled; only the supporting numbers are unbacked.

&#x20; \*\*GENUINELY UNBUILT — this is the entire remaining list:\*\*
&#x20; \*\*Phase 10 (frontend)\*\* — nothing exists under `frontend/`. Read the
&#x20; "PHASE 10 BEHAVIOR SPEC" bullet below, and
&#x20; `docs/MIXED_TRAFFIC_RESEARCH.md`, before writing a line of it.
&#x20; \*\*Phase 11's actual pipeline\*\* — `backend/voice/` is empty.
&#x20; \*\*Phase 12 (evaluation suite)\*\* — including the \*\*Greedy baseline\*\*
&#x20; (`set_baseline_mode("greedy")` is plumbed but STUBBED and returns
&#x20; `applied: false`) and \*\*STEP 2's signal-violation detector\*\*. Both are
&#x20; deliberately deferred until Phase 10 exists to give them somewhere to
&#x20; render — a violation detector with no UI proves nothing to a judge, and
&#x20; §19 names Greedy-vs-PsychoFlow as the strongest demo beat.
&#x20; `evaluation/heldout.py` (the §15.4 held-out seed set) IS built; the eval
&#x20; harness that consumes it is not.
&#x20; \*\*Y-merge (§3)\*\* — untouched, and stays that way unless everything above
&#x20; is done with real time to spare.

&#x20; \*\*Open items carried forward, none of them blocking:\*\* D1
&#x20; (`training/checkpoints/stage5_graph_attention_d1/`, finished 2026-08-28 at
&#x20; `num_timesteps=156624`) is trained but \*\*NEVER EVALUATED\*\*, so the
&#x20; data-diversity hypothesis for the post-51k collapse remains open;
&#x20; `served_on_arrival` can over-report (§11.2); STEP 1's recorded baseline
&#x20; does not reproduce bit-for-bit. \*\*No background run is live and the SUMO
&#x20; beacon is free\*\* (`python -m sim.sumo_activity`, confirmed 2026-09-02).

\- \*\*Stage-3-era status note (2026-08-15), kept for history — SUPERSEDED by the
&#x20; CURRENT STATUS bullet above; do not read this as the project's state.\*\*
&#x20; Stage 3 (`+ randomize_density=True`) resumed from Stage 2's
&#x20; final checkpoint (`num_timesteps≈51200`) rather than starting fresh — the
&#x20; first stage to do so; Stage 1→2 was discovered to have been two
&#x20; disconnected fresh-model runs, not a continuous curriculum (see
&#x20; BUILD_LOG's Stage 3 entry), and this was corrected going forward without
&#x20; retroactively re-running Stage 2. Trained in two bursts (Burst A to
&#x20; `num_timesteps=61440`, Burst B resumed to `num_timesteps=102400`), passes
&#x20; the applicable checkpoint bar (§16 has no explicit "after Stage 3" row;
&#x20; the generic "reward trending up, not collapsing" plus an extension of
&#x20; Stage 2's consistency-sweep methodology to the density axis was applied
&#x20; instead) — reward improved under genuinely harder (not easier) draw
&#x20; conditions, majority of `total_lanes` buckets improve (4 of 6) though the
&#x20; single largest bucket (10 lanes, n=23) does not. `(4,2,4)`'s gap confirmed
&#x20; structural and density-independent (present at every density level
&#x20; including the lowest); `(2,4,2)`'s new density-triggered gap logged above,
&#x20; not investigated further. Decision: proceed to Stage 4 rather than
&#x20; investigate either gap further right now. All 27 lane-count combos remain
&#x20; pre-generated in `sim/networks/generated/` — Stage 4 needs no new network
&#x20; generation either. No per-combo random/Tier 0 baselines exist outside
&#x20; 4/3/2 and the 5 swept combos — any future sweep on a new combo is
&#x20; self-referential (comparing the trained policy's own numbers), not
&#x20; baseline-relative, unless a baseline is run for that combo specifically.

\- \*\*STAGE 5 MODE COMPARISON — `graph_attention`'s ACTUAL total is
&#x20; `num_timesteps=51624`; `shared_policy` must be trained to a comparable
&#x20; budget.\*\* §9.5's whole point is deciding whether attention earns its
&#x20; complexity over the fallback, and that decision is meaningless if the two
&#x20; modes trained on different budgets. `graph_attention`: Burst A to 10,240,
&#x20; Burst B to 35,240 (interrupted there by a laptop battery power-cut, not a
&#x20; code fault; checkpoint integrity re-verified by size progression and CSV
&#x20; tail before resuming), then resumed 15,000 more to land at \*\*51,624\*\*.
&#x20; Note 51,624 ≠ the 50,240 that was aimed for: PPO only stops on a rollout
&#x20; boundary, and 35,240 + 8×2048 = 51,624, an overshoot of 1,384. This is
&#x20; exactly why the actual final `num_timesteps` must be READ OFF each run
&#x20; rather than assumed from the `--timesteps` argument — the two modes will
&#x20; not land on identical totals by accident, and a "50k vs 50k" claim would
&#x20; be wrong for both. Record both actuals when comparing, and treat a
&#x20; difference of a few thousand steps as a caveat on the comparison rather
&#x20; than pretending it away. Both modes are evaluated with the SAME mode-unaware harness
&#x20; (`evaluate_stage.py --j1-recheck` / `--emergency-recheck`, which take only
&#x20; a checkpoint path), so the budget is the one axis that has to be
&#x20; controlled by hand.
&#x20; \*\*INTERIM MEASUREMENTS on `graph_attention` @ 51,624 — NOT new watch-items
&#x20; and NOT verdicts on attention.\*\*
&#x20; \*\*CORRECTED 2026-08-28 — these were originally described as readings "from
&#x20; an UNDERTRAINED checkpoint (1/3 of Stage 4's 153,600 budget)". That framing
&#x20; was WRONG and is retracted. 51,624 is the PEAK of this run, not a waypoint
&#x20; toward a better one.\*\* The run was subsequently trained to 154,024 and
&#x20; COLLAPSED: `starved_pct` 1.20% -> 25.82%, `ovrS` 1.08 -> 15.75, reward
&#x20; 1.2347 -> 0.2414 (4a bake-off; corroborated by `reward_term_replay.json`
&#x20; at 61,624 and by `phase0_baselines.json`'s ovrS=98). \*\*Do not read
&#x20; "undertrained" as "train it longer" — that has been measured three times
&#x20; and makes it worse.\*\* See BUILD_LOG's 2026-08-28 Phase 0 close-out entry.
&#x20; (a) \*\*`--j1-recheck`: uniform regression; the gap is currently
&#x20; UNMEASURABLE.\*\* All four combos now land in a narrow 121-125s band —
&#x20; `(3,2,3)` 4/4 seeds spiking (was 2/8 at Stage 4), `(3,2,4)` 4/4 (was 1/8),
&#x20; and critically the `j1=4` CONTROLS `(4,2,3)`/`(4,2,4)` 4/4 as well (were
&#x20; 0/4, clean at 56-61s). `starved_pct` is 1.1-3.5% everywhere vs 0.0% on the
&#x20; Stage 4 controls. The `j1=3`-vs-`j1=4` distinction has vanished, but NOT
&#x20; because `j1=3` improved — because `j1=4` degraded and everything converged
&#x20; to one bad band. Nothing can be concluded about the original gap from this;
&#x20; re-test once `shared_policy` provides a budget-matched comparator.
&#x20; Side-note: Stage 4's spikes sat at 119-120s, just BELOW
&#x20; `STARVATION_CEILING_S = 120` (and were confirmed then to fire zero
&#x20; overrides), whereas these sit at 121-125s, just ABOVE it — consistent with
&#x20; the ceiling now actually engaging.
&#x20; \*\*NOW CONFIRMED (2026-08-28) — this was recorded here as "UNVERIFIED /
&#x20; has not been confirmed"; it is neither any more.\*\* The `--j1-recheck`
&#x20; harness genuinely does not capture override counts, but
&#x20; `reward_term_pre51k.json` does: at 51,624 the same 12 pinned pairs fire
&#x20; `ovrS` 1.42 per run (vs 0.00 for Stage 4). The ceiling IS engaging at
&#x20; Stage 5 and was not at Stage 4. Confirmed answer lives in BUILD_LOG's
&#x20; 2026-08-28 Phase 0 close-out entry, section 2.
&#x20; (b) \*\*`--emergency-recheck`: first non-zero proactive handling ever
&#x20; recorded.\*\* 11/15 override-firing, against Stage 4's 15/15 baseline.
&#x20; Of the 4 non-firing runs, \*\*2 are confirmed clean\*\* (`(4,3,2)` seed 42,
&#x20; `(4,2,4)` seed 42: no override, zero penalty, zero blocked events) and 2
&#x20; are AMBIGUOUS (`(4,4,4)` seed 7, `(4,2,4)` seed 7: no override fired yet
&#x20; `penalty=20.00` with 1 blocked event). The ambiguous pair is most likely
&#x20; the documented `_green_lanes()` vs `phase_served_lanes()` asymmetry — mid-
&#x20; yellow, §9.4's reward reads the LIVE RYG state and counts the ambulance
&#x20; blocked while §10 sees the target slot already serving it and correctly
&#x20; declines to override — which would make it good behaviour caught at an
&#x20; awkward instant, but this is NOT verified. Honest range: \*\*2/15 confirmed,
&#x20; up to 4/15\*\*, vs 0/15 at Stage 4. Also notable: `(2,4,2)`'s mean summed
&#x20; `emergency_penalty` dropped 46.667 -> 13.333.
&#x20; \*\*FINAL (2026-08-16), both modes measured — emergency handling is
&#x20; measurably improved vs single-agent Stage 4, though not fully closed.\*\*
&#x20; Override-firing (lower = fewer forced §10 interventions = more proactive):
&#x20; Stage 4 single-agent \*\*15/15\*\* -> `graph_attention` \*\*11/15\*\* ->
&#x20; `shared_policy` 13/15. Confirmed-clean runs (no override, zero penalty,
&#x20; zero blocked events): 0 -> \*\*2\*\* -> 1. Attention leads on this metric too,
&#x20; and it is the metric §9.5 predicts it should help with (an ambulance
&#x20; approaching along a corridor route IS cross-junction state). NOT closed:
&#x20; 11/15 still means the safety validator is doing most of the work, and
&#x20; §16's Stage 4 bar ("near-100% emergency priority") remains FAILED and
&#x20; unremediated — see the Stage 4 BUILD_LOG entry. The sparse-signal
&#x20; hypothesis (one ambulance per episode against hundreds of ordinary
&#x20; decision steps) is still untested.
&#x20; \*\*The `--j1-recheck` interim reading above ("uniform regression, gap
&#x20; unmeasurable") is SUPERSEDED and was WRONG\*\* — see the Stage 2 watch-item's
&#x20; RESOLVED-IN-PART note; it compared against a mismatched budget and
&#x20; architecture at once. Attention wins that comparison 12/12 against its
&#x20; budget-matched peer.
&#x20; \*\*Not re-measured at Stage 5:\*\* the Stage 1 determinism/per-head
&#x20; watch-item and the Stage 3 `(2,4,2)` density watch-item were NOT
&#x20; specifically re-tested against either Stage 5 checkpoint. Both remain
&#x20; open with their status unchanged; do not read Stage 5's results as
&#x20; bearing on either.

\- \*\*STANDING RULE — EVERY BURST RESTARTS THE SCENARIO SEQUENCE. A resumed run
&#x20; adds PASSES, not DATA. Distinct scenarios ≈ the LONGEST single burst, never
&#x20; the sum of bursts.\*\* Each burst constructs a fresh env with `seed=7`, so the
&#x20; `reset(seed=...)` sequence restarts at episode 1 and re-draws the identical
&#x20; scenarios in the identical order. \*\*Verified across every stage that logs
&#x20; enough to test it\*\* (2026-08-18): stage3 Burst A vs B \*\*16/16 identical\*\* on
&#x20; `(lane_counts, density_mult_corridor, density_mult_cross)`; stage4 Burst A vs
&#x20; B \*\*16/16\*\* on `(lane_counts, density, emergency_route, emergency_depart_s)`;
&#x20; Stage 5 Burst C vs D \*\*81/81\*\*, Burst B vs C \*\*46/46\*\*. It also holds ACROSS
&#x20; STAGES sharing a config: Stage 4 Burst B vs Stage 5 Burst C \*\*64/64
&#x20; identical\*\* — Stage 4 and Stage 5 trained on the SAME scenario sequence,
&#x20; because both use `STAGES[4]` and `seed=7`. There is no stage-specific
&#x20; seed-handling path.
&#x20; \*\*Measured distinct-scenario counts:\*\* stage1/2/3 ≈ 65 each, stage4 ≈ 64,
&#x20; Stage 5 `graph_attention` \*\*81 distinct from 248 logged episodes — each seen
&#x20; ~3.1x\*\*. Stage 5's Burst D (51,200 steps) introduced \*\*ZERO\*\* new scenarios.
&#x20; \*\*Consequences, all load-bearing:\*\*
&#x20; (1) \*\*Never explain a result by "more budget" or "more data" without checking
&#x20; whether the extra steps were new scenarios or repeats.\*\* Within-stage Burst
&#x20; A→B reward gains conflate additional training with repeated exposure.
&#x20; (2) \*\*Within-burst analysis is UNAFFECTED\*\* — episodes inside one burst are
&#x20; all distinct (81/81 for Stage 5 Burst C), so Stage 2's bucket analysis and
&#x20; Stage 3's early/late splits compare genuinely different scenarios.
&#x20; (3) \*\*The §9.5 graph_attention-vs-shared_policy A/B is UNAFFECTED and in fact
&#x20; DEPENDS on this\*\* — identical sequences are what make it paired. BUILD_LOG
&#x20; documents the property deliberately as a feature for that comparison; the
&#x20; side effect on RESUMED TRAINING is what went unnoticed until 2026-08-18.
&#x20; (4) Stage 1/2 could not be tested (their Burst A predates `lane_counts`
&#x20; logging). \*\*Episode LENGTH is not a valid proxy\*\* for scenario identity —
&#x20; it varies with the policy, and differs across bursts in stage3/4 where the
&#x20; scenarios are provably identical.
&#x20; \*\*Do NOT "fix" this globally without thought\*\* — pinning is required for the
&#x20; paired mode comparison and for every reproducible eval. The fix belongs in
&#x20; the TRAINING draw only (D1: a seed counter that persists across resumes),
&#x20; with eval/demo left pinned.

\- \*\*Eval configs are STRUCTURALLY DISJOINT from training — but only by
&#x20; accident, so do not rely on it.\*\* `evaluate_stage.py --j1-recheck` runs
&#x20; `randomize_density=False` (density exactly 1.0) and `spawn_emergencies=False`;
&#x20; measured over 162 Stage 5 training episodes, \*\*162/162 have an ambulance and
&#x20; 0/162 have `density_mult_corridor == 1.0`\*\*, so no eval episode can coincide
&#x20; with a training episode. This is what keeps the 2026-08-18 j1 curve, per-term
&#x20; decomposition and proposal-quality results uncontaminated. \*\*It is incidental
&#x20; protection, not a designed held-out set\*\* — any future eval that MATCHES the
&#x20; training config would silently evaluate on trained scenarios. See master plan
&#x20; §15.4, which now requires an explicit held-out set.

\- \*\*`sim/networks/generated/` is git-tracked, not gitignored\*\* (confirmed via
&#x20; `git check-ignore` — no match). Stage 2's pre-generation step will add up
&#x20; to 26 new `corridor_{j1}{j2}{j3}.{net,edg,nod}.xml` file sets to that
&#x20; directory; they need `git add`/commit like any other source file once
&#x20; generated, not left sitting untracked. Contrast with
&#x20; `training/checkpoints/*`, which IS gitignored by design (only a
&#x20; deliberately un-ignored final model should ever be tracked there).

\- \*\*PHASE 8 WARNING — do NOT reuse the Stage 4 emergency-latency
&#x20; measurement for §11.2's `clearance_time_s`. It is KNOWN BROKEN and
&#x20; UNFIXED.\*\*
&#x20; \*\*SATISFIED 2026-08-29 — the warning still stands about the STAGE 4 HARNESS,
&#x20; which is still broken and must still never be reused. But the fix it demanded
&#x20; HAS been built:\*\* `coordinator/emergency_clearance.py` (Phase 8, commit
&#x20; `9cf19af`) tracks detection and green onset PER JUNCTION and floors at 0.0
&#x20; with `served_on_arrival=True` instead of emitting a negative latency. The
&#x20; sentence below reading "there is currently NO working latency measurement in
&#x20; the repo" was true when written and is now FALSE — there is one, it is
&#x20; `EmergencyClearanceEvent.clearance_time_s`, and §11.2 already consumes it.
&#x20; Read the rest of this bullet as the rationale for that design, not as
&#x20; outstanding work.
&#x20; The Stage 4 emergency sweep produced NEGATIVE latencies
&#x20; (−42.0s to −2.0s) because the harness tracked detection and green-onset
&#x20; at the FIRST junction the ambulance was seen at, while the §10 override
&#x20; can fire at a LATER junction on a corridor-through route (J1→J2→J3) —
&#x20; and green-onset recovered as `sim_time - time_since_switch_s` can predate
&#x20; detection when that junction was already green for unrelated reasons.
&#x20; `training/evaluate_stage.py`'s `--emergency-recheck` deliberately OMITS
&#x20; latency rather than emit a known-bad number (see its docstring), so there
&#x20; is currently NO working latency measurement in the repo. §11.2's
&#x20; responder message reports a clearance time to an operator, so inheriting
&#x20; this silently would put a wrong — possibly negative — number in front of
&#x20; a human. Fix first: track detection/green-onset PER JUNCTION and attribute
&#x20; the override to the junction it actually fired at. Phase 4's B2
&#x20; (`sim/run_tier0_episode.py --b2`) has a correct single-junction
&#x20; implementation to model it on — it measures from FIRST DETECTION at a
&#x20; known junction and recovers green onset exactly; it is only the
&#x20; multi-junction generalisation that is missing.

\- \*\*Backend (Phase 9, §13) commands\*\*: run the live server with
&#x20; `venv/Scripts/python.exe -m backend.main` (or `uvicorn backend.main:app`) —
&#x20; flags `--topology 432 --realtime-factor 0.3 --fast --no-checkpoint
&#x20; --host --port`. Phase 9 done-bar check: `venv/Scripts/python.exe
&#x20; sim/run_backend_smoke.py` (boots the app in-process via `TestClient`, no
&#x20; external server needed; 7-point §13.1/§13.2 checklist + a no-SUMO unit
&#x20; check of the new Tier 0 `lane_weights` param). \*\*Last run: 50/50 pass\*\*
&#x20; (2026-08-31; the 45/45 this line quoted was pre-`force_phase` — check 4f is
&#x20; +5. Corrected 2026-09-02, NOT re-run in that pass: it needs SUMO and a
&#x20; sign-off GUI window was open. Re-run it to confirm.)  \*\*was 45/45\*\*
&#x20; (2026-08-30, project venv, against the deployed Stage 4 checkpoint). The
&#x20; count has moved five times since the 21/21 this line used to quote, and went
&#x20; uncorrected for the first two: `3496057` added checks 1b/1c (the auto-mode
&#x20; `decisions`-dict contract) -> 23, `f3d5908` added the §15.2
&#x20; metrics-populated check -> 24, the Phase 8 adapter swap added 13 more
&#x20; (1d/1e/1f, 2b, 4a, 4b, 4c, 4d, 4e x2, 5c x3) -> 37, the §13.2 `predictions`
&#x20; commit added P1/P2/P3 + live 1g -> 41, and `inject_incident` added check 8
&#x20; (x4) -> 45. \*\*Cite the number from a run, not from this
&#x20; line\*\*, and update it here when it moves. The 2026-08-30
&#x20; `shadow_advisor` commit added NO check here — it widened check 1's
&#x20; additive-key set to three and put its own S1-S6 in
&#x20; `sim/run_shadow_advisor_check.py`; 45/45 re-verified unchanged after it.

\- \*\*§13.2 `shadow_advisor` (2026-08-30) — the §9.5 MARL checkpoint runs a
&#x20; READ-ONLY forward pass alongside the deployed policy. IT NEVER DRIVES THE
&#x20; ROAD.\*\* `graph_attention` (`psychoflow_stage5_51624_steps_final.zip`)
&#x20; predicts on the SAME pre-step obs/mask the deployed policy just used and its
&#x20; recommendation rides the frame as an ADDITIVE THIRD top-level key (after
&#x20; `predictions` and `responder_messages`). Stage 4 single-agent remains the
&#x20; sole driver, unconditionally — `DEFAULT_CHECKPOINT` is unchanged,
&#x20; `COORDINATION_MODE` is unchanged, §9.5 is NOT reopened.
&#x20; \*\*THE HONESTY NOTE — carry it wherever the field is shown.\*\* The shadow is
&#x20; the \*\*WORSE\*\* policy, and on the DEMO CORRIDOR (4,3,2) specifically:
&#x20; Stage 4 = \*\*0\*\* starvation events / \*\*0\*\* overrides / \*\*38-42s\*\* worst,
&#x20; vs `ga_51624`'s \*\*4 / 1 / 121-125s\*\* (4a bake-off; full grid `starved_pct`
&#x20; 0.08% vs 1.20%). It shows WHAT THE MARL ARCHITECTURE WOULD HAVE DONE — not a
&#x20; better idea being ignored. A disagreement is NOT evidence the deployed policy
&#x20; erred; the measured prior runs the other way. \*\*Do not build a panel
&#x20; labelling it "recommended"/"suggested" without that context\*\*, and note §20
&#x20; still requires saying out loud that the demo runs SINGLE-AGENT PPO.
&#x20; \*\*Placement is load-bearing:\*\* the call sits between `_pick_action()` and
&#x20; `env.step()`. After `step()` it would compare the two proposals against
&#x20; DIFFERENT states and nothing would raise. `_pick_action()` is NOT modified,
&#x20; so the advisor calls `env.action_masks()` a second time — S4 is what proves
&#x20; that second read returns the same mask.
&#x20; \*\*Both `recommended_phase` and `deployed_proposed_phase` are PRE-SHIELD.\*\*
&#x20; Never compute agreement against `executed_phase` (post-§10) — that conflates
&#x20; a policy disagreement with the shield's own intervention.
&#x20; \*\*Failure isolation:\*\* any exception logs once, latches
&#x20; `_shadow_enabled = False`, and the key stops being emitted. A missing
&#x20; checkpoint file is NOT an error — same silent-off path as `--no-shadow`.
&#x20; Flags: `--shadow-checkpoint` / `--no-shadow` (mirroring
&#x20; `--checkpoint` / `--no-checkpoint`); default ON when the file exists.
&#x20; `episode_agreement_rate` resets in `_reset_counters()` with the other
&#x20; per-episode counters.
&#x20; \*\*Check: `venv/Scripts/python.exe sim/run_shadow_advisor_check.py`\*\*
&#x20; (`--s1`..`--s6` individually selectable). \*\*Last run: 35/35 pass\*\*
&#x20; (2026-08-30). S1/S2/S3 need no SUMO; S4/S5/S6 do. \*\*S6 is the one that
&#x20; actually proves "advisory"\*\*: two SimRunners run SEQUENTIALLY (never
&#x20; concurrently — TraCI is process-global), same seed and pinned scenario, one
&#x20; off one on, frames captured via `frame_sink` DIRECTLY and not the WebSocket
&#x20; (the `Hub` queue is bounded and drops frames for a slow consumer, which would
&#x20; make the sequences differ for a reason unrelated to the advisor). Measured:
&#x20; decision/throughput/actuated-phase sequences IDENTICAL while the advisor
&#x20; disagreed on \*\*60/120\*\* frames. S3 matters because `MaskablePPO.load()`
&#x20; reseeds Python/numpy/torch global RNGs — the sim is immune only because
&#x20; `scenario_generator`/`v2x`/`vision_mock` all use instance-local
&#x20; `random.Random(seed)`, and S3/S6 measure that rather than assuming it.

\- \*\*Pre-event completions of Phase 8/9 modules (2026-08-30) — five separable
&#x20; commits, NOT Phase 10/11/12 work.\*\*
&#x20; (1) \*\*§17\*\*: lane closures are OUT OF SCOPE as a system output — interventions
&#x20; are signal-phase control (§9) + emergency corridors (§10/§11). New §17 bullet;
&#x20; §17 notes in `backend/control_api.py` and `coordinator/responder_messaging.py`.
&#x20; (2) \*\*`explainability/narrator.narrate(entry, *, register=...)`\*\* — `register`
&#x20; is keyword-only, `"operator"` (default, §12.2 wording FROZEN — downstream
&#x20; tests pin fragments) or `"public"` (plain-language, no phase/slot/ceiling/
&#x20; threshold jargon, no raw lane id). `REGISTERS`, `REGISTER_OPERATOR`,
&#x20; `REGISTER_PUBLIC` exported. Unknown register -> `ValueError`. `sim_runner`
&#x20; and `query_interface` still call it positionally = operator.
&#x20; (3) \*\*§13.2 frame gained an ADDITIVE `predictions` key\*\* (omitted unless
&#x20; material, same contract as `responder_messages`): `predictions.spillover`
&#x20; (§8.1 shape, only pairs with `abs(predicted_queue_delta) >=
&#x20; _SPILLOVER_MIN_DELTA = 1.0` in `backend/sim_runner.py`) and
&#x20; `predictions.incident_impact` (§8.2 shape, one per active §7.3 incident).
&#x20; Spillover is computed by a SECOND, read-side `SpilloverPredictor`
&#x20; (`SimRunner._spillover_view`, reset in `_reset_counters()`) — do NOT call the
&#x20; env's stateful one from the frame path, it would double-advance its rate
&#x20; calc. `SimRunner._predictions(snap)` is the reference. Master plan §13.2
&#x20; updated same commit.
&#x20; (4) \*\*§13.1 `inject_incident(junction_id, affected_lanes, incident_type=
&#x20; "lane_blocked", severity="high", lane_id=None, estimated_duration_s=600)`\*\*
&#x20; in `backend/control_api.py` — the live trigger for "detects incidents".
&#x20; Queues a `Command` the sim thread applies via `env.twin.incidents.report()`
&#x20; between steps (registry write, NOT TraCI). `POST /control/inject_incident`.
&#x20; From the next step it rides `digital_twin.active_incidents`,
&#x20; `predictions.incident_impact` and §8.1's confidence penalty. Master plan
&#x20; §13.1 table row added. There is deliberately NO `close_lane` (§17).
&#x20; (5) \*\*§15.2 `emergency_clearance_time_s` is DEFINED as
&#x20; `EmergencyClearanceEvent.clearance_time_s`\*\* (`coordinator/emergency_clearance.py`)
&#x20; — was "BLOCKED". Per-junction detection->green, 0.0 floor + `served_on_arrival`.
&#x20; NOT the Stage 4 harness (still broken, still never reuse). §11.2's PHASE 8
&#x20; BLOCKER got a RESOLVED pointer at its head. Cross-scenario aggregation is
&#x20; Phase 12. Definition-level only — no eval harness built.

\- \*\*BACKEND SECURITY HARDENING (2026-08-31) — `backend/` only, no Phase 10.
&#x20; The §13 control API is UNAUTHENTICATED and stays a LOCAL DEMO SURFACE.\*\*
&#x20; New standing rules:
&#x20; (a) \*\*Loopback by default.\*\* `backend/main.py` refuses a non-loopback
&#x20; `--host` unless `--allow-lan` is also passed (`_host_rejection()`), and
&#x20; prints a warning banner when it is. Do NOT weaken this — there is no auth
&#x20; layer behind it.
&#x20; (b) \*\*All operator input is range-checked in `backend/control_api.py`\*\*
&#x20; (`math.isfinite` first, then bounds): `set_lane_bias` weight ∈
&#x20; `LANE_BIAS_WEIGHT_RANGE` (0.1–10.0), duration ∈
&#x20; `LANE_BIAS_DURATION_RANGE_S` (10–900); `inject_incident`
&#x20; `estimated_duration_s` ∈ `INCIDENT_DURATION_RANGE_S` (1–7200),
&#x20; `affected_lanes` de-duped and capped at the loaded corridor's REAL lane
&#x20; count — `len(state.snapshot_stats()["lanes"])`, dynamic per topology, NOT
&#x20; a fixed literal (corrected 2026-08-31; there is no `MAX_AFFECTED_LANES`
&#x20; constant). The sim thread TRUSTS whatever it dequeues, so the check has
&#x20; to be here.
&#x20; (c) \*\*`control_api.dispatch(state, name, args)` is the guarded entry point
&#x20; for §14 voice / any generic caller\*\* — rejects any `name` not in
&#x20; `CONTROL_FUNCTIONS` before argument binding. `_DISPATCH_TABLE` has a
&#x20; module-level assert against drift. The sim thread mirrors this with
&#x20; `_APPLIABLE_KINDS`.
&#x20; (d) \*\*`SimRunner._run()` wraps each iteration in try/except\*\*
&#x20; (`_run_iteration()` extracted); `_MAX_CONSECUTIVE_FAILURES` (5) in a row
&#x20; re-raises to the fatal handler. One transient error no longer kills the
&#x20; demo.
&#x20; (e) \*\*`set_topology`\*\*: no-op when the combo already matches; sim-thread
&#x20; cooldown `_TOPOLOGY_COOLDOWN_S` (10 s wall-clock) between rebuilds.
&#x20; \*\*`inject_incident`\*\*: sim-thread cap `_MAX_ACTIVE_INCIDENTS` (32) on
&#x20; simultaneously-active operator incidents. All lane-referencing control
&#x20; calls FAIL CLOSED (`applied: False`) until the sim publishes a lane set.
&#x20; (f) \*\*CORS\*\*: `CORSMiddleware` with an explicit origin allowlist
&#x20; (`ALLOWED_ORIGINS` = the Vite dev server only), `allow_credentials=False`.
&#x20; (g) \*\*`/health`\*\* exposes `sim_error` (bool) + `sim_error_class` (str) —
&#x20; never the traceback (that stays on stdout). If you add a `/health`
&#x20; consumer, read `sim_error_class`, not a message body.
&#x20; (h) `.claude/settings.local.json` added to the repo `.gitignore` (a
&#x20; collaborator/CI checkout has no `~/.gitignore_global`).
&#x20; \*\*Check: `venv/Scripts/python.exe sim/run_backend_security_check.py`\*\*
&#x20; (offline, no SUMO, no beacon — launches nothing; same category as
&#x20; `stage4_contamination.py`). \*\*Last run: 62/62 pass\*\* (RE-RUN 2026-09-02
&#x20; during the closure pass, project venv). \*\*This line said 58/58\*\* — stale
&#x20; since the same-day `803afbc` commit made `inject_incident`'s affected_lanes
&#x20; cap dynamic and added `check_affected_lanes_dynamic_cap()` (+4).
&#x20; Touched `explainability/decision_log.py` too: `record_step` now carries
&#x20; `transcript`/`action_taken` through from the decision dict (was
&#x20; `record_voice`-only) so a §13.1 `force_phase` row narrates properly;
&#x20; `.get()`-guarded, transparent to every existing caller.

\- \*\*§13.1 `force_phase(junction_id, phase)` / `clear_override(junction_id=None)`
&#x20; (2026-08-31)\*\* — operator pins a junction to a green `phase`. DEFERRED
&#x20; (applied on the normal action path at the next decision step, so §10 still
&#x20; validates and an emergency/ceiling override still outranks it) and
&#x20; MASK-CHECKED (`SimRunner._apply_forced_phases` tests the live
&#x20; `action_masks()` slice AND `phase_served_lanes()` — NOT `_green_lanes()`);
&#x20; an invalid pin is dropped with a log line. The §12.1 entry carries
&#x20; `reason="voice_command"` (§12.2), and `_emit_junction` now surfaces a
&#x20; force_phase ahead of an ordinary switch so a manual intervention is always
&#x20; on the frame's `decision`. Pins are cleared by `clear_override`, a
&#x20; `set_topology` rebuild, or an episode boundary (`_reset_counters`).
&#x20; `run_backend_smoke.py` check 4f covers it live; \*\*Last run: 50/50 pass\*\*
&#x20; (2026-08-31, was 45 — the 4f block is +5).

\- \*\*APPROVED VOICE DESIGN (Phase 11, recorded 2026-08-31 — NOT yet built).\*\*
&#x20; The pipeline itself is Phase 11 and stays unbuilt; these are the settled
&#x20; decisions it must follow.
&#x20; (1) \*\*Model\*\*: browser Web Speech API for STT (see §2 — this is NOT local,
&#x20; it can hit Google's cloud; free, no key), local Gemma via Ollama
&#x20; (`ollama pull gemma3`) for intent parsing. No Claude API, no paid
&#x20; inference, ever (§0/§2).
&#x20; (2) \*\*Scope = the `control_api.CONTROL_FUNCTIONS` allowlist\*\*, reached only
&#x20; through `control_api.dispatch()`. §14's prompt names four
&#x20; (`set_mode`, `set_lane_bias`, `get_stats`, `trigger_emergency`);
&#x20; `force_phase`/`clear_override` are also allowlisted and voice-reachable.
&#x20; (3) \*\*Lane-numbering convention\*\*: `explainability/narrator.py` renders
&#x20; `{lane}` as the RAW 0-BASED SUMO lane index today
&#x20; (`lane_slot = _lane_index(lane_id)` = trailing int of e.g. `N1_J1_0` ->
&#x20; 0; NO `+1`). §14's example command says "give lane 3 more priority" —
&#x20; a human "lane 3" ≠ the narration's "Lane 3". Phase 11 must reconcile
&#x20; this (either +1 in the narration, or document that voice "lane N" means
&#x20; 0-based slot N); do NOT silently assume they match.
&#x20; (4) \*\*Fail-closed (VIP no-op)\*\*: an intent that is unparseable, or whose
&#x20; function is not on the allowlist, does NOTHING — display "Command not
&#x20; understood, please try again", log the miss, take no action. Never guess
&#x20; a function or an argument. `dispatch()` already enforces the name half.
&#x20; (5) `set_lane_bias` under `mode="auto"` is recorded but INERT (the RL
&#x20; policy has no per-lane score) — the echo says so; same for a voice
&#x20; `set_lane_bias`.

\- \*\*`Tier0Controller.act()` now takes an optional `lane_weights:
&#x20; dict[str, float]`\*\* — §13.1's `set_lane_bias(lane_id, weight, duration_s)`,
&#x20; a per-lane multiplier on that lane's whole §9.1 score contribution. It
&#x20; lives in `agents/rule_based.py` (the one place lane scoring is defined),
&#x20; NOT in a backend wrapper. `lane_weights=None` reproduces the unbiased
&#x20; controller byte-for-byte, so every existing caller is unaffected. It has
&#x20; a hook only under `mode="manual"`; the RL policy has no per-lane score,
&#x20; so `set_lane_bias` under `mode="auto"` is recorded and echoed but inert.

\- \*\*Backend auto-mode checkpoint = `training/checkpoints/stage4/
&#x20; psychoflow_stage4_153600_steps_final.zip`\*\* (`DEFAULT_CHECKPOINT` in
&#x20; `backend/sim_runner.py`). \*\*SUPERSEDES the earlier `ga_51624` choice\*\*,
&#x20; which was recorded here before the 4a bake-off measured it. Decided by
&#x20; `python -m training.scripts.checkpoint_bakeoff` (48 episodes: 4 controllers
&#x20; x 4 topologies x 3 seeds, pinned config; raw data
&#x20; `_sweeps/checkpoint_bakeoff.json`). Means:
&#x20; | controller | starv_ev | wait_var | starved% | reward | ovrS |
&#x20; |---|---|---|---|---|---|
&#x20; | tier0 | 0.00 | 50.7 | 0.00 | 1.2011 | 0.00 |
&#x20; | \*\*stage4_153600\*\* | \*\*0.08\*\* | 72.2 | \*\*0.08\*\* | \*\*1.3450\*\* | \*\*0.00\*\* |
&#x20; | ga_51624 | 3.33 | 109.8 | 1.20 | 1.2347 | 1.08 |
&#x20; | ga_154024 | 132.75 | 592.9 | 25.82 | 0.2414 | 15.75 |
&#x20; On the DEMO CORRIDOR (4,3,2) Stage 4 is 0 events / 0 overrides / 38-42s
&#x20; worst on all 3 seeds, vs ga_51624's 4 / 1 / 121-125s. Stage 4 also leads
&#x20; §8.2 emergency proposal quality — but see the correction immediately below
&#x20; before citing a number for it.
&#x20; \*\*CORRECTED 2026-08-28 — this bullet previously cited "0.885 vs 0.778".
&#x20; That pair is CONTAMINATED; do not cite it.\*\* Clean held-out figures are
&#x20; Stage 4 \*\*0.8298\*\* vs `graph_attention` @154,024 \*\*0.7656\*\*, and the gap
&#x20; between them is \*\*NOT significant\*\* (z = +0.824, p = 0.410). \*\*This metric is
&#x20; a non-significant directional edge and was never a reason Stage 4 was
&#x20; deployed\*\* — 4a rests on the fairness grid above. Full derivation, the
&#x20; three-checkpoint table and the asymmetric-contamination finding live in the
&#x20; proposal-quality contamination bullet earlier in this section; deliberately
&#x20; NOT duplicated here, so the two cannot drift apart.
&#x20; \*\*§9.5 IS NOT REOPENED\*\* — `COORDINATION_MODE` stays `graph_attention` as
&#x20; the MARL-architecture answer (attention beat shared_policy 12/12). Which
&#x20; CHECKPOINT the backend serves is a separate axis from which MARL extractor
&#x20; won; see the Stage 5 ESSENTIAL QUALIFIER above, which already recorded that
&#x20; single-agent beats both MARL modes. \*\*Demo-honesty consequence (§17, §20):
&#x20; the deployed policy is SINGLE-AGENT PPO, not MARL\*\* — say so out loud
&#x20; rather than describing the live demo as multi-agent.
&#x20; \*\*Re-evaluate once the D1 (persistent-seed-counter) run completes.\*\*
&#x20; Deterministic policy (`deterministic=True`) — deployment runs the greedy
&#x20; policy, per §16.

\- \*\*`worst_wait` is a SATURATED statistic — never use it to rank policies or
&#x20; show it to a judge (Phase 0 close-out, 2026-08-28).\*\* §10's ceiling
&#x20; (`STARVATION_CEILING_S=120`) intervenes before a lane can run far past 120s,
&#x20; so an episode MAX collapses to a near-binary "did the ceiling fire." In the
&#x20; 4a bake-off, on the demo corridor (4,3,2), `ga_51624` scores `worst_wait`
&#x20; 121/124/125s and `ga_154024` scores 125/125/132s — OVERLAPPING bands — while
&#x20; their `starvation_events_count` on those same runs is 4/4/4 versus 85/186/208.
&#x20; A ~50x difference in policy quality that `worst_wait` renders as ~5s.
&#x20; Use `starvation_events_count`, `wait_time_variance_across_lanes` and
&#x20; `starved_pct`; master plan §15.2 now defines all three, and
&#x20; `training/scripts/checkpoint_bakeoff.py` is the reference implementation.

\- \*\*4a bake-off command\*\*: `python -m training.scripts.checkpoint_bakeoff`
&#x20; (48 episodes, ~27 min; `--timing` runs one timed episode). Re-run it when a
&#x20; new candidate checkpoint appears — it is the deployment decision procedure,
&#x20; not a one-off. It reproduces the recorded Tier 0 B1 baseline exactly
&#x20; (627 steps / 4668 arrived / 41.0s / 0 starved / 1.1947) and ga_51624's
&#x20; recorded (4,3,2) seed 7 row exactly (1.2859 / 121.0s / 1.09%), which is how
&#x20; it is known to be measuring the same thing the existing record does.

\- \*\*TWO DATA-LOCATION FACTS that cost time in the 2026-08-29 emergency
&#x20; diagnosis — record them so the next session doesn't re-derive them.\*\*
&#x20; (1) \*\*Stage 3, Stage 4 and Stage 5 TensorBoard event files all live under
&#x20; `training/checkpoints/stage2/tb/MaskablePPO_1/`\*\*, NOT under their own
&#x20; `stageN/tb/`. `MaskablePPO.load()` restores `tensorboard_log` from the
&#x20; checkpoint, and Stages 3->4->5 resumed the Stage 2 lineage, so every
&#x20; resumed run keeps writing to Stage 2's directory. Identify which run a
&#x20; given event file belongs to by matching its filename Unix timestamp to the
&#x20; `t_start` in the matching `stageN/monitor*.csv` header. `train.py` sets
&#x20; `tensorboard_log=str(stage_dir / "tb")` only on a FRESH model (Burst A);
&#x20; a resume never re-points it. \*\*Scalars available\*\*: `rollout/ep_rew_mean`,
&#x20; `rollout/ep_len_mean`, `train/{value_loss, explained_variance, entropy_loss,
&#x20; approx_kl, clip_fraction, policy_gradient_loss, loss, learning_rate}` — one
&#x20; point per rollout (2048 steps). No per-state value predictions are logged
&#x20; anywhere; `explained_variance` / `value_loss` are the only critic-fit signal.
&#x20; (2) \*\*No per-term reward decomposition exists for Stage 4 anywhere in the
&#x20; repo.\*\* `reward_term_pre51k.json` / `reward_term_replay.json` /
&#x20; `det_stoch_diag.json` carry `sum_starvation_penalty` / `sum_throughput_bonus`
&#x20; / `sum_emergency_penalty` / `sum_switch_penalty` — but ONLY for
&#x20; `graph_attention` checkpoints. `stage4_*.json` and `checkpoint_bakeoff.json`
&#x20; carry `mean_reward` / `ovrE` / `ovrS` and no term split. To get Stage 4's
&#x20; term shares, reconstruct from `stage4/monitor*.csv` via the reward identity
&#x20; `total = throughput_bonus - starvation - emergency - switch` with
&#x20; `throughput_bonus = 0.25 * arrived` and `arrived ~= 4666.7 *
&#x20; (0.357*density_mult_corridor + 0.643*density_mult_cross)` — the split that
&#x20; matches 2x1000 + 6x600 veh/h over 3000s. A first pass of that diagnosis
&#x20; wrongly used `graph_attention` per-step terms for Stage 4 and had to be
&#x20; corrected.

\- \*\*STANDING RULE (Phase 9): `backend/` is a hard TraCI-single-thread
&#x20; boundary.\*\* `backend/sim_runner.py`'s `SimRunner` thread is the ONLY code
&#x20; that may call `env.step()/reset()` or anything touching TraCI. FastAPI
&#x20; handlers and the WebSocket `Hub` never do — control endpoints push a
&#x20; `Command` onto `ControlState.pending` which the sim thread drains between
&#x20; decision steps; `get_stats()` reads a lock-protected cache the sim thread
&#x20; publishes. `enable_safety_validator` is not referenced anywhere under
&#x20; `backend/` except the three standing-rule comments citing this — the §20
&#x20; pre-event grep should treat those as the expected hits.

\- \*\*PHASE 8 SEAM (Phase 9 → Phase 8 handoff): the §13.2 frame's `decision`
&#x20; and `narration` fields are produced by a thin adapter in
&#x20; `backend/sim_runner.py`\*\* (`_decision_entry` / `_narrate`), built from
&#x20; `Tier0Controller.act()`'s `decisions` return + `info["safety_overrides"]`
&#x20; + `info["reward_breakdown"]`, emitting the frozen §12.1 / §12.2 shapes.
&#x20; Phase 8's `explainability/{decision_log,narrator,query_interface}.py`
&#x20; replace the adapter; the wire schema does not move.
&#x20; \*\*STATUS UPDATE (2026-08-28): PHASE 8 HAS LANDED (commit `9cf19af`).\*\* This
&#x20; bullet was written while it was still in flight and described it as pending.
&#x20; The three `explainability/` modules and `coordinator/` now exist with their
&#x20; done-bar verified. \*\*CORRECTED 2026-08-29 — this bullet said "items (2) and
&#x20; (3) remain open" while the Phase 8 bullet further down said all three are
&#x20; CLOSED. The Phase 8 bullet is right; ALL THREE ARE CLOSED.\*\* (1) `"rl_policy"`
&#x20; is final and asserted in `decision_log._selftest`; (2) `explainability/
&#x20; narrator.py` carries final `starvation_ceiling` and `rl_policy` templates —
&#x20; note the PLACEHOLDER wording still sitting in `sim_runner._NARRATION` is the
&#x20; ADAPTER's copy, which is exactly what the swap deletes, not an open Phase 8
&#x20; item; (3) `record_step` accepts empty `score_breakdown`/`alternative_scores`
&#x20; under RL mode and exempts them from its phase-key check.
&#x20; \*\*THE SEAM IS NOW CLOSED (2026-08-29, commit `ad9e4df`). The adapter is DELETED.\*\*
&#x20; This bullet previously ended "what IS actually outstanding at this seam is one
&#x20; thing only: the adapter in `backend/sim_runner.py` has not been swapped out for
&#x20; the real modules." It has been. `_NARRATION`, `_decision_entry()`,
&#x20; `_reason_for()`, `_representative_lane()` and `_narrate()` are gone from
&#x20; `backend/sim_runner.py`; the §13.2 frame's `decision` is
&#x20; `DecisionLogEntry.to_dict()` and its `narration` is
&#x20; `explainability.narrator.narrate(entry)`. `_emit_junction()` survives as a pure
&#x20; selector over the entries `record_step` returns, with the same precedence it
&#x20; always had (emergency override > starvation ceiling > lowest switched index >
&#x20; rotate), so the wire schema did not move. §11.2 responder messages are now on
&#x20; the wire too, ADDITIVE and only when non-empty.
&#x20; §11.2's `clearance_time_s` was BUILT correctly by Phase 8 (per-junction
&#x20; attribution, generalised from B2) — the PHASE 8 WARNING above is SATISFIED,
&#x20; not outstanding. \*\*But see the `served_on_arrival` open item below\*\* — it is
&#x20; the one thing at this seam that is NOT closed.
&#x20; Original open items, each marked `# PHASE 8 SEAM` in code — all three now
&#x20; closed per the correction above: (1) the sixth
&#x20; `reason` value for a no-override RL decision is used as `"rl_policy"`
&#x20; (Session 2's stated string) — confirm and finalise; (2) §12.2 defines
&#x20; only 4 narration templates — `starvation_ceiling` and `rl_policy` have
&#x20; placeholder wording; (3) `score_breakdown`/`alternative_scores` are `{}`
&#x20; under RL control — Phase 8's decision log owns the RL-mode breakdown.
&#x20; Auto-mode's override classification checks BOTH `emergency_override` and
&#x20; `starvation_ceiling` (emergency outranks, then lowest corridor index),
&#x20; mirroring Phase 8's reconciliation order. §11.2 responder messaging is
&#x20; NOT in §13.2's frame and is not emitted by Phase 9 — it stays Phase 8 /
&#x20; §11.2. \*\*CORRECTED 2026-08-29: this sentence used to end "and the PHASE 8
&#x20; WARNING above about `clearance_time_s` still applies to whoever builds it",
&#x20; contradicting the STATUS UPDATE at the top of this same bullet. Phase 8
&#x20; already built it correctly\*\* (`coordinator/emergency_clearance.py`,
&#x20; per-junction attribution + 0.0 floor). Responder messaging is still not on
&#x20; the §13.2 wire — putting it there is part of the adapter swap.

\- \*\*`set_baseline_mode("greedy")` is PLUMBED BUT STUBBED\*\* — the §13.1
&#x20; switch, its state flag and stream echo all exist, but it returns
&#x20; `{"applied": false, "reason": "...Phase 12..."}` because the Greedy
&#x20; controller itself is a Phase 12 deliverable (§18) and CLAUDE.md §3
&#x20; forbids building ahead (same precedent as Phase 4's Greedy note). §19
&#x20; names Greedy-vs-PsychoFlow as the strongest demo beat — Phase 12 needs
&#x20; real rehearsal runway before the event.

\- \*\*Phase 8 (Coordinator + Explainability, §11/§12) commands\*\*:
&#x20; `python sim/run_explainability_episode.py` (done-bar harness — two rule-based
&#x20; segments, 8-step check: decision-log structure + override reconciliation,
&#x20; narration of all 6 reasons, "why" queries, §11.2 responder messages) and the
&#x20; five module self-tests, no SUMO process, run after ANY change to those
&#x20; modules: `python -m explainability.decision_log` / `explainability.narrator`
&#x20; / `explainability.query_interface` / `coordinator.emergency_clearance` /
&#x20; `coordinator.responder_messaging`.
&#x20; \*\*§11.1's coordinator emits `EmergencyClearanceEvent`s only — it does NOT
&#x20; move vehicles.\*\* The visible "vehicles part for the ambulance" is Phase 10
&#x20; frontend animation fed by that event stream; moving vehicles via TraCI
&#x20; fights SUMO's car-following model. Green onset is per-junction
&#x20; (`sim_time - time_since_switch_s`, B2's method) — the PHASE 8 WARNING fix
&#x20; above; if a phase was already green on arrival, `clearance_time_s` floors at
&#x20; 0.0 with `served_on_arrival=True` rather than reporting a negative latency.
&#x20; \*\*Two triggers, one clearance event (added 2026-08-29).\*\* §10's emergency
&#x20; branch fires on EITHER a sensed ambulance OR a lane in
&#x20; `forced_emergency_lanes` (§13.1's `trigger_emergency`), so
&#x20; `EmergencyClearanceCoordinator.observe()` takes
&#x20; `forced_emergency_lanes: frozenset[str] = frozenset()` — same name, type and
&#x20; default as `safety.validator.validate()` — and UNIONS the two per junction.
&#x20; \*\*Pass the SAME tracked set you hand the validator, not a second copy\*\*;
&#x20; `backend/sim_runner.py` computes `forced = frozenset(self._forced)` once per
&#x20; step and gives it to both. `EmergencyClearanceEvent.source` is
&#x20; `"detected"` | `"operator"`, DETECTION WINS on overlap, and it is fixed when
&#x20; the episode opens and never mutated — it is the provenance of the TRIGGER,
&#x20; so an ambulance arriving later at an operator-opened junction does not
&#x20; relabel it. §11.2 reports it as `trigger_source` and the summary wording
&#x20; follows it. Omitting the kwarg reproduces detection-only behaviour exactly
&#x20; (pinned by a `to_dict()`-equality assertion in the self-test).
&#x20; \*\*§11.2's `baseline_clearance_time_s` is a LABELLED MODEL ESTIMATE\*\*
&#x20; (`baseline_is_estimate: true` in the payload), a conservative
&#x20; signal-rotation estimate — NOT a measured counterfactual. `clearance_time_s`
&#x20; itself IS real (per-junction detection -> green).
&#x20; The three Phase 9 seam items (the backend note above) are CLOSED: `rl_policy`
&#x20; string final; `starvation_ceiling`/`rl_policy` narration templates final;
&#x20; empty `score_breakdown`/`alternative_scores` accepted under RL mode by
&#x20; `DecisionLog.record_step` and exempted from its phase-key structural check.

\- \*\*A `DecisionLog` covers exactly ONE episode — replace it on reset, never
&#x20; reuse it (added 2026-08-29).\*\* `record_step`/`record_voice` RAISE on a
&#x20; `sim_time` earlier than the highest already recorded. The reason it must
&#x20; raise rather than sort: `entries_for` / `latest` / §12.3's `why()` read the
&#x20; deque POSITIONALLY and take the last match, so an out-of-order entry raises
&#x20; nothing and instead makes every later at-or-before query answer with the
&#x20; wrong decision — a run that passes while proving nothing. `env.reset()`
&#x20; sends sim_time back to ~0, so a log carried across an episode boundary hits
&#x20; this on its first post-reset step. `backend/sim_runner.py::_reset_counters`
&#x20; REPLACES `self._log`, `self._query` and `self._coord` (all three are
&#x20; per-episode) and is called after BOTH the natural episode end and a
&#x20; `set_topology` rebuild. Equal timestamps stay legal (one entry per junction
&#x20; per step; a voice entry may share an instant). Covered by
&#x20; `decision_log._selftest` scenario 8 (offline) and `run_backend_smoke.py`
&#x20; checks 1d/1e/1f + 5c (live, across a real reset).

\- \*\*Two snapshots per backend step, and the order is load-bearing (added
&#x20; 2026-08-29).\*\* `env.step()` rebinds the twin's snapshot
&#x20; (`twin.update()` builds a NEW dict), so `backend/sim_runner.py` holds two
&#x20; distinct objects per iteration. The \*\*PRE-step\*\* snapshot goes to
&#x20; `DecisionLog.record_step()` — it is what §10's validator judged the action
&#x20; against (`psychoflow_env.py` step 2b) and what the observation was built
&#x20; from, so an override's `lane_id`/`wait_s` and §12.1's triggering-lane pick
&#x20; both resolve against the state the decision was actually made on. The
&#x20; \*\*POST-step\*\* snapshot is the frame's `digital_twin` field and what §11.1's
&#x20; clearance coordinator observes (matching `run_explainability_episode.py`).
&#x20; \*\*Swapping them fails SILENTLY\*\* — lane ids exist in both, so every field
&#x20; still populates, just describing the wrong instant.

\- \*\*OPEN ITEM (§11.2, found 2026-08-29 during the Phase 8 adapter swap):
&#x20; `served_on_arrival` can be reported for a lane that was NOT already clear,
&#x20; and the operator-facing summary then says so in words.\*\* NOT fixed — the
&#x20; fix is a §11.1 semantic change and the call is the user's.
&#x20; \*\*Mechanism, traced not inferred:\*\* §11.1's `observe()` is called with
&#x20; `info["sim_time"]` (POST-step) so `first_detection_sim_time` is stamped on
&#x20; the 5s decision grid, while `green_onset` is recovered as
&#x20; `sim_time - time_since_switch_s` at true 1s resolution. When §10 clears the
&#x20; lane INSIDE the same decision step, green onset lands BEFORE detection and
&#x20; `served_on_arrival` fires. Measured on the live backend, operator trigger on
&#x20; `N1_J1_0` at J1:
&#x20; `t=90.0 forced=['N1_J1_0'] ovr=[('J1','emergency_override',1,0,'applied')]`
&#x20; -> `det=90.0 green_onset=88.0 green_age=2.0 clearance=0.0 on_arrival=True`.
&#x20; The lane went green at t=88, ~2s after the request; the true latency is
&#x20; bounded by the 5s decision interval, and 3.0s (90-3 detection at the
&#x20; PRE-step boundary) is the honest figure.
&#x20; \*\*Why it matters:\*\* §11.2 is the one place in this system a number and a
&#x20; sentence are shown to a HUMAN as decision support (master plan §11.2's own
&#x20; blocker note), and the summary currently reads "was already clear when the
&#x20; clearance was requested" about a lane the shield had to clear.
&#x20; \*\*Pre-existing, not introduced by the swap\*\* — `run_explainability_episode.py`
&#x20; calls `observe(info["sim_time"], ...)` the same way and BUILD_LOG's Phase 8
&#x20; caveat 1 records the same 0.0s `served_on_arrival` result. The swap is what
&#x20; put it on an operator-facing wire.
&#x20; \*\*Candidate fixes, neither applied:\*\* stamp detection at the PRE-step
&#x20; sim_time (the boundary the operator's request was actually live from), or
&#x20; floor `green_onset` at the previous decision boundary. Either changes
&#x20; §11.1's recorded numbers, so re-run `python -m coordinator.emergency_clearance`
&#x20; and `sim/run_explainability_episode.py` and re-record the Phase 8 figures.

\- \*\*STANDING RULE — NEVER launch SUMO while another process is driving it.
&#x20; Check the Tier 1 beacon first: `python -m sim.sumo_activity`\*\* (prints
&#x20; `free` or `HELD by pid=... kind=... for N min — note`).
&#x20; \*\*Why this exists:\*\* two coordination failures in one evening. A
&#x20; multi-SUMO sweep launched into the live D1 training run killed it with
&#x20; `FatalTraCIError: Could not connect`; separately, a leg3/leg4 collision
&#x20; between concurrent sessions. In the first case the session DID check, saw a
&#x20; live SUMO process and a fresh training log, and talked itself out of both —
&#x20; which is the argument for a mechanism that returns a hard refusal rather
&#x20; than a signal needing interpretation.
&#x20; \*\*How it works\*\* (`sim/sumo_activity.py`): long-running SUMO owners
&#x20; (`training/train.py` via `_SumoBeaconCallback`, `backend/sim_runner.py`'s
&#x20; loop) call `beat()` periodically, writing `.sumo_active.json` (gitignored).
&#x20; Every SUMO-launching harness calls `require_free("<label>")` as the FIRST
&#x20; statement inside its `if __name__ == "__main__":` guard — inside the guard
&#x20; on purpose, because several harnesses import each other and the check must
&#x20; fire on invocation, never on import. \*\*14 harnesses are instrumented\*\*
&#x20; (`training/scripts/*.py`, `training/evaluate_stage.py`, `sim/run_*.py`);
&#x20; \*\*any new harness that launches SUMO must add the same two lines.\*\*
&#x20; \*\*"Launches SUMO" means it calls `env.reset()`\*\* — that is where
&#x20; `traci.start()` lives. Merely importing or CONSTRUCTING a `PsychoFlowEnv`
&#x20; starts nothing. Two scripts deliberately have NO guard and must keep none:
&#x20; `training/scripts/stage4_contamination.py` and `evaluation/heldout.py`,
&#x20; which set `env._rng` and call `_draw_scenario()` only. Guarding them would
&#x20; protect nothing AND make them un-runnable during training — exactly when
&#x20; you most want to ask whether an eval seed collides with the training draw.
&#x20; Both carry a NOTE block saying so, because the obvious "fix" is to add the
&#x20; guard back. (One WAS wrongly guarded in 49e621b by a classifier that
&#x20; matched the `PsychoFlowEnv` import rather than a real launch; caught and
&#x20; stripped the same evening.)
&#x20; \*\*Self-clearing\*\*, so a stale beacon can never block work: ignored if the
&#x20; PID is dead OR the file is older than `STALE_AFTER_S` (300s). Override with
&#x20; `PSYCHOFLOW_IGNORE_SUMO_BEACON=1` — it proceeds but prints what it ignored.
&#x20; \*\*It is a BEACON, not a lock\*\* — no queueing or blocking, and two sweeps
&#x20; can still collide with each other. Tier 2 (acquire/release, lock classes,
&#x20; instance caps) was DEFERRED on schedule grounds, not because the case is
&#x20; weak; see `sim/sumo_activity.py`'s docstring.
&#x20; \*\*Two traps when testing this — both produced false negatives once:\*\*
&#x20; (1) `os.kill(pid, 0)` is the POSIX liveness idiom but on Windows routes to
&#x20; TerminateProcess and would KILL the training run — use
&#x20; `psutil.pid_exists()`, which this module does. (2) Git Bash's `$!` is an
&#x20; MSYS pid, NOT the Windows pid `psutil` sees, so planting a test beacon from
&#x20; bash reads as dead and the harness runs anyway. Drive the test from Python
&#x20; with `subprocess.Popen(...).pid`.

\- \*\*DEMO-ONLY MIXED-TRAFFIC DRIVING MODEL (STEP 1, 2026-08-31; REFINED to
&#x20; aggressiveness tiers same day). Built and measured. NEVER reachable from
&#x20; training or evaluation.\*\*
&#x20; \*\*STATUS CORRECTION 2026-09-02: "verified" overstated it and is withdrawn.\*\*
&#x20; The mechanical guarantees below ARE verified (byte-identical default argv,
&#x20; the `train.py` assert, the backend default-off switch — all re-checked). What
&#x20; is NOT done is the model's actual DONE-BAR: \*\*a human watching `sumo-gui`\*\*.
&#x20; Until that happens this work is unsigned-off AND uncommitted — see §10/§11
&#x20; and the CURRENT STATUS bullet. Read this bullet for the SEAM (how the demo
&#x20; model is reached and how it is kept off the training path); read
&#x20; `docs/MIXED_TRAFFIC_RESEARCH.md` §6 for the shipped model's actual numbers,
&#x20; since the ones quoted here are STEP 1's untiered arm.
&#x20; \*\*The file:\*\* `sim/networks/vehicle_types_demo.add.xml`. The default
&#x20; `sim/networks/vehicle_types.add.xml` is UNCHANGED, byte-for-byte, and remains
&#x20; the only file training/eval/the 4a bake-off ever load.
&#x20; \*\*The seam:\*\* `PsychoFlowEnv.__init__` gained two kwargs, both defaulting to
&#x20; today's behaviour — `vtype_file=ADD_FILE` and `lateral_resolution=None`.
&#x20; With both omitted the `traci.start()` argv is \*\*BYTE-IDENTICAL to pre-STEP-1
&#x20; HEAD (18 tokens, asserted against `git show HEAD:` — not inspected)\*\*. Passing
&#x20; `lateral_resolution` also appends `--collision.action warn` and
&#x20; `--collision.mingap-factor 0`; neither appears on the default path.
&#x20; \*\*The guard:\*\* `training/train.py` asserts `vtype_file == ADD_FILE` and
&#x20; `lateral_resolution is None` before `model.learn()`, beside the existing
&#x20; spillover assert. Verified non-vacuously: passes on default, raises on each
&#x20; demo kwarg. \*\*Backend:\*\* `backend/main.py --demo-driving` (default OFF) ->
&#x20; `SimRunner(demo_driving=...)` -> `DEMO_VTYPE_FILE` /
&#x20; `DEMO_LATERAL_RESOLUTION` (0.4) in `backend/sim_runner.py`.
&#x20; \*\*THE ONE TRAP THAT COST FOUR TUNING PASSES — `tau` MUST BE >= `STEP_LENGTH_S`
&#x20; (1.0).\*\* bike tau=0.5 / auto tau=0.6 produced \*\*14.55% of all vehicles in a
&#x20; collision\*\* (272 of 1870). Krauss's safe-velocity cannot guarantee a
&#x20; collision-free follow below the step size. Diagnosed by measurement, not
&#x20; guesswork: \*\*3557 of 3565 collision events were on-lane, only 8 at junctions\*\*,
&#x20; so the `jm*` group was innocent and two earlier guesses (lcMaxSpeedLatStanding,
&#x20; then lateral softening) were both wrong. Raising tau to 0.9/1.0/1.2/1.6/1.0
&#x20; gave \*\*0.00% collisions\*\* while KEEPING the per-type ordering. If you ever
&#x20; lower tau again, re-measure collisions — do not assume.
&#x20; \*\*`lcMaxSpeedLatStanding` is load-bearing and must stay set explicitly:\*\* it
&#x20; defaults to 0 for every vClass except bicycle/pedestrian, so a STOPPED `auto`
&#x20; cannot move sideways at all and queue-front filtering becomes impossible
&#x20; regardless of `minGapLat`/`lcPushy`.
&#x20; \*\*Honest boundary (§17 class):\*\* this approximates mixed traffic with SUMO's
&#x20; sublane model, the best lever SUMO has. It is NOT lane-free driving — the road
&#x20; is still lanes with centre-lines and lane-to-lane connections; SL2015 adds a
&#x20; continuous LATERAL position. Say that, not "lane-free".
&#x20; \*\*Every recorded number was measured WITHOUT this.\*\* Never re-measure a
&#x20; checkpoint under demo driving and compare to a recorded figure.

\- \*\*STEP 1 MEASURED RESULTS (2026-08-31) — the A/B that signed it off.\*\*
&#x20; \*\*SUPERSEDED 2026-09-02 FOR THE SHIPPED MODEL — this bullet describes STEP
&#x20; 1's UNIFORM (untiered) arm, which is NOT what the file ships today.\*\* The
&#x20; shipped model is TIERED, and its numbers are different: `bike_over_car`
&#x20; \*\*31.73%\*\* (not 37.13%), bike mean advancement \*\*+1.717\*\* (not +1.949),
&#x20; collisions \*\*0.00%\*\* only after the 2026-09-01 truck lateral-recentring fix.
&#x20; \*\*Worse, this bullet's BASELINE column is not a valid comparator at all\*\* —
&#x20; STEP 1's baseline does not reproduce bit-for-bit (1868 vehicles vs its
&#x20; recorded 1870; `bike_over_car` 5.61% vs the 4.17% below), so the 4.17% ->
&#x20; 37.13% contrast spans two different route files. \*\*Cite
&#x20; `docs/MIXED_TRAFFIC_RESEARCH.md` §6, which is same-route-file throughout,
&#x20; for any current figure.\*\* Retained below because the METHOD (1s resolution,
&#x20; the advancement metric's definition, the speed calibration, the item-5
&#x20; spawn-patterning finding) is still exactly right and is what §6 re-ran.
&#x20; Harness `measure_driving.py` (`sim/mixed_traffic/`): standalone SUMO, netconvert's
&#x20; static TLS, \*\*1s resolution\*\* (5s would alias the crossing entirely — a
&#x20; 13.9 m/s vehicle covers 69m in 5s), corridor 4/3/2, seed 7, 1200s, 1870
&#x20; vehicles, 151-152 red->green queue samples. No `PsychoFlowEnv`, no checkpoint
&#x20; — it measures the DRIVING MODEL, so it cannot perturb Stage 4.
&#x20; \*\*Item 1, queue-front filtering — CONFIRMED, and the ordering INVERTED.\*\*
&#x20; Metric: at each green onset, rank queued (halted) vehicles by arrival time
&#x20; and by distance to the stop line; advancement = arrival\_rank − stopline\_rank,
&#x20; positive = filtered forward.
&#x20; | | baseline | demo |
&#x20; |---|---|---|
&#x20; | bike mean advancement | \*\*−1.578\*\* | \*\*+1.949\*\* |
&#x20; | `bike_over_car` | 4.17% | \*\*37.13%\*\* |
&#x20; | `bike_over_truck` | 10.81% | \*\*41.18%\*\* |
&#x20; | `car_over_bike` | 25.00% | 11.58% |
&#x20; | `truck_over_bike` | 17.50% | 8.82% |
&#x20; | collisions | 0.00% | \*\*0.00%\*\* |
&#x20; Baseline: a car overtook a bike \*\*6x\*\* more often than the reverse. Demo: a
&#x20; bike overtakes a car \*\*3.2x\*\* more often than the reverse. `auto` sits mid-pack
&#x20; by design (gains on car/truck, loses to bike), which is why its mean
&#x20; advancement is slightly negative — expected, not a regression.
&#x20; \*\*Item 2, urban speed calibration.\*\* Baseline car reached \*\*16.70 m/s = 60.1
&#x20; km/h\*\* (its own maxSpeed, above the road's 50 km/h limit, since SUMO's default
&#x20; speedFactor spread allows ~1.2x) — a highway desired speed on a 300m-spaced
&#x20; signalised arterial. Demo, observed mean\_moving / p85 / max in \*\*km/h\*\*:
&#x20; bike \*\*30.9 / 37.0 / 39.6\*\*, auto \*\*27.8 / 33.0 / 34.2\*\*,
&#x20; car \*\*33.1 / 43.1 / 45.0\*\*, truck \*\*28.0 / 35.4 / 36.0\*\*,
&#x20; ambulance \*\*38.9 / 48.6 / 53.2\*\*. \*\*`DEFAULT_SPEED_MPS` (13.89 m/s) in
&#x20; `generate_corridor.py` is NOT touched\*\* — changing it would require
&#x20; regenerating all 27 networks, including the ones every checkpoint trained on.
&#x20; \*\*Item 5, spawn variety — TOO PATTERNED, reported not fixed.\*\*
&#x20; `write_route_file` uses `<flow vehsPerHour=...>`, which SUMO inserts
&#x20; \*\*equally spaced\*\*. Measured: every cross route has gap \*\*exactly 6.000s,
&#x20; stdev 0.0000, ONE distinct gap value\*\*; corridor routes alternate 3.0/4.0s
&#x20; (3600/1000 = 3.6s rounded to the 1s step). Within-episode variety today is
&#x20; only `departLane="random"` + the vType draw. The fix, if wanted, is
&#x20; `probability=` (Bernoulli/Poisson-ish) instead of `vehsPerHour=`, and/or
&#x20; randomised `departPos`. \*\*NOT changed\*\* — it would alter the scenario draw
&#x20; that every recorded number depends on. Cross-run reproducibility is
&#x20; unaffected either way and is a feature.

\- \*\*ITEM 3 — "the signals switch too fast" DISENTANGLED (2026-08-31,
&#x20; measurement only; Stage 4 timing NOT touched).\*\* Three separate things:
&#x20; \*\*(a) GUI `--delay` is cosmetic, zero effect on anything measured.\*\* It is a
&#x20; `sumo-gui` wall-clock sleep between rendered steps and does not enter the
&#x20; simulation. Proof: the headless runs carry no delay concept at all and
&#x20; reproduce Stage 4's recorded row bit-for-bit. At `--delay 120` a 15s green is
&#x20; 1.8 wall-seconds; at `--delay 300` it is 4.5. That alone explains most of the
&#x20; "fast" impression.
&#x20; \*\*(b) Vehicle speed does NOT meaningfully change actual durations.\*\* Measured
&#x20; A/B, same checkpoint/corridor/seed, baseline vs demo driving: median
&#x20; slot-interval \*\*15.0s in BOTH\*\*, mean 19.88 -> 20.58s, p75 25.0 both, arrived
&#x20; \*\*4668 in both\*\*. So faster/denser traffic changes the LOOK (more motion per
&#x20; phase), not the cadence.
&#x20; \*\*(c) THE REAL NUMBER, Stage 4 @153,600 on (4,3,2) seed 7, 465 phase changes
&#x20; over a 3145s episode:\*\* slot-to-slot interval \*\*min 15.0s, p25 15.0, median
&#x20; 15.0, p75 25.0, p90 25.0, max 50.0, mean 19.88s\*\*; per junction J1 20.03 /
&#x20; J2 19.69 / J3 19.94; ~150-159 switches each. \*\*This interval INCLUDES the
&#x20; ~3s yellow\*\* (it is measured green-slot-to-green-slot), so effective green is
&#x20; ~12s and ~22s — a bimodal 15/25 pattern giving a ~40s cycle per junction.
&#x20; Constants that shape it: `DECISION_INTERVAL_S=5.0`, `MIN_GREEN_S=10.0`.
&#x20; \*\*So "fast" is mostly playback, but ~12s effective green is genuinely short
&#x20; for an urban arterial — both are true, say both.\*\* Command:
&#x20; `measure_phases.py` (`sim/mixed_traffic/`), `--demo` for the demo-driving arm.
&#x20; \*\*Stage 4's decision timing is LOCKED (§2/§16) and was not modified.\*\*

\- \*\*`docs/MIXED_TRAFFIC_RESEARCH.md` is the evidence base for the demo driving
&#x20; model — read it before retuning `vehicle_types_demo.add.xml` (added
&#x20; 2026-08-31).\*\* It separates, explicitly and by section, three things this
&#x20; project must not let blur together: \*\*§1 SOURCED\*\* findings from Indian
&#x20; traffic-engineering literature (relayed second-hand in the refinement brief,
&#x20; \*\*not\*\* retrieved or verified here — directional evidence, never quotable as
&#x20; something PsychoFlow measured); \*\*§2 REASONED DEFAULTS\*\* (the tier split
&#x20; ratios and every parameter value — engineering starting points, \*\*no sourced
&#x20; proportion anywhere in that section\*\*); and \*\*§3 MEASURED IN THIS REPO\*\*
&#x20; (the only numbers that are ours, each with its harness and raw-data path).
&#x20; Four things in it are load-bearing beyond the tuning: \*\*(a)\*\* item 2 is
&#x20; SETTLED and the overtake-logic worry is CLOSED — `lcSpeedGain` is correctly
&#x20; speed-gated at BOTH the wish level (rate 0.0100 -> 0.2737, a 27.4x span) and
&#x20; the outcome level (\*\*exactly ZERO passes\*\* where the leader is already at or
&#x20; above the follower's desired speed). \*\*An earlier reading in this same file
&#x20; said execution was ungated; it is WITHDRAWN\*\* — that came from bucketing each
&#x20; pass by the delta at relationship OPEN, and a `≤0` relationship runs 16-17s
&#x20; median while the leader slows. \*\*Bucket outcomes at the PASS INSTANT, never
&#x20; at open.\*\* What IS real is VOLUME: the demo completes ~2x the baseline's
&#x20; passes (1527 vs 818), which is what reads as constant overtaking on screen.
&#x20; \*\*(b)\*\* §4's structural consequence — `getTypeID()` returns the
&#x20; CONCRETE sub-type (`bike.normal`), so `perception/lane_sensor.py`'s
&#x20; `VEHICLE_TYPES` match silently zeroes `type_composition` (obs
&#x20; `LF_TYPE_START+0..4`) for every tiered type. Ambulance is single-type so §10
&#x20; is unaffected; the fix is a base-name resolution that is inert on the default
&#x20; file. \*\*(c)\*\* §4b — a SECOND silent breakage the same audit found:
&#x20; `traci.vehicletype.getTau("bike")`/`setTau` do NOT raise on a distribution id,
&#x20; they resolve to \*\*one RANDOMLY SAMPLED member\*\* (measured: a read hit
&#x20; `bike.aggressive`, the next write hit `bike.normal`, 1 of 3 tiers reached), so
&#x20; `perception/weather.py` would have made §7.4 ~1/3 true, silently and
&#x20; non-reproducibly. Fixed via `WeatherModel._resolve_members()`. \*\*Never address
&#x20; a vType by base name under the demo model — resolve to concrete members.\*\*
&#x20; \*\*(d)\*\* §2.4 — `tau` MUST be >= `STEP_LENGTH_S` (1.0); every tier now
&#x20; complies and SUMO's load warning is gone.
&#x20; Harnesses (all now in `sim/mixed_traffic/`): `measure_overtake.py` (items 2/3),
&#x20; `verify_perception_fixes.py` (the BEFORE/AFTER type_composition evidence),
&#x20; `probe_dist.py` / `probe_weather_propagation.py` (the two SUMO-semantics
&#x20; probes) — all standalone raw SUMO, no env, no checkpoint.
&#x20; \*\*The baseline arm is a real control, not decoration\*\*: LC2013 has no
&#x20; sublane, so its strict lane-sharing share MUST read 0.00%, and that is what
&#x20; caught a same-edge-vs-same-lane bug in the first classifier.
&#x20; \*\*STEP 1's recorded baseline does NOT reproduce bit-for-bit\*\* (1868 vehicles
&#x20; vs its recorded 1870; `bike_over_car` 5.61% vs 4.17%), so its recorded
&#x20; \*\*37.13%\*\* demo figure is NOT a valid comparator — re-run STEP 1's own table
&#x20; on the same route file instead, as §6 does.

\- \*\*PHASE 10 BEHAVIOR SPEC — what the frontend must know, so it is not
&#x20; re-derived during the event (recorded 2026-08-31).\*\*
&#x20; \*\*1. Vehicles do NOT stay in arrival order. Render actual position every
&#x20; frame; never infer order from arrival.\*\* Measured (above): a bike that
&#x20; arrives later reaches the stop line ahead of an earlier car \*\*31.7%\*\* of the
&#x20; time on the SHIPPED TIERED model (5.6% on the lane-disciplined baseline
&#x20; control), and bike mean advancement is \*\*+1.717\*\* queue places against the
&#x20; baseline's \*\*−1.699\*\*, i.e. the ordering INVERTS. \*\*(Corrected 2026-09-02:
&#x20; this line previously read 37.1% / 4.2% / +1.949, which are STEP 1's untiered
&#x20; arm measured on a route file that does not reproduce. The conclusion is
&#x20; unchanged and if anything the per-tier spread makes it stronger — see the
&#x20; next sentence.)\*\* Per tier the effect separates further —
&#x20; bike.aggressive \*\*+2.500\*\*, bike.normal \*\*+1.462\*\*, bike.cautious
&#x20; \*\*+0.889\*\* — so not all bikes behave alike and a renderer that treats them
&#x20; as one class loses visible detail.
&#x20; A frontend that draws a queue as an arrival-ordered list \*\*will be visibly
&#x20; wrong\*\*, most obviously for bikes and autos. Vehicles also have a continuous
&#x20; LATERAL offset within a lane and may straddle a lane boundary or sit
&#x20; two-abreast — position is `(x, y)`, not `(lane, index)`.
&#x20; \*\*2. Speed ranges for animation sanity-checks (km/h, mean\_moving / p85 /
&#x20; max):\*\* bike 30.9 / 37.0 / 39.6 · auto 27.8 / 33.0 / 34.2 · car 33.1 / 43.1 /
&#x20; 45.0 · truck 28.0 / 35.4 / 36.0 · ambulance 38.9 / 48.6 / 53.2. If rendered
&#x20; motion implies something far outside these, the animation timing is wrong,
&#x20; not the sim.
&#x20; \*\*3. Signal phase durations:\*\* expect a switch every \*\*15-25s of SIM time\*\*
&#x20; (median 15, mean ~20, occasional 30-50), ~40s cycle per junction, ~3s yellow
&#x20; inside each interval. A judge asking "why did it just switch?" is asking
&#x20; about a real 15-25s number, and §12.1's `decision.reason` on that frame is
&#x20; the answer. Note SIM time != wall time — pacing is `realtime_factor`.
&#x20; \*\*4. The frontend NEVER simulates or approximates any of this.\*\* Every value
&#x20; above arrives as live TraCI-derived state on the §13.2 WebSocket frame, every
&#x20; frame, already computed correctly by the backend. Render what is on the
&#x20; frame; do not interpolate physics, re-order queues, or infer signal state.
&#x20; \*\*5. Positions are in the netconvert-shifted frame\*\* (J1 is at (150,150), not
&#x20; the authored (0,0)) — read coordinates from the net file, never from
&#x20; `generate_corridor.py`'s parameters. See the existing standing rule above.
&#x20; \*\*6. AGGRESSIVENESS TIERS EXIST IN THE DATA — optional for Phase 10, not
&#x20; required (added 2026-08-31 with the STEP 1 refinement).\*\* Under demo driving
&#x20; the vehicle types are `bike.cautious` / `bike.normal` / `bike.aggressive`,
&#x20; `auto.{cautious,normal,aggressive}`, `car.{normal,aggressive}`, plus untiered
&#x20; `truck` / `ambulance`. \*\*The §13.2 frame does NOT carry the tier today\*\* —
&#x20; §7.1's `type_composition` is keyed by BASE type (`bike`, `auto`, …) and stays
&#x20; that way, because the observation schema (§9.2's `LF_TYPE_START+0..4`) is
&#x20; locked to five types. If the frontend ever wants to distinguish them visually
&#x20; (a faster, more weaving bike reading as a different sprite), the tier is
&#x20; available from `traci.vehicle.getTypeID()` and would need a new ADDITIVE
&#x20; frame field — do not repurpose `type_composition` for it. Per-tier behaviour
&#x20; is genuinely distinguishable on screen: measured mean queue advancement is
&#x20; bike.aggressive \*\*2.500\*\* / bike.normal \*\*1.462\*\* / bike.cautious
&#x20; \*\*0.889\*\*, and mean moving speed 31.3 / 29.3 / 27.5 km/h.

\- (Add training/test/run commands here as each phase is built — this

&#x20; section should grow; keep it accurate, delete anything that stops

&#x20; being true.)



\## 9. Keeping this file alive



This file should change as the project does. When a phase completes and

introduces a new standing rule, command, or convention, add it here in

the same session — don't leave it only in your response to the user.

This file staying accurate is what makes the next session fast instead

of a re-discovery exercise.


\---



\## 10. WHAT A FRESH SESSION CANNOT VERIFY — HUMAN OR EXTERNAL ACTION REQUIRED



Everything in this section is \*\*outside what any Claude Code session can

check\*\*, no matter how much time it spends. A session that reports these as

"done", "confirmed" or "verified" is wrong by construction — it has no

instrument that reaches them. Do not run a test and infer any of them; do not

soften them into "should be fine"; hand them to the human and say plainly that

they are unverified.



\- \*\*THE SUMO GUI WATCH for the mixed-traffic driving model. This is the

&#x20; single blocking item on the whole project.\*\* STEP 1 and its refinement share

&#x20; ONE combined done-bar, and it is not closed until a \*\*human\*\* has watched

&#x20; `sumo-gui`, coloured by vehicle type, and confirmed that the behaviour

&#x20; \*looks\* right: bikes and autos genuinely filtering to the queue front rather

&#x20; than sitting in arrival order; the three aggressiveness tiers visibly

&#x20; differing within a single type; vehicles straddling lane boundaries and

&#x20; sitting two-abreast without looking broken; overtakes happening when the

&#x20; leader is actually slower rather than constantly; and nothing that reads as

&#x20; a glitch (jitter, vehicles inside one another, a truck stuck sideways).

&#x20; \*\*No test suite substitutes for this and none ever will.\*\* Every number in

&#x20; `docs/MIXED_TRAFFIC_RESEARCH.md` §3/§6 is a summary statistic; the failure

&#x20; modes that matter here are visual, and this project has already recorded

&#x20; five instances of a measurement that passed while proving nothing. The

&#x20; \*\*GUI watch is precisely the check that cannot be gamed that way\*\*, which is

&#x20; why it is the done-bar rather than a table. The launch command is in §11.



\- \*\*Kannada voice recognition — cannot be tested headlessly, at all.\*\* It needs

&#x20; three things no session has: a \*\*live browser\*\* (the Web Speech API exists

&#x20; only in a real browser context — there is no CLI or Python entry point to

&#x20; it), a \*\*real microphone\*\* capturing real audio, and a \*\*human Kannada

&#x20; speaker\*\* to judge whether the transcription is actually right. A session

&#x20; can build the pipeline and can verify that a given TEXT string parses to the

&#x20; correct intent; it \*\*cannot\*\* verify that spoken Kannada becomes that text.

&#x20; Note also §2's standing correction: browser STT is \*\*not local\*\* — in Chrome

&#x20; it streams audio to Google's speech service — so Kannada support is a

&#x20; property of \*that service\*, not of anything in this repo, and cannot be

&#x20; fixed here if it turns out to be poor. Test it early with the actual mic in

&#x20; an actually-noisy room (§20 already requires this for the four English

&#x20; commands); if Kannada recognition is unreliable, the fallback options are

&#x20; the optional local-Whisper path (§14) or demoing in English — both are

&#x20; \*\*human decisions\*\*, not something to be silently chosen in code.



\- \*\*Whether the hackathon's rules actually require Phase 10-12 code to be

&#x20; written live during the 48-hour event.\*\* The entire "reserve Phase 10/11/12

&#x20; for the event" plan rests on this, and \*\*it was never confirmed against the

&#x20; real event rules — it was assumed.\*\* Nobody has checked whether the

&#x20; organisers timestamp-check commits, require a fresh repo, allow

&#x20; pre-existing work with disclosure, or say nothing at all. These lead to

&#x20; materially different strategies: if pre-built work is allowed, building

&#x20; Phase 10 now is strictly better than building it under time pressure; if

&#x20; commits are timestamp-checked, the current discipline is correct and

&#x20; necessary. \*\*Read the actual rules before the event and record the answer

&#x20; here.\*\* Until then this is a self-imposed discipline that may be costing

&#x20; real preparation time for no required reason.



\- \*\*The demo laptop's actual hardware — RAM and VRAM — under the real

&#x20; simultaneous load.\*\* Phase 11 runs a local Gemma model via Ollama at the

&#x20; same time as SUMO, the FastAPI backend and a React frontend, all on one

&#x20; machine, live in front of judges. This was \*\*measured once, on one

&#x20; machine\*\*, and it has never been confirmed that machine \*\*is the actual demo

&#x20; machine\*\*. Two separate things to check, both human: (1) which physical

&#x20; laptop is being used on the day, and (2) the four processes running

&#x20; together on it — not benchmarked one at a time, which is the measurement

&#x20; that would pass while proving nothing. If the demo machine differs from the

&#x20; one measured, the numbers do not transfer. Note also that this repo's

&#x20; recorded training numbers are CPU-bound SUMO figures and say nothing about

&#x20; the voice model's memory footprint.



\- \*\*Anything about team logistics, the 5-person task assignments, or event-day

&#x20; scheduling.\*\* Who is building what, who is presenting which beat, when

&#x20; rehearsals happen, submission deadlines and format, travel and setup time,

&#x20; who has the backup video — \*\*no code session can know or verify any of

&#x20; this\*\*, and none of it is derivable from the repo. It is not recorded here

&#x20; and should not be guessed at. If a session is asked "are we ready?", the

&#x20; honest answer covers the code only, and must say so.



\## 11. PENDING WORK — split by WHEN, deliberately



Two lists. The split is the point: the first is small, finishable now, and

mostly unblocking; the second is the actual build and is reserved (see §10's

third bullet — that reservation is an assumption, not a confirmed rule).



\### 11.1 PENDING — SHOULD BE DONE BEFORE THE EVENT



\- \*\*THE HUMAN GUI WATCH AND SIGN-OFF (§10, blocking).\*\* Launch:



&#x20;   `venv/Scripts/python.exe sim/run_demo_gui.py`



&#x20;   Or by hand, if that harness is unavailable:



&#x20;   `sumo-gui -n sim/networks/generated/corridor_432.net.xml -a sim/networks/vehicle_types_demo.add.xml -r <routes> --lateral-resolution 0.4 --collision.action warn --collision.mingap-factor 0 --step-length 1.0 --seed 7 --waiting-time-memory 1000 --time-to-teleport 600 --end 1200 --quit-on-end false --delay 250 -g sim/networks/demo_gui_settings.xml`



&#x20;   \*\*`--end` plus `--quit-on-end false` is the already-diagnosed fix\*\* for the

&#x20;   window closing before it can be watched — without both, sumo-gui exits at

&#x20;   the end of the run and the watch is lost. \*\*Colour-by-type must be on\*\*

&#x20;   (the settings file sets it; if it is missing, set it manually as the very

&#x20;   first action: View Settings -> Vehicles -> Color by "type"). Two decisions

&#x20;   ride on this watch besides the pass/fail: whether the `bike_over_car`

&#x20;   36.29% -> 31.73% drop is accepted, and whether bike lane-sharing at 51.38%

&#x20;   against a ~62% target is close enough.



\- \*\*COMMIT the mixed-traffic work, immediately after the watch passes.\*\* 8

&#x20; modified tracked files + 2 untracked, listed in the CURRENT STATUS bullet.

&#x20; \*\*A fresh clone, a `git checkout .` or a `git stash` destroys all of it\*\*,

&#x20; including `docs/MIXED_TRAFFIC_RESEARCH.md`, which is the only record of why

&#x20; any parameter in the demo vType file has the value it has. This is the

&#x20; highest-consequence, lowest-effort item on either list.



\- \*\*DONE 2026-09-02 — the mixed-traffic harnesses are now IN THE REPO at

&#x20; `sim/mixed_traffic/`.\*\* They previously lived only in a session-scoped OS

&#x20; temp scratchpad, which put the entire evidence base for

&#x20; `docs/MIXED_TRAFFIC_RESEARCH.md` §3/§6 one directory-clear away from being

&#x20; unreproducible. Relocated: 15 scripts, the \*\*pinned `measure.rou.xml`\*\*

&#x20; every §6 arm shares (the thing that makes §6 internally controlled), the

&#x20; four comparison-arm vType tables §6.1's columns need, the two probe

&#x20; fixtures, and 9 raw-result JSONs under `data/`.

&#x20; \*\*`sim/mixed_traffic/README.md`\*\* maps each script to the research section

&#x20; that cites it, and records the two conventions they must keep.

&#x20; Each script's hardcoded absolute repo path became

&#x20; `Path(__file__).resolve().parents[2]`; `check_fix.py` gained the

&#x20; `require_free()` beacon guard it was missing (it launches SUMO and had no

&#x20; `__main__` guard at all); `verify_argv.py` and `inspect_geometry.py`

&#x20; deliberately keep NO guard — they start no SUMO — and gained the NOTE

&#x20; block saying so, matching `stage4_contamination.py`'s precedent.

&#x20; \*\*Verified FROM THE NEW LOCATION, not assumed:\*\* `py_compile` on all 15;

&#x20; `verify_argv.py` -> ALL CHECKS PASS (default argv byte-identical to HEAD,

&#x20; 18 tokens); `probe_dist.py` -> nested distribution resolves, bike share

&#x20; 0.140 vs the 0.15 target; `probe_weather_propagation.py` -> reproduces the

&#x20; 1-of-3-tiers finding; `verify_perception_fixes.py` -> default-file

&#x20; inertness control identical on every row (18/37/64/13/0);

&#x20; `probe_collisions.py` -> 0 collision events; `check_fix.py` ->

&#x20; `arrived=1737 collision_events=0`, matching §6.6's recorded post-fix

&#x20; figures exactly.




\- \*\*Resolve the STEP 1 baseline non-reproducibility (minor, open).\*\* STEP 1's

&#x20; recorded baseline does not reproduce bit-for-bit — 1868 vehicles against its

&#x20; recorded 1870, `bike_over_car` 5.61% against 4.17% — so the route file the

&#x20; refinement session generated is not byte-identical to STEP 1's, and STEP 1's

&#x20; recorded \*\*37.13%\*\* demo figure is \*\*not a valid comparator\*\*. Already

&#x20; handled correctly (the refinement re-ran STEP 1's own table on its own route

&#x20; file, so every §6 number is same-route-file and internally controlled), and

&#x20; `flows_end_s` has been tested and ruled out. \*\*This affects no conclusion\*\*

&#x20; — it is a harness-trust loose end. Fix by pinning the route file itself

&#x20; alongside the harnesses in the item above.



\- \*\*Optional, non-blocking:\*\* evaluate the D1 checkpoint

&#x20; (`training/checkpoints/stage5_graph_attention_d1/`, trained to 156,624 and

&#x20; never evaluated) via `python -m training.scripts.checkpoint_bakeoff` — it is

&#x20; the only outstanding test of the data-diversity hypothesis for the post-51k

&#x20; collapse. \*\*It cannot change the deployment decision unless it wins the

&#x20; bake-off outright\*\*, so it is a curiosity, not a dependency. Also optional:

&#x20; delete the redundant `backend-security-hardening` branch ref.



\- \*\*Rehearsal items already in §20 of the master plan\*\* — voice with real

&#x20; background noise, the Greedy/PsychoFlow toggle and emergency override run

&#x20; live multiple times, the backup demo video, and everyone able to state out

&#x20; loud what is rule-based vs RL-learned vs simulated-mock. Several of these

&#x20; depend on Phase 10/11/12 existing and so realistically happen at the event.



\### 11.2 PENDING — TO BE DONE DURING THE EVENT



\- \*\*Phase 10 — Frontend (§18.10).\*\* Intersection view, metrics panel, decision

&#x20; log, voice panel, Greedy/PsychoFlow toggle. \*\*Read the "PHASE 10 BEHAVIOR

&#x20; SPEC" bullet in §8 and `docs/MIXED_TRAFFIC_RESEARCH.md` before writing any

&#x20; of it\*\* — rendering the heterogeneous traffic authentically is a real

&#x20; constraint, not a polish item: vehicles do \*\*not\*\* stay in arrival order

&#x20; (a bike beats an earlier car to the stop line 37.1% of the time), position

&#x20; is `(x, y)` with a continuous lateral offset rather than `(lane, index)`,

&#x20; and coordinates are in the netconvert-shifted frame. A frontend that draws

&#x20; a queue as an arrival-ordered list \*\*will be visibly wrong on stage.\*\* The

&#x20; frontend never simulates or approximates any of this — every value arrives

&#x20; on the §13.2 frame already computed.



\- \*\*Phase 11 — the actual voice pipeline (§18.11, §14).\*\* Design is approved

&#x20; and recorded (see CURRENT STATUS and the APPROVED VOICE DESIGN bullet);

&#x20; what is unbuilt is `backend/voice/stt.py` and

&#x20; `backend/voice/intent_agent.py` plus the frontend panel. Build it against

&#x20; `control_api.dispatch()` from the first line — that allowlist \*\*is\*\* the

&#x20; safety argument. Reconcile the 0-based vs 1-based lane numbering explicitly.

&#x20; Sanity-check the lost benchmark numbers rather than trusting or re-deriving

&#x20; them.



\- \*\*Phase 12 — Evaluation suite (§18.12, §15).\*\* The \*\*Greedy baseline\*\*

&#x20; (currently stubbed — this is §19's strongest beat, so it needs real

&#x20; rehearsal runway, not a last-hour build), the \*\*held-out evaluation\*\*

&#x20; consuming the already-built `evaluation/heldout.py` (§15.4 requires

&#x20; programmatic disjointness assertion, not inspection), emissions (§15.3),

&#x20; and \*\*STEP 2's signal-violation detector if time allows\*\* — deferred to here

&#x20; deliberately, because it has nowhere to render until Phase 10 exists.



\- \*\*Y-merge (§3, Tier 2) — ONLY if everything above is done with real time to

&#x20; spare.\*\* This is a locked decision (§2), not a judgement call: do not write,

&#x20; extend or "quickly fix" Y-merge code before Tiers 0/1/1.5/3 are all built

&#x20; and demo-rehearsed. It maps to no bullet in the problem statement and buys

&#x20; nothing against the rubric.

