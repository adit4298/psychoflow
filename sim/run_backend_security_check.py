"""Targeted checks for the 2026-08-31 backend security hardening.

Every check drives ONE fix and prints the raw rejection / behaviour it
proves. All of it is offline — no SUMO process, no `env.reset()`, so (like
`training/scripts/stage4_contamination.py` and `evaluation/heldout.py`)
this harness deliberately carries NO Tier 1 beacon guard: it launches
nothing to collide with.

Fixes covered (task STEP 1-3):

  1  math.isfinite + range checks on set_lane_bias (weight, duration_s)
  1  math.isfinite + upper bound on inject_incident.estimated_duration_s
  1  affected_lanes length cap
  1  sim-thread inner try/except — one bad iteration is survivable
  1  --host loopback guard (_host_rejection) + --allow-lan
  1  CORSMiddleware with an explicit credential-less origin allowlist
  1  set_topology cooldown + no-op when the combo already matches
  1  active-incident cap; fail-closed while the lane set is empty
  1  /health leaks only a boolean + an exception class, never a traceback
  2  server-side function allowlist (control_api.dispatch)
  3  force_phase / clear_override — mask-checked, deferred, voice_command
  8  .claude/settings.local.json is in the repo .gitignore
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend.sim_runner as sim_runner  # noqa: E402
from backend import control_api  # noqa: E402
from backend.control_api import (  # noqa: E402
    Command,
    ControlState,
    INCIDENT_DURATION_RANGE_S,
    LANE_BIAS_DURATION_RANGE_S,
    LANE_BIAS_WEIGHT_RANGE,
    MAX_AFFECTED_LANES,
    clear_override,
    dispatch,
    force_phase,
    inject_incident,
    set_lane_bias,
    set_topology,
    trigger_emergency,
)
from backend.main import (  # noqa: E402
    ALLOWED_ORIGINS,
    LOOPBACK_HOSTS,
    _host_rejection,
    create_app,
)

RULE = "=" * 78
_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {label}" + (f"  --  {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1


def _state_with_lanes() -> ControlState:
    """A ControlState whose stats cache carries a small published lane set,
    so the fail-closed guard is satisfied and the numeric checks are what
    actually decide the outcome."""
    s = ControlState()
    s.publish_stats({
        "lane_counts": [4, 3, 2],
        "lanes": {
            "N1_J1_0": {"junction_id": "J1"},
            "N1_J1_1": {"junction_id": "J1"},
            "E1_J2_0": {"junction_id": "J2"},
        },
    })
    return s


# ---------------------------------------------------------------------------
# STEP 1.1 — set_lane_bias: finiteness + [0.1,10.0] weight, [10,900] duration
# ---------------------------------------------------------------------------
def check_lane_bias_bounds() -> None:
    print("\n-- STEP 1.1  set_lane_bias finiteness + range --")
    s = _state_with_lanes()
    lo_w, hi_w = LANE_BIAS_WEIGHT_RANGE
    lo_d, hi_d = LANE_BIAS_DURATION_RANGE_S

    for label, w, d in [
        ("NaN weight", float("nan"), 300.0),
        ("+inf weight", float("inf"), 300.0),
        ("NaN duration", 2.0, float("nan")),
        (f"weight below {lo_w}", 0.05, 300.0),
        (f"weight above {hi_w}", 25.0, 300.0),
        (f"duration below {lo_d}s", 2.0, 5.0),
        (f"duration above {hi_d}s", 2.0, 100_000.0),
        ("negative weight", -3.0, 300.0),
    ]:
        r = set_lane_bias(s, "N1_J1_0", w, d)
        check(f"1.1 rejects {label}", r["applied"] is False, r.get("reason", ""))

    r = set_lane_bias(s, "N1_J1_0", 5.0, 300.0)
    check("1.1 accepts an in-range bias (weight 5.0 / 300s)",
          r["applied"] is True, str(r))


# ---------------------------------------------------------------------------
# STEP 1.1 — inject_incident: finiteness + duration upper bound + lane cap
# ---------------------------------------------------------------------------
def check_inject_incident_bounds() -> None:
    print("\n-- STEP 1.1  inject_incident finiteness / duration cap / lane cap --")
    s = _state_with_lanes()
    lo_d, hi_d = INCIDENT_DURATION_RANGE_S

    for label, kw in [
        ("non-finite duration", dict(estimated_duration_s=float("inf"))),
        (f"duration above {hi_d}s", dict(estimated_duration_s=hi_d + 1.0)),
        ("duration <= 0", dict(estimated_duration_s=0.0)),
    ]:
        r = inject_incident(s, "J1", ["N1_J1_0"], **kw)
        check(f"1.1 inject_incident rejects {label}",
              r["applied"] is False, r.get("reason", ""))

    too_many = [f"N1_J1_{i}" for i in range(MAX_AFFECTED_LANES + 1)]
    r = inject_incident(s, "J1", too_many)
    check(f"1.1 rejects affected_lanes longer than {MAX_AFFECTED_LANES}",
          r["applied"] is False and "affect at most" in r.get("reason", ""),
          r.get("reason", ""))

    # de-dup: 3 copies of one id is length 1 after de-dup, and still gets the
    # unknown-lane / placement treatment normally.
    r = inject_incident(s, "J1", ["N1_J1_0", "N1_J1_0", "N1_J1_0"])
    check("1.1 de-dups affected_lanes before the cap / lane checks",
          r["applied"] is True and r["incident"]["affected_lanes"] == ["N1_J1_0"],
          str(r.get("incident", r)))


# ---------------------------------------------------------------------------
# STEP 1.6 — fail closed while no lane set has been published
# ---------------------------------------------------------------------------
def check_fail_closed_no_lane_set() -> None:
    print("\n-- STEP 1.6  fail closed when the live lane set is empty --")
    s = ControlState()  # nothing published yet
    for label, call in [
        ("set_lane_bias", lambda: set_lane_bias(s, "L0", 2.0, 60.0)),
        ("trigger_emergency", lambda: trigger_emergency(s, "L0")),
        ("inject_incident", lambda: inject_incident(s, "J1", ["L0"])),
    ]:
        r = call()
        check(f"1.6 {label} refuses before a lane set exists",
              r["applied"] is False and "lane set" in r.get("reason", ""),
              r.get("reason", ""))


# ---------------------------------------------------------------------------
# STEP 2 — server-side function allowlist
# ---------------------------------------------------------------------------
def check_dispatch_allowlist() -> None:
    print("\n-- STEP 2  control_api.dispatch() allowlist --")
    s = _state_with_lanes()
    expected = {
        "set_mode", "set_lane_bias", "get_stats", "trigger_emergency",
        "set_topology", "set_baseline_mode", "inject_incident",
        "force_phase", "clear_override",
    }
    check("2 CONTROL_FUNCTIONS is exactly the approved set",
          set(control_api.CONTROL_FUNCTIONS) == expected,
          str(sorted(control_api.CONTROL_FUNCTIONS)))
    check("2 CONTROL_FUNCTIONS and the dispatch table cannot drift "
          "(module-level assert)",
          set(control_api._DISPATCH_TABLE) == set(control_api.CONTROL_FUNCTIONS))

    for bad in ("os.system", "eval", "set_enable_safety_validator", "", "__import__"):
        r = dispatch(s, bad, {})
        check(f"2 dispatch refuses {bad!r} before argument binding",
              r["applied"] is False and "unknown control function" in r["reason"],
              r["reason"])
    r = dispatch(s, "set_topology", {"topology_id": "234"})
    check("2 dispatch routes an allowed name (set_topology '234')",
          r.get("applied") is True, str(r))
    r = dispatch(s, "set_mode", {"bogus_kwarg": 1})
    check("2 dispatch turns a bad-argument TypeError into a clean rejection",
          r["applied"] is False and "bad arguments" in r["reason"], r["reason"])


# ---------------------------------------------------------------------------
# STEP 3 — force_phase / clear_override
# ---------------------------------------------------------------------------
def check_force_phase_api() -> None:
    print("\n-- STEP 3  force_phase / clear_override validation + queueing --")
    s = ControlState()
    check("3 force_phase rejects an unknown junction",
          force_phase(s, "J9", 0)["applied"] is False)
    check("3 force_phase rejects a non-integer phase",
          force_phase(s, "J1", "north")["applied"] is False)
    check("3 force_phase rejects a phase outside [0, MAX_PHASES)",
          force_phase(s, "J1", 7)["applied"] is False)

    r = force_phase(s, "J2", 1)
    queued = s.pending.get_nowait()
    check("3 force_phase queues a deferred Command (not applied inline)",
          r["applied"] is True and queued.kind == "force_phase"
          and queued.args == {"junction_id": "J2", "phase": 1},
          f"{r}  ->  {queued}")

    r = clear_override(s, None)
    queued = s.pending.get_nowait()
    check("3 clear_override(None) queues a clear-all Command",
          r["applied"] is True and queued.kind == "clear_override"
          and queued.args == {"junction_id": None},
          f"{r}  ->  {queued}")
    check("3 clear_override rejects a bad junction id",
          clear_override(s, "nope")["applied"] is False)


def check_apply_forced_phases() -> None:
    print("\n-- STEP 3  _apply_forced_phases: mask-checked, phase_served_lanes --")
    from twin.digital_twin import CORRIDOR_JUNCTIONS

    r = sim_runner.SimRunner.__new__(sim_runner.SimRunner)
    # 3 junctions x MAX_PHASES(3): J1 slots {0,1} valid, J2 {0,1}, J3 {0}.
    masks = np.array([1, 1, 0,  1, 1, 0,  1, 0, 0], dtype=bool)

    class _Env:
        def action_masks(self):
            return masks

    r._env = _Env()
    r._served = {jid: {0: frozenset({f"L_{jid}_0"}), 1: frozenset({f"L_{jid}_1"})}
                 for jid in CORRIDOR_JUNCTIONS}
    del r._served["J3"][1]  # J3 only greens slot 0

    base = np.array([0, 0, 0])
    decisions = {jid: {"junction_id": jid, "phase_selected": 0,
                       "score_breakdown": {}, "alternative_scores": {},
                       "reason": "rl_policy"} for jid in CORRIDOR_JUNCTIONS}

    # Valid pin: J1 -> slot 1 (masked in, in phase_served_lanes).
    r._forced_phase = {"J1": 1}
    out = r._apply_forced_phases(base.copy(), decisions, masks)
    d1 = decisions["J1"]
    check("3 a valid force_phase rewrites that junction's action + entry",
          out[0] == 1 and d1["reason"] == "voice_command"
          and d1["phase_selected"] == 1
          and d1["action_taken"] == "force_phase(J1, 1)",
          f"action={list(out)} entry={d1}")

    # Invalid pin: J3 -> slot 1 is masked out AND not in served map -> dropped.
    decisions = {jid: {"junction_id": jid, "phase_selected": 0,
                       "score_breakdown": {}, "alternative_scores": {},
                       "reason": "rl_policy"} for jid in CORRIDOR_JUNCTIONS}
    r._forced_phase = {"J3": 1}
    out = r._apply_forced_phases(np.array([0, 0, 0]), decisions, masks)
    check("3 an invalid force_phase is dropped, action + entry untouched",
          out[2] == 0 and decisions["J3"]["reason"] == "rl_policy"
          and r._forced_phase == {},
          f"action={list(out)} forced_phase={r._forced_phase}")


# ---------------------------------------------------------------------------
# STEP 1.5 — set_topology cooldown + no-op (sim-thread side)
# ---------------------------------------------------------------------------
def check_set_topology_guard() -> None:
    print("\n-- STEP 1.5  set_topology cooldown + no-op --")

    r = sim_runner.SimRunner.__new__(sim_runner.SimRunner)

    class _Env:
        _sim_time = 0.0

    r._env = _Env()
    r._lane_counts = (4, 3, 2)
    r._pending_lane_counts = None
    r._forced_phase = {}
    r._last_topology_change = time.time()  # just changed -> inside cooldown

    r._apply_command(Command("set_topology", {"lane_counts": [3, 3, 3]}))
    check("1.5 a second change inside the cooldown window is ignored",
          r._pending_lane_counts is None, f"pending={r._pending_lane_counts}")

    r._apply_command(Command("set_topology", {"lane_counts": [4, 3, 2]}))
    check("1.5 a request equal to the current topology is a no-op",
          r._pending_lane_counts is None, f"pending={r._pending_lane_counts}")

    r._last_topology_change = time.time() - (sim_runner._TOPOLOGY_COOLDOWN_S + 1.0)
    r._apply_command(Command("set_topology", {"lane_counts": [3, 3, 3]}))
    check("1.5 a distinct change after the cooldown expires goes through",
          r._pending_lane_counts == (3, 3, 3), f"pending={r._pending_lane_counts}")


# ---------------------------------------------------------------------------
# STEP 1.6 — active-incident cap (sim-thread side)
# ---------------------------------------------------------------------------
def check_incident_cap() -> None:
    print("\n-- STEP 1.6  active-incident cap --")

    class _Reg:
        def __init__(self, n_active):
            self._n = n_active
            self.reports = 0

        def get_active(self, _t):
            return list(range(self._n))

        def report(self, *a, **k):
            self.reports += 1

    r = sim_runner.SimRunner.__new__(sim_runner.SimRunner)

    class _Twin:
        pass

    class _Env:
        _sim_time = 100.0

    r._env = _Env()
    r._env.twin = _Twin()

    r._env.twin.incidents = _Reg(sim_runner._MAX_ACTIVE_INCIDENTS)
    args = dict(incident_type="lane_blocked", junction_id="J1", lane_id="L0",
                severity="high", affected_lanes=["L0"], estimated_duration_s=600.0)
    r._apply_command(Command("inject_incident", dict(args)))
    check(f"1.6 inject_incident is dropped at the "
          f"{sim_runner._MAX_ACTIVE_INCIDENTS}-incident cap",
          r._env.twin.incidents.reports == 0)

    r._env.twin.incidents = _Reg(sim_runner._MAX_ACTIVE_INCIDENTS - 1)
    r._apply_command(Command("inject_incident", dict(args)))
    check("1.6 inject_incident below the cap still reports",
          r._env.twin.incidents.reports == 1)


# ---------------------------------------------------------------------------
# STEP 1.2 — sim-thread inner try/except
# ---------------------------------------------------------------------------
def check_inner_guard() -> None:
    print("\n-- STEP 1.2  sim-thread per-iteration try/except --")
    orig = sim_runner._MAX_CONSECUTIVE_FAILURES
    sim_runner._MAX_CONSECUTIVE_FAILURES = 3
    try:
        # (a) a persistently broken iteration stops after the threshold and
        #     surfaces as a FATAL, rather than spinning forever.
        r = sim_runner.SimRunner.__new__(sim_runner.SimRunner)
        r._stop = threading.Event()
        r._started = threading.Event()
        r._env = None
        r._error = None
        r._load_model = lambda: None
        r._load_shadow_model = lambda: None
        r._build_env = lambda: None
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("wedged iteration")

        r._run_iteration = boom
        r._run()
        check("1.2 a persistently broken iteration re-raises after "
              f"{sim_runner._MAX_CONSECUTIVE_FAILURES} tries (no infinite spin)",
              len(calls) == sim_runner._MAX_CONSECUTIVE_FAILURES
              and r._error is not None and "RuntimeError" in r._error,
              f"iterations={len(calls)} error={(r._error or '').splitlines()[-1:]}")

        # (b) transient failures are absorbed — the thread keeps running and
        #     the failure counter resets on the first success.
        r = sim_runner.SimRunner.__new__(sim_runner.SimRunner)
        r._stop = threading.Event()
        r._started = threading.Event()
        r._env = None
        r._error = None
        r._load_model = lambda: None
        r._load_shadow_model = lambda: None
        r._build_env = lambda: None
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) <= 2:
                raise RuntimeError("transient")
            r._stop.set()  # third pass: succeed, then ask the loop to exit

        r._run_iteration = flaky
        r._run()
        check("1.2 two transient failures then a success -> thread survives, "
              "no FATAL",
              len(calls) == 3 and r._error is None,
              f"iterations={len(calls)} error={r._error}")
    finally:
        sim_runner._MAX_CONSECUTIVE_FAILURES = orig


# ---------------------------------------------------------------------------
# STEP 1.3 — --host loopback guard
# ---------------------------------------------------------------------------
def check_host_guard() -> None:
    print("\n-- STEP 1.3  --host loopback guard --")
    for host in sorted(LOOPBACK_HOSTS):
        check(f"1.3 loopback host {host!r} is allowed without --allow-lan",
              _host_rejection(host, False) is None)
    for host in ("0.0.0.0", "192.168.1.50", "::"):
        rej = _host_rejection(host, False)
        check(f"1.3 non-loopback {host!r} is refused without --allow-lan",
              rej is not None and "UNAUTHENTICATED" in rej, (rej or "")[:70])
        check(f"1.3 non-loopback {host!r} is permitted WITH --allow-lan",
              _host_rejection(host, True) is None)


# ---------------------------------------------------------------------------
# STEP 1.4 / 1.7 / 2 — live app: CORS, /health, fail-closed over HTTP
# ---------------------------------------------------------------------------
def check_live_app() -> None:
    print("\n-- STEP 1.4 / 1.7  CORS allowlist + /health disclosure (live app, "
          "no lifespan / no SUMO) --")
    app = create_app(checkpoint=None, shadow_checkpoint=None)
    client = TestClient(app)  # NOT `with` -> lifespan does not run -> no sim thread

    good = ALLOWED_ORIGINS[0]
    r = client.get("/health", headers={"Origin": good})
    check("1.4 an allowlisted Origin is echoed in "
          "access-control-allow-origin",
          r.headers.get("access-control-allow-origin") == good,
          r.headers.get("access-control-allow-origin"))

    r = client.get("/health", headers={"Origin": "http://evil.example"})
    acao = r.headers.get("access-control-allow-origin")
    check("1.4 a non-allowlisted Origin is NOT granted CORS access",
          acao != "http://evil.example",
          f"access-control-allow-origin={acao!r}")

    r = client.options(
        "/control/set_topology",
        headers={"Origin": good,
                 "Access-Control-Request-Method": "POST"},
    )
    check("1.4 preflight from the dashboard origin succeeds",
          r.status_code in (200, 204)
          and r.headers.get("access-control-allow-origin") == good,
          f"status={r.status_code}")
    r = client.options(
        "/control/set_topology",
        headers={"Origin": "http://evil.example",
                 "Access-Control-Request-Method": "POST"},
    )
    check("1.4 preflight from a foreign origin is not granted",
          r.headers.get("access-control-allow-origin") != "http://evil.example",
          f"acao={r.headers.get('access-control-allow-origin')!r}")

    body = client.get("/health").json()
    check("1.7 /health exposes a boolean sim_error + an exception class only",
          body["sim_error"] is False and "sim_error_class" in body
          and body["sim_error_class"] is None
          and not isinstance(body["sim_error"], str),
          str(body))

    # Fault injection: stuff a fake traceback into runner.error and confirm
    # the wire still only carries the class name, never the body.
    runner = app.state.runner
    runner._error = ("Traceback (most recent call last):\n"
                     '  File "x.py", line 1, in <module>\n'
                     "FatalTraCIError: connection closed by SUMO")
    body = client.get("/health").json()
    check("1.7 with an error present, /health carries the class, not the "
          "traceback",
          body["sim_error"] is True
          and body["sim_error_class"] == "FatalTraCIError"
          and "Traceback" not in str(body)
          and "x.py" not in str(body),
          str(body))

    # Fail-closed, live over HTTP: no lane set has been published (no sim
    # thread), so a lane-referencing control call must refuse.
    r = client.post("/control/set_lane_bias",
                    json={"lane_id": "L0", "weight": 2.0, "duration_s": 60.0})
    j = r.json()
    check("1.6 (live) set_lane_bias over HTTP fails closed with no lane set",
          j["applied"] is False and "lane set" in j["reason"], j["reason"])


# ---------------------------------------------------------------------------
# STEP 1.8 — .gitignore
# ---------------------------------------------------------------------------
def check_gitignore() -> None:
    print("\n-- STEP 1.8  .claude/settings.local.json in .gitignore --")
    txt = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    check("1.8 repo .gitignore lists .claude/settings.local.json",
          ".claude/settings.local.json" in txt.splitlines(),
          "present" if ".claude/settings.local.json" in txt else "MISSING")


def main() -> None:
    print(RULE)
    print("BACKEND SECURITY HARDENING — targeted checks (offline, no SUMO)")
    print(RULE)

    check_lane_bias_bounds()
    check_inject_incident_bounds()
    check_fail_closed_no_lane_set()
    check_dispatch_allowlist()
    check_force_phase_api()
    check_apply_forced_phases()
    check_set_topology_guard()
    check_incident_cap()
    check_inner_guard()
    check_host_guard()
    check_live_app()
    check_gitignore()

    print(RULE)
    print(f"  {_passed} passed, {_failed} failed")
    print(RULE)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
