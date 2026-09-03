"""FastAPI app: WebSocket live-state stream (§13.2) + control API (§13.1).

    uvicorn backend.main:app            # default: auto-mode checkpoint, live pacing
    python -m backend.main --help       # options

Layout:
  * one `SimRunner` (backend/sim_runner.py) owns the SUMO/env loop on its own
    thread and is the only thing that touches TraCI;
  * `Hub` fans each §13.2 frame out to every connected WebSocket client, off the
    sim thread via `loop.call_soon_threadsafe`;
  * the control endpoints are thin wrappers over `backend/control_api.py`'s plain
    functions — the same functions §14's voice intent agent will import.

`--no-shadow` / `--shadow-checkpoint` control the §13.2 `shadow_advisor` key —
a READ-ONLY second opinion from the §9.5 MARL checkpoint that never drives the
road. Default ON when the checkpoint file exists. Read the honesty note at
`backend/sim_runner.py`'s DEFAULT_SHADOW_CHECKPOINT before showing it to anyone.

STANDING RULE (CLAUDE.md §8): nothing here references `enable_safety_validator`.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.control_api import (
    ControlState,
    DEFAULT_INCIDENT_DURATION_S,
    clear_override,
    force_phase,
    get_stats,
    inject_incident,
    set_baseline_mode,
    set_lane_bias,
    set_mode,
    set_topology,
    trigger_emergency,
)

# Dashboard dev server (Vite default). The §13 control API is unauthenticated,
# so the browser-side allowlist is deliberately tiny and credential-less.
ALLOWED_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

# Hosts that are safe to bind without --allow-lan.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _host_rejection(host: str, allow_lan: bool) -> str | None:
    """Return an error string if binding `host` should be refused, else None.

    The control API has no auth (see backend/control_api.py's SECURITY
    BOUNDARY note); a non-loopback bind exposes set_topology /
    trigger_emergency / inject_incident / force_phase to the whole network,
    so it requires an explicit --allow-lan opt-in.
    """
    if host in LOOPBACK_HOSTS or allow_lan:
        return None
    return (
        f"--host {host!r} is not loopback and --allow-lan was not given. The "
        f"§13 control API is UNAUTHENTICATED; binding it to a reachable "
        f"interface lets anyone on the network drive the simulation. Re-run "
        f"with --allow-lan if that is genuinely intended."
    )
from backend.sim_runner import (
    DEFAULT_CHECKPOINT,
    DEFAULT_SHADOW_CHECKPOINT,
    SimRunner,
)


class Hub:
    """Broadcast the latest §13.2 frame to all connected WebSocket clients.

    `publish_from_thread` is called from the sim thread; it hops onto the event
    loop with `call_soon_threadsafe`, so every `asyncio.Queue` touch happens on
    the loop thread. Slow clients simply miss frames (bounded queue) rather than
    back-pressuring the simulation.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[asyncio.Queue] = set()
        self._latest: dict | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish_from_thread(self, frame: dict) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._broadcast, frame)

    def _broadcast(self, frame: dict) -> None:
        self._latest = frame
        for q in list(self._clients):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass  # slow client — drop this frame for them only

    async def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=16)
        self._clients.add(q)
        if self._latest is not None:
            q.put_nowait(self._latest)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)


class SetModeBody(BaseModel):
    mode: str


class SetLaneBiasBody(BaseModel):
    lane_id: str
    weight: float
    duration_s: float


class TriggerEmergencyBody(BaseModel):
    lane_id: str


class SetTopologyBody(BaseModel):
    topology_id: str


class SetBaselineModeBody(BaseModel):
    baseline: str


class InjectIncidentBody(BaseModel):
    junction_id: str
    affected_lanes: list[str]
    incident_type: str = "lane_blocked"
    severity: str = "high"
    lane_id: str | None = None
    estimated_duration_s: float = DEFAULT_INCIDENT_DURATION_S


class ForcePhaseBody(BaseModel):
    junction_id: str
    phase: int


class ClearOverrideBody(BaseModel):
    junction_id: str | None = None


def create_app(
    *,
    checkpoint: Path | None = DEFAULT_CHECKPOINT,
    shadow_checkpoint: Path | None = DEFAULT_SHADOW_CHECKPOINT,
    lane_counts: tuple[int, int, int] = (4, 3, 2),
    randomize_density: bool = True,
    spawn_emergencies: bool = True,
    realtime_factor: float = 0.3,
    fast: bool = False,
    seed: int = 7,
    demo_driving: bool = False,
    enable_orchestrator: bool = True,
    vision_source: str = "mock",
) -> FastAPI:
    state = ControlState()
    hub = Hub()
    runner = SimRunner(
        state,
        checkpoint=checkpoint,
        shadow_checkpoint=shadow_checkpoint,
        lane_counts=lane_counts,
        randomize_density=randomize_density,
        spawn_emergencies=spawn_emergencies,
        realtime_factor=realtime_factor,
        fast=fast,
        seed=seed,
        frame_sink=hub.publish_from_thread,
        demo_driving=demo_driving,
        enable_orchestrator=enable_orchestrator,
        vision_source=vision_source,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        runner.start()
        try:
            yield
        finally:
            runner.stop()

    app = FastAPI(title="PsychoFlow Backend (§13)", lifespan=lifespan)
    # Explicit origin allowlist — the dashboard dev server only. No wildcard,
    # and allow_credentials=False so a browser can never be coaxed into
    # sending cookies/auth to this unauthenticated surface from another site.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.state.control = state
    app.state.hub = hub
    app.state.runner = runner

    router = APIRouter(prefix="/control")

    @router.post("/set_mode")
    def _set_mode(body: SetModeBody):
        return set_mode(state, body.mode)

    @router.post("/set_lane_bias")
    def _set_lane_bias(body: SetLaneBiasBody):
        return set_lane_bias(state, body.lane_id, body.weight, body.duration_s)

    @router.get("/get_stats")
    def _get_stats():
        return get_stats(state)

    @router.post("/trigger_emergency")
    def _trigger_emergency(body: TriggerEmergencyBody):
        return trigger_emergency(state, body.lane_id)

    @router.post("/set_topology")
    def _set_topology(body: SetTopologyBody):
        return set_topology(state, body.topology_id)

    @router.post("/set_baseline_mode")
    def _set_baseline_mode(body: SetBaselineModeBody):
        return set_baseline_mode(state, body.baseline)

    @router.post("/inject_incident")
    def _inject_incident(body: InjectIncidentBody):
        return inject_incident(
            state, body.junction_id, body.affected_lanes,
            incident_type=body.incident_type, severity=body.severity,
            lane_id=body.lane_id, estimated_duration_s=body.estimated_duration_s,
        )

    @router.post("/force_phase")
    def _force_phase(body: ForcePhaseBody):
        return force_phase(state, body.junction_id, body.phase)

    @router.post("/clear_override")
    def _clear_override(body: ClearOverrideBody):
        return clear_override(state, body.junction_id)

    app.include_router(router)

    @app.get("/health")
    def _health():
        # PUBLIC surface: a boolean plus the exception CLASS only. The full
        # traceback stays server-side — the sim thread prints it to stdout
        # and it never rides the wire.
        err = runner.error
        err_class = None
        if err:
            tail = err.strip().splitlines()[-1] if err.strip() else ""
            err_class = (tail.split(":", 1)[0].strip() or "error")[:80]
        return {
            "sim_ready": runner._started.is_set(),
            "sim_error": err is not None,
            "sim_error_class": err_class,
            "has_checkpoint": state.has_checkpoint,
            "mode": state.mode,
            "baseline_mode": state.baseline_mode,
        }

    @app.websocket("/ws")
    async def _ws(websocket: WebSocket):
        await websocket.accept()
        q = await hub.register()
        try:
            while True:
                frame = await q.get()
                await websocket.send_json(frame)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unregister(q)

    return app


app = create_app()


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--allow-lan", action="store_true",
                        help="Permit binding a non-loopback --host. The §13 "
                             "control API is UNAUTHENTICATED — only use this "
                             "on a trusted, isolated network.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--no-checkpoint", action="store_true",
                        help="Run Tier 0 only (auto mode unavailable).")
    # SHADOW ADVISOR (§13.2 `shadow_advisor`) — read-only, advisory. Default
    # ON when the file exists; an absent file is not an error (the key is
    # simply never emitted). It never drives the road — see
    # backend/sim_runner.py's DEFAULT_SHADOW_CHECKPOINT honesty note: the
    # shadow is the WORSE policy on every 4a bake-off metric.
    parser.add_argument("--shadow-checkpoint", type=Path,
                        default=DEFAULT_SHADOW_CHECKPOINT,
                        help="§9.5 MARL checkpoint to run in read-only "
                             "shadow mode alongside the deployed policy.")
    parser.add_argument(
        "--demo-driving", action="store_true",
        help="Use the DEMO-ONLY mixed-traffic driving model "
             "(sim/networks/vehicle_types_demo.add.xml + SUMO sublane). "
             "Changes vehicle dynamics, so figures produced under it are NOT "
             "comparable to any recorded evaluation number. Default off.")
    # ORCHESTRATOR (§13.2 `agent_activity`) — READ-ONLY six-agent blackboard.
    # Default ON. The OFF switch exists so the paired-equality check in
    # sim/run_orchestrator_check.py can run the same seed with and without it
    # and prove the control path is byte-identical; without the flag that
    # check is impossible. Mirrors --no-shadow exactly.
    # §7.2 VISION SOURCE (Part 4c). "mock" is the default and performs NO
    # swap at all, so the default frame stream is byte-identical to before
    # this flag existed. "detector" needs Track A's
    # perception.vision_source factory; if it is not importable the runner
    # falls back to the mock loudly rather than taking the demo down.
    parser.add_argument("--vision-source", choices=("mock", "detector"),
                        default="mock",
                        help="§7.2 perception feed. 'mock' (default) is the "
                             "simulated CCTV envelope and detects nothing; "
                             "'detector' uses Track A's real detector when "
                             "available.")
    parser.add_argument("--no-orchestrator", action="store_true",
                        help="Disable the orchestrator blackboard (no "
                             "`agent_activity` key on the §13.2 stream).")
    parser.add_argument("--no-shadow", action="store_true",
                        help="Disable the shadow advisor (no "
                             "`shadow_advisor` key on the §13.2 stream).")
    parser.add_argument("--topology", default="432", help="Initial lane counts, e.g. 432.")
    parser.add_argument("--realtime-factor", type=float, default=0.3,
                        help="Wall-clock seconds to sleep per decision step.")
    parser.add_argument("--fast", action="store_true", help="No pacing sleep.")
    args = parser.parse_args()

    rejection = _host_rejection(args.host, args.allow_lan)
    if rejection is not None:
        parser.error(rejection)
    if args.host not in LOOPBACK_HOSTS:
        bang = "!" * 74
        print(f"\n{bang}\n"
              f"!!  PsychoFlow backend binding {args.host}:{args.port} — "
              f"NON-LOOPBACK, UNAUTHENTICATED.\n"
              f"!!  Anyone who can reach this port can drive the simulation "
              f"(set_topology,\n"
              f"!!  trigger_emergency, inject_incident, force_phase). "
              f"--allow-lan was given.\n"
              f"{bang}\n")

    import uvicorn

    lane_counts = tuple(int(d) for d in args.topology)
    globals()["app"] = create_app(
        checkpoint=None if args.no_checkpoint else args.checkpoint,
        shadow_checkpoint=None if args.no_shadow else args.shadow_checkpoint,
        demo_driving=args.demo_driving,
        enable_orchestrator=not args.no_orchestrator,
        vision_source=args.vision_source,
        lane_counts=lane_counts,
        realtime_factor=args.realtime_factor,
        fast=args.fast,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    _main()
