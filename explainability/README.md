# explainability

Phase 8 (§12 of the master plan) — the decision log and narration layer that
answers "why did the corridor just do that?" from the same record a judge
sees on screen, rather than inventing a second explanation that could
disagree with it.

- **`decision_log.py`** — `DecisionLog` records one entry per junction per
  step: the action taken, whether §10's safety validator overrode it (and
  which rule), and the score breakdown behind it. One log per episode —
  replaced on every reset, never reused across an episode boundary.
- **`narrator.py`** — turns a `DecisionLogEntry` into the operator-facing
  sentence rendered in the dashboard's decision log, covering all six
  possible reasons a junction's phase changed (including `starvation_ceiling`
  and `rl_policy`).
- **`query_interface.py`** — answers "why" queries (e.g. "why did J2 switch
  at t=140s?") by looking up the relevant `DecisionLogEntry` rather than
  re-deriving an explanation from scratch.

Self-tests: `python -m explainability.decision_log`,
`python -m explainability.narrator`, `python -m explainability.query_interface`
(no SUMO needed). Integration check: `sim/run_explainability_episode.py`.
