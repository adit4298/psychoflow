"""Weather awareness (§7.4) — a real behavioral hook, not a label.

This is the one perception module that WRITES to SUMO as well as reading
from it. §7.4 requires that on a weather change, vType parameters
actually shift (increase `tau` follow-time gap, reduce `maxSpeed`,
increase `sigma` driver imperfection) so vehicle behavior genuinely
changes — a state string that no downstream physics reads would be
decoration, and the reward function (§9.4) would never see the
difference.

Baselines are snapshotted once at attach() and every subsequent change is
applied relative to those baselines, never to the current (already
modified) values — otherwise clear -> rain -> clear -> rain would
compound and drift the network into an unintended regime over a long
episode.
"""

from __future__ import annotations

import traci

from perception.lane_sensor import VEHICLE_TYPES

WEATHER_STATES = ("clear", "rain", "heavy_rain")

# Multipliers/offsets applied against the attach()-time baseline vType values.
WEATHER_PROFILES: dict[str, dict[str, float]] = {
    "clear": {"tau_mult": 1.0, "max_speed_mult": 1.0, "sigma_add": 0.0},
    "rain": {"tau_mult": 1.4, "max_speed_mult": 0.85, "sigma_add": 0.10},
    "heavy_rain": {"tau_mult": 1.9, "max_speed_mult": 0.70, "sigma_add": 0.20},
}


class WeatherModel:
    def __init__(self, vehicle_types: tuple[str, ...] = VEHICLE_TYPES):
        self.vehicle_types = vehicle_types
        self._state = "clear"
        self._changed_at_sim_time = 0.0
        self._baselines: dict[str, dict[str, float]] = {}

    def attach(self) -> None:
        """Snapshot baseline vType params. Call once, after traci.start()."""
        self._baselines = {
            vtype: {
                "tau": traci.vehicletype.getTau(vtype),
                "max_speed": traci.vehicletype.getMaxSpeed(vtype),
                "sigma": traci.vehicletype.getImperfection(vtype),
            }
            for vtype in self.vehicle_types
        }

    def set_state(self, state: str, sim_time: float) -> None:
        """Change weather and push the corresponding vType changes into SUMO."""
        if state not in WEATHER_STATES:
            raise ValueError(f"state={state!r} invalid — must be one of {WEATHER_STATES}")
        if not self._baselines:
            raise RuntimeError("WeatherModel.attach() must be called before set_state()")

        profile = WEATHER_PROFILES[state]
        for vtype, base in self._baselines.items():
            traci.vehicletype.setTau(vtype, base["tau"] * profile["tau_mult"])
            traci.vehicletype.setMaxSpeed(vtype, base["max_speed"] * profile["max_speed_mult"])
            # sigma is a [0, 1] driver-imperfection factor — clamp so a
            # profile offset can't push it out of range.
            sigma = min(1.0, max(0.0, base["sigma"] + profile["sigma_add"]))
            traci.vehicletype.setImperfection(vtype, sigma)

        self._state = state
        self._changed_at_sim_time = sim_time

    def get_state(self) -> dict:
        """The §7.4 contract."""
        return {"state": self._state, "changed_at_sim_time": self._changed_at_sim_time}

    def current_vtype_params(self) -> dict[str, dict[str, float]]:
        """Live vType values read back from SUMO — evidence that set_state()
        actually landed, rather than only updating this object's label."""
        return {
            vtype: {
                "tau": round(traci.vehicletype.getTau(vtype), 3),
                "max_speed": round(traci.vehicletype.getMaxSpeed(vtype), 3),
                "sigma": round(traci.vehicletype.getImperfection(vtype), 3),
            }
            for vtype in self.vehicle_types
        }

    def reset(self, sim_time: float = 0.0) -> None:
        """Restore baselines — called on env.reset() (§9.2)."""
        if self._baselines:
            self.set_state("clear", sim_time)
        else:
            self._state = "clear"
            self._changed_at_sim_time = sim_time
