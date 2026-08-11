"""Parametrized generator for the PsychoFlow J1->J2->J3 linear corridor (§0.1, §6).

Builds plain-XML node/edge files and compiles them via SUMO's `netconvert`
into a .net.xml. Lane count per junction (2/3/4 per approach) is an
independent runtime parameter, not a fixed file per topology.

Junction IDs are locked as "J1", "J2", "J3" — these must match
digital_twin.junctions keys (§7.6) and corridor_adjacency pairs (§9.5)
verbatim in later phases. Do not rename.
"""

import argparse
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

VALID_LANE_COUNTS = (2, 3, 4)
DEFAULT_SPEED_MPS = 13.89  # ~50 km/h, uniform default for all edges
GENERATED_DIR = Path(__file__).parent / "generated"


def _node(nid: str, x: float, y: float, node_type: str) -> dict:
    return {"id": nid, "x": str(x), "y": str(y), "type": node_type}


def _build_nodes(spacing_m: float, arm_m: float) -> list[dict]:
    j1_x, j2_x, j3_x = 0.0, spacing_m, 2 * spacing_m
    return [
        _node("J1", j1_x, 0.0, "traffic_light"),
        _node("J2", j2_x, 0.0, "traffic_light"),
        _node("J3", j3_x, 0.0, "traffic_light"),
        _node("W1", j1_x - arm_m, 0.0, "priority"),
        _node("N1", j1_x, arm_m, "priority"),
        _node("S1", j1_x, -arm_m, "priority"),
        _node("N2", j2_x, arm_m, "priority"),
        _node("S2", j2_x, -arm_m, "priority"),
        _node("E3", j3_x + arm_m, 0.0, "priority"),
        _node("N3", j3_x, arm_m, "priority"),
        _node("S3", j3_x, -arm_m, "priority"),
    ]


def _build_edges(j1_lanes: int, j2_lanes: int, j3_lanes: int) -> list[dict]:
    """Each physical road is two directed edges. An edge's lane count is
    set by the junction it approaches (its `to` node) — this is what
    lets a single corridor edge (e.g. J1-J2) carry different lane
    counts in each direction when its two junctions differ."""
    lanes_at = {"J1": j1_lanes, "J2": j2_lanes, "J3": j3_lanes}
    segments = [
        ("W1", "J1"), ("N1", "J1"), ("S1", "J1"),
        ("J1", "J2"),
        ("N2", "J2"), ("S2", "J2"),
        ("J2", "J3"),
        ("E3", "J3"), ("N3", "J3"), ("S3", "J3"),
    ]
    edges = []
    for a, b in segments:
        for src, dst in ((a, b), (b, a)):
            lanes = lanes_at.get(dst, lanes_at.get(src))
            edges.append({
                "id": f"{src}_{dst}",
                "from": src,
                "to": dst,
                "numLanes": str(lanes),
                "speed": str(DEFAULT_SPEED_MPS),
            })
    return edges


def _write_nod_xml(nodes: list[dict], path: Path) -> None:
    root = ET.Element("nodes")
    for n in nodes:
        ET.SubElement(root, "node", n)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def _write_edg_xml(edges: list[dict], path: Path) -> None:
    root = ET.Element("edges")
    for e in edges:
        ET.SubElement(root, "edge", e)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def generate_corridor(
    j1_lanes: int,
    j2_lanes: int,
    j3_lanes: int,
    junction_spacing_m: float = 300.0,
    approach_arm_m: float = 150.0,
    output_name: str = "corridor",
) -> Path:
    """Builds the J1->J2->J3 linear corridor with independently-set
    per-junction lane counts and compiles it to a .net.xml via netconvert.

    Returns the path to the generated .net.xml.
    """
    for name, lanes in (("j1_lanes", j1_lanes), ("j2_lanes", j2_lanes), ("j3_lanes", j3_lanes)):
        if lanes not in VALID_LANE_COUNTS:
            raise ValueError(f"{name}={lanes} invalid — must be one of {VALID_LANE_COUNTS}")

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    nod_path = GENERATED_DIR / f"{output_name}.nod.xml"
    edg_path = GENERATED_DIR / f"{output_name}.edg.xml"
    net_path = GENERATED_DIR / f"{output_name}.net.xml"

    _write_nod_xml(_build_nodes(junction_spacing_m, approach_arm_m), nod_path)
    _write_edg_xml(_build_edges(j1_lanes, j2_lanes, j3_lanes), edg_path)

    result = subprocess.run(
        [
            "netconvert",
            f"--node-files={nod_path}",
            f"--edge-files={edg_path}",
            f"--output-file={net_path}",
            "--tls.default-type=static",
            "--no-turnarounds",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"netconvert failed:\n{result.stdout}\n{result.stderr}")

    return net_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the PsychoFlow J1->J2->J3 corridor")
    parser.add_argument("--j1", type=int, required=True, help="Lanes per approach at J1 (2/3/4)")
    parser.add_argument("--j2", type=int, required=True, help="Lanes per approach at J2 (2/3/4)")
    parser.add_argument("--j3", type=int, required=True, help="Lanes per approach at J3 (2/3/4)")
    parser.add_argument("--spacing", type=float, default=300.0, help="Distance between adjacent junctions (m)")
    parser.add_argument("--arm", type=float, default=150.0, help="Length of boundary approach arms (m)")
    parser.add_argument("--name", type=str, default="corridor", help="Output filename stem")
    args = parser.parse_args()

    net_path = generate_corridor(
        j1_lanes=args.j1,
        j2_lanes=args.j2,
        j3_lanes=args.j3,
        junction_spacing_m=args.spacing,
        approach_arm_m=args.arm,
        output_name=args.name,
    )
    print(f"Generated: {net_path}")


if __name__ == "__main__":
    main()
