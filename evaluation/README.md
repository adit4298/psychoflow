# evaluation

§15.4 of the master plan — the held-out evaluation seed set, kept structurally
disjoint from every training run so a benchmark number can't be quietly
measuring memorisation instead of generalisation.

- **`heldout.py`** — builds and validates the held-out seed/scenario set,
  asserting (programmatically, not by inspection) that none of it overlaps
  the scenarios any training run actually drew.
- **`heldout_manifest.json`** — the pinned manifest of held-out seeds this
  project's benchmark numbers are measured against.

This module is deliberately **not** guarded by the SUMO-activity beacon
(`sim/sumo_activity.py`) — it only sets `env._rng` and calls
`_draw_scenario()`, launching no SUMO process, so it stays runnable even
while a training run is live. See its own module docstring for why.
