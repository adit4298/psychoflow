"""Unified corridor state model (§7.6).

One object, updated once per simulation step, merging §7.1-7.5 into the
single corridor-wide state every downstream module reads from. No module
outside perception/ queries SUMO/TraCI directly — that rule is what
guarantees prediction (§8), intervention (§9), the safety validator
(§10), the coordinator (§11) and the WebSocket stream (§13.2) are all
reasoning about the same reality at the same instant.

Update model: PULL, with a stateful-registry carve-out.
  - lane_sensor (§7.1), vision_mock (§7.2) and v2x (§7.5) are pure
    functions of current TraCI state and are re-read fresh every step.
  - incident_intake (§7.3) and weather (§7.4) own a lifecycle nothing in
    TraCI holds, so they are registries: external callers PUSH events in
    (twin.incidents.report(...), twin.weather.set_state(...)) and the
    twin PULLS their current state out during update().

Pure push (modules mutating a shared object) was rejected: write ordering
becomes implicit, no instant is guaranteed to hold a consistent snapshot,
and "which module wrote this field" turns into a debugging problem. Pull
gives env.step() (§9.2) exactly one coherent, reproducible snapshot per
step, and hands §13.2's WebSocket the same object for free.

Corridor topology is the layout locked in §0.1: 3 junctions, linear,
J1 -> J2 -> J3. Junction ids match the Phase 1 network verbatim.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import sumolib
import traci

from perception.incident_intake import IncidentIntake
from perception.lane_sensor import DEFAULT_STARVATION_THRESHOLD_S, LaneSensor
from perception.v2x import V2XEmitter
from perception.vision_mock import VisionMock
from perception.weather import WeatherModel

CORRIDOR_JUNCTIONS = ("J1", "J2", "J3")
# §7.6 / §9.5 — J2 has two neighbours, J1 and J3 one each.
CORRIDOR_ADJACENCY = [["J1", "J2"], ["J2", "J3"]]

DEFAULT_V2X_BUFFER_SIZE = 50


def _compass_direction(from_x: float, from_y: float, at_x: float, at_y: float) -> str:
    """Direction an approach arrives FROM, derived geometrically.

    Derived from node coordinates rather than parsed out of edge id
    strings so it stays correct if the generator's naming changes.
    """
    dx, dy = from_x - at_x, from_y - at_y
    if abs(dx) >= abs(dy):
        return "west" if dx < 0 else "east"
    return "south" if dy < 0 else "north"


class DigitalTwin:
    def __init__(
        self,
        net_file: str | Path,
        starvation_threshold_s: float = DEFAULT_STARVATION_THRESHOLD_S,
        v2x_buffer_size: int = DEFAULT_V2X_BUFFER_SIZE,
        seed: int | None = None,
    ):
        self._net = sumolib.net.readNet(str(net_file))
        self._topology = self._derive_topology()

        self.lane_sensor = LaneSensor(starvation_threshold_s=starvation_threshold_s)
        self.vision = VisionMock(seed=seed)
        self.incidents = IncidentIntake()
        self.weather = WeatherModel()
        self.v2x = V2XEmitter(seed=None if seed is None else seed + 1)

        self._v2x_recent: deque[dict] = deque(maxlen=v2x_buffer_size)
        self._snapshot: dict | None = None

    # ------------------------------------------------------------------
    # Topology (derived once from the .net.xml, not hardcoded)
    # ------------------------------------------------------------------
    def _derive_topology(self) -> dict[str, dict]:
        """Map each junction to its approach lanes, directions, lane count.

        Only INCOMING lanes are sensed: §7.1 is about queues waiting at a
        signal, and an outgoing lane has no queue at that junction.
        """
        topology: dict[str, dict] = {}
        for junction_id in CORRIDOR_JUNCTIONS:
            node = self._net.getNode(junction_id)
            at_x, at_y = node.getCoord()

            lane_approach_map: dict[str, str] = {}
            lane_counts = set()
            for edge in node.getIncoming():
                from_x, from_y = edge.getFromNode().getCoord()
                direction = _compass_direction(from_x, from_y, at_x, at_y)
                lane_counts.add(edge.getLaneNumber())
                for lane in edge.getLanes():
                    lane_approach_map[lane.getID()] = direction

            # By construction (Phase 1) every edge approaching a junction
            # carries that junction's lane count. If that ever stops being
            # true the observation padding in §9.2 would silently mis-size.
            if len(lane_counts) != 1:
                raise ValueError(
                    f"{junction_id}: inconsistent approach lane counts {sorted(lane_counts)} — "
                    "§9.2 obs padding assumes one lane count per junction"
                )

            topology[junction_id] = {
                "lane_approach_map": lane_approach_map,
                "lane_count": lane_counts.pop(),
            }
        return topology

    @property
    def topology(self) -> dict[str, dict]:
        return self._topology

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def attach(self) -> None:
        """Call once after traci.start() — snapshots weather baselines."""
        self.weather.attach()

    def reset(self, sim_time: float = 0.0) -> None:
        """New episode (§9.2): clear incidents, restore clear weather."""
        self.incidents.reset()
        self.weather.reset(sim_time)
        self._v2x_recent.clear()
        self._snapshot = None

    # ------------------------------------------------------------------
    # Per-step update
    # ------------------------------------------------------------------
    def update(self, sim_time: float | None = None) -> dict:
        """Assemble one §7.6-shaped snapshot for the current step."""
        if sim_time is None:
            sim_time = traci.simulation.getTime()

        junctions = {}
        for junction_id, topo in self._topology.items():
            readings = self.lane_sensor.read_lanes(topo["lane_approach_map"])
            junctions[junction_id] = {
                "lanes": {lane_id: r.to_dict() for lane_id, r in readings.items()},
                # §7.2 rides alongside §7.1 rather than replacing it, so a
                # consumer can be pointed at either feed. §7.6's schema
                # elides this ("...per §7.1 shape..."); it is the honest
                # merge of the fifth perception input into the twin.
                "vision": self.vision.observe_all(readings),
                "current_phase": traci.trafficlight.getPhase(junction_id),
                "lane_count": topo["lane_count"],
            }

        self._v2x_recent.extend(self.v2x.collect(sim_time))

        self._snapshot = {
            "sim_time": sim_time,
            "corridor_adjacency": CORRIDOR_ADJACENCY,
            "junctions": junctions,
            "active_incidents": [inc.to_dict() for inc in self.incidents.get_active(sim_time)],
            "weather": self.weather.get_state(),
            "v2x_messages_recent": list(self._v2x_recent),
        }
        return self._snapshot

    @property
    def snapshot(self) -> dict:
        if self._snapshot is None:
            raise RuntimeError("DigitalTwin.update() has not been called yet")
        return self._snapshot
