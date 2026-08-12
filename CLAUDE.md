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

\- \*\*WATCH-ITEM (Phase 6, added Stage 1): re-check the deterministic-vs-stochastic
&#x20; reward gap at every future stage checkpoint (Stage 2/3/4).\*\* Stage 1's final
&#x20; checkpoint scores +1.03 mean reward/step deterministic vs. 1.297 stochastic
&#x20; (10-episode average, seed-independent, bit-for-bit reproducible — see
&#x20; BUILD_LOG.md's Phase 6 entry) — likely `ent_coef=0.0` plus independent-per-head
&#x20; argmax over the `MultiDiscrete([3,3,3])` action space not yet coinciding with the
&#x20; jointly-best combination, but this is an unconfirmed hypothesis. If the gap does
&#x20; NOT narrow as training progresses and entropy naturally decays, escalate before
&#x20; the Stage 5 MARL checkpoint — §9.5's graph-attention and shared-policy extractors
&#x20; both sit on this same per-junction action-head structure, so an unresolved gap
&#x20; here is not a Stage-1-only quirk.

\- \*\*Current status (2026-08-12): Stage 2 design plan fully approved, not yet
&#x20; started.\*\* Plan: pre-generate all 27 lane-count combos in
&#x20; `sim/networks/generated/` before Burst A (currently only `corridor_432` is
&#x20; cached — every Phase 2-6 script through Stage 1 used the fixed 4/3/2
&#x20; corridor exclusively), then Burst A (10k timesteps) with the same
&#x20; stop-and-review discipline as Stage 1. The consistency sweep (§16's "Stage
&#x20; 2: consistent across all 3 lane-counts" checkpoint) uses 3 seeds
&#x20; `{1, 7, 42}` across 5 representative combos — `(4,3,2)`, `(2,2,2)`,
&#x20; `(4,4,4)`, `(2,4,2)`, `(4,2,4)` — not a single seed, per the seed-spread
&#x20; finding from Stage 1 (seeds 1/3/7/42 on the SAME 4/3/2 topology already
&#x20; showed a 93-124s worst-wait range, so a single-seed-per-combo sweep could
&#x20; not distinguish a real topology effect from ordinary seed noise). No
&#x20; per-combo random/Tier 0 baselines exist yet outside 4/3/2 — the sweep is
&#x20; self-referential (comparing the trained policy's own numbers across
&#x20; combos), not baseline-relative.

\- \*\*`sim/networks/generated/` is git-tracked, not gitignored\*\* (confirmed via
&#x20; `git check-ignore` — no match). Stage 2's pre-generation step will add up
&#x20; to 26 new `corridor_{j1}{j2}{j3}.{net,edg,nod}.xml` file sets to that
&#x20; directory; they need `git add`/commit like any other source file once
&#x20; generated, not left sitting untracked. Contrast with
&#x20; `training/checkpoints/*`, which IS gitignored by design (only a
&#x20; deliberately un-ignored final model should ever be tracked there).

\- (Add training/test/run commands here as each phase is built — this

&#x20; section should grow; keep it accurate, delete anything that stops

&#x20; being true.)



\## 9. Keeping this file alive



This file should change as the project does. When a phase completes and

introduces a new standing rule, command, or convention, add it here in

the same session — don't leave it only in your response to the user.

This file staying accurate is what makes the next session fast instead

of a re-discovery exercise.

