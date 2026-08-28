"""Tier 1 SUMO-activity beacon — advisory cross-session coordination.

WHY THIS EXISTS. Nothing in this repo knew when another process was already
driving SUMO. Two coordination failures in one evening came from that: a
multi-SUMO sweep launched into a live training run killed it with
`FatalTraCIError: Could not connect` (D1, ~2,640 steps lost), and a separate
leg3/leg4 collision between concurrent sessions. Both were "somebody didn't
know somebody else was running" — a verbal heads-up between two sessions does
not scale to four.

WHAT THIS IS: a beacon, NOT a lock. A long-running SUMO owner (training, the
backend) calls `beat()` periodically; a sweep calls `require_free()` once at
`main()` entry and refuses to start if someone else is active. There is no
queueing, no blocking, no acquire/release protocol, and two sweeps can still
collide with each other. That is deliberate — see "TIER 2 DEFERRED" below.

SELF-CLEARING, because a stale beacon that blocks work is worse than the
problem it solves. A beacon is ignored if EITHER its PID is no longer alive OR
its file has not been touched in STALE_AFTER_S. A crashed or kill -9'd owner
therefore frees the beacon automatically; nobody has to remember to clean up.

WINDOWS FOOTGUN, recorded because the obvious implementation is destructive:
do NOT probe liveness with `os.kill(pid, 0)`. That is the standard POSIX idiom,
but on Windows `os.kill` routes to TerminateProcess for any signal other than
CTRL_C_EVENT/CTRL_BREAK_EVENT — so the "harmless" probe would KILL the training
run this module exists to protect. `psutil.pid_exists()` is used instead.

TIER 2 DEFERRED — a deliberate tradeoff, not an oversight. A fuller design
(acquire/release context manager, training-vs-sweep lock classes, declared
instance counts so concurrent sweeps can be capped, a --status query) was
scoped at ~90 lines plus ~8 call sites, 1-2 hours. It is NOT being built: with
the Sep 5 deadline close and Phase 10 unbuilt, that time is worth more on the
demo. Tier 1 covers the sweep-vs-long-running-owner collision, which is what
actually bit twice; sweep-vs-sweep contention has not. Recorded plainly because
it overrides this module author's own earlier "build Tier 2 on a third
incident" threshold — the schedule, not new evidence, is the reason.

USAGE
    # long-running SUMO owner (training/train.py, backend/main.py)
    from sim.sumo_activity import beat, clear
    beat("training", "stage5 D1 resume")   # call periodically; refreshes mtime
    clear()                                # on clean exit

    # any harness that launches SUMO episodes
    from sim.sumo_activity import require_free
    require_free("checkpoint bake-off")    # exits with a message if busy

Escape hatch: set PSYCHOFLOW_IGNORE_SUMO_BEACON=1 to bypass `require_free`
for deliberate parallelism. It prints what it is overriding — never silent.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
BEACON = REPO_ROOT / ".sumo_active.json"

# Longer than a PPO rollout takes to collect (~2 min at ~17 fps for 2048
# steps), so a healthy trainer never looks stale between beats.
STALE_AFTER_S = 300.0


def beat(kind: str, note: str = "") -> None:
    """Announce that this process is driving SUMO. Safe to call every rollout."""
    started = time.time()
    if BEACON.exists():
        try:
            prev = json.loads(BEACON.read_text())
            if prev.get("pid") == os.getpid():
                started = prev.get("started", started)
        except (OSError, ValueError):
            pass
    payload = {"pid": os.getpid(), "kind": kind, "note": note,
               "started": started, "updated": time.time()}
    try:
        BEACON.write_text(json.dumps(payload, indent=2))
    except OSError:
        pass  # advisory only — never take down a training run over the beacon


def clear() -> None:
    """Remove our own beacon. Never removes another process's."""
    try:
        if BEACON.exists() and json.loads(BEACON.read_text()).get("pid") == os.getpid():
            BEACON.unlink()
    except (OSError, ValueError):
        pass


def holder() -> dict | None:
    """The live beacon owner, or None if absent / stale / dead."""
    try:
        rec = json.loads(BEACON.read_text())
    except (OSError, ValueError):
        return None
    pid = rec.get("pid")
    if not isinstance(pid, int) or not psutil.pid_exists(pid):
        return None
    if time.time() - float(rec.get("updated", 0)) > STALE_AFTER_S:
        return None
    return rec


def require_free(what: str) -> None:
    """Refuse to start `what` while another process is driving SUMO."""
    rec = holder()
    if rec is None:
        return
    age = (time.time() - float(rec["started"])) / 60.0
    msg = (f"\n  REFUSING TO START: {what}\n"
           f"  Another process is already driving SUMO:\n"
           f"    pid={rec['pid']}  kind={rec['kind']}  running {age:.0f} min\n"
           f"    note: {rec['note'] or '(none)'}\n"
           f"  Launching concurrent SUMO instances has crashed a training run\n"
           f"  before (FatalTraCIError: Could not connect). Wait for it, or set\n"
           f"  PSYCHOFLOW_IGNORE_SUMO_BEACON=1 if you know it is safe.\n")
    if os.environ.get("PSYCHOFLOW_IGNORE_SUMO_BEACON") == "1":
        print(msg.replace("REFUSING TO START", "OVERRIDDEN (beacon ignored) —"))
        return
    raise SystemExit(msg)


def status() -> str:
    rec = holder()
    if rec is None:
        return "SUMO beacon: free (no live owner)"
    age = (time.time() - float(rec["started"])) / 60.0
    return (f"SUMO beacon: HELD by pid={rec['pid']} kind={rec['kind']} "
            f"for {age:.0f} min — {rec['note'] or '(no note)'}")


if __name__ == "__main__":
    print(status())
