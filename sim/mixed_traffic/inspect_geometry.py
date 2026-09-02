import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
# ^ Derived, not hardcoded. This harness ran from a scratchpad during the
#   mixed-traffic work and carried an absolute path to one machine's checkout.
#   parents[2] is the repo root from sim/mixed_traffic/. See README.md here.
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# NOTE — THIS HARNESS DELIBERATELY HAS NO `require_free()` BEACON GUARD, AND
# MUST KEEP NONE. It reads the network file statically via sumolib and starts
# no SUMO process and no TraCI connection. Same category as
# training/scripts/stage4_contamination.py and evaluation/heldout.py; see
# CLAUDE.md §8's beacon standing rule.
# ---------------------------------------------------------------------------
import sumolib

net = sumolib.net.readNet(str(REPO_ROOT / "sim/networks/generated/corridor_432.net.xml"))

for lid in ["J3_J2_0", "J3_J2_1", "J2_J1_0", "J2_J1_1", "J2_J1_2"]:
    try:
        lane = net.getLane(lid)
        print(lid, "width=", lane.getWidth(), "shape=", lane.getShape())
    except Exception as e:
        print(lid, "ERROR", e)

print()
print("Internal lanes at J2 connecting J3_J2 -> J2_J1:")
for edge_id in ["J3_J2_0", "J3_J2_1", "J3_J2_2"]:
    try:
        lane = net.getLane(edge_id)
    except Exception:
        continue
    for conn in lane.getOutgoing():
        print(f"  {edge_id} -> {conn.getToLane().getID()}  via={conn.getViaLaneID()} dir={conn.getDirection()}")
