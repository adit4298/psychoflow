"""Graph-attention feature extractor — §9.5's MARL primary path.

    Each junction's node embedding attends over its DIRECT NEIGHBOURS'
    current state (per `corridor_adjacency`, §7.6) before the policy head
    selects a phase — so a junction can hold or shorten a green
    specifically because of what a neighbour is doing, not just its own
    local state.

Shares the shared-policy path's per-junction encoder design (identical
weights across junctions, no positional input) and adds exactly one thing
on top: a masked multi-head self-attention layer restricted to the real
corridor graph. That single-difference framing is deliberate — it is what
makes a Stage-5 A/B between the two extractors a test of ATTENTION
specifically, rather than of two unrelated architectures.

THE ADJACENCY MASK IS THE WHOLE POINT. Unmasked self-attention would let
J1 attend directly to J3, which is not a corridor edge — the two are not
adjacent (§0.1's locked linear J1-J2-J3 topology), and allowing it would
quietly make this a fully-connected network wearing a graph's name. The
mask below permits only:

    J1 <- {J1, J2}          J2 <- {J1, J2, J3}          J3 <- {J2, J3}

i.e. each junction attends to itself plus its immediate neighbours, and
information reaches J1 from J3 only indirectly, through J2 — which is
exactly the physical structure of the corridor.

Adjacency is IMPORTED from twin/digital_twin.py's CORRIDOR_ADJACENCY, not
re-declared here — CLAUDE.md §8 makes that a standing rule precisely so a
topology change cannot silently desynchronise §8.1's spillover forecast
from §9.5's attention graph.

Self-loops are added on top of CORRIDOR_ADJACENCY's edges. Without them a
junction could not attend to its own state at all, and every embedding
would be a pure function of its neighbours — which is not what "attends
over its neighbours" means and would discard the local state the policy
head most needs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gymnasium as gym
import torch
import torch.nn as nn
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from twin.digital_twin import CORRIDOR_ADJACENCY, CORRIDOR_JUNCTIONS  # noqa: E402

DEFAULT_EMBED_DIM = 64
DEFAULT_N_HEADS = 4


def build_attention_mask() -> torch.Tensor:
    """(N, N) bool mask for nn.MultiheadAttention, True = NOT allowed.

    PyTorch's convention for a bool `attn_mask` is inverted relative to the
    adjacency matrix — True means "block this attention pair". Getting this
    backwards would produce a network that attends ONLY to non-neighbours,
    which would still train and still emit plausible numbers, so it is
    asserted in the smoke test rather than left to reading.
    """
    n = len(CORRIDOR_JUNCTIONS)
    index = {jid: i for i, jid in enumerate(CORRIDOR_JUNCTIONS)}

    allowed = torch.eye(n, dtype=torch.bool)  # self-loops
    for a, b in CORRIDOR_ADJACENCY:
        ia, ib = index[a], index[b]
        allowed[ia, ib] = True
        allowed[ib, ia] = True  # undirected: influence runs both ways

    return ~allowed


class GraphAttentionExtractor(BaseFeaturesExtractor):
    """Per-junction encoder + neighbour-masked self-attention.

    observation_space: Box(N_JUNCTIONS, OBS_FEATURES) == (3, 191).
    features_dim: N_JUNCTIONS * embed_dim (same as the shared-policy
    fallback, so swapping the config flag does not change the downstream
    policy-head geometry).
    """

    def __init__(
        self,
        observation_space: gym.Space,
        embed_dim: int = DEFAULT_EMBED_DIM,
        n_heads: int = DEFAULT_N_HEADS,
    ):
        n_junctions, n_features = observation_space.shape
        super().__init__(observation_space, features_dim=n_junctions * embed_dim)

        self.n_junctions = n_junctions
        self.n_features = n_features
        self.embed_dim = embed_dim

        # Identical to SharedPolicyExtractor's encoder by design — see this
        # module's docstring on why the two paths differ by attention alone.
        self.encoder = nn.Sequential(
            nn.Linear(n_features, embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, embed_dim),
            nn.ReLU(),
        )

        self.attention = nn.MultiheadAttention(
            embed_dim=embed_dim, num_heads=n_heads, batch_first=True,
        )
        self.norm = nn.LayerNorm(embed_dim)

        # Registered as a buffer, not a plain attribute, so .to(device)
        # moves it with the model — a CPU mask against CUDA tensors is a
        # runtime error that would only surface on a GPU machine.
        self.register_buffer("attn_mask", build_attention_mask())

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        # (batch, 3, 191) -> (batch, 3, embed_dim)
        embedded = self.encoder(observations)

        attended, _ = self.attention(
            embedded, embedded, embedded, attn_mask=self.attn_mask, need_weights=False,
        )
        # Residual around attention: a junction keeps direct access to its
        # own encoded state even if attention learns to route around it.
        out = self.norm(embedded + attended)

        return out.flatten(start_dim=1)
