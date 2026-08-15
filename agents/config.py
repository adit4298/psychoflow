"""§9.5's single coordination-mode flag.

Switching MARL paths must be a one-line config change and a re-run, never
new code — the master plan is explicit that both extractors are built and
tested in parallel, NOT the fallback written only if attention fails.

§9.5's flip rule, quoted rather than paraphrased because it governs a
judgement call made under time pressure at the §16 Stage 5 checkpoint:

    "If graph-attention hasn't shown a clean upward reward trend by that
    checkpoint, flip to `shared_policy` and continue — this is treated as
    expected/normal, not a failure requiring root-cause debugging under
    time pressure."

NOTE on switching modes mid-curriculum: the two extractors have different
parameter sets, so a checkpoint trained under one mode CANNOT be resumed
under the other (`MaskablePPO.load()` requires matching network shapes).
Flipping the flag means starting that stage's training fresh, not
resuming. This is a property of the architectures, not a limitation of
the flag.
"""

from __future__ import annotations

from agents.policy_extractor_attention import GraphAttentionExtractor
from agents.policy_extractor_shared import SharedPolicyExtractor

COORDINATION_MODE = "graph_attention"  # or "shared_policy"

VALID_MODES = ("graph_attention", "shared_policy")


def get_feature_extractor(mode: str):
    """Return the BaseFeaturesExtractor subclass for `mode`.

    Raises on an unknown mode rather than silently falling back to one of
    the two — a typo'd mode string quietly training the wrong architecture
    for 50k timesteps is exactly the kind of expensive-to-discover-late
    failure §16's checkpoints exist to prevent.
    """
    if mode == "graph_attention":
        return GraphAttentionExtractor
    if mode == "shared_policy":
        return SharedPolicyExtractor
    raise ValueError(f"COORDINATION_MODE must be one of {VALID_MODES}, got {mode!r}")
