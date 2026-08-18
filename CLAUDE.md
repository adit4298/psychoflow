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

\- Voice = local only — Web Speech API + Ollama/Gemma (§14). Never a

&#x20; Claude API call anywhere in the runtime path. This is a hard budget

&#x20; constraint, not a style preference.

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
&#x20; \*\*FINAL READ: CONFIRMED, narrowly scoped to `(3,2,3)` and `(3,2,4)`
&#x20; specifically — do NOT generalize to "`j1=3` combos" as a category.\*\*
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
&#x20; gap to single-agent performance."\*\* Most likely cause — PLAUSIBLE BUT
&#x20; UNCONFIRMED — is Stage 4's ~3x training budget (153,600 vs ~51-53k) plus
&#x20; the MARL modes starting from scratch; testing it needs a Stage 5 mode
&#x20; trained to ~153,600, which has not been done. Do not assert it.
&#x20; Original follow-up note, superseded by the above:
&#x20; re-check `(3,2,3)`/`(3,2,4)` specifically at the Stage 5 MARL checkpoint —
&#x20; this is exactly the kind of localized, demand-skew-sensitive gap
&#x20; graph-attention (§9.5, neighbor-aware) should plausibly help with over
&#x20; the shared-policy fallback, making it a real test of whether attention
&#x20; earns its complexity, not just a box to check. Not investigating further
&#x20; before Stage 5 — bounded to the two checks above by design.

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

\- \*\*STANDING GOTCHA: `mean_reward` is NOT a valid axis for cross-density
&#x20; comparison — always use `worst_wait`/`starved_pct` instead.\*\* `env/reward.py`'s
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

\- \*\*Current status (2026-08-15): Stage 3 complete, Stage 4 design plan
&#x20; pending.\*\* Stage 3 (`+ randomize_density=True`) resumed from Stage 2's
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
&#x20; \*\*INTERIM MEASUREMENTS on `graph_attention` @ 51,624 — these are readings
&#x20; from an UNDERTRAINED checkpoint (1/3 of Stage 4's 153,600 budget, trained
&#x20; from scratch), NOT new watch-items and NOT verdicts on attention.\*\*
&#x20; (a) \*\*`--j1-recheck`: uniform regression; the gap is currently
&#x20; UNMEASURABLE.\*\* All four combos now land in a narrow 121-125s band —
&#x20; `(3,2,3)` 4/4 seeds spiking (was 2/8 at Stage 4), `(3,2,4)` 4/4 (was 1/8),
&#x20; and critically the `j1=4` CONTROLS `(4,2,3)`/`(4,2,4)` 4/4 as well (were
&#x20; 0/4, clean at 56-61s). `starved_pct` is 1.1-3.5% everywhere vs 0.0% on the
&#x20; Stage 4 controls. The `j1=3`-vs-`j1=4` distinction has vanished, but NOT
&#x20; because `j1=3` improved — because `j1=4` degraded and everything converged
&#x20; to one bad band. Nothing can be concluded about the original gap from this;
&#x20; re-test once `shared_policy` provides a budget-matched comparator.
&#x20; UNVERIFIED side-note worth checking later: Stage 4's spikes sat at 119-120s,
&#x20; just BELOW `STARVATION_CEILING_S = 120` (and were confirmed then to fire
&#x20; zero overrides), whereas these sit at 121-125s, just ABOVE it — consistent
&#x20; with the ceiling now actually engaging, but the `--j1-recheck` harness does
&#x20; not capture override counts so this has not been confirmed.
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

\- \*\*`sim/networks/generated/` is git-tracked, not gitignored\*\* (confirmed via
&#x20; `git check-ignore` — no match). Stage 2's pre-generation step will add up
&#x20; to 26 new `corridor_{j1}{j2}{j3}.{net,edg,nod}.xml` file sets to that
&#x20; directory; they need `git add`/commit like any other source file once
&#x20; generated, not left sitting untracked. Contrast with
&#x20; `training/checkpoints/*`, which IS gitignored by design (only a
&#x20; deliberately un-ignored final model should ever be tracked there).

\- \*\*PHASE 8 WARNING — do NOT reuse the Stage 4 emergency-latency
&#x20; measurement for §11.2's `clearance_time_s`. It is KNOWN BROKEN and
&#x20; UNFIXED.\*\* The Stage 4 emergency sweep produced NEGATIVE latencies
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

\- (Add training/test/run commands here as each phase is built — this

&#x20; section should grow; keep it accurate, delete anything that stops

&#x20; being true.)



\## 9. Keeping this file alive



This file should change as the project does. When a phase completes and

introduces a new standing rule, command, or convention, add it here in

the same session — don't leave it only in your response to the user.

This file staying accurate is what makes the next session fast instead

of a re-discovery exercise.

