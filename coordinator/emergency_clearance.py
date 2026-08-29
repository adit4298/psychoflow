"""Emergency corridor clearance (§11.1).

WHAT THIS MODULE DOES: tracks the emergency-clearance EPISODE per
junction from the live (snapshot, runtime, step-info) stream, and emits
structured `EmergencyClearanceEvent` objects — first detection, override
onset, green onset, close. §11.2's responder message is built from these.

WHAT IT DELIBERATELY DOES NOT DO: move vehicles. §11.1's visible
behaviour ("vehicles already in the intersection move to the sides,
queued vehicles split to open a path") is a Phase 10 FRONTEND animation,
driven by this event stream. Shoving vehicles sideways through TraCI
(`vehicle.moveTo` / `changeLane`) fights SUMO's car-following model and
risks the exact conflicts §10 exists to prevent. SUMO already handles
the ambulance physics (`vClass="emergency"`, Phase 1) and §10 already
clears the signals; this module's job is the coordination RECORD.

PER-JUNCTION attribution is load-bearing (CLAUDE.md §8 PHASE 8 WARNING):
the Stage 4 emergency-latency sweep produced NEGATIVE latencies because
it tracked detection and green-onset at the FIRST junction the ambulance
was seen at, while the §10 override can fire at a LATER junction on a
corridor route (J1->J2->J3). Here every junction keeps its own episode,
so the latency reported for a junction is always measured at that
junction. Modelled on `sim/run_tier0_episode.py`'s B2, which does this
correctly for a single junction.

Green onset is recovered exactly (1s resolution, not the 5s decision
interval) as `sim_time - time_since_switch_s`, since the env zeroes that
counter at the instant a green is set. If the ambulance's phase was
ALREADY green when the ambulance arrived, that subtraction predates
detection; `clearance_time_s` floors at 0.0 and `served_on_arrival` is
set — the ambulance did not wait, which is the honest reading, not a
negative latency.

TWO TRIGGERS, ONE EVENT (`source`). §10's emergency branch fires on
EITHER a sensed ambulance in a lane OR that lane being in
`forced_emergency_lanes` — §13.1's `trigger_emergency(lane_id)`, an
operator forcing the same override by hand. `observe()` takes the same
`forced_emergency_lanes` argument as `safety.validator.validate()`, with
the same name, type and default, and unions the two sets per junction so
one junction produces ONE clearance episode however it was triggered.

`source` records what OPENED the episode: `"detected"` when a real
ambulance is sensed at that junction on that step, `"operator"` when only
a forced lane is. Detection WINS when both are present at the opening
instant — a sensed ambulance is the stronger evidence and its lane is the
one §10 will actually target. The field is fixed at open and never
mutated: it is the provenance of the TRIGGER, so a real ambulance
arriving later at an operator-opened junction does not retroactively make
the trigger a detection. §11.2 reports it as `trigger_source`, because
an operator reading a clearance message needs to know whether the system
saw a vehicle or they themselves forced it.

The default is `frozenset()`, so a caller that does not pass it gets the
pre-existing detection-only behaviour byte for byte.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from safety.validator import RULE_EMERGENCY

# What opened the clearance episode. `detected` = a real ambulance was
# sensed in the lane (§7.1's type_composition); `operator` = §13.1's
# trigger_emergency(lane_id) put that lane in forced_emergency_lanes and
# no ambulance was sensed at that junction on the opening step.
SOURCE_DETECTED = "detected"
SOURCE_OPERATOR = "operator"
SOURCES = frozenset({SOURCE_DETECTED, SOURCE_OPERATOR})


@dataclass
class EmergencyClearanceEvent:
    junction_id: str
    lane_id: str
    direction: str
    first_detection_sim_time: float
    override_sim_time: float | None = None
    green_onset_sim_time: float | None = None
    closed_sim_time: float | None = None
    # Provenance of the TRIGGER, fixed when the episode opens. See the
    # module docstring: detection wins over an operator force when both
    # are present at that instant, and this is never mutated afterwards.
    source: str = SOURCE_DETECTED

    @property
    def served_on_arrival(self) -> bool:
        """The ambulance's phase was already green when it was detected."""
        return (
            self.green_onset_sim_time is not None
            and self.green_onset_sim_time < self.first_detection_sim_time
        )

    @property
    def clearance_time_s(self) -> float | None:
        """Detection -> green, at THIS junction. Floors at 0.0."""
        if self.green_onset_sim_time is None:
            return None
        return max(0.0, self.green_onset_sim_time - self.first_detection_sim_time)

    @property
    def override_fired(self) -> bool:
        return self.override_sim_time is not None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["clearance_time_s"] = self.clearance_time_s
        d["served_on_arrival"] = self.served_on_arrival
        d["override_fired"] = self.override_fired
        return d


class EmergencyClearanceCoordinator:
    """Consumes the per-step stream, produces clearance events.

    `served_lanes` is the static {junction: {slot: lanes}} map
    (`PsychoFlowEnv.phase_served_lanes()`), used to tell whether the
    junction's current green slot serves the ambulance's lane — the same
    check B2 uses, so no live RYG read is needed here.
    """

    def __init__(self, served_lanes: dict[str, dict[int, frozenset[str]]]):
        self._served = served_lanes
        self._open: dict[str, EmergencyClearanceEvent] = {}
        self.completed: list[EmergencyClearanceEvent] = []

    def observe(
        self,
        sim_time: float,
        snapshot: dict,
        runtime: dict[str, dict],
        info: dict,
        *,
        forced_emergency_lanes: frozenset[str] = frozenset(),
    ) -> list[EmergencyClearanceEvent]:
        """One post-step observation. Returns events that CLOSED this step.

        `forced_emergency_lanes` is §13.1's `trigger_emergency(lane_id)` —
        the SAME argument, name and default as
        `safety.validator.validate()`, and callers must pass the SAME
        tracked set they hand the validator, not a second copy. Omitting
        it reproduces detection-only behaviour exactly.
        """
        sim_time = float(sim_time)
        emergency_junctions = {
            record["junction_id"]
            for record in info.get("safety_overrides", [])
            if record["rule"] == RULE_EMERGENCY
        }

        newly_closed: list[EmergencyClearanceEvent] = []
        for junction_id, jdata in snapshot["junctions"].items():
            lanes = jdata["lanes"]
            detected = sorted(
                lane_id for lane_id, reading in lanes.items()
                if reading["type_composition"].get("ambulance", 0) > 0
            )
            # Forced lanes are matched against THIS junction's lanes, so a
            # lane forced at J2 never opens an episode at J1.
            forced = sorted(set(forced_emergency_lanes) & set(lanes))
            # The UNION is what keeps one junction to one episode. Detection
            # wins the lane and the source when both are present.
            amb_lanes = detected or forced
            event = self._open.get(junction_id)

            if amb_lanes:
                lane_id = amb_lanes[0]
                if event is None:
                    event = EmergencyClearanceEvent(
                        junction_id=junction_id,
                        lane_id=lane_id,
                        direction=lanes[lane_id]["approach"],
                        first_detection_sim_time=sim_time,
                        source=SOURCE_DETECTED if detected else SOURCE_OPERATOR,
                    )
                    self._open[junction_id] = event

                if junction_id in emergency_junctions and event.override_sim_time is None:
                    event.override_sim_time = sim_time

                if event.green_onset_sim_time is None:
                    cur_slot = runtime[junction_id]["current_green_slot"]
                    serving = self._served.get(junction_id, {}).get(
                        cur_slot, frozenset()
                    )
                    if event.lane_id in serving:
                        event.green_onset_sim_time = (
                            sim_time
                            - float(runtime[junction_id]["time_since_switch_s"])
                        )
            elif event is not None:
                event.closed_sim_time = sim_time
                self.completed.append(event)
                newly_closed.append(event)
                del self._open[junction_id]

        return newly_closed

    def finalize(self, sim_time: float) -> list[EmergencyClearanceEvent]:
        """Close any episodes still open at episode end."""
        out: list[EmergencyClearanceEvent] = []
        for junction_id, event in list(self._open.items()):
            event.closed_sim_time = float(sim_time)
            self.completed.append(event)
            out.append(event)
            del self._open[junction_id]
        return out


# --------------------------------------------------------------------------
# Self-test — no SUMO process
# --------------------------------------------------------------------------
def _snap(amb_at: dict[str, str] | None = None) -> dict:
    """amb_at: {junction_id: lane_id_with_ambulance}."""
    amb_at = amb_at or {}
    junctions = {}
    for jid in ("J1", "J2", "J3"):
        lanes = {
            f"N_{jid}_0": {"approach": "north",
                           "type_composition": {"ambulance": 0}},
            f"E_{jid}_0": {"approach": "east",
                           "type_composition": {"ambulance": 0}},
        }
        if jid in amb_at:
            lanes[amb_at[jid]]["type_composition"]["ambulance"] = 1
        junctions[jid] = {"lanes": lanes}
    return {"junctions": junctions}


def _rt(slot: dict[str, int], age: dict[str, float]) -> dict[str, dict]:
    return {
        jid: {"current_green_slot": slot.get(jid, 0),
              "time_since_switch_s": age.get(jid, 40.0)}
        for jid in ("J1", "J2", "J3")
    }


def _selftest() -> None:
    print("§11.1 emergency_clearance self-test\n")

    served = {jid: {0: frozenset({f"N_{jid}_0"}), 1: frozenset({f"E_{jid}_0"})}
              for jid in ("J1", "J2", "J3")}

    # -- 1: ambulance on J2 east (red), override fires, then green ------
    coord = EmergencyClearanceCoordinator(served)

    # t=100: detected on J2 east; J2 serving slot 0 (north) -> not yet green.
    closed = coord.observe(100.0, _snap({"J2": "E_J2_0"}),
                           _rt({"J2": 0}, {"J2": 40.0}),
                           {"safety_overrides": []})
    assert closed == [] and "J2" in coord._open
    ev = coord._open["J2"]
    assert ev.first_detection_sim_time == 100.0 and ev.direction == "east"

    # t=105: override record present for J2.
    coord.observe(105.0, _snap({"J2": "E_J2_0"}), _rt({"J2": 0}, {"J2": 45.0}),
                  {"safety_overrides": [{"junction_id": "J2",
                                         "rule": RULE_EMERGENCY}]})
    assert ev.override_sim_time == 105.0

    # t=108: J2 now serving slot 1 (east), green set 3s ago -> onset t=105.
    coord.observe(108.0, _snap({"J2": "E_J2_0"}), _rt({"J2": 1}, {"J2": 3.0}),
                  {"safety_overrides": []})
    assert ev.green_onset_sim_time == 105.0, ev.green_onset_sim_time
    assert ev.clearance_time_s == 5.0, ev.clearance_time_s   # 105 - 100

    # t=115: ambulance gone -> episode closes.
    closed = coord.observe(115.0, _snap({}), _rt({"J2": 1}, {"J2": 10.0}),
                           {"safety_overrides": []})
    assert closed == [ev] and ev.closed_sim_time == 115.0
    assert not ev.served_on_arrival and ev.override_fired
    print(f"  [OK] override case: detect t=100, override t=105, green t=105, "
          f"clearance {ev.clearance_time_s:.1f}s")

    # -- 2: corridor route — J1 resolves and closes as the ambulance moves
    #       on to J3, which opens its own SEPARATE episode ----------------
    coord = EmergencyClearanceCoordinator(served)
    # t=50: J1 already serving slot 1 (east), green set 2s ago -> onset t=48,
    #       which predates detection -> served_on_arrival, clearance floors at 0.
    coord.observe(50.0, _snap({"J1": "E_J1_0"}), _rt({"J1": 1}, {"J1": 2.0}),
                  {"safety_overrides": []})
    j1 = coord._open["J1"]
    assert j1.green_onset_sim_time == 48.0
    assert j1.served_on_arrival and j1.clearance_time_s == 0.0

    # t=200: ambulance has left J1 and reached J3. J1's episode closes;
    #        J3's opens independently and is still unresolved.
    closed = coord.observe(200.0, _snap({"J3": "E_J3_0"}),
                           _rt({"J3": 0}, {"J3": 30.0}), {"safety_overrides": []})
    assert closed == [j1] and j1.closed_sim_time == 200.0
    assert set(coord._open) == {"J3"}
    assert coord._open["J3"].clearance_time_s is None
    assert [e.junction_id for e in coord.completed] == ["J1"]
    print(f"  [OK] per-junction: J1 served-on-arrival (clearance 0.0s) closes "
          f"at t=200, J3 opens separately, no cross-contamination")

    # -- 3: finalize closes stragglers --------------------------------
    out = coord.finalize(3600.0)
    assert [e.junction_id for e in out] == ["J3"]
    assert len(coord.completed) == 2 and not coord._open
    print(f"  [OK] finalize() closed {len(out)} open episode at t=3600")

    # -- 4: OPERATOR trigger with no vehicle anywhere -----------------
    # §13.1's trigger_emergency(lane_id): §10 fires on the forced lane, so a
    # clearance episode must exist for it even though nothing is sensed.
    coord = EmergencyClearanceCoordinator(served)
    coord.observe(300.0, _snap({}), _rt({"J2": 0}, {"J2": 40.0}),
                  {"safety_overrides": [{"junction_id": "J2",
                                         "rule": RULE_EMERGENCY}]},
                  forced_emergency_lanes=frozenset({"E_J2_0"}))
    assert set(coord._open) == {"J2"}, sorted(coord._open)
    op = coord._open["J2"]
    assert op.source == SOURCE_OPERATOR and op.lane_id == "E_J2_0"
    assert op.direction == "east" and op.override_sim_time == 300.0
    # t=306: J2 now serving slot 1 (east), green set 3s ago -> onset 303.
    coord.observe(306.0, _snap({}), _rt({"J2": 1}, {"J2": 3.0}),
                  {"safety_overrides": []},
                  forced_emergency_lanes=frozenset({"E_J2_0"}))
    assert op.green_onset_sim_time == 303.0, op.green_onset_sim_time
    assert op.clearance_time_s == 3.0, op.clearance_time_s
    # The force is lifted (EMERGENCY_HOLD_S expiry) -> the episode closes.
    closed = coord.observe(320.0, _snap({}), _rt({"J2": 1}, {"J2": 20.0}),
                           {"safety_overrides": []})
    assert closed == [op] and op.closed_sim_time == 320.0
    print(f"  [OK] operator trigger with NO sensed vehicle: one episode at "
          f"J2, source={op.source!r}, lane={op.lane_id}, clearance "
          f"{op.clearance_time_s:.1f}s, closes when the force is lifted")

    # -- 5: BOTH at once — detection wins, ONE event ------------------
    # The case the union exists for: an ambulance sensed on J2 north while
    # the operator has separately forced J2 east. Must be one coherent
    # episode (not two, not dropped), keyed on the DETECTED lane.
    coord = EmergencyClearanceCoordinator(served)
    both = coord.observe(
        400.0, _snap({"J2": "N_J2_0"}), _rt({"J2": 1}, {"J2": 40.0}),
        {"safety_overrides": [{"junction_id": "J2", "rule": RULE_EMERGENCY}]},
        forced_emergency_lanes=frozenset({"E_J2_0"}),
    )
    assert both == []                                   # nothing closed yet
    assert len(coord._open) == 1, sorted(coord._open)   # not two events
    ev = coord._open["J2"]                              # not dropped
    assert ev.source == SOURCE_DETECTED, ev.source      # detection wins
    assert ev.lane_id == "N_J2_0" and ev.direction == "north"
    print(f"  [OK] detected + forced at J2 simultaneously -> "
          f"{len(coord._open)} event, source={ev.source!r}, "
          f"lane={ev.lane_id} (the sensed one, not the forced one)")

    # Same lane forced AND sensed: still one event, still 'detected'.
    coord2 = EmergencyClearanceCoordinator(served)
    coord2.observe(400.0, _snap({"J2": "E_J2_0"}), _rt({"J2": 0}, {"J2": 40.0}),
                   {"safety_overrides": []},
                   forced_emergency_lanes=frozenset({"E_J2_0"}))
    assert len(coord2._open) == 1
    assert coord2._open["J2"].source == SOURCE_DETECTED
    print(f"  [OK] same lane forced AND sensed -> 1 event, "
          f"source={coord2._open['J2'].source!r}")

    # source is fixed at OPEN: a vehicle arriving later at an
    # operator-opened junction does not rewrite the trigger's provenance.
    coord.observe(410.0, _snap({"J2": "N_J2_0"}), _rt({"J2": 1}, {"J2": 50.0}),
                  {"safety_overrides": []})
    assert coord._open["J2"].source == SOURCE_DETECTED
    coord3 = EmergencyClearanceCoordinator(served)
    coord3.observe(500.0, _snap({}), _rt({"J2": 0}, {"J2": 40.0}),
                   {"safety_overrides": []},
                   forced_emergency_lanes=frozenset({"E_J2_0"}))
    coord3.observe(505.0, _snap({"J2": "E_J2_0"}), _rt({"J2": 0}, {"J2": 45.0}),
                   {"safety_overrides": []},
                   forced_emergency_lanes=frozenset({"E_J2_0"}))
    assert coord3._open["J2"].source == SOURCE_OPERATOR
    print(f"  [OK] source is the TRIGGER's provenance, fixed at open — a "
          f"vehicle sensed later keeps source={coord3._open['J2'].source!r}")

    # -- 6: forced lanes are attributed to their OWN junction ---------
    coord = EmergencyClearanceCoordinator(served)
    coord.observe(600.0, _snap({}), _rt({}, {}), {"safety_overrides": []},
                  forced_emergency_lanes=frozenset({"E_J2_0"}))
    assert set(coord._open) == {"J2"}, sorted(coord._open)
    print(f"  [OK] a lane forced at J2 opens no episode at J1/J3 "
          f"(open={sorted(coord._open)})")

    # -- 7: backward compatibility — the default is detection-only ----
    # Byte-for-byte the pre-change path: same inputs, kwarg omitted vs
    # explicitly empty, and a force that is NOT passed changes nothing.
    a = EmergencyClearanceCoordinator(served)
    b = EmergencyClearanceCoordinator(served)
    c = EmergencyClearanceCoordinator(served)
    for t, amb in ((700.0, {"J2": "E_J2_0"}), (705.0, {"J2": "E_J2_0"}),
                   (715.0, {})):
        rt = _rt({"J2": 1}, {"J2": 3.0})
        a.observe(t, _snap(amb), rt, {"safety_overrides": []})
        b.observe(t, _snap(amb), rt, {"safety_overrides": []},
                  forced_emergency_lanes=frozenset())
        c.observe(t, _snap(amb), rt, {"safety_overrides": []},
                  forced_emergency_lanes=frozenset({"N_J2_0"}))
    assert [e.to_dict() for e in a.completed] == [e.to_dict() for e in b.completed]
    assert len(a.completed) == 1 and a.completed[0].source == SOURCE_DETECTED
    # c had a DIFFERENT lane forced throughout, so its episode is still open
    # at t=715 (the force outlives the vehicle) — the union working, and the
    # proof that a's/b's identical result is the detection-only path.
    assert not a._open and set(c._open) == {"J2"}
    print(f"  [OK] omitted kwarg == frozenset() (to_dict identical, "
          f"source={a.completed[0].source!r}); a force that IS passed keeps "
          f"the episode open past the vehicle")

    print(f"\nAll emergency_clearance self-tests passed.")


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _selftest()
