"""V2X-shaped connected-vehicle data (§7.5).

Confirmed lightweight approach (§0): reformat TraCI vehicle state and
apply a synthetic imperfection model. No Veins/OMNeT++, no network-layer
simulation — integrating a second independently-versioned simulator is a
toolchain risk with no visible payoff on screen.

On the `dropped` field: §7.5 specifies dropping ~2-5% of messages and
says to drop the message entirely rather than emit malformed data. So a
dropped message never appears in the returned batch at all, and every
message that IS emitted carries dropped=False. The field is retained for
shape fidelity with the §7.5 contract; drop counts are tracked on the
emitter for observability.
"""

from __future__ import annotations

import math
import random

import traci

DEFAULT_DROP_RATE = 0.03
DEFAULT_MAX_DELAY_MS = 300
DEFAULT_POSITION_JITTER_M = (0.5, 1.5)


class V2XEmitter:
    def __init__(
        self,
        drop_rate: float = DEFAULT_DROP_RATE,
        max_delay_ms: int = DEFAULT_MAX_DELAY_MS,
        position_jitter_m: tuple[float, float] = DEFAULT_POSITION_JITTER_M,
        seed: int | None = None,
    ):
        self.drop_rate = drop_rate
        self.max_delay_ms = max_delay_ms
        self.position_jitter_m = position_jitter_m
        self._rng = random.Random(seed)
        self.emitted_count = 0
        self.dropped_count = 0

    def collect(self, sim_time: float) -> list[dict]:
        """One batch of V2X messages for the current step, per §7.5."""
        messages = []
        for vehicle_id in traci.vehicle.getIDList():
            if self._rng.random() < self.drop_rate:
                self.dropped_count += 1
                continue

            x, y = traci.vehicle.getPosition(vehicle_id)
            # Jitter as a random-direction offset of random magnitude, so
            # error is isotropic rather than biased along an axis.
            magnitude = self._rng.uniform(*self.position_jitter_m)
            angle = self._rng.uniform(0.0, 2.0 * math.pi)

            messages.append(
                {
                    "vehicle_id": vehicle_id,
                    "position": {
                        "x": round(x + magnitude * math.cos(angle), 2),
                        "y": round(y + magnitude * math.sin(angle), 2),
                    },
                    "speed": round(traci.vehicle.getSpeed(vehicle_id), 2),
                    "heading": round(traci.vehicle.getAngle(vehicle_id), 2),
                    "timestamp": round(sim_time, 2),
                    "delay_ms": self._rng.randint(0, self.max_delay_ms),
                    "dropped": False,
                }
            )
            self.emitted_count += 1

        return messages

    def stats(self) -> dict:
        total = self.emitted_count + self.dropped_count
        return {
            "emitted": self.emitted_count,
            "dropped": self.dropped_count,
            "drop_rate_observed": round(self.dropped_count / total, 4) if total else 0.0,
        }
