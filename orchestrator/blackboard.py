"""The blackboard — an append-only, per-episode record of who said what.

Modelled directly on `explainability/decision_log.py`: bounded deque, frozen
entries, a monotonic-time guard that RAISES rather than sorting, and ONE
instance per episode (replaced at the boundary, never cleared in place).

WHY THE TIME GUARD RAISES, and it is the same reason `DecisionLog` gives:
`round()` and `entries_for(upto_at=...)` read the deque POSITIONALLY and take
the last match. An out-of-order entry therefore raises nothing and instead
makes every later at-or-before query answer with the PREVIOUS episode's line —
a run that passes while proving nothing. `env.reset()` sends sim_time back to
~0, so a blackboard carried across an episode boundary hits this on its first
post-reset publish.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Sequence

from orchestrator.types import (
    AGENT_NAMES,
    BLACKBOARD_MAXLEN,
    KINDS,
    AgentEntry,
    BlackboardError,
)


class Blackboard:
    """Bounded, append-only, sim-thread-only.

    NOT thread-safe — the same discipline `DecisionLog` and
    `IncidentPriorityAgent` follow. If a `/agents` HTTP endpoint is ever
    wanted, publish an immutable tail into `ControlState` the way
    `publish_stats` does; do not share this deque across threads.
    """

    def __init__(self, maxlen: int | None = BLACKBOARD_MAXLEN):
        self._entries: deque[AgentEntry] = deque(maxlen=maxlen)
        self._watermark: float | None = None

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[AgentEntry, ...]:
        """A tuple COPY — a caller can never mutate the record."""
        return tuple(self._entries)

    # -- writing --------------------------------------------------------
    def publish(self, entry: AgentEntry) -> AgentEntry:
        """Validate at the boundary, then append.

        Both guards run BEFORE anything is appended, so a rejected publish
        leaves the deque and the watermark exactly as they were.
        """
        self._validate(entry)
        self._watermark = entry.at
        self._entries.append(entry)
        return entry

    def publish_round(self, rows: Sequence[AgentEntry]) -> tuple[AgentEntry, ...]:
        """Publish one round. Every row is validated before ANY is appended,
        so a malformed row cannot leave a half-written round behind."""
        for entry in rows:
            self._validate(entry)
        return tuple(self.publish(entry) for entry in rows)

    def _validate(self, entry: AgentEntry) -> None:
        if not isinstance(entry, AgentEntry):
            raise BlackboardError(
                f"publish expects an AgentEntry, got {type(entry).__name__}")
        if entry.agent not in AGENT_NAMES:
            raise BlackboardError(
                f"unknown agent {entry.agent!r} — allowed: {AGENT_NAMES}")
        if entry.kind not in KINDS:
            raise BlackboardError(
                f"unknown kind {entry.kind!r} — allowed: {KINDS}")
        if not entry.said:
            raise BlackboardError(f"{entry.agent}: `said` must be non-empty")
        if self._watermark is not None and entry.at < self._watermark:
            raise BlackboardError(
                f"BACKWARDS `at` ({self._watermark} -> {entry.at}) from "
                f"{entry.agent}. A Blackboard covers exactly ONE episode — "
                f"replace it at the boundary, as sim_runner replaces its "
                f"DecisionLog. Equal timestamps are legal (one round shares "
                f"one `at`)."
            )

    # -- reading --------------------------------------------------------
    def entries_for(self, agent: str | None = None,
                    upto_at: float | None = None) -> tuple[AgentEntry, ...]:
        return tuple(
            e for e in self._entries
            if (agent is None or e.agent == agent)
            and (upto_at is None or e.at <= upto_at)
        )

    def round(self, step: int) -> tuple[AgentEntry, ...]:
        return tuple(e for e in self._entries if e.step == step)

    def tail(self, n: int) -> tuple[AgentEntry, ...]:
        return tuple(self._entries)[-n:] if n > 0 else ()

    def to_jsonl(self, path: str | Path) -> Path:
        """Dump the episode for offline inspection. Not used by the backend."""
        target = Path(path)
        with target.open("w", encoding="utf-8") as handle:
            for entry in self._entries:
                handle.write(json.dumps(entry.to_dict()) + "\n")
        return target
