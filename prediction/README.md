# prediction

Phase 5 (§8 of the master plan) — the two forward-looking signals that feed
the RL observation, gating when real training is allowed to start.

- **`spillover.py`** — `SpilloverPredictor` forecasts queue growth/drain at a
  junction from its upstream neighbour's outflow rate (`LINK_APPROACH =
  "west"`, a fact of this corridor's fixed W→E topology, §0.1). It is
  **stateful** and must be reset every episode — `PsychoFlowEnv.reset()`
  already does this; any standalone script constructing one directly must
  reset it itself.
- **`incident_impact.py`** — estimates downstream delay from an injected
  incident, scaled by severity and hop distance along
  `CORRIDOR_ADJACENCY` (`twin/digital_twin.py`).

**Hard gate (CLAUDE.md §3):** no real, kept training run may happen before
this module exists and `PsychoFlowEnv` is constructed with a non-`None`
`spillover_predictor` — training against permanently-zero spillover features
teaches the policy to ignore them, and that doesn't un-learn later.

Self-tests: `python -m prediction.spillover` and
`python -m prediction.incident_impact` (no SUMO needed). Live integration
check: `sim/run_prediction_episode.py`.
