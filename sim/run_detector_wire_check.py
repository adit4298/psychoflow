"""Part 5c done-bar: detector-source vision + incident alerts on the §13.2 wire.

Boots the real backend with `--vision-source detector --vision-clip <clip>` and
asserts the four things Part 5c claims:

  (a) a DETECTOR-SOURCED reading reached the twin, with counts present and the
      three fields a camera cannot measure reported as unmeasured.
  (b) an INJECTED incident produced an `incident_alerts` entry with a NON-NULL
      `distance_m` and a sane `approach` + 0-based `lane_index`.
  (c) all SIX agents logged into `agent_activity` in the same episode.
  (d) `emergency_vehicle_flag` NEVER appears in a `safety_overrides` record —
      NOTES §9.2's advisory-only decision, enforced rather than asserted in a
      docstring.

TWO HONEST CORRECTIONS TO THE DONE-BAR'S WORDING, both verified in this repo
--------------------------------------------------------------------------
1. (a) says "reached the OBSERVATION". It does not, and cannot: §7.2 `vision`
   rides ALONGSIDE §7.1 in the twin snapshot, and the observation vector is
   §7.1-only — `vision` appears nowhere in `env/obs_action_spec.py` or
   `env/psychoflow_env.py` (grep them). The detector reaches the twin snapshot
   and the §13.2 frame, which is the whole §7.2 contract. This harness asserts
   THAT, and separately asserts the obs is §7.1-shaped, rather than quietly
   claiming a path that does not exist. MQTT counts (Part 5b) DO reach the
   observation, because they enter through §7.1's lane sensor — a different
   channel, and `sim/run_iot_feed_check.py` proves that one.

2. The clip is `sim/media/_synthetic_selftest.mp4`, a SYNTHETIC fixture.
   `sim/media/README.md` records that no real intersection footage is in this
   repo — downloading it is a human task. So this run proves the detector is
   correctly WIRED (a real YOLOv8n forward pass over a real decoded video,
   flowing through the same consumer path as the mock), and proves nothing
   about detection quality on real traffic. Do not report it as the latter.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.control_api import ControlState, inject_incident   # noqa: E402
from backend.sim_runner import DEFAULT_CHECKPOINT, SimRunner    # noqa: E402

DEFAULT_CLIP = REPO / "sim" / "media" / "_synthetic_selftest.mp4"

#: §13.2's six named agent views (NOTES §2).
SIX_AGENTS = ("Detection", "Vision", "Prediction", "IncidentPriority",
              "Control", "Supervisor")

#: §7.2 fields a camera structurally cannot measure. The detector reports them
#: as unmeasured rather than as zero — a structural zero read as an observed
#: zero is the defect this project keeps catching.
UNMEASURABLE = ("wait_time_current", "wait_time_max_single_vehicle")

_PASS = 0
_FAIL = 0


def _check(ok: bool, label: str, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}"
          + (f"  --  {detail}" if detail else ""))


def _wait_for(predicate, frames: list, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = predicate(frames)
        if got:
            return got
        time.sleep(0.15)
    print(f"       (timed out after {timeout:.0f}s waiting for {what})")
    return None


def main(clip: Path = DEFAULT_CLIP) -> int:
    print("=" * 78)
    print("  Part 5c — detector-source vision + incident_alerts on the wire")
    print("=" * 78)
    print(f"  clip: {clip}  (SYNTHETIC fixture — see this file's docstring)")

    frames: list[dict] = []
    lock = threading.Lock()

    def sink(frame: dict) -> None:
        with lock:
            frames.append(frame)

    state = ControlState()
    runner = SimRunner(
        state,
        checkpoint=DEFAULT_CHECKPOINT,
        shadow_checkpoint=None,
        lane_counts=(4, 3, 2),
        randomize_density=False,
        spawn_emergencies=True,      # so an ambulance CAN appear for (d)
        fast=True,
        seed=7,
        frame_sink=sink,
        vision_source="detector",
        vision_clip=str(clip),
    )
    runner.start()
    try:
        if not runner.wait_until_ready(timeout=300.0):
            raise TimeoutError("sim thread never became ready")
        if runner.error:
            raise RuntimeError(f"sim thread crashed:\n{runner.error}")

        _check(runner.vision_source == "detector",
               "the detector was actually built (no silent fallback to mock)",
               f"vision_source={runner.vision_source!r}")
        _check(type(runner._env.twin.vision).__name__ == "VisionDetector",
               "the twin's §7.2 feed IS a VisionDetector",
               type(runner._env.twin.vision).__name__)

        first = _wait_for(lambda f: f[0] if f else None, frames, 120.0,
                          "the first frame")
        if first is None:
            raise TimeoutError("no frames at all")

        # -- (a) a detector-sourced reading reached the twin ------------
        vis = None
        for jdata in first["digital_twin"]["junctions"].values():
            for lane_id, obs in (jdata.get("vision") or {}).items():
                if obs.get("source") and "detector" in str(obs["source"]):
                    vis = (lane_id, obs)
                    break
            if vis:
                break
        _check(vis is not None,
               "(a) a DETECTOR-sourced §7.2 reading is in the twin snapshot",
               f"{vis[0]}: source={vis[1].get('source')!r}" if vis
               else "no detector-tagged reading found")
        if vis is not None:
            _, obs = vis
            _check(obs.get("vehicle_count") is not None,
                   "(a) it carries a vehicle_count", str(obs.get("vehicle_count")))
            unmeasured = {k: obs.get(k) for k in UNMEASURABLE if k in obs}
            _check(all(v is None for v in unmeasured.values())
                   or obs.get("wait_times_measured") is False,
                   "(a) the fields a camera cannot measure are reported "
                   "unmeasured, not zero",
                   f"wait_times_measured={obs.get('wait_times_measured')} "
                   f"{unmeasured}")
            _check(obs.get("type_composition", {}).get("ambulance", 0) == 0,
                   "(a) type_composition['ambulance'] is 0 for a detector "
                   "source (COCO has no ambulance class — fail-closed, §9.2)",
                   str(obs.get("type_composition")))
        # The honest half of correction 1: the obs vector is §7.1-only.
        _check(runner._obs is not None and runner._obs.shape[1] == 191,
               "(a) the observation vector is §7.1-shaped (vision is NOT in "
               "it — see this file's docstring)",
               f"obs shape={None if runner._obs is None else runner._obs.shape}")

        # -- (b) an injected incident -> a real distance_m ---------------
        jid, lanes = None, []
        for j, jdata in first["digital_twin"]["junctions"].items():
            lanes = list(jdata.get("lanes") or {})
            if lanes:
                jid = j
                break
        target = lanes[0]
        res = inject_incident(state, junction_id=jid, affected_lanes=[target],
                              incident_type="lane_blocked", severity="high",
                              lane_id=target, estimated_duration_s=600.0)
        print(f"  injected incident on {target} at {jid}: {res}")
        with lock:
            mark = len(frames)
        hit = _wait_for(
            lambda f: next(
                (a for fr in f[mark:] for a in fr.get("incident_alerts", [])
                 if a.get("distance_m") is not None), None),
            frames, 180.0, "an alert carrying a real distance_m")
        _check(hit is not None,
               "(b) an incident_alerts entry carries a NON-NULL distance_m",
               f"distance_m={hit['distance_m']}m "
               f"confidence={hit['distance_confidence']}" if hit else "none seen")
        if hit is not None:
            _check(hit["distance_m"] > 0.0,
                   "(b) the distance is a real measurement, not a 0.0 standing "
                   "in for 'unknown'", f"{hit['distance_m']}m")
            _check(hit.get("approach") in ("north", "south", "east", "west"),
                   "(b) approach is sane", str(hit.get("approach")))
            _check(isinstance(hit.get("lane_index"), int)
                   and hit["lane_index"] >= 0,
                   "(b) lane_index is a sane 0-based index",
                   str(hit.get("lane_index")))

        # -- (c) all six agents in one episode ---------------------------
        with lock:
            mark_c = len(frames)
        _wait_for(lambda f: len(f) - mark_c >= 60, frames, 300.0,
                  "60 decision steps")
        seen: set[str] = set()
        with lock:
            window = list(frames)
        for fr in window:
            for entry in fr.get("agent_activity", []) or []:
                if entry.get("agent"):
                    seen.add(entry["agent"])
        missing = [a for a in SIX_AGENTS if a not in seen]
        _check(not missing,
               "(c) all six agents logged into agent_activity in one episode",
               f"missing={missing} seen={sorted(seen)}" if missing
               else f"{len(seen)} agents over {len(window)} frames")

        # -- (d) the advisory flag never actuates ------------------------
        overrides = 0
        leaked = []
        for fr in window:
            entry = fr.get("decision")
            ovr = entry.get("override") if isinstance(entry, dict) else None
            if not ovr:
                continue
            overrides += 1
            blob = repr(ovr)
            if "emergency_vehicle_flag" in blob or "advisory" in blob:
                leaked.append(ovr)
        _check(not leaked,
               "(d) emergency_vehicle_flag NEVER appears in a safety_overrides "
               "record (§9.2 advisory-only)",
               f"{overrides} override(s) inspected, 0 leaked" if not leaked
               else str(leaked[:2]))
        # Non-vacuity: the advisory really was flowing while (d) held.
        adv = sum(1 for fr in window
                  for jd in fr["digital_twin"]["junctions"].values()
                  for o in (jd.get("vision") or {}).values()
                  if "emergency_vehicle_flag" in o)
        _check(adv > 0,
               "(d) ...and the flag WAS present on the wire, so (d) is not "
               "vacuous", f"{adv} vision readings carried the flag")
    finally:
        runner.stop()

    print("=" * 78)
    print(f"  {_PASS} passed, {_FAIL} failed")
    print("=" * 78)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("detector wire check (Part 5c)")
    arg = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CLIP
    raise SystemExit(main(arg))
