"""Per-lane traffic sensing (§7.1).

Reads live TraCI state for a single approach lane and emits the §7.1
schema. This is the ground-truth sensing module — vision_mock (§7.2)
re-emits these same readings through a camera-shaped envelope.

TraCI field mapping (§7.1):
  vehicle_count  -> lane.getLastStepVehicleNumber
  halted_count   -> lane.getLastStepHaltingNumber   (this is what reward
                    and scoring consume, not raw vehicle_count)
  wait_time_current -> lane.getWaitingTime
  wait_time_max_single_vehicle -> max over vehicle.getAccumulatedWaitingTime

NOTE on accumulated waiting time: SUMO's default --waiting-time-memory is
100s. The starvation threshold is 90s (§0.1), so with the default the
metric saturates ~10s past the line and a badly starved lane is
indistinguishable from a marginally starved one. Callers MUST launch SUMO
with a larger memory window (see WAITING_TIME_MEMORY_S) or §9.1's
non-linear starvation_bonus and §9.4's starvation penalty lose their
magnitude signal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import traci

# The five vehicle types defined in sim/networks/vehicle_types.add.xml,
# matching §7.1's type_composition schema.
VEHICLE_TYPES = ("bike", "auto", "car", "truck", "ambulance")

DEFAULT_STARVATION_THRESHOLD_S = 90.0

# Required SUMO launch option — see module docstring.
WAITING_TIME_MEMORY_S = 1000


@dataclass
class LaneReading:
    """One lane, one simulation step — the §7.1 contract.

    `approach` is an addition to §7.1's literal schema: §7.1 illustrates
    lane ids like "north_approach_0", but the generated corridor (§6,
    Phase 1) produces raw SUMO ids like "N1_J1_0". The raw id stays
    canonical because TraCI and the safety validator (§10) must act on
    it; `approach` carries the compass direction that §12.2's narration
    templates need ("Lane 3, North - selected").
    """

    lane_id: str
    approach: str
    vehicle_count: int
    halted_count: int
    type_composition: dict[str, int]
    wait_time_current: float
    wait_time_max_single_vehicle: float
    starvation_flag: bool

    def to_dict(self) -> dict:
        return asdict(self)


class LaneSensor:
    """Stateless per-step reader. Holds only configuration."""

    def __init__(self, starvation_threshold_s: float = DEFAULT_STARVATION_THRESHOLD_S):
        self.starvation_threshold_s = starvation_threshold_s
        # Diagnostics only, never part of the emitted schema: any vType id
        # seen on the road that isn't one of VEHICLE_TYPES. Should stay
        # empty — we control the vTypes — but silence here would hide a
        # scenario-generator bug later.
        self.unknown_types: set[str] = set()

    def read_lane(self, lane_id: str, approach: str = "unknown") -> LaneReading:
        vehicle_ids = traci.lane.getLastStepVehicleIDs(lane_id)

        composition = {vtype: 0 for vtype in VEHICLE_TYPES}
        max_single_wait = 0.0

        for vehicle_id in vehicle_ids:
            # SUMO appends "@..." to the type id when a vehicle has been given
            # a singular (per-vehicle modified) type; strip it back to the base.
            type_id = traci.vehicle.getTypeID(vehicle_id).split("@")[0]
            if type_id in composition:
                composition[type_id] += 1
            else:
                self.unknown_types.add(type_id)

            accumulated = traci.vehicle.getAccumulatedWaitingTime(vehicle_id)
            if accumulated > max_single_wait:
                max_single_wait = accumulated

        return LaneReading(
            lane_id=lane_id,
            approach=approach,
            vehicle_count=traci.lane.getLastStepVehicleNumber(lane_id),
            halted_count=traci.lane.getLastStepHaltingNumber(lane_id),
            type_composition=composition,
            wait_time_current=round(traci.lane.getWaitingTime(lane_id), 2),
            wait_time_max_single_vehicle=round(max_single_wait, 2),
            starvation_flag=max_single_wait > self.starvation_threshold_s,
        )

    def read_lanes(self, lane_approach_map: dict[str, str]) -> dict[str, LaneReading]:
        """Read every lane in {lane_id: approach}, keyed by lane_id."""
        return {
            lane_id: self.read_lane(lane_id, approach)
            for lane_id, approach in lane_approach_map.items()
        }
