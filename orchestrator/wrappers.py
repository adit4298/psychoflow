"""The six agent wrappers — THIN VIEWS, no new logic anywhere in this file.

Each wrapper declares a name, a role, the module it wraps, its input and its
output, and a `tick(ctx) -> tuple[AgentEntry, ...]`. Every one of them
delegates: it reports what the module it wraps ALREADY produced, which the sim
thread already had in hand and passed in on the `AgentContext`.

=======================================================================
THE REVIEWABLE RULE (constraint 1), and it is greppable
=======================================================================
A wrapper MAY filter, count, max, sort and format over `ctx`.
A wrapper MAY NOT introduce a threshold, weight, score, or comparison
against a constant this package defines.

Practically: **no numeric literal in this file** outside 0/1 and format
precision — every selection/format number is a named import from
`orchestrator.types`. So `Detection` may count lanes whose `starvation_flag`
is set (a flag `perception/lane_sensor.py` computed against ITS OWN
threshold); it may NOT write `wait > 90.0`. If a wrapper needs a value that
is not on `ctx`, that value gets ADDED to `AgentContext` by the caller —
which already has it — never derived here.

The self-test (W7) AST-scans this file for numeric literals and for imports
of `apply` / `dispatch` / `validate` / `forecast` / `predict_incident_impact`
/ `ControlState`. That scan is the tripwire for "a wrapper started computing
something".
"""

from __future__ import annotations

from typing import Mapping

from agents.incident_priority import IncidentPriorityAgent
from orchestrator.types import (
    AGENT_CONTROL,
    AGENT_DETECTION,
    AGENT_INCIDENT_PRIORITY,
    AGENT_PREDICTION,
    AGENT_SUPERVISOR,
    AGENT_VISION,
    CONFIDENCE_DECIMALS,
    DELTA_DECIMALS,
    KIND_ACTION,
    KIND_ARBITRATION,
    KIND_FORECAST,
    KIND_IDLE,
    KIND_OBSERVATION,
    KIND_VETO,
    LaneRow,
    TOP_N_REPORTED,
    WAIT_DECIMALS,
    AgentContext,
    AgentEntry,
)


class _Wrapper:
    """Base: identity + the one-line entry factory. No behaviour."""

    name: str = ""
    role: str = ""
    wraps: str = ""
    reads: str = ""
    emits: str = ""

    def _entry(self, ctx: AgentContext, kind: str, said: str,
               detail: Mapping | None = None) -> AgentEntry:
        return AgentEntry(agent=self.name, role=self.role, wraps=self.wraps,
                          kind=kind, said=said, at=ctx.sim_time,
                          step=ctx.step, detail=dict(detail or {}))

    def tick(self, ctx: AgentContext) -> tuple[AgentEntry, ...]:
        raise NotImplementedError

    def reset(self) -> None:
        """Per-episode state, if any. Default: none."""


def _lane_rows(snapshot: Mapping):
    """One LaneRow per sensed lane. Pure selection — nothing is derived."""
    for jid, block in (snapshot.get("junctions") or {}).items():
        for lane_id, reading in (block.get("lanes") or {}).items():
            if isinstance(reading, Mapping):
                yield LaneRow(jid, lane_id, reading)


class DetectionAgent(_Wrapper):
    name = AGENT_DETECTION
    wraps = "perception/lane_sensor.py"
    role = ("reports the §7.1 per-lane counts and waits the sensor already "
            "read this step — it measures nothing itself")
    reads = "ctx.pre_snapshot.junctions[*].lanes"
    emits = "one observation line per round"

    def tick(self, ctx: AgentContext) -> tuple[AgentEntry, ...]:
        rows = list(_lane_rows(ctx.pre_snapshot))
        if not rows:
            return (self._entry(ctx, KIND_IDLE,
                                "No sensed lanes in this snapshot."),)
        # `starvation_flag` is the sensor's OWN verdict against its OWN
        # threshold. Counting flags is selection; re-deriving them would be
        # new logic and is exactly what the rule above forbids.
        starved = [r.lane_id for r in rows if r.reading.get("starvation_flag")]
        worst = max(rows, key=lambda r: (
            r.reading.get("wait_time_max_single_vehicle", 0), r.lane_id))
        worst_j, worst_lane = worst.junction_id, worst.lane_id
        wait = worst.reading.get("wait_time_max_single_vehicle", 0)
        said = (f"{len(rows)} lanes sensed. Worst: {worst_lane} "
                f"({worst.reading.get('approach', '?')}) at {worst_j} — "
                f"{wait:.{WAIT_DECIMALS}f}s, "
                f"{worst.reading.get('halted_count', 0)} halted. "
                f"starvation_flag set on {len(starved)} lane(s).")
        return (self._entry(ctx, KIND_OBSERVATION, said, {
            "lanes_sensed": len(rows), "worst_lane": worst_lane,
            "worst_junction": worst_j, "worst_wait_s": wait,
            "starved_lanes": len(starved),
        }),)


class VisionAgent(_Wrapper):
    name = AGENT_VISION
    wraps = "perception/vision_mock.py"
    # §7.2's own docstring is emphatic that this detects nothing. The demo
    # panel must not launder that, so the disclaimer rides every entry.
    role = ("re-emits §7.1 counts through a §7.2 camera-shaped envelope — "
            "a MOCK; it runs no detection model and detects nothing")
    reads = "ctx.pre_snapshot.junctions[*].vision"
    emits = "one observation line per round"

    def tick(self, ctx: AgentContext) -> tuple[AgentEntry, ...]:
        seen = [obs
                for block in (ctx.pre_snapshot.get("junctions") or {}).values()
                for obs in (block.get("vision") or {}).values()
                if isinstance(obs, Mapping)]
        if not seen:
            return (self._entry(ctx, KIND_IDLE,
                                "No §7.2 vision block on this snapshot."),)
        confidences = [o["confidence"] for o in seen if "confidence" in o]
        ambulances = [o.get("lane_id") for o in seen
                      if (o.get("type_composition") or {}).get("ambulance")]
        sources = sorted({str(o.get("source", "?")) for o in seen})
        band = (f"{min(confidences):.{CONFIDENCE_DECIMALS}f}-"
                f"{max(confidences):.{CONFIDENCE_DECIMALS}f}"
                if confidences else "n/a")
        said = (f"Camera envelope on {len(seen)} lanes, source="
                f"{','.join(sources)}, confidence {band}. "
                f"{len(ambulances)} lane(s) report an ambulance. "
                f"MOCK — no detection model ran.")
        return (self._entry(ctx, KIND_OBSERVATION, said, {
            "lanes_observed": len(seen), "source": ",".join(sources),
            "ambulance_lanes": len(ambulances), "is_mock": True,
        }),)


class PredictionAgent(_Wrapper):
    name = AGENT_PREDICTION
    wraps = "prediction/spillover.py + prediction/incident_impact.py"
    # Says post-step explicitly: _predictions() runs on the POST-step snapshot
    # and _spillover_view.forecast() is STATEFUL and must be called exactly
    # once per step. Calling it again from here would double-advance the
    # read-side predictor and corrupt the next frame — the sharpest example
    # of why a wrapper must never compute.
    role = ("reports the §8.1/§8.2 forecast the backend already computed on "
            "the post-step snapshot — the one the dashboard draws")
    reads = "ctx.predictions"
    emits = "one forecast line per round"

    def tick(self, ctx: AgentContext) -> tuple[AgentEntry, ...]:
        spill = list((ctx.predictions or {}).get("spillover") or [])
        impacts = list((ctx.predictions or {}).get("incident_impact") or [])
        if not spill and not impacts:
            return (self._entry(ctx, KIND_IDLE,
                                "No material forecast this step — no spillover "
                                "above the §13.2 threshold, no active incident."),)
        parts = []
        for f in spill[:TOP_N_REPORTED]:
            parts.append(f"§8.1 {f.get('from_junction')}->{f.get('to_junction')} "
                         f"{f.get('predicted_queue_delta', 0):+.{DELTA_DECIMALS}f} veh "
                         f"over {f.get('horizon_s', 0):.0f}s "
                         f"(confidence {f.get('confidence', 0):.{CONFIDENCE_DECIMALS}f})")
        for i in impacts[:TOP_N_REPORTED]:
            parts.append(f"§8.2 {i.get('incident_id')} "
                         f"+{i.get('estimated_delay_increase_s', 0):.{WAIT_DECIMALS}f}s "
                         f"across {','.join(i.get('estimated_affected_junctions') or [])}")
        return (self._entry(ctx, KIND_FORECAST, "; ".join(parts) + ".", {
            "spillover_pairs": len(spill), "active_incidents": len(impacts),
        }),)


class IncidentPriorityWrapper(_Wrapper):
    name = AGENT_INCIDENT_PRIORITY
    wraps = "agents/incident_priority.py"
    # ADVISORY in Part 4b: tick() only. apply() would dispatch
    # trigger_emergency, mutate sim_runner._forced and change what §10 does —
    # a direct breach of "the control path stays unchanged". With no apply()
    # there is nothing to confirm(), so directives are never promoted to
    # STATUS_ACTIVE and the same proposal is re-reported while the state
    # holds. That repetition is TRUTHFUL ("the arbitration still ranks this
    # first"); suppressing it would be new logic in a wrapper.
    role = ("ranks incidents under emergency > accident > major_congestion > "
            "fairness. ADVISORY in this build — nothing it proposes is "
            "dispatched")
    reads = "ctx.pre_snapshot, ctx.predictions, ctx.forced_emergency_lanes"
    emits = "one arbitration line per round"

    def __init__(self) -> None:
        self._agent = IncidentPriorityAgent()

    def reset(self) -> None:
        self._agent = IncidentPriorityAgent()

    def tick(self, ctx: AgentContext) -> tuple[AgentEntry, ...]:
        result = self._agent.tick(
            ctx.pre_snapshot,
            sim_time=ctx.sim_time,
            spillover=(ctx.predictions or {}).get("spillover"),
            incident_impacts=(ctx.predictions or {}).get("incident_impact"),
            # Track A HAS landed (Part 5c). The adapter is still the
            # caller's job per §A1 — backend/vision_alerts.py builds these and
            # the sim thread puts them on the context. They carry
            # `emergency_vehicle_flag` as a low-confidence ADVISORY and never
            # `emergency` (§9.2), so the mock's echo of the SAME ambulance
            # cannot be double-counted as an emergency through two channels.
            vision_events=(ctx.vision_events or None),
            forced_emergency_lanes=ctx.forced_emergency_lanes,
        )
        if not result.events:
            return (self._entry(ctx, KIND_IDLE,
                                "Nothing to arbitrate — no emergency, "
                                "incident, congestion or fairness event."),)
        top = result.events[0]
        proposed = (f" Would propose {result.directives[0].function}"
                    f"({result.directives[0].args.get('lane_id')})."
                    if result.directives else " No directive this step.")
        said = (f"{len(result.events)} event(s) ranked; top = {top.event_id} "
                f"at {top.junction_id} ({top.severity}).{proposed} "
                f"ADVISORY — nothing was dispatched.")
        return (self._entry(ctx, KIND_ARBITRATION, said, {
            "events": len(result.events), "top_event": top.event_id,
            "top_class": top.event_class, "top_severity": top.severity,
            "directives": len(result.directives),
            "preempted": len(result.preempted),
            "suppressed": len(result.suppressed),
            "dispatched": False,
        }),)


class ControlAgent(_Wrapper):
    name = AGENT_CONTROL
    wraps = "agents/rule_based.py / the deployed PPO policy"
    role = ("reports the action _pick_action() ALREADY chose and what §10 "
            "actually executed — it does not choose")
    reads = "ctx.decisions, ctx.info['executed_action'], ctx.mode"
    emits = "one action line per round"

    def tick(self, ctx: AgentContext) -> tuple[AgentEntry, ...]:
        decisions = ctx.decisions or {}
        if not decisions:
            return (self._entry(ctx, KIND_IDLE,
                                "No decision row this step."),)
        executed = list(ctx.info.get("executed_action") or [])
        proposed = " ".join(f"{jid}={d.get('phase_selected')}"
                            for jid, d in decisions.items())
        ran = " ".join(f"{jid}={int(p)}"
                       for jid, p in zip(decisions, executed))
        reasons = sorted({str(d.get("reason")) for d in decisions.values()})
        # Interpolates the two side by side and lets the Supervisor's own line
        # name what moved. No comparison here.
        said = (f"[{ctx.mode}] {'/'.join(reasons)} proposed {proposed}; "
                f"executed {ran or 'n/a'}.")
        return (self._entry(ctx, KIND_ACTION, said, {
            "mode": ctx.mode, "reasons": "/".join(reasons),
            "junctions": len(decisions),
        }),)


class SupervisorAgent(_Wrapper):
    name = AGENT_SUPERVISOR
    wraps = "safety/validator.py"
    # The sentence says "§10 vetoed", NEVER "I vetoed".
    role = ("reports the §10 overrides that ALREADY fired inside env.step() "
            "— a RECORD of a veto, not a veto power")
    reads = "ctx.info['safety_overrides']"
    emits = "one veto line per override, or one idle line"

    def tick(self, ctx: AgentContext) -> tuple[AgentEntry, ...]:
        overrides = list(ctx.info.get("safety_overrides") or [])
        if not overrides:
            return (self._entry(
                ctx, KIND_IDLE,
                "§10 validated the corridor and raised no override this step."),)
        rows = []
        for record in overrides:
            # Reported VERBATIM from info. The Supervisor never re-derives a
            # judgement from the snapshot — that is what makes this a record
            # rather than an opinion, and the self-test (W4c) pins it by
            # feeding a lane_id that exists nowhere in the snapshot.
            said = (f"§10 VETO at {record.get('junction_id')}: "
                    f"{record.get('rule')} — {record.get('lane_id')} waited "
                    f"{record.get('wait_s', 0):.{WAIT_DECIMALS}f}s; phase "
                    f"{record.get('from_slot')} -> {record.get('to_slot')} "
                    f"({record.get('outcome')}).")
            rows.append(self._entry(ctx, KIND_VETO, said, {
                "junction_id": record.get("junction_id"),
                "rule": record.get("rule"), "lane_id": record.get("lane_id"),
                "wait_s": record.get("wait_s"),
                "from_slot": record.get("from_slot"),
                "to_slot": record.get("to_slot"),
                "outcome": record.get("outcome"),
            }))
        return tuple(rows)


def default_wrappers() -> tuple[_Wrapper, ...]:
    """The roster, in panel order. Must cover exactly AGENT_NAMES (W1)."""
    return (DetectionAgent(), VisionAgent(), PredictionAgent(),
            IncidentPriorityWrapper(), ControlAgent(), SupervisorAgent())
