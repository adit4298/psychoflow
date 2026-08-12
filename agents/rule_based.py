"""Tier 0 fairness-first rule-based controller (§9.1, §2).

    score(lane) = 0.6 * halted_count + 0.4 * wait_time_current
                + starvation_bonus(lane)

This is Tier 0's entire decision rule and also the seed the RL agent
starts from and learns to deviate from (§9.3) — extended by RL, not
replaced by it. §2 makes it the guaranteed demo floor: a complete,
demoable project on its own, with no trained checkpoint anywhere.

Pure. No traci import — it is a function of the twin snapshot, the
signal runtime, the action mask, and the static phase->lane map. That is
what lets it sit at exactly the same seam the RL policy will occupy
later (§13.1's set_mode swaps them without either side knowing).

PHASE SELECTION. Score every green phase by SUMMING the scores of the
lanes it would green, take the argmax among mask-valid slots:

    phase_score[s] = sum(score(lane) for lane in served_lanes[s])

Sum rather than mean because sum is total demand served; mean would
prefer one badly congested lane over four moderately congested ones,
which is wrong for throughput. The known cost is that sum favours phases
serving more lanes, and on this corridor the through-corridor phase
serves more lanes than a cross-street phase. Two things absorb that: the
starvation bonus is per-lane and cubic, so one starved cross-street lane
produces a spike a broad-but-shallow corridor phase cannot match; and
§10's ceiling is the hard backstop underneath. If a Tier 0 episode ever
shows cross-streets systematically starved, dividing by len(served) is
the one-line alternative — decide that from measurement, not taste.

MASK DISCIPLINE. The controller selects only among slots the §9.2 mask
marks valid, so it structurally cannot raise InvalidActionError, and it
inherits anti-flicker (MIN_GREEN_S) and yellow-transition safety from
the env rather than re-implementing either. Mid-yellow the mask leaves
exactly one valid slot and this reduces to "hold".

§9.1's UNITS. `wait_time_current` is TraCI lane.getWaitingTime(), a SUM
over the vehicles on the lane, so it runs O(0-1000) while halted_count
runs O(0-20) — the 0.6/0.4 weights are therefore nominal rather than a
true blend, and the wait term dominates. That is §9.1 taken literally,
which is deliberate: §9.1 is the SEED the RL agent deviates from, not an
optimum, and rewriting a stated formula for aesthetics is scope creep.
"""

from __future__ import annotations

from dataclasses import dataclass

from env.obs_action_spec import MAX_PHASES
from perception.lane_sensor import DEFAULT_STARVATION_THRESHOLD_S
from twin.digital_twin import CORRIDOR_JUNCTIONS

# §12.2's reason enum, for the decision breakdown this returns.
REASON_STARVATION = "wait_time_threshold"
REASON_COUNT = "raw_count"


@dataclass
class Tier0Config:
    """§9.1's weights plus the starvation bonus shape."""

    w_halted: float = 0.6
    w_wait: float = 0.4

    # Shared with §9.4's reward and §7.1's starvation_flag — imported, never
    # retyped, so the three cannot drift apart.
    starvation_threshold_s: float = DEFAULT_STARVATION_THRESHOLD_S

    # §9.1: "grows non-linearly as wait_time_max_single_vehicle approaches
    # the starvation threshold."
    #
    #     bonus = scale * min(r, r_cap) ** exponent,  r = wait / threshold
    #
    # DELIBERATELY A DIFFERENT SHAPE FROM §9.4's REWARD PENALTY, which is
    # r^2 + 4*max(0, r-1)^2. Different jobs:
    #
    #   this bonus     chooses among <=3 phases NOW; the range that matters
    #                  is [0, T) because that is where starvation can still
    #                  be PREVENTED. Above T, §10's ceiling has taken over.
    #   reward penalty scores an outcome AFTER the fact; the range that
    #                  matters is [T, inf) because it has to keep
    #                  discriminating among failures (200s much worse than
    #                  100s), which is why it is unbounded.
    #
    # Reusing the reward's shape here would fail twice over. Its hinge sits
    # AT T, so it is nearly flat below T (r^2 = 0.81 at r=0.9) — Tier 0 would
    # only react once it was already too late. And it is unbounded above, so
    # a single catastrophic lane would swamp every other term and Tier 0
    # would tunnel-vision on it while the rest of the junction rotted.
    #
    # Cubic so the bonus is negligible early (r=0.33 -> 0.036*scale) and
    # decisive late (r=0.9 -> 0.729*scale). Capped at r=2 because past the
    # ceiling the §10 gate owns the decision anyway, and an uncapped term
    # would only add numerical noise to §12.1's score_breakdown.
    starvation_bonus_exponent: float = 3.0
    starvation_bonus_r_cap: float = 2.0

    # MEASURED, not guessed — `python sim/run_tier0_episode.py --measure-scale`
    # (1800s, corridor 4/3/2, seed 11, bonus disabled). Sampling only the 367
    # decision points that offered a real CHOICE (2+ valid slots; the other 707
    # were min-green locked, and including them buries the distribution under
    # zeros), the "bar to beat" — the highest competing base score at each
    # choice point — came out:
    #     min 0.00   p25 9.00   median 13.00   p75 17.60   p90 27.60   max 56.60
    # Calibrated so a lane at r=0.9 (81s, nine seconds from the flag) clears the
    # MEDIAN bar:  scale = 13.00 / 0.9**3 = 17.8  ->  20.
    #
    # The resulting ramp: 5.9 at 60s (in the noise), 14.6 at 81s (clears the
    # median competitor), 20.0 at the 90s flag (clears p75), 47.4 at the 120s
    # §10 ceiling (clears p90 nearly twice over). So the bonus becomes decisive
    # against a typical competitor right as the lane is flagged, and dominates
    # well before the hard ceiling has to intervene — which is exactly the job
    # §9.1 gives it.
    #
    # Re-measure if the density defaults in ScenarioConfig change: this is
    # calibrated against a base-score distribution, not a physical constant.
    starvation_bonus_scale: float = 20.0


def starvation_bonus(wait_max_s: float, config: Tier0Config) -> float:
    """§9.1's non-linear bonus. Exposed for hand-checking and calibration."""
    r = min(wait_max_s / config.starvation_threshold_s, config.starvation_bonus_r_cap)
    return config.starvation_bonus_scale * (r ** config.starvation_bonus_exponent)


def lane_score(reading: dict, config: Tier0Config) -> float:
    """§9.1's per-lane score, verbatim."""
    return (
        config.w_halted * reading["halted_count"]
        + config.w_wait * reading["wait_time_current"]
        + starvation_bonus(reading["wait_time_max_single_vehicle"], config)
    )


class Tier0Controller:
    """Fairness-first controller. Stateless — one snapshot in, one action out."""

    def __init__(self, config: Tier0Config | None = None):
        self.config = config or Tier0Config()

    def act(
        self,
        snapshot: dict,
        runtime: dict[str, dict],
        masks,
        served_lanes: dict[str, dict[int, frozenset[str]]],
    ) -> tuple[tuple[int, ...], dict[str, dict]]:
        """Returns (action, decisions).

        `decisions` carries §12.1's score_breakdown / alternative_scores /
        reason per junction. This is data the controller already computes,
        returned rather than discarded — §12's decision log, narrator and
        query interface are Phase 8 and are NOT built here.
        """
        config = self.config
        action: list[int] = []
        decisions: dict[str, dict] = {}

        for i, junction_id in enumerate(CORRIDOR_JUNCTIONS):
            lanes = snapshot["junctions"][junction_id]["lanes"]
            served = served_lanes[junction_id]

            valid = [s for s in range(MAX_PHASES) if masks[i * MAX_PHASES + s]]
            if not valid:
                # §9.2 guarantees the current phase is always legal, so an
                # empty mask means the runtime and the mask have diverged.
                raise RuntimeError(f"{junction_id}: no valid phase slots in mask")

            totals: dict[int, float] = {}
            bases: dict[int, float] = {}
            parts: dict[int, dict[str, float]] = {}

            for slot in valid:
                halted = wait = bonus = 0.0
                for lane_id in served.get(slot, frozenset()):
                    reading = lanes.get(lane_id)
                    if reading is None:
                        continue  # not an approach lane the twin senses
                    halted += config.w_halted * reading["halted_count"]
                    wait += config.w_wait * reading["wait_time_current"]
                    bonus += starvation_bonus(
                        reading["wait_time_max_single_vehicle"], config
                    )
                bases[slot] = halted + wait
                totals[slot] = halted + wait + bonus
                parts[slot] = {"halted_count": halted, "wait_time": wait,
                               "starvation_bonus": bonus}

            # Ties break to the lowest slot index so runs are reproducible.
            chosen = max(valid, key=lambda s: (totals[s], -s))

            # The bonus was DECISIVE iff dropping it changes the argmax —
            # a precise test, not a threshold guess.
            chosen_without_bonus = max(valid, key=lambda s: (bases[s], -s))
            reason = REASON_STARVATION if chosen != chosen_without_bonus else REASON_COUNT

            action.append(chosen)
            decisions[junction_id] = {
                "junction_id": junction_id,
                "phase_selected": chosen,
                "score_breakdown": {k: round(v, 3) for k, v in parts[chosen].items()},
                "alternative_scores": {f"phase_{s}": round(totals[s], 3) for s in valid},
                "reason": reason,
            }

        return tuple(action), decisions


class BaseScoreProbe:
    """Calibration instrument for Tier0Config.starvation_bonus_scale.

    Run against a controller configured with scale=0, so `alternative_scores`
    carries pure §9.1 BASE scores. The bonus is then set against the
    distribution it actually has to compete with, rather than a guessed
    constant — the same discipline that made MAX_PHASES a measured value.

    ONLY samples decision points with 2+ valid slots. Most steps are
    min-green-locked to a single slot (MIN_GREEN_S=10s against a 5s decision
    interval, so two of every three steps after a switch offer no choice),
    and including them buries the real distribution under zeros — the
    controller is not deciding anything at those steps.

    `maxima` is the statistic that matters: at each real choice point, the
    HIGHEST competing base score. That is the bar a starved lane's phase has
    to clear for the bonus to flip the decision.

    Not part of the controller's runtime path — used only by
    sim/run_tier0_episode.py --measure-scale.
    """

    def __init__(self):
        self.samples: list[float] = []  # every competing slot's base score
        self.maxima: list[float] = []  # the bar to beat, per choice point
        self.choice_points = 0
        self.locked_points = 0

    def observe(self, decisions: dict[str, dict]) -> None:
        for decision in decisions.values():
            scores = list(decision["alternative_scores"].values())
            if len(scores) < 2:
                self.locked_points += 1
                continue
            self.choice_points += 1
            self.samples.extend(scores)
            self.maxima.append(max(scores))

    @staticmethod
    def _percentiles(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)

        def pct(p: float) -> float:
            return ordered[min(len(ordered) - 1, int(p * len(ordered)))]

        return {
            "n": len(ordered),
            "min": ordered[0],
            "p25": pct(0.25),
            "median": pct(0.50),
            "p75": pct(0.75),
            "p90": pct(0.90),
            "max": ordered[-1],
            "mean": sum(ordered) / len(ordered),
        }

    def summary(self) -> dict[str, dict]:
        if not self.maxima:
            raise RuntimeError(
                "no real choice points sampled — every decision was min-green "
                "locked to a single slot"
            )
        return {
            "all_competing": self._percentiles(self.samples),
            "bar_to_beat": self._percentiles(self.maxima),
            "choice_points": self.choice_points,
            "locked_points": self.locked_points,
        }
