"""Interactive querying (§12.3).

Answers "why did you do that?" by pulling the ACTUAL §12.1 decision-log
entry for the referenced time/lane and rendering it through the same
§12.2 templates — never a canned or generic response (§12.3), never an
LLM call (§2).

Deterministic lookup:

  * `sim_time` omitted  -> the most recent decision.
  * otherwise           -> the decision in force at that instant, i.e. the
                           latest entry at-or-before `sim_time` (queries
                           land between the 5s decision steps).
  * `lane_id` given without `junction_id` -> the junction is resolved from
    a lane->junction map (built from the digital twin's topology), not by
    string-parsing the lane id.

Return shape is structured + rendered so §13.2's WebSocket, the frontend
query box, and §14's voice channel can all consume it:

    {"sim_time", "junction_id", "entry": <§12.1 dict>, "narration": <str>}
"""

from __future__ import annotations

from explainability.decision_log import DecisionLog, DecisionLogEntry
from explainability.narrator import narrate


class DecisionNotFound(LookupError):
    """No logged decision matches the query."""


class QueryInterface:
    def __init__(
        self,
        decision_log: DecisionLog,
        lane_to_junction: dict[str, str] | None = None,
    ):
        self._log = decision_log
        self._lane_to_junction = dict(lane_to_junction or {})

    @classmethod
    def from_twin_topology(
        cls, decision_log: DecisionLog, topology: dict[str, dict]
    ) -> "QueryInterface":
        """`topology` is `DigitalTwin.topology` — {jid: {"lane_approach_map": {lane: dir}}}."""
        lane_to_junction = {
            lane_id: junction_id
            for junction_id, topo in topology.items()
            for lane_id in topo.get("lane_approach_map", {})
        }
        return cls(decision_log, lane_to_junction)

    # ------------------------------------------------------------------
    def why(
        self,
        sim_time: float | None = None,
        junction_id: str | None = None,
        lane_id: str | None = None,
    ) -> dict:
        """Explain the decision in force at (sim_time, junction/lane)."""
        if junction_id is None and lane_id is not None:
            junction_id = self._lane_to_junction.get(lane_id)
            if junction_id is None:
                raise DecisionNotFound(
                    f"lane {lane_id!r} is not on any known junction — pass "
                    f"junction_id explicitly or build the interface with "
                    f"from_twin_topology()"
                )

        candidates = self._log.entries_for(
            junction_id=junction_id, upto_sim_time=sim_time
        )
        if not candidates:
            scope = f"junction {junction_id}" if junction_id else "the corridor"
            when = "ever" if sim_time is None else f"at or before t={sim_time:.0f}s"
            raise DecisionNotFound(f"no logged decision for {scope} {when}")

        entry: DecisionLogEntry = candidates[-1]
        return {
            "sim_time": entry.sim_time,
            "junction_id": entry.junction_id,
            "entry": entry.to_dict(),
            "narration": narrate(entry),
        }


# --------------------------------------------------------------------------
# Self-test — no SUMO process
# --------------------------------------------------------------------------
def _selftest() -> None:
    from explainability.decision_log import REASON_RAW_COUNT, REASON_WAIT_THRESHOLD

    print("§12.3 query_interface self-test\n")

    log = DecisionLog()
    snapshot = {"junctions": {"J1": {"lanes": {
        "N1_J1_0": {"approach": "north", "halted_count": 5,
                    "wait_time_max_single_vehicle": 20.0}}}}}
    served = {"J1": {0: frozenset({"N1_J1_0"})}}

    def rec(t, reason):
        d = {"J1": {"junction_id": "J1", "phase_selected": 0,
                    "score_breakdown": {"halted_count": 3.0, "wait_time": 8.0,
                                        "starvation_bonus": 0.0},
                    "alternative_scores": {"phase_0": 11.0}, "reason": reason}}
        log.record_step(t, d, {"safety_overrides": []}, snapshot, served)

    rec(100.0, REASON_RAW_COUNT)
    rec(200.0, REASON_WAIT_THRESHOLD)
    rec(300.0, REASON_RAW_COUNT)

    qi = QueryInterface.from_twin_topology(
        log, {"J1": {"lane_approach_map": {"N1_J1_0": "north"}}}
    )

    # at-or-before semantics: a query at t=250 returns the t=200 decision.
    r = qi.why(sim_time=250.0, junction_id="J1")
    assert r["sim_time"] == 200.0, r["sim_time"]
    assert r["entry"]["reason"] == REASON_WAIT_THRESHOLD
    assert "Wait threshold crossed" in r["narration"]
    print(f"  [OK] why(t=250) -> t={r['sim_time']:.0f} :: {r['narration']}")

    # sim_time omitted -> most recent.
    r = qi.why(junction_id="J1")
    assert r["sim_time"] == 300.0
    print(f"  [OK] why(latest) -> t={r['sim_time']:.0f} :: {r['narration']}")

    # lane_id resolves the junction.
    r = qi.why(sim_time=150.0, lane_id="N1_J1_0")
    assert r["junction_id"] == "J1" and r["sim_time"] == 100.0
    print(f"  [OK] why(lane=N1_J1_0, t=150) -> {r['junction_id']} t={r['sim_time']:.0f}")

    # The entry returned is the REAL logged one, not a template stand-in.
    assert r["entry"]["score_breakdown"] == {"halted_count": 3.0, "wait_time": 8.0,
                                             "starvation_bonus": 0.0}
    print(f"  [OK] returned entry carries the real logged score_breakdown")

    # Misses raise, not return a canned answer.
    for kwargs in (dict(sim_time=50.0, junction_id="J1"),
                   dict(junction_id="J9"),
                   dict(lane_id="not_a_lane")):
        try:
            qi.why(**kwargs)
        except DecisionNotFound as exc:
            print(f"  [OK] {kwargs} -> DecisionNotFound: {exc}")
        else:
            raise AssertionError(f"{kwargs} should have raised")

    print(f"\nAll query_interface self-tests passed.")


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # narration is UTF-8
    _selftest()
