"""The orchestrator — runs one round of six agents and records what they said.

=======================================================================
WHAT THIS IS, AND THE BOUNDARY THAT MATTERS (§17 class)
=======================================================================

**It OBSERVES and RECORDS. It cannot change the action.**

That is a property of STATEMENT ORDER, not of a convention. The single call
site in `backend/sim_runner.py::_run_iteration` sits AFTER `_pick_action()`,
AFTER `env.step()` (inside which §10's validator already ran), after
`record_step()`, after `coord.observe()`, after `_update_metrics()` and after
`_assemble_frame()`. Its only write is `frame["agent_activity"]`, on a dict
that is one statement away from `frame_sink`. By the time any wrapper runs,
the phase has already reached the road.

**The Supervisor's "veto" is a RECORD of a veto that already happened.**
§10's validator runs inside `env.step()` and its results arrive in
`info["safety_overrides"]`. The Supervisor reports those. It has no authority
of its own and cannot block anything — the self-test pins this by feeding it
an override naming a lane that exists nowhere in the snapshot and asserting
the lane id is still reported verbatim, which only a reporter can do.

**Wrappers are thin.** See `orchestrator/wrappers.py` for the reviewable
no-new-logic rule and the AST tripwire that enforces it.

FAILURE ISOLATION, on the `_shadow_advice` precedent: one broken wrapper is
caught, logged once, and LATCHED OFF for the rest of the process — the other
five keep reporting. An exception escaping `observe()` itself disables the
whole orchestrator; the caller then omits the frame key entirely. Nothing
here can take down the sim thread.

Self-test:  python -m orchestrator.selftest      (no SUMO)
"""

from __future__ import annotations

import traceback
from typing import Iterable, Sequence

from orchestrator.blackboard import Blackboard
from orchestrator.types import (
    AGENT_NAMES,
    MAX_ENTRIES_PER_TICK,
    AgentContext,
    AgentEntry,
)
from orchestrator.wrappers import _Wrapper, default_wrappers


class Orchestrator:
    """ONE per EPISODE. Replace at the boundary — never carry one across.

    Both pieces of per-episode state refuse a backwards clock: the
    `Blackboard` raises on a backwards `at`, and the `IncidentPriorityAgent`
    inside the IncidentPriority wrapper raises on a backwards `sim_time`
    (NOTES-FOR-INTEGRATION §1, caller contract 1). Carrying either across
    `env.reset()` would not merely error once — the per-wrapper latch below
    would SWALLOW the exception and switch IncidentPriority off for the rest
    of the process, so the demo would silently lose an agent from episode 2
    onward while every smoke test still passed.
    """

    def __init__(self, wrappers: Sequence[_Wrapper] | None = None,
                 blackboard: Blackboard | None = None,
                 disabled: Iterable[str] = ()):
        self._wrappers = tuple(wrappers if wrappers is not None
                               else default_wrappers())
        self.blackboard = blackboard if blackboard is not None else Blackboard()
        self._disabled: set[str] = set(disabled)

    @classmethod
    def for_episode(cls, disabled: Iterable[str] = ()) -> "Orchestrator":
        """A NEW orchestrator for a new episode (immutable pattern).

        The DISABLED SET is carried forward on purpose: a structurally broken
        wrapper is still broken next episode, and re-arming it would print a
        traceback every decision step forever.
        """
        return cls(disabled=disabled)

    @property
    def disabled(self) -> frozenset[str]:
        return frozenset(self._disabled)

    @property
    def agent_names(self) -> tuple[str, ...]:
        return tuple(w.name for w in self._wrappers)

    def roster(self) -> tuple[dict, ...]:
        """Static self-description — name/role/wraps/reads/emits per agent."""
        return tuple({"agent": w.name, "role": w.role, "wraps": w.wraps,
                      "reads": w.reads, "emits": w.emits}
                     for w in self._wrappers)

    def observe(self, ctx: AgentContext) -> tuple[AgentEntry, ...]:
        """Tick every live wrapper, publish the round, return it.

        Read-only with respect to everything on `ctx`: wrappers receive a
        shallow read-only view, and the self-test asserts deep equality of
        the whole context across a round.
        """
        view = ctx.readonly()
        rows: list[AgentEntry] = []
        for wrapper in self._wrappers:
            if wrapper.name in self._disabled:
                continue
            rows.extend(self._tick_one(wrapper, view))
        return self.blackboard.publish_round(rows)

    def _tick_one(self, wrapper: _Wrapper,
                  view: AgentContext) -> tuple[AgentEntry, ...]:
        try:
            rows = tuple(wrapper.tick(view))
        except Exception:
            self._disabled.add(wrapper.name)
            print(f"[orchestrator] agent {wrapper.name!r} DISABLED after an "
                  f"exception. The other agents, the deployed policy, §10's "
                  f"validator and the sim loop are unaffected:\n"
                  + traceback.format_exc())
            return ()
        if not rows:
            # Contract: every live wrapper emits >= 1 line per round (an idle
            # line when it has nothing material), so "each agent ticked and
            # published" is a per-round invariant rather than a hope.
            self._disabled.add(wrapper.name)
            print(f"[orchestrator] agent {wrapper.name!r} DISABLED — returned "
                  f"no entry; every wrapper must emit at least an idle line.")
            return ()
        return rows[:MAX_ENTRIES_PER_TICK]

    def reset(self) -> None:
        """In-place reset. PREFER `for_episode()` — replacing the instance is
        the rule `_reset_counters` follows for `DecisionLog`."""
        self.blackboard = Blackboard()
        for wrapper in self._wrappers:
            wrapper.reset()


assert set(Orchestrator().agent_names) == set(AGENT_NAMES), (
    "orchestrator roster drifted from AGENT_NAMES"
)
