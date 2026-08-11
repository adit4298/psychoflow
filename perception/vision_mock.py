"""Simulated CCTV input (§7.2) — a mock, deliberately, per §0.

This does NOT run a detection model, and is not a placeholder for one.
A real detector would add training data, inference latency inside the
per-step loop, and an integration surface, in exchange for zero visible
difference on a judge's screen (§0). What it would buy architecturally
is already bought here: this module re-emits lane_sensor's (§7.1) exact
ground truth through a camera-pipeline-shaped envelope, which is what
proves the digital twin and everything downstream consume a *shape* and
cannot tell whether that shape came from an induction loop or a camera
(§4).

Counts are passed through unmodified — no fabricated detections, no
perturbed numbers. The only addition is `confidence`, standing in for
the fact that real vision carries detection uncertainty, plus a `source`
tag so a consumer can tell the two feeds apart.
"""

from __future__ import annotations

import random

from perception.lane_sensor import LaneReading

CONFIDENCE_RANGE = (0.85, 0.98)
SOURCE_TAG = "vision_mock"


class VisionMock:
    def __init__(self, confidence_range: tuple[float, float] = CONFIDENCE_RANGE, seed: int | None = None):
        self.confidence_range = confidence_range
        # Own RNG instance so vision noise is reproducible under a seed
        # independently of the V2X noise stream (§7.5).
        self._rng = random.Random(seed)

    def observe(self, reading: LaneReading) -> dict:
        """Re-emit one §7.1 reading as a §7.2 vision observation."""
        observation = reading.to_dict()
        observation["confidence"] = round(self._rng.uniform(*self.confidence_range), 3)
        observation["source"] = SOURCE_TAG
        return observation

    def observe_all(self, readings: dict[str, LaneReading]) -> dict[str, dict]:
        return {lane_id: self.observe(reading) for lane_id, reading in readings.items()}
