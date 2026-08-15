"""Shared-policy feature extractor — §9.5's MARL fallback path.

    One encoder, same weights, applied independently to every junction row.
    No neighbour state, no attention. Coordination emerges only implicitly,
    through the corridor-wide reward (§9.4) that all three junctions share.

WHY THIS IS NOT WHAT STAGES 1-4 ALREADY DID. Stages 1-4 used SB3's default
`MlpPolicy`, which FLATTENS the (3, 191) observation to 573 and feeds it to
one dense network. That network has a separate weight for "J1's halted
count" and "J3's halted count" — it can, and does, learn three unrelated
per-junction functions. §9.5's shared-policy fallback means something
structurally different: ONE function of a single junction's 191 features,
applied three times with IDENTICAL weights. That is what makes the demo
claim "junctions share a learned policy" literally true rather than a
relabelling of the single-agent baseline, and it is why this file exists
instead of just reusing the Stage 1-4 setup as the fallback.

Concretely, the difference is weight sharing, not depth: a junction's
embedding here cannot depend on WHICH junction it is (there is no
positional input), so the learned policy must generalise across corridor
positions rather than memorising three of them.

Output layout: the three per-junction embeddings are concatenated in
CORRIDOR_JUNCTIONS order (J1, J2, J3 — the same order
`build_observation()` writes its rows in, verified against
env/obs_action_spec.py's `for row, junction_id in enumerate(...)` loop).
The policy head downstream is a MultiDiscrete([3,3,3]) with one head per
junction, so preserving per-junction structure in the feature vector —
rather than pooling it away — is what lets each head read its own
junction's embedding.
"""

from __future__ import annotations

import gymnasium as gym
import torch
import torch.nn as nn
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

DEFAULT_EMBED_DIM = 64


class SharedPolicyExtractor(BaseFeaturesExtractor):
    """Per-junction encoder with weights shared across all junctions.

    observation_space: Box(N_JUNCTIONS, OBS_FEATURES) == (3, 191).
    features_dim: N_JUNCTIONS * embed_dim.
    """

    def __init__(self, observation_space: gym.Space, embed_dim: int = DEFAULT_EMBED_DIM):
        n_junctions, n_features = observation_space.shape
        super().__init__(observation_space, features_dim=n_junctions * embed_dim)

        self.n_junctions = n_junctions
        self.n_features = n_features
        self.embed_dim = embed_dim

        # Applied to ONE junction's feature vector. nn.Linear broadcasts over
        # leading dims, so calling it on (batch, 3, 191) applies the same
        # weights to each of the 3 rows — that IS the weight sharing, no
        # explicit loop needed.
        self.encoder = nn.Sequential(
            nn.Linear(n_features, embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, embed_dim),
            nn.ReLU(),
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        # (batch, 3, 191) -> (batch, 3, embed_dim) -> (batch, 3 * embed_dim)
        embedded = self.encoder(observations)
        return embedded.flatten(start_dim=1)
