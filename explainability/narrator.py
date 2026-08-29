"""Plain-language narration (§12.2).

Template-based, NOT a per-step LLM call — §12.2 is explicit that an
LLM-per-cycle is too slow for a live decision log, and §2 locks "no
Claude API / no LLM anywhere in the runtime path" regardless.

Pure function: `narrate(entry, register=...) -> str`. No state, no I/O.

TWO REGISTERS
-------------
The problem statement asks for "operator/public-ready explanations", so
every reason has TWO renderings:

  * ``register="operator"`` (default) — §12.2's wording verbatim, for the
    control-room decision log. Names the junction, the lane index and the
    mechanism ("wait threshold crossed", "starvation ceiling", "phase N").
    This is what `backend/sim_runner.py` and §12.3's query interface emit;
    its exact fragments are pinned by several downstream tests, so the
    operator strings are frozen.
  * ``register="public"`` — the same decision in plain language, for a
    public information channel (a sign, a feed, a press line). No
    control-room jargon: no "phase", no "slot", no "ceiling/threshold", no
    raw lane id. Same facts, said the way you would say them to a driver.

§12.2 gives four operator templates verbatim. Three confirmed deviations
for this build (design plan, Phase 8 + this change):

  1. A fifth reason, `starvation_ceiling` — §12.2 lists only four reasons
     but §10's validator has two rules.
  2. A sixth, `rl_policy`, for Phase 9's RL auto mode, which has no
     Tier-0-style rule-based justification to render. Its line names the
     busiest served lane as CONTEXT, not as the stated cause — the trained
     policy's actual reason is opaque, and phrasing it "Lane N — selected"
     would attribute a rationale the system cannot attest.
  3. Every operator template is prefixed with the junction id ("J2 · ...").
     The §12.2 templates predate §0.1's 3-junction corridor lock, where an
     operator has to know WHICH junction a line is about.

`{lane}` is `entry.lane_slot` — the within-approach index of the lane the
decision turned on (see decision_log._triggering_lane). `{direction}` is
that lane's compass approach, capitalised to match §12.2's "North".
"""

from __future__ import annotations

from explainability.decision_log import (
    REASON_EMERGENCY_OVERRIDE,
    REASON_RAW_COUNT,
    REASON_RL_POLICY,
    REASON_STARVATION_CEILING,
    REASON_VOICE_COMMAND,
    REASON_WAIT_THRESHOLD,
    REASONS,
    DecisionLogEntry,
)

REGISTER_OPERATOR = "operator"
REGISTER_PUBLIC = "public"
REGISTERS = (REGISTER_OPERATOR, REGISTER_PUBLIC)


def _lane(entry: DecisionLogEntry) -> str:
    return str(entry.lane_slot) if entry.lane_slot is not None else "?"


def _direction(entry: DecisionLogEntry) -> str:
    return (entry.direction or "unknown").capitalize()


def _ceiling_wait_s(entry: DecisionLogEntry) -> float:
    return entry.override["wait_s"] if entry.override else 0.0


def _narrate_operator(entry: DecisionLogEntry) -> str:
    """§12.2's control-room wording. Fragments here are frozen (tests pin them)."""
    reason = entry.reason
    j = entry.junction_id

    if reason == REASON_WAIT_THRESHOLD:
        return (f"{j} · Lane {_lane(entry)}, {_direction(entry)} — selected. "
                f"Wait threshold crossed.")

    if reason == REASON_RAW_COUNT:
        return (f"{j} · Lane {_lane(entry)}, {_direction(entry)} — selected. "
                f"Highest vehicle count.")

    if reason == REASON_EMERGENCY_OVERRIDE:
        return (f"{j} · Emergency override — {_direction(entry)} cleared "
                f"for ambulance.")

    if reason == REASON_STARVATION_CEILING:
        return (f"{j} · Starvation ceiling — Lane {_lane(entry)}, "
                f"{_direction(entry)} forced green after "
                f"{_ceiling_wait_s(entry):.0f}s wait.")

    if reason == REASON_VOICE_COMMAND:
        return (f"Voice command received: '{entry.transcript}' "
                f"→ {entry.action_taken}.")

    if reason == REASON_RL_POLICY:
        # The trained policy's actual reason is opaque; the lane is CONTEXT
        # (the busiest lane the selected phase serves), not the stated cause.
        return (f"{j} · Learned policy selected phase {entry.phase_selected} "
                f"(busiest served lane: {_lane(entry)}, {_direction(entry)}).")

    raise KeyError(f"no operator narration template for reason {reason!r}")


def _narrate_public(entry: DecisionLogEntry) -> str:
    """The same decision, plain language, no control-room jargon.

    Deliberately drops the lane INDEX (meaningless to the public) and every
    mechanism term — a driver hears "had been waiting too long", not "wait
    threshold crossed"; "the longest we allow", not "starvation ceiling".
    """
    reason = entry.reason
    j = entry.junction_id
    d = _direction(entry).lower()

    if reason == REASON_WAIT_THRESHOLD:
        return (f"Junction {j}: traffic from the {d} had been waiting too "
                f"long — it now has a green light.")

    if reason == REASON_RAW_COUNT:
        return (f"Junction {j}: the {d} approach had the most traffic "
                f"waiting — it now has a green light.")

    if reason == REASON_EMERGENCY_OVERRIDE:
        return (f"Junction {j}: an emergency vehicle is approaching — the "
                f"{d} route has been cleared for it.")

    if reason == REASON_STARVATION_CEILING:
        return (f"Junction {j}: traffic from the {d} had waited about "
                f"{_ceiling_wait_s(entry):.0f} seconds, the longest we "
                f"allow — it now has a green light.")

    if reason == REASON_VOICE_COMMAND:
        return (f"An operator instruction was received (\"{entry.transcript}\") "
                f"and carried out: {entry.action_taken}.")

    if reason == REASON_RL_POLICY:
        return (f"Junction {j}: signals are being adjusted automatically to "
                f"keep the corridor moving (currently favouring the {d} "
                f"approach).")

    raise KeyError(f"no public narration template for reason {reason!r}")


def narrate(entry: DecisionLogEntry, *, register: str = REGISTER_OPERATOR) -> str:
    """Render one §12.1 decision-log entry to a single line.

    `register="operator"` (default) — §12.2's control-room wording, frozen.
    `register="public"` — plain-language, for public communication.
    """
    if register == REGISTER_OPERATOR:
        return _narrate_operator(entry)
    if register == REGISTER_PUBLIC:
        return _narrate_public(entry)
    raise ValueError(f"register must be one of {REGISTERS}, got {register!r}")


# --------------------------------------------------------------------------
# Self-test — no SUMO process
# --------------------------------------------------------------------------
def _entry(reason: str, **kw) -> DecisionLogEntry:
    base = dict(
        sim_time=1840.0, junction_id="J2", phase_selected=2,
        score_breakdown={}, alternative_scores={}, reason=reason,
        lane_id="N2_J2_1", direction="north", lane_slot=1,
    )
    base.update(kw)
    return DecisionLogEntry(**base)


# Control-room mechanism terms that must NEVER appear in the public register.
_JARGON = ("phase", "slot", "ceiling", "threshold", "override", "N2_J2_1")


def _selftest() -> None:
    print("§12.2 narrator self-test\n")

    cases = {
        REASON_WAIT_THRESHOLD: _entry(REASON_WAIT_THRESHOLD),
        REASON_RAW_COUNT: _entry(REASON_RAW_COUNT),
        REASON_EMERGENCY_OVERRIDE: _entry(REASON_EMERGENCY_OVERRIDE),
        REASON_STARVATION_CEILING: _entry(
            REASON_STARVATION_CEILING,
            override={"rule": REASON_STARVATION_CEILING, "lane_id": "N2_J2_1",
                      "wait_s": 131.0, "from_slot": 1, "to_slot": 0,
                      "outcome": "applied"},
        ),
        REASON_VOICE_COMMAND: _entry(
            REASON_VOICE_COMMAND, junction_id="J1", phase_selected=-1,
            transcript="give lane 3 more priority",
            action_taken="set_lane_bias(3, high, 300s)",
        ),
        REASON_RL_POLICY: _entry(REASON_RL_POLICY),
    }

    # Every reason in the enum has a working template in BOTH registers.
    assert set(cases) == REASONS, set(cases) ^ REASONS
    for register in REGISTERS:
        print(f"  --- register={register!r} ---")
        for reason, entry in cases.items():
            line = narrate(entry, register=register)
            assert isinstance(line, str) and line.strip(), (register, reason, repr(line))
            print(f"  [{reason:>20}]  {line}")
        print()

    # --- operator register: §12.2's wording is FROZEN (downstream tests
    #     pin these fragments; this is also the default). --------------------
    op = {r: narrate(e) for r, e in cases.items()}
    assert op == {r: narrate(e, register=REGISTER_OPERATOR) for r, e in cases.items()}
    assert "Wait threshold crossed" in op[REASON_WAIT_THRESHOLD]
    assert "Highest vehicle count" in op[REASON_RAW_COUNT]
    assert "Emergency override" in op[REASON_EMERGENCY_OVERRIDE]
    assert "131s wait" in op[REASON_STARVATION_CEILING]
    assert "give lane 3 more priority" in op[REASON_VOICE_COMMAND]
    assert "Learned policy selected phase 2" in op[REASON_RL_POLICY]
    assert "busiest served lane" in op[REASON_RL_POLICY]   # lane = context, not cause
    assert op[REASON_WAIT_THRESHOLD].startswith("J2 · ")

    # --- public register: same facts, none of the control-room jargon,
    #     and genuinely different text from the operator line. --------------
    pub = {r: narrate(e, register=REGISTER_PUBLIC) for r, e in cases.items()}
    for reason, line in pub.items():
        low = line.lower()
        bad = [tok for tok in _JARGON if tok.lower() in low]
        assert not bad, f"public {reason} leaked jargon {bad}: {line!r}"
        assert line != op[reason], f"public {reason} identical to operator line"
    assert "waiting too long" in pub[REASON_WAIT_THRESHOLD]
    assert "most traffic" in pub[REASON_RAW_COUNT]
    assert "emergency vehicle is approaching" in pub[REASON_EMERGENCY_OVERRIDE]
    assert "the longest we allow" in pub[REASON_STARVATION_CEILING]
    assert "131 seconds" in pub[REASON_STARVATION_CEILING]   # the wait still surfaces
    assert "give lane 3 more priority" in pub[REASON_VOICE_COMMAND]
    assert "automatically" in pub[REASON_RL_POLICY]

    # An unknown reason must raise in either register (§12.3 — never canned).
    for register in REGISTERS:
        try:
            narrate(_entry("not_a_reason"), register=register)
        except KeyError as exc:
            print(f"  [OK] unknown reason raises ({register}): {exc}")
        else:
            raise AssertionError(f"unknown reason must raise KeyError ({register})")

    # An unknown REGISTER must raise ValueError, not fall through to a default.
    try:
        narrate(cases[REASON_RAW_COUNT], register="press-release")
    except ValueError as exc:
        print(f"\n  [OK] unknown register raises: {exc}")
    else:
        raise AssertionError("unknown register must raise ValueError")

    print(f"\nAll narrator self-tests passed "
          f"({len(REASONS)} reasons x {len(REGISTERS)} registers).")


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # narration is UTF-8
    _selftest()
