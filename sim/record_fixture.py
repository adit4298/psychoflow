"""Record `frontend/fixtures/recorded_session.json` — Part 4c.

    venv/Scripts/python.exe sim/record_fixture.py [--frames 200] [--seed 7]

Captures N consecutive §13.2 frames from a real run and drives two operator
actions partway through, so the fixture actually exercises the paths a
frontend has to render rather than 200 quiet frames:

  * `inject_incident` (§13.1)  -> `incident_alerts`, `predictions.incident_impact`
  * `trigger_emergency` (§13.1) -> §10 emergency override, an `agent_activity`
    Supervisor veto, and a §11.2 `responder_messages` payload when the hold
    expires

Frames are captured via `frame_sink` DIRECTLY, not over the WebSocket, because
`Hub`'s per-client queue is bounded and drops frames for a slow consumer — a
fixture with silent gaps in it would be worse than none.

WHAT IS DELIBERATELY NOT IN THE FIXTURE: `iot_sensors`. No IoT/MQTT source
exists in this repo yet (Track A owns it), and synthesising one so the file
looks complete would put fabricated sensor readings into an artifact named
"recorded_session". Its shape is documented in NOTES-FOR-INTEGRATION §A3 and
unit-asserted by `sim/run_backend_smoke.py` check 8c.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend import control_api                                  # noqa: E402
from backend.control_api import ControlState                     # noqa: E402
from backend.sim_runner import DEFAULT_CHECKPOINT, SimRunner     # noqa: E402

OUT = REPO_ROOT / "frontend" / "fixtures" / "recorded_session.json"
INJECT_AT_FRAME = 40      # after the lane set is published
EMERGENCY_AT_FRAME = 90   # far enough after to be a separate, readable beat


def record(frames: int, seed: int, out: Path) -> dict:
    captured: list[dict] = []
    done = threading.Event()
    state = ControlState()
    fired: dict[str, object] = {}

    def sink(frame: dict) -> None:
        if len(captured) >= frames:
            return
        captured.append(frame)
        n = len(captured)
        # Operator actions are pushed onto ControlState.pending, which the sim
        # thread drains between decision steps — the same path the dashboard
        # and §14 voice use. Nothing here touches TraCI.
        if n == INJECT_AT_FRAME and "incident" not in fired:
            lanes = state.snapshot_stats().get("lanes", {})
            target = next((l for l, m in sorted(lanes.items())
                           if m.get("junction_id") == "J1"), None)
            if target:
                fired["incident"] = control_api.inject_incident(
                    state, "J1", [target], incident_type="accident",
                    severity="high", estimated_duration_s=600.0)
                print(f"[fixture] frame {n}: inject_incident J1 {target} -> "
                      f"{fired['incident'].get('applied')}")
        if n == EMERGENCY_AT_FRAME and "emergency" not in fired:
            lanes = state.snapshot_stats().get("lanes", {})
            target = next((l for l, m in sorted(lanes.items())
                           if m.get("junction_id") == "J2"), None)
            if target:
                fired["emergency"] = control_api.trigger_emergency(state, target)
                print(f"[fixture] frame {n}: trigger_emergency {target} -> "
                      f"{fired['emergency'].get('applied')}")
        if n >= frames:
            done.set()

    runner = SimRunner(
        state,
        checkpoint=DEFAULT_CHECKPOINT,
        shadow_checkpoint=None,
        lane_counts=(4, 3, 2),
        randomize_density=False,
        spawn_emergencies=True,
        fast=True,
        seed=seed,
        frame_sink=sink,
    )
    runner.start()
    if not runner.wait_until_ready(timeout=180.0):
        raise RuntimeError(f"sim never became ready: {runner.error}")
    done.wait(timeout=900.0)
    runner.stop()
    if runner.error:
        raise RuntimeError(f"sim errored: {runner.error}")

    captured = captured[:frames]
    out.parent.mkdir(parents=True, exist_ok=True)
    # Compact separators: the digital_twin field is large and this file is
    # committed. Still valid JSON, still one array of frames.
    out.write_text(json.dumps(captured, separators=(",", ":")),
                   encoding="utf-8")
    return _summarise(captured, out)


def _summarise(frames: list[dict], out: Path) -> dict:
    keys: dict[str, int] = {}
    for frame in frames:
        for key in frame:
            keys[key] = keys.get(key, 0) + 1
    vetoes = sum(1 for f in frames
                 for e in f.get("agent_activity", []) if e["kind"] == "veto")
    emergencies = sum(
        1 for f in frames
        if any(a.get("type") == "emergency_vehicle"
               for a in f.get("incident_alerts", [])))
    summary = {
        "path": str(out), "bytes": out.stat().st_size, "frames": len(frames),
        "sim_time_range": [frames[0]["sim_time"], frames[-1]["sim_time"]],
        "key_counts": dict(sorted(keys.items())),
        "supervisor_vetoes": vetoes,
        "frames_with_emergency_alert": emergencies,
    }
    print("\n" + "=" * 70)
    print("  RECORDED SESSION FIXTURE")
    print("=" * 70)
    for key, value in summary.items():
        print(f"  {key:30s} {value}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    record(args.frames, args.seed, args.out)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from sim.sumo_activity import require_free

    require_free("fixture recording")
    main()
