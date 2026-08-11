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
&#x20; It is already active in the shell; `python`/`pip` resolve into it.

\- Generate the corridor (Phase 1): `python sim/networks/generate_corridor.py --j1 4 --j2 3 --j3 2`

\- Run the perception episode (Phase 2 verification): `python sim/run_perception_episode.py`

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

\- (Add training/test/run commands here as each phase is built — this

&#x20; section should grow; keep it accurate, delete anything that stops

&#x20; being true.)



\## 9. Keeping this file alive



This file should change as the project does. When a phase completes and

introduces a new standing rule, command, or convention, add it here in

the same session — don't leave it only in your response to the user.

This file staying accurate is what makes the next session fast instead

of a re-discovery exercise.

