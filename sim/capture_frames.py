"""Capture N §13.2 frames to JSON — the A/B instrument for Part 4c.

    venv/Scripts/python.exe sim/capture_frames.py OUT.json [--frames N]
                                                 [--vision-source mock|detector]

Deliberately standalone and dependency-light so the SAME file can be run
inside a `git worktree` checked out at an older commit, which is how the
"identical to pre-change HEAD" comparison is made without stashing anything.
Older revisions have no `--vision-source`, so that flag is passed to
`SimRunner` only when the constructor actually accepts it.

Frames are captured via `frame_sink` DIRECTLY, never over the WebSocket:
`Hub`'s per-client queue is bounded and drops frames for a slow consumer,
which would make two captures differ for a reason having nothing to do with
the change under test.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.control_api import ControlState                    # noqa: E402
from backend.sim_runner import DEFAULT_CHECKPOINT, SimRunner    # noqa: E402


def capture(out: Path, frames: int, seed: int, vision_source: str,
            lane_counts=(4, 3, 2)) -> list[dict]:
    captured: list[dict] = []
    done = threading.Event()

    def sink(frame: dict) -> None:
        if len(captured) < frames:
            captured.append(frame)
            if len(captured) >= frames:
                done.set()

    kwargs = dict(
        checkpoint=DEFAULT_CHECKPOINT,
        shadow_checkpoint=None,      # loading it reseeds the global RNGs (S3)
        lane_counts=lane_counts,
        randomize_density=False,
        spawn_emergencies=True,      # the fixture needs an emergency sequence
        fast=True,
        seed=seed,
        frame_sink=sink,
    )
    # Only pass flags this revision's SimRunner actually has, so the same
    # script runs against an older worktree.
    accepted = inspect.signature(SimRunner.__init__).parameters
    if "vision_source" in accepted:
        kwargs["vision_source"] = vision_source

    runner = SimRunner(ControlState(), **kwargs)
    runner.start()
    if not runner.wait_until_ready(timeout=180.0):
        raise RuntimeError(f"sim never became ready: {runner.error}")
    done.wait(timeout=600.0)
    runner.stop()
    if runner.error:
        raise RuntimeError(f"sim errored: {runner.error}")

    captured = captured[:frames]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(captured, indent=1), encoding="utf-8")
    print(f"[capture] {len(captured)} frames -> {out}")
    return captured


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path)
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--vision-source", default="mock")
    args = ap.parse_args()
    capture(args.out, args.frames, args.seed, args.vision_source)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from sim.sumo_activity import require_free

    require_free("frame capture")
    main()
