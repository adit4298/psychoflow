"""Part 5b done-bar: live MQTT telemetry reaches the observation and the twin.

Boots the real backend sim thread with `--iot` against a real local broker and
a real publisher, then asserts the three things Part 5b claims:

  (a) an MQTT counts message CHANGED A LANE READING IN THE OBSERVATION
      — checked twice over: in `twin.update()`'s snapshot (the object the
        observation is built from) AND in the observation ARRAY itself, at
        `obs[j][slot * LANE_FEATURES + LF_VEHICLE_COUNT] == count /
        NORM_VEHICLE_COUNT`. Snapshot-only would prove the overlay ran, not
        that the agent can see it.
  (b) an MQTT weather message CHANGED THE TWIN SNAPSHOT'S WEATHER (§7.4).
  (c) with the publisher STOPPED MID-RUN the sim CONTINUES ON FALLBACK — the
      overlaid lane reverts to §7.1 TraCI ground truth, frames keep flowing,
      and the sim thread records no error.

Why a sentinel count rather than "the number moved"
---------------------------------------------------
`SENTINEL_COUNT` is 19 on a corridor whose lanes carry roughly 0-8 vehicles, so
a frame reporting it cannot be a natural fluctuation that happened to coincide
with the publish. This harness is the exact shape CLAUDE.md warns about — "a
verification run that passes while proving nothing" — and a delta-based check
would pass on ordinary traffic noise with the whole feed disconnected. Check
(c) is the same argument in reverse: it is what proves (a) was the FEED and
not the corridor, because the identical assertion must FAIL once the publisher
stops.

No SUMO/torch import is added to `iot/`; this file is the consumer side.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.control_api import ControlState                      # noqa: E402
from backend.sim_runner import DEFAULT_CHECKPOINT, SimRunner      # noqa: E402
from env.obs_action_spec import (                                 # noqa: E402
    LANE_FEATURES,
    LANE_SLOTS,
    LF_VEHICLE_COUNT,
    NORM_VEHICLE_COUNT,
)
from iot.publisher import IoTPublisher                            # noqa: E402
from iot.schema import LaneCountsPayload, WeatherPayload          # noqa: E402

#: Far outside the corridor's natural per-lane occupancy — see the docstring.
SENTINEL_COUNT = 19
#: Not the twin's default ("clear"), so an assertion cannot pass by accident.
SENTINEL_WEATHER = "heavy_rain"
#: Shortened from the 10.0s production default so check (c) does not idle for
#: ten wall-seconds. Set on the live bridge, not via a new constructor param —
#: this is test scaffolding, not API surface.
TEST_FRESHNESS_S = 2.0

_PASS = 0
_FAIL = 0


def _check(ok: bool, label: str, detail: str = "") -> None:
    global _PASS, _FAIL
    tag = "OK  " if ok else "FAIL"
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
    print(f"  [{tag}] {label}" + (f"  --  {detail}" if detail else ""))


class _Broker:
    """`python -m iot.broker` as a subprocess, because it owns an asyncio loop."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "_Broker":
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "iot.broker"],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        # Wait for the listening line rather than sleeping a guessed interval.
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("broker exited during startup")
            line = self.proc.stdout.readline() if self.proc.stdout else ""
            if "listening" in line.lower():
                print(f"  [broker] {line.strip()}")
                # Drain the rest in the background so the pipe cannot fill and
                # block the broker mid-run.
                threading.Thread(target=self._drain, daemon=True).start()
                return self
        raise TimeoutError("broker never reported it was listening")

    def _drain(self) -> None:
        try:
            for _ in self.proc.stdout:  # type: ignore[union-attr]
                pass
        except Exception:
            pass

    def __exit__(self, *_exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _counts_payload(junction: str, lane: str, approach: str, count: int,
                    sim_time: float) -> LaneCountsPayload:
    return LaneCountsPayload(
        junction_id=junction, lane_id=lane, approach=approach,
        vehicle_count=count, halted_count=0,
        type_composition={"bike": 0, "auto": 0, "car": count,
                          "truck": 0, "ambulance": 0},
        wait_time_current=0.0, wait_time_max_single_vehicle=0.0,
        starvation_flag=False, sim_time=sim_time,
    )


def _lane_count_in_snapshot(frame: dict, lane_id: str) -> int | None:
    for jdata in frame.get("digital_twin", {}).get("junctions", {}).values():
        lane = jdata.get("lanes", {}).get(lane_id)
        if lane is not None:
            return lane.get("vehicle_count")
    return None


def _obs_has_count(obs, junction_index: int, count: int) -> bool:
    """Is `count` present in ANY lane slot of this junction's observation row?

    Scans slots rather than resolving lane_id -> slot: the mapping is the env's
    to own, and re-deriving it here would be a second implementation that can
    drift from the one under test.
    """
    target = count / NORM_VEHICLE_COUNT
    row = obs[junction_index]
    return any(
        abs(float(row[slot * LANE_FEATURES + LF_VEHICLE_COUNT]) - target) < 1e-6
        for slot in range(LANE_SLOTS)
    )


def _wait_for(predicate, frames: list, timeout: float, what: str):
    """Poll `predicate(frames)` until it is truthy. Returns it, or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = predicate(frames)
        if got:
            return got
        time.sleep(0.15)
    print(f"       (timed out after {timeout:.0f}s waiting for {what})")
    return None


def main() -> int:
    print("=" * 78)
    print("  Part 5b — MQTT feed into the twin  (broker + publisher + live sim)")
    print("=" * 78)

    frames: list[dict] = []
    lock = threading.Lock()

    def sink(frame: dict) -> None:
        with lock:
            frames.append(frame)

    with _Broker():
        state = ControlState()
        runner = SimRunner(
            state,
            checkpoint=DEFAULT_CHECKPOINT,
            shadow_checkpoint=None,
            lane_counts=(4, 3, 2),
            randomize_density=False,
            spawn_emergencies=False,
            fast=True,
            seed=7,
            frame_sink=sink,
            iot=True,
        )
        runner.start()
        pub: IoTPublisher | None = None
        try:
            if not runner.wait_until_ready(timeout=180.0):
                raise TimeoutError("sim thread never became ready")
            if runner.error:
                raise RuntimeError(f"sim thread crashed:\n{runner.error}")

            _check(runner._iot is not None and runner._iot.connected,
                   "--iot brought up a connected subscriber",
                   f"connected={runner._iot.connected if runner._iot else None}")
            # Shorten the freshness window for check (c) — scaffolding only.
            if runner._iot is not None:
                runner._iot.freshness_window_s = TEST_FRESHNESS_S

            # -- learn the live corridor's real lane ids -------------------
            first = _wait_for(lambda f: f[0] if f else None, frames, 60.0,
                              "the first frame")
            if first is None:
                raise TimeoutError("no frames at all")
            junctions = first["digital_twin"]["junctions"]
            jid = list(junctions)[0]
            j_index = list(junctions).index(jid)
            lane_id, lane_row = next(iter(junctions[jid]["lanes"].items()))
            approach = lane_row["approach"]
            natural = lane_row["vehicle_count"]
            print(f"  target lane {lane_id} at {jid} (approach={approach}, "
                  f"natural count={natural}), obs row {j_index}")
            _check(natural != SENTINEL_COUNT,
                   "the sentinel count does not occur naturally on that lane",
                   f"natural={natural} sentinel={SENTINEL_COUNT}")

            # -- (a) counts reach the snapshot AND the observation ---------
            pub = IoTPublisher()
            pub.connect()
            sim_now = first["digital_twin"]["sim_time"]

            # Republished each poll so the reading stays inside the (now 2s)
            # freshness window while we wait for a frame carrying it.
            stop_pub = threading.Event()

            def pump() -> None:
                while not stop_pub.is_set():
                    try:
                        pub.publish_counts(_counts_payload(
                            jid, lane_id, approach, SENTINEL_COUNT, sim_now))
                    except Exception as exc:
                        print(f"       (publish failed: {exc})")
                        return
                    time.sleep(0.3)

            pump_thread = threading.Thread(target=pump, daemon=True)
            pump_thread.start()

            with lock:
                mark = len(frames)
            hit = _wait_for(
                lambda f: next(
                    (fr for fr in f[mark:]
                     if _lane_count_in_snapshot(fr, lane_id) == SENTINEL_COUNT),
                    None),
                frames, 90.0, "a frame carrying the MQTT count")
            _check(hit is not None,
                   "(a) an MQTT counts message changed the lane reading in the "
                   "twin snapshot",
                   f"{lane_id}.vehicle_count={SENTINEL_COUNT}" if hit
                   else "never observed")
            _check(hit is not None and _obs_has_count(runner._obs, j_index,
                                                      SENTINEL_COUNT),
                   "(a) the same value is present in the OBSERVATION array",
                   f"obs[{j_index}][slot*{LANE_FEATURES}+{LF_VEHICLE_COUNT}] == "
                   f"{SENTINEL_COUNT}/{NORM_VEHICLE_COUNT:.0f}"
                   f" = {SENTINEL_COUNT / NORM_VEHICLE_COUNT}")

            # -- (b) weather reaches the twin ------------------------------
            before = hit["digital_twin"]["weather"]["state"] if hit else None
            pub.publish_weather(WeatherPayload(
                state=SENTINEL_WEATHER, changed_at_sim_time=sim_now))
            with lock:
                mark_w = len(frames)
            wf = _wait_for(
                lambda f: next(
                    (fr for fr in f[mark_w:]
                     if fr["digital_twin"]["weather"]["state"] == SENTINEL_WEATHER),
                    None),
                frames, 60.0, "a frame carrying the MQTT weather state")
            _check(wf is not None,
                   "(b) an MQTT weather message changed the twin snapshot's "
                   "weather",
                   f"{before!r} -> {SENTINEL_WEATHER!r}")

            # -- ~40 decision steps under the live feed --------------------
            with lock:
                mark_run = len(frames)
            _wait_for(lambda f: len(f) - mark_run >= 40, frames, 120.0,
                      "40 decision steps under the feed")
            with lock:
                ran = len(frames) - mark_run
            _check(ran >= 40, "ran ~40 decision steps with the feed live",
                   f"{ran} frames")

            # -- (c) publisher stops mid-run -> fallback, no error ---------
            stop_pub.set()
            pump_thread.join(timeout=5)
            pub.disconnect()
            pub = None
            time.sleep(TEST_FRESHNESS_S + 1.0)      # let the reading go stale
            with lock:
                mark_c = len(frames)
            after = _wait_for(
                lambda f: next(
                    (fr for fr in f[mark_c:]
                     if _lane_count_in_snapshot(fr, lane_id) is not None),
                    None),
                frames, 60.0, "a frame after the publisher stopped")
            _check(after is not None,
                   "(c) frames keep flowing after the publisher stopped",
                   f"{len(frames) - mark_c} more frames")
            reverted = (after is not None
                        and _lane_count_in_snapshot(after, lane_id) != SENTINEL_COUNT)
            _check(reverted,
                   "(c) the stale lane reverted to §7.1 TraCI ground truth",
                   f"{lane_id}.vehicle_count="
                   f"{_lane_count_in_snapshot(after, lane_id) if after else None}"
                   f" (was {SENTINEL_COUNT})")
            _check(runner.error is None,
                   "(c) the sim thread recorded no error",
                   runner.error.splitlines()[-1] if runner.error else "clean")
            _check(runner._iot is not None and runner._iot.stats["messages_seen"] > 0,
                   "the bridge actually consumed messages (not a vacuous pass)",
                   str(runner._iot.stats) if runner._iot else "no bridge")
        finally:
            if pub is not None:
                try:
                    pub.disconnect()
                except Exception:
                    pass
            runner.stop()

    print("=" * 78)
    print(f"  {_PASS} passed, {_FAIL} failed")
    print("=" * 78)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    from sim.sumo_activity import require_free
    require_free("iot feed check (Part 5b)")
    raise SystemExit(main())
