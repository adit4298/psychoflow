"""Which vision feed the system runs on (§7.2 - `mock` or `detector`).

    get_vision_source()                      -> VisionMock   (the default)
    get_vision_source("mock", seed=7)        -> VisionMock
    get_vision_source("detector", source=..) -> VisionDetector

Why a factory rather than a swap
--------------------------------
§2 originally locked vision to a simulated mock, and that lock was
reopened on 2026-09-03 for a real YOLO detector - but reopened as an
ADDITION. `perception/vision_mock.py` stays, remains the default, and
remains the fallback: it re-emits §7.1 ground truth through a
camera-shaped envelope, which is what makes the rest of the system
provably agnostic to where a lane reading came from (§4).

The two sources are NOT equivalent, and the factory does not pretend they
are:

- `mock` sees the SUMO corridor and reports exact per-lane truth.
- `detector` sees a *video file or camera*. It cannot see SUMO, cannot
  measure accumulated waiting time, has no COCO class for `auto` or
  `ambulance`, and knows approaches rather than SUMO lane ids.

Both expose `observe()` / `observe_all()` and both emit the same key set,
so a consumer written against one runs against the other. What differs is
which of those keys carry a measurement - the detector's observations say
so themselves, via `wait_times_measured`, `lane_fanout` and
`emergency_flag_is_experimental`. A consumer that ignores those flags will
read a structural zero as an observed zero.

Importing this module does NOT import ultralytics or torch: the detector
class is imported inside the branch that builds one, so the default path
stays as cheap as it was before the detector existed.
"""

from __future__ import annotations

from typing import Any

MODE_MOCK = "mock"
MODE_DETECTOR = "detector"

VISION_MODES = (MODE_MOCK, MODE_DETECTOR)
DEFAULT_MODE = MODE_MOCK


def get_vision_source(mode: str = DEFAULT_MODE, **kwargs: Any):
    """Build the vision source for `mode`.

    Args:
        mode: `"mock"` (default) or `"detector"`.
        **kwargs: passed through.
            mock     - `confidence_range`, `seed`
            detector - `source` (video path or camera index, required),
                       `config`, `weights`, `junction_id`, `frame_size`,
                       `lazy`, `track`

    Raises:
        ValueError: on any mode not in `VISION_MODES`. There is no
            fall-back-to-mock on a bad mode: silently running the mock
            when someone asked for the detector would put simulated
            numbers on a screen labelled "camera", which is exactly the
            claim §17 forbids.
    """
    if mode not in VISION_MODES:
        raise ValueError(f"vision mode {mode!r} invalid - must be one of {VISION_MODES}")

    if mode == MODE_MOCK:
        from perception.vision_mock import VisionMock

        return VisionMock(**kwargs)

    from perception.vision_detector import VisionDetector  # heavy: pulls ultralytics/torch

    if "source" not in kwargs:
        raise ValueError(
            "mode='detector' needs source=<video path or camera index> - "
            "the detector reads a camera, not the SUMO corridor"
        )
    return VisionDetector(**kwargs)


def describe(mode: str = DEFAULT_MODE) -> str:
    """One line a UI or a presenter can show, so the honest boundary
    travels with the feed instead of living only in a docstring."""
    if mode not in VISION_MODES:
        raise ValueError(f"vision mode {mode!r} invalid - must be one of {VISION_MODES}")
    if mode == MODE_MOCK:
        return "simulated camera feed (re-emits SUMO ground truth; exact per-lane counts)"
    return (
        "real YOLOv8n detector on camera footage (per-approach counts; "
        "auto/ambulance not detectable, wait times not measurable)"
    )


if __name__ == "__main__":
    from tests import test_vision_detector

    raise SystemExit(test_vision_detector.main())
