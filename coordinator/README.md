# coordinator

Phase 8 (§11 of the master plan) — the emergency-clearance layer that sits
between the safety validator and the operator-facing dashboard.

- **`emergency_clearance.py`** — `EmergencyClearanceCoordinator` observes each
  §13.2 frame and tracks, per junction, when an ambulance was first detected
  and when that junction's signal actually went green. It never moves a
  vehicle itself (that would fight SUMO's own car-following model) — it only
  emits `EmergencyClearanceEvent`s the frontend can animate. Two trigger
  sources are unioned per step: a sensed ambulance (`"detected"`) and an
  operator's `trigger_emergency` call (`"operator"`), with detection winning
  on overlap and the source fixed at episode-open so it never relabels a
  later arrival.
- **`responder_messaging.py`** — turns a clearance event into the §11.2
  operator-facing message: real `clearance_time_s` (measured, per-junction)
  alongside a clearly-labelled `baseline_clearance_time_s` estimate (a
  conservative signal-rotation model, not a measured counterfactual).

Self-tests: `python -m coordinator.emergency_clearance` and
`python -m coordinator.responder_messaging` (no SUMO needed).
