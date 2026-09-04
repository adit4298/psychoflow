"""One command that brings the whole demo up, and one Ctrl-C that takes it down.

    venv/Scripts/python.exe sim/run_demo.py            # the full stack
    venv/Scripts/python.exe sim/run_demo.py --dry-run  # print, launch nothing

Starts four processes, in dependency order, and shuts them down in reverse:

  1. `python -m iot.broker`              the local MQTT broker (loopback)
  2. `python -m backend.main --iot ...`  Stage 4 single-agent PPO, deterministic
  3. `python -m iot.publisher`           the simulated sensor feed
  4. `npm run dev` in frontend/          the dashboard

The frontend is pointed at the live backend through `VITE_WS_URL` in the CHILD
process environment, deliberately not through a committed `.env`: a plain
`npm run dev` must keep falling back to `frontend/fixtures/recorded_session.json`
so the dashboard still demos with no backend at all. `createSource()` in
`frontend/src/data/source.ts` picks the socket only when a URL is supplied.

WHAT THIS RUNS, said plainly for demo day (§17, §20)
----------------------------------------------------
* The deployed policy is **single-agent PPO** (Stage 4 @153,600), run
  `deterministic=True`. It is NOT MARL. `COORDINATION_MODE` staying
  `graph_attention` answers a different question — which MARL extractor won —
  and the shadow advisor, when on, is the WORSE policy shown for contrast.
* `--vision-source detector` runs a real YOLOv8n forward pass over a real
  decoded video. With the default synthetic clip it detects NOTHING (measured:
  0 COCO detections over 20 frames) because that fixture contains no vehicles.
  Pass `--clip` with real footage for the "acts on footage" beat; see
  `sim/media/README.md`, which records that downloading it is a human task.
* `--iot` makes MQTT counts overlay the §7.1 lane readings the observation is
  built from. An MQTT message can never forge an ambulance: the ambulance count
  always comes from ground truth (see backend/iot_bridge.py).
* Every §13 control endpoint is UNAUTHENTICATED and bound to loopback. This is
  a local demo surface, on purpose.
* THE METRICS ON SCREEN ARE NOT STAGE 4'S RECORDED NUMBERS while `--iot` is on.
  The simulated publisher injects synthetic counts that overlay §7.1 ground
  truth, so the policy is acting on partly-fabricated lane readings and the
  mean wait / starvation tiles read far worse than the benchmark. That is the
  IoT path being demonstrated, not a regression — but do not quote those tiles
  as PsychoFlow's performance. Run with `--no-iot` for representative figures,
  and cite `training/scripts/checkpoint_bakeoff.py` for the real ones.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Imported for the CHOICES ONLY — `backend.voice.{stt,tts}` deliberately pull
# in no SUMO, no torch and no numpy (see `backend/voice/__init__.py`), so this
# stays a launcher that starts processes rather than one that loads a
# simulator into its own interpreter.
from backend.voice.stt import (  # noqa: E402
    DEFAULT_STT_PROVIDER,
    STT_PROVIDERS,
)
from backend.voice.tts import (  # noqa: E402
    DEFAULT_TTS_PROVIDER,
    TTS_PROVIDERS,
)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PY = sys.executable
FRONTEND = REPO / "frontend"
DEFAULT_CLIP = REPO / "sim" / "media" / "_synthetic_selftest.mp4"
DASHBOARD_URL = "http://localhost:5173"
WS_URL = "ws://127.0.0.1:8000/ws"


def _commands(args) -> list[tuple[str, list[str], Path, dict]]:
    """(label, argv, cwd, extra_env) for each child, in start order."""
    backend = [PY, "-m", "backend.main",
               "--topology", args.topology,
               "--realtime-factor", str(args.realtime_factor),
               # §14 assistant. Forwarded verbatim, so the backend stays the
               # one place the choices and defaults are defined. `whisper`
               # needs no key and no network; `sarvam` is the only value that
               # spends anything, and it only ever gets here because someone
               # typed it.
               "--stt", args.stt,
               "--tts", args.tts]
    if args.vision_source == "detector":
        backend += ["--vision-source", "detector", "--vision-clip", str(args.clip)]
    if args.iot:
        backend += ["--iot"]
    if args.no_shadow:
        backend += ["--no-shadow"]

    out: list[tuple[str, list[str], Path, dict]] = []
    if args.iot:
        out.append(("broker", [PY, "-m", "iot.broker"], REPO, {}))
    out.append(("backend", backend, REPO, {}))
    if args.iot:
        out.append(("iot-publisher",
                    [PY, "-m", "iot.publisher",
                     "--steps", str(args.publish_steps),
                     "--interval", str(args.publish_interval)], REPO, {}))
    if not args.no_frontend:
        # VITE_WS_URL only in the CHILD env — see the module docstring.
        out.append(("frontend", ["npm", "run", "dev"], FRONTEND,
                    {"VITE_WS_URL": WS_URL}))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--clip", type=Path, default=DEFAULT_CLIP,
                   help="Footage for --vision-source detector.")
    p.add_argument("--vision-source", choices=("mock", "detector"),
                   default="detector")
    p.add_argument("--topology", default="432")
    p.add_argument("--realtime-factor", type=float, default=0.3)
    p.add_argument("--no-iot", dest="iot", action="store_false",
                   help="Skip the broker and the simulated sensor feed.")
    p.add_argument("--no-frontend", action="store_true",
                   help="Backend only (useful when Vite is already running).")
    p.add_argument("--no-shadow", action="store_true")
    # Choices deliberately NOT re-listed here — imported from the modules that
    # define them, so a provider added later cannot be silently unreachable
    # through the demo entrypoint.
    p.add_argument("--stt", choices=STT_PROVIDERS, default=DEFAULT_STT_PROVIDER,
                   help="Speech-to-text for the §14 assistant. Default "
                        "`whisper` (local, no key, no network). `sarvam` "
                        "needs SARVAM_API_KEY and spends free-tier credit; "
                        "`webspeech` means the BROWSER transcribes, which is "
                        "NOT on-device. Intent parsing is the local model "
                        "either way.")
    p.add_argument("--tts", choices=TTS_PROVIDERS, default=DEFAULT_TTS_PROVIDER,
                   help="Speak the confirmation line. Default `none`.")
    p.add_argument("--publish-steps", type=int, default=100000,
                   help="Sensor publishes to make; the default outlasts a demo.")
    p.add_argument("--publish-interval", type=float, default=1.0)
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would run and exit. Launches nothing.")
    args = p.parse_args(argv)

    if args.vision_source == "detector" and not args.clip.exists():
        p.error(f"--clip {args.clip} does not exist. Use --vision-source mock, "
                f"or add footage per sim/media/README.md.")

    plan = _commands(args)
    if args.dry_run:
        print("Would start, in order:\n")
        for label, argv_, cwd, env in plan:
            extra = f"\n    env: {env}" if env else ""
            print(f"  [{label}]\n    {' '.join(str(a) for a in argv_)}"
                  f"\n    cwd: {cwd}{extra}\n")
        print(f"Dashboard: {DASHBOARD_URL}   (frames from {WS_URL})")
        return 0

    procs: list[tuple[str, subprocess.Popen]] = []
    stopped = False

    def shutdown(*_a) -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        print("\n[demo] stopping ...")
        for label, proc in reversed(procs):
            if proc.poll() is None:
                print(f"[demo]   {label}")
                try:
                    proc.terminate()
                except Exception:
                    pass
        deadline = time.monotonic() + 12.0
        for _label, proc in reversed(procs):
            try:
                proc.wait(timeout=max(0.5, deadline - time.monotonic()))
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        print("[demo] stopped.")

    try:
        for label, argv_, cwd, extra_env in plan:
            env = {**os.environ, **extra_env}
            print(f"[demo] starting {label}: {' '.join(str(a) for a in argv_)}")
            procs.append((label, subprocess.Popen(
                argv_, cwd=str(cwd), env=env,
                shell=(label == "frontend" and os.name == "nt"),
            )))
            # The backend owns SUMO and takes a while to boot; the publisher
            # must not race ahead of the subscriber, nor Vite ahead of the
            # socket.
            time.sleep(6.0 if label == "backend" else 1.5)

        print("\n" + "=" * 72)
        print(f"  DASHBOARD   {DASHBOARD_URL}")
        print(f"  FRAMES      {WS_URL}")
        print("  POLICY      Stage 4 SINGLE-AGENT PPO, deterministic — say so")
        print(f"  VISION      {args.vision_source}"
              + (f"  ({args.clip.name})" if args.vision_source == "detector" else ""))
        print(f"  IOT         {'on (MQTT overlaying §7.1)' if args.iot else 'off'}")
        print("=" * 72)
        print("  Ctrl-C stops everything.\n")

        while True:
            for label, proc in procs:
                code = proc.poll()
                if code is not None:
                    print(f"[demo] {label} exited ({code}) — shutting down")
                    shutdown()
                    return code or 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    finally:
        shutdown()


if __name__ == "__main__":
    # The backend this launches owns a live SUMO instance for as long as it
    # runs, so check the Tier 1 beacon before starting one.
    from sim.sumo_activity import require_free
    require_free("end-to-end demo (Part 5d)")
    signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())
