"""Structured incident intake (§7.3).

A stateful registry, not a TraCI reader — nothing in the simulator holds
"an incident was reported." Incidents are pushed in from outside
(test harness now; scenario generator, backend control API, and the RL
scenario randomizer later) and the digital twin pulls the currently
active set out each step.

The schema is deliberately the same shape a human hotline operator would
log: structured fields, not free text (§7.3).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

INCIDENT_TYPES = ("lane_blocked", "accident", "roadworks")
SEVERITIES = ("low", "medium", "high")


@dataclass
class Incident:
    incident_id: str
    type: str
    location: dict[str, str]
    severity: str
    affected_lanes: list[str]
    reported_at_sim_time: float
    estimated_duration_s: float

    def to_dict(self) -> dict:
        return asdict(self)

    def is_active_at(self, sim_time: float) -> bool:
        return (
            self.reported_at_sim_time
            <= sim_time
            < self.reported_at_sim_time + self.estimated_duration_s
        )


@dataclass
class IncidentIntake:
    """Registry of reported incidents, with expiry by estimated duration."""

    _incidents: list[Incident] = field(default_factory=list)
    _next_id: int = 1

    def report(
        self,
        incident_type: str,
        junction_id: str,
        lane_id: str,
        severity: str,
        affected_lanes: list[str],
        reported_at_sim_time: float,
        estimated_duration_s: float,
    ) -> Incident:
        if incident_type not in INCIDENT_TYPES:
            raise ValueError(f"type={incident_type!r} invalid — must be one of {INCIDENT_TYPES}")
        if severity not in SEVERITIES:
            raise ValueError(f"severity={severity!r} invalid — must be one of {SEVERITIES}")

        incident = Incident(
            incident_id=f"inc_{self._next_id:04d}",
            type=incident_type,
            location={"junction_id": junction_id, "lane_id": lane_id},
            severity=severity,
            affected_lanes=list(affected_lanes),
            reported_at_sim_time=reported_at_sim_time,
            estimated_duration_s=estimated_duration_s,
        )
        self._incidents.append(incident)
        self._next_id += 1
        return incident

    def get_active(self, sim_time: float) -> list[Incident]:
        return [inc for inc in self._incidents if inc.is_active_at(sim_time)]

    def get_all(self) -> list[Incident]:
        return list(self._incidents)

    def reset(self) -> None:
        """Called on env.reset() (§9.2) — a new episode starts incident-free."""
        self._incidents.clear()
        self._next_id = 1
