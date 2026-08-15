"""Randomized scenario / route generation (§6, §9.2).

Minimal version, built for Phase 3's env.reset(). Scope was confirmed
before building: reset() has to produce a route file with varying density
for §16's Stage 3, so the route writer exists now; anything beyond that
(incident scripting, per-scenario weather schedules) is deliberately not
here yet.

Route topology is identical across every lane-count combination — the
generator (§6) always emits the same edge ids (W1_J1, J1_J2, ...), only
lane counts change — so one writer covers all 27 corridors.

Flows deliberately END before the episode horizon (flows_end_s <
episode_horizon_s). That is what makes §9.2's "vehicles-cleared target"
reachable at all: with flows running the whole episode there is always
another vehicle pending and the corridor never empties.
"""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from pathlib import Path

# Corridor through-routes plus the cross street at each junction.
ROUTES = {
    "r_we": "W1_J1 J1_J2 J2_J3 J3_E3",
    "r_ew": "E3_J3 J3_J2 J2_J1 J1_W1",
    "r_ns1": "N1_J1 J1_S1",
    "r_sn1": "S1_J1 J1_N1",
    "r_ns2": "N2_J2 J2_S2",
    "r_sn2": "S2_J2 J2_N2",
    "r_ns3": "N3_J3 J3_S3",
    "r_sn3": "S3_J3 J3_N3",
}
CORRIDOR_ROUTES = ("r_we", "r_ew")
CROSS_ROUTES = ("r_ns1", "r_sn1", "r_ns2", "r_sn2", "r_ns3", "r_sn3")

# §7.1's four everyday types; ambulance is spawned explicitly so its
# arrival time is deterministic rather than a draw from the distribution.
MIX_TYPES = ("bike", "auto", "car", "truck")
MIX_PROBABILITIES = (0.15, 0.25, 0.50, 0.10)


def write_route_file(
    path: str | Path,
    rng: random.Random,
    corridor_veh_per_hour: float = 1000.0,
    cross_veh_per_hour: float = 600.0,
    flows_end_s: float = 3000.0,
    randomize_density: bool = False,
    density_range: tuple[float, float] = (0.6, 1.4),
    emergency_departures: tuple[float, ...] = (),
) -> tuple[Path, dict[str, float], list[dict]]:
    """Write one episode's route file.

    Returns (path, density_summary, emergency_info).

    density_summary is the mean drawn multiplier per route group
    ({"corridor_mean", "cross_mean"}, both 1.0 when randomize_density=False),
    for Stage 3's §16 checkpoint to log per-episode alongside lane_counts
    (same confound-check need that drove the lane_counts logging fix — see
    CLAUDE.md/BUILD_LOG's Stage 2 entry). Each of the 8 flows draws its OWN
    multiplier (see `flow()` below), so the mean is a lightweight summary,
    not the full per-flow draw.

    emergency_info is a list of {"route", "depart_s"} dicts, one per spawned
    emergency vehicle (empty when emergency_departures is empty), for
    Stage 4's §16 checkpoint — a cross-street route is a structurally
    different, potentially harder-to-serve draw than a corridor-through one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    root = ET.Element("routes")
    ET.SubElement(root, "vTypeDistribution", {
        "id": "mixed",
        "vTypes": " ".join(MIX_TYPES),
        "probabilities": " ".join(str(p) for p in MIX_PROBABILITIES),
    })
    for route_id, edges in ROUTES.items():
        ET.SubElement(root, "route", {"id": route_id, "edges": edges})

    corridor_mults: list[float] = []
    cross_mults: list[float] = []

    def flow(route_id: str, base_vph: float, mults: list[float]) -> None:
        # Each flow draws its own multiplier, so a scenario can be busy
        # on the corridor and quiet on the cross streets rather than
        # uniformly scaled — that asymmetry is what fairness has to cope
        # with (§9.3).
        mult = rng.uniform(*density_range) if randomize_density else 1.0
        mults.append(mult)
        ET.SubElement(root, "flow", {
            "id": f"f_{route_id}",
            "type": "mixed",
            "route": route_id,
            "begin": "0",
            "end": str(flows_end_s),
            "vehsPerHour": f"{base_vph * mult:.1f}",
            "departLane": "random",
            "departSpeed": "max",
        })

    for route_id in CORRIDOR_ROUTES:
        flow(route_id, corridor_veh_per_hour, corridor_mults)
    for route_id in CROSS_ROUTES:
        flow(route_id, cross_veh_per_hour, cross_mults)

    density_summary = {
        "corridor_mean": sum(corridor_mults) / len(corridor_mults),
        "cross_mean": sum(cross_mults) / len(cross_mults),
    }

    emergency_info: list[dict] = []
    for i, depart in enumerate(emergency_departures):
        route_id = rng.choice(list(ROUTES))
        emergency_info.append({"route": route_id, "depart_s": depart})
        ET.SubElement(root, "vehicle", {
            "id": f"amb_{i + 1}",
            "type": "ambulance",
            "route": route_id,
            "depart": f"{depart:.1f}",
            "departLane": "best",
            "departSpeed": "max",
        })

    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)
    return path, density_summary, emergency_info
