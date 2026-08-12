"""§18 Phase 4 verification harness — Tier 0 controller + §10 safety validator.

Done bar: "rule-based controller runs live in SUMO, starvation ceiling and
emergency override both verifiably trigger."

  --measure-scale  calibrate Tier0Config.starvation_bonus_scale against the
                   measured distribution of per-phase base scores
  --b1  Tier 0 standalone, full episode, compared to §16's random baseline
  --b2  emergency override latency, measured in seconds, two variants
  --b3  starvation ceiling: same-seed A/B, validator off vs on
  --b4  random-action baseline RE-MEASURED with the validator on

No arguments runs measure-scale + B1-B4 in order.

Not part of §6's folder structure — verification scaffolding, same category
as sim/run_perception_episode.py and sim/run_env_smoke.py.

This file is the ONLY place outside unit tests permitted to construct
PsychoFlowEnv(enable_safety_validator=False) — see CLAUDE.md §8. B3 needs
an unshielded run to prove the ceiling is what bounds the wait.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import traci  # noqa: E402

from agents.rule_based import BaseScoreProbe, Tier0Config, Tier0Controller  # noqa: E402
from env.obs_action_spec import MAX_PHASES  # noqa: E402
from env.psychoflow_env import (  # noqa: E402
    DECISION_INTERVAL_S,
    MIN_GREEN_S,
    PsychoFlowEnv,
    ScenarioConfig,
)
from safety.validator import STARVATION_CEILING_S  # noqa: E402
from twin.digital_twin import CORRIDOR_JUNCTIONS  # noqa: E402

RULE = "=" * 78

# §16's measured random-action baseline, recorded 2026-08-12 during Phase 3,
# corridor 4/3/2 seed 7, WITHOUT a safety validator. B1 and B4 quote it.
PHASE3_BASELINE = {
    "steps": 718,
    "mean_reward": -224.8,
    "arrived": 4604,
    "worst_wait": 793.0,
    "starved_steps": 624,
}


# --------------------------------------------------------------------------
# Shared episode driver
# --------------------------------------------------------------------------
def run_episode(env, pick_action, horizon_s=None, probe=None, on_step=None):
    """Drive one episode with an arbitrary action-picking callable.

    `pick_action(snapshot, runtime, masks, served) -> (action, decisions)`
    """
    obs, info = env.reset()
    served = env.phase_served_lanes()

    stats = {
        "steps": 0, "total_reward": 0.0, "worst_wait": 0.0, "starved_steps": 0,
        "overrides": [], "arrived": 0, "sim_time": 0.0,
        "terminated": False, "truncated": False,
    }

    while True:
        snapshot = env.twin.snapshot
        runtime = env._runtime()
        masks = env.action_masks()

        action, decisions = pick_action(snapshot, runtime, masks, served)
        if probe is not None:
            probe.observe(decisions)

        obs, reward, terminated, truncated, info = env.step(action)

        stats["steps"] += 1
        stats["total_reward"] += reward
        stats["arrived"] = info["arrived_total"]
        stats["sim_time"] = info["sim_time"]
        breakdown = info["reward_breakdown"]
        worst = breakdown["worst_lane"]["wait_s"]
        stats["worst_wait"] = max(stats["worst_wait"], worst)
        if worst > 90.0:
            stats["starved_steps"] += 1
        stats["overrides"].extend(info["safety_overrides"])

        if on_step is not None:
            on_step(env, info)

        if horizon_s is not None and info["sim_time"] >= horizon_s:
            stats["truncated"] = True
            break
        if terminated or truncated:
            stats["terminated"], stats["truncated"] = terminated, truncated
            break

    stats["mean_reward"] = stats["total_reward"] / max(1, stats["steps"])
    return stats


def random_picker(rng):
    def pick(snapshot, runtime, masks, served):
        action = [
            int(rng.choice([s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]]))
            for j in range(len(CORRIDOR_JUNCTIONS))
        ]
        return action, {}
    return pick


class AdversarialController:
    """Deliberately starves one approach at one junction. B3 only.

    Not §15.1's Greedy baseline (that is Phase 12) — this exists purely to
    manufacture the starvation the ceiling is supposed to catch, so the A/B
    has something to contrast. Other junctions run a seeded random policy,
    identical across both runs, so the ONLY difference between run A and run
    B is the validator.
    """

    def __init__(self, junction_id: str, victim_lanes: frozenset[str], rng):
        self.junction_id = junction_id
        self.victim_lanes = victim_lanes
        self.rng = rng

    def act(self, snapshot, runtime, masks, served):
        action = []
        for j, junction_id in enumerate(CORRIDOR_JUNCTIONS):
            valid = [s for s in range(MAX_PHASES) if masks[j * MAX_PHASES + s]]
            if junction_id == self.junction_id:
                # Fewest victim lanes served; ties to the lowest slot.
                choice = min(
                    valid,
                    key=lambda s: (len(served[junction_id].get(s, frozenset())
                                       & self.victim_lanes), s),
                )
            else:
                choice = int(self.rng.choice(valid))
            action.append(choice)
        return tuple(action), {}


def victim_lanes_for(env, junction_id: str, approach: str) -> frozenset[str]:
    lanes = env.twin.snapshot["junctions"][junction_id]["lanes"]
    return frozenset(
        lane_id for lane_id, reading in lanes.items() if reading["approach"] == approach
    )


def report(label, stats, baseline=None):
    print(f"\n  {label}")
    print(f"    steps={stats['steps']}  sim_time={stats['sim_time']:.0f}s  "
          f"terminated={stats['terminated']} truncated={stats['truncated']}")
    rows = [
        ("mean reward / step", f"{stats['mean_reward']:+.1f}",
         f"{baseline['mean_reward']:+.1f}" if baseline else None),
        ("vehicles arrived", f"{stats['arrived']}",
         f"{baseline['arrived']}" if baseline else None),
        ("worst single-vehicle wait", f"{stats['worst_wait']:.1f}s",
         f"{baseline['worst_wait']:.1f}s" if baseline else None),
        ("steps with a starved lane",
         f"{stats['starved_steps']}/{stats['steps']} "
         f"({100 * stats['starved_steps'] / max(1, stats['steps']):.0f}%)",
         f"{baseline['starved_steps']}/{baseline['steps']} "
         f"({100 * baseline['starved_steps'] / baseline['steps']:.0f}%)" if baseline else None),
    ]
    width = max(len(r[0]) for r in rows)
    if baseline:
        print(f"    {'metric':<{width}}   {'this run':>18}   {'§16 random baseline':>22}")
        for name, mine, theirs in rows:
            print(f"    {name:<{width}}   {mine:>18}   {theirs:>22}")
    else:
        for name, mine, _ in rows:
            print(f"    {name:<{width}}   {mine:>18}")

    if stats["overrides"]:
        by_rule = {}
        for record in stats["overrides"]:
            key = (record["rule"], record["outcome"])
            by_rule[key] = by_rule.get(key, 0) + 1
        print(f"    §10 overrides: {len(stats['overrides'])} total")
        for (rule, outcome), n in sorted(by_rule.items()):
            print(f"        {rule:<20} {outcome:<22} {n}")
    else:
        print("    §10 overrides: none")


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------
def measure_scale(horizon_s=1800.0):
    print(RULE)
    print("MEASURE — calibrating Tier0Config.starvation_bonus_scale")
    print(RULE)
    print(f"  Tier 0 with the bonus DISABLED (scale=0), {horizon_s:.0f}s, recording")
    print("  per-phase BASE scores (§9.1 without the bonus) at every decision point")
    print("  that offers a real CHOICE (2+ valid slots). Min-green-locked steps are")
    print("  excluded — the controller is not deciding anything there.")

    env = PsychoFlowEnv(scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)), seed=11)
    controller = Tier0Controller(Tier0Config(starvation_bonus_scale=0.0))
    probe = BaseScoreProbe()
    stats = run_episode(env, controller.act, horizon_s=horizon_s, probe=probe)
    env.close()

    summary = probe.summary()
    print(f"\n  {stats['steps']} decision steps -> {summary['choice_points']} real choice "
          f"points, {summary['locked_points']} min-green locked")

    for name, key in (("ALL competing slots", "all_competing"),
                      ("BAR TO BEAT (max competitor per choice point)", "bar_to_beat")):
        dist = summary[key]
        print(f"\n  {name}  (n={dist['n']})")
        print("    " + "  ".join(f"{k:>8}" for k in
                                 ("min", "p25", "median", "p75", "p90", "max", "mean")))
        print("    " + "  ".join(f"{dist[k]:8.2f}" for k in
                                 ("min", "p25", "median", "p75", "p90", "max", "mean")))

    # A lane at r=0.9 (81s — nine seconds from the flag) should be able to
    # flip a typical decision, so the bonus there must clear the median BAR:
    #     scale * 0.9**3 = median_bar   ->   scale = median_bar / 0.729
    bar = summary["bar_to_beat"]
    scale = bar["median"] / (0.9 ** 3)
    rounded = max(10.0, round(scale / 10.0) * 10.0)
    print(f"\n  calibration: bonus at r=0.9 must clear the MEDIAN bar to beat")
    print(f"    scale = {bar['median']:.2f} / 0.9^3 = {bar['median']:.2f} / 0.729 = {scale:.1f}")
    print(f"\n  ==> starvation_bonus_scale = {rounded:.0f}  (rounded)")
    print(f"\n  resulting bonus curve:")
    for wait_s in (30.0, 45.0, 60.0, 81.0, 90.0, 120.0, 180.0):
        r = min(wait_s / 90.0, 2.0)
        print(f"    wait {wait_s:5.0f}s (r={r:.2f})  bonus = {rounded * r ** 3:8.2f}"
              f"{'   <- 90s starvation flag' if wait_s == 90.0 else ''}"
              f"{'   <- 120s §10 ceiling' if wait_s == 120.0 else ''}")
    return rounded


# --------------------------------------------------------------------------
# B1 — Tier 0 standalone
# --------------------------------------------------------------------------
def b1():
    print(RULE)
    print("B1 — Tier 0 standalone, full episode (validator ON)")
    print(RULE)
    env = PsychoFlowEnv(scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)), seed=7)
    controller = Tier0Controller()
    stats = run_episode(env, controller.act)
    env.close()
    report("Tier 0 vs §16's random-action baseline (same corridor 4/3/2, seed 7)",
           stats, baseline=PHASE3_BASELINE)
    return stats


# --------------------------------------------------------------------------
# B2 — emergency override latency
# --------------------------------------------------------------------------
def b2_variant(label, force_fresh_green: bool, seed=23):
    """Inject an ambulance on a RED approach at J2 and time the override.

    Latency is measured from FIRST DETECTION (the first twin snapshot
    containing the ambulance) rather than from injection, because §10's
    clock starts at "ambulance detected in any lane". Injecting between
    decision steps costs an extra interval that a naturally-arriving
    ambulance would not — reported separately for transparency.

    Green onset is recovered exactly (1s resolution, not the 5s decision
    interval) as  sim_time - time_since_switch_s, since _set_green() zeroes
    that counter at the instant the green is set.
    """
    print(f"\n  --- variant {label} ---")
    env = PsychoFlowEnv(scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)), seed=seed)
    obs, info = env.reset()
    served = env.phase_served_lanes()

    victim = victim_lanes_for(env, "J2", "north")
    amb_slots = {s for s, lanes in served["J2"].items() if lanes & victim}

    # The controller at J2 REFUSES to serve north, so any green there is
    # attributable solely to §10. Using Tier 0 here would confound the
    # measurement: its own scoring serves a queueing north approach on the
    # ordinary path, and a green it chose for queue reasons is not evidence
    # that the override works.
    controller = AdversarialController("J2", victim, np.random.default_rng(seed))

    state = {"injected_at": None, "detected_at": None, "green_at": None,
             "override": None, "green_age_at_injection": None, "bypassed": False}

    def j2_serves_north(runtime):
        return runtime["J2"]["current_green_slot"] in amb_slots

    steps = 0
    while steps < 700:
        snapshot = env.twin.snapshot
        runtime = env._runtime()
        masks = env.action_masks()
        sim_time = snapshot["sim_time"]

        # --- injection ---------------------------------------------------
        if state["injected_at"] is None and sim_time >= 120.0:
            green_age = runtime["J2"]["time_since_switch_s"]
            if force_fresh_green:
                # Variant (b): the green must ALSO be younger than
                # MIN_GREEN_S, so the ONLY way the override can fire is by
                # bypassing the min-green mask.
                ready = not j2_serves_north(runtime) and green_age < MIN_GREEN_S
            else:
                # Variant (a): the ordinary path — green already past
                # min-green, so no bypass is needed and none should be
                # reported. Without this condition (a) and (b) collapse onto
                # the same scenario and (a) proves nothing extra.
                ready = not j2_serves_north(runtime) and green_age >= MIN_GREEN_S
            if ready:
                traci.switch(env._label)
                traci.vehicle.add("amb_probe", routeID="r_ns2", typeID="ambulance",
                                  depart="now", departLane="best", departPos="free",
                                  departSpeed="max")
                state["injected_at"] = sim_time
                state["green_age_at_injection"] = runtime["J2"]["time_since_switch_s"]
                print(f"    t={sim_time:6.0f}s  injected ambulance on J2 north "
                      f"(current slot {runtime['J2']['current_green_slot']} does NOT "
                      f"serve north; green_age={state['green_age_at_injection']:.0f}s)")

        action, _ = controller.act(snapshot, runtime, masks, served)
        obs, reward, terminated, truncated, info = env.step(action)
        steps += 1

        snapshot = env.twin.snapshot
        runtime = env._runtime()
        sim_time = snapshot["sim_time"]

        # --- detection ---------------------------------------------------
        if state["injected_at"] is not None and state["detected_at"] is None:
            lanes = snapshot["junctions"]["J2"]["lanes"]
            hit = [lid for lid, r in lanes.items()
                   if r["type_composition"].get("ambulance", 0) > 0]
            if hit:
                state["detected_at"] = sim_time
                state["lane"] = hit[0]
                print(f"    t={sim_time:6.0f}s  DETECTED on {hit[0]}")

        for record in info["safety_overrides"]:
            if record["rule"] == "emergency_override" and state["override"] is None:
                state["override"] = record
                state["bypassed"] = "J2" in info["safety_bypass_min_green"]
                print(f"    t={sim_time:6.0f}s  §10 OVERRIDE fired: J2 slot "
                      f"{record['from_slot']}->{record['to_slot']} lane={record['lane_id']} "
                      f"outcome={record['outcome']}  "
                      f"bypass_min_green={info['safety_bypass_min_green']}")

        # --- green onset, exact to 1s ------------------------------------
        if state["detected_at"] is not None and state["green_at"] is None:
            if j2_serves_north(runtime):
                state["green_at"] = sim_time - runtime["J2"]["time_since_switch_s"]
                print(f"    t={sim_time:6.0f}s  J2 north GREEN "
                      f"(onset t={state['green_at']:.0f}s, slot "
                      f"{runtime['J2']['current_green_slot']})")
                break

        if terminated or truncated:
            break

    env.close()

    if state["green_at"] is None:
        print("    *** ambulance never got a green — FAILED ***")
        return None
    if state["override"] is None:
        print("    *** green appeared WITHOUT an override — this variant proves "
              "nothing about §10 ***")
        return None

    from_detect = state["green_at"] - state["detected_at"]
    from_inject = state["green_at"] - state["injected_at"]
    bound = DECISION_INTERVAL_S + 5.0  # one interval + yellow (~3-4s)
    print(f"\n    injected      t={state['injected_at']:.0f}s")
    print(f"    detected      t={state['detected_at']:.0f}s")
    print(f"    green onset   t={state['green_at']:.0f}s")
    print(f"    LATENCY from detection : {from_detect:.1f}s   (§10's clock; "
          f"bound = interval {DECISION_INTERVAL_S:.0f}s + yellow ~4s = ~{bound:.0f}s)")
    print(f"    latency from injection : {from_inject:.1f}s   "
          f"(includes the extra interval my between-step injection costs)")
    if state["green_age_at_injection"] is not None:
        print(f"    green age at injection : {state['green_age_at_injection']:.0f}s "
              f"(MIN_GREEN_S={MIN_GREEN_S:.0f}s)")
    print(f"    min-green BYPASSED     : {state['bypassed']}")
    return {"from_detect": from_detect, "from_inject": from_inject,
            "green_age": state["green_age_at_injection"],
            "bypassed": state["bypassed"], "override": state["override"]}


def b2():
    print(RULE)
    print("B2 — emergency override latency, measured")
    print(RULE)
    a = b2_variant("(a) ordinary case — green older than MIN_GREEN_S",
                   force_fresh_green=False)
    b = b2_variant("(b) MIN_GREEN BYPASS — green YOUNGER than MIN_GREEN_S, so the "
                   "mask\n      would have blocked this switch; only §10 can make it happen",
                   force_fresh_green=True)
    return a, b


# --------------------------------------------------------------------------
# B3 — starvation ceiling A/B
# --------------------------------------------------------------------------
def b3(horizon_s=1200.0, seed=31):
    print(RULE)
    print(f"B3 — starvation ceiling, same-seed A/B  (horizon {horizon_s:.0f}s, seed {seed})")
    print(RULE)
    print("  An adversarial controller deliberately never serves J2's north approach.")
    print("  Identical seed, identical scenario, identical controller in both runs —")
    print("  the ONLY difference is enable_safety_validator.")

    results = {}
    for label, enabled in (("A  validator OFF", False), ("B  validator ON", True)):
        env = PsychoFlowEnv(
            scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)),
            seed=seed,
            enable_safety_validator=enabled,
        )
        env.reset()
        victim = victim_lanes_for(env, "J2", "north")
        env.close()

        env = PsychoFlowEnv(
            scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)),
            seed=seed,
            enable_safety_validator=enabled,
        )
        controller = AdversarialController("J2", victim, np.random.default_rng(seed))

        peak = {"victim_wait": 0.0}

        def track(e, info, peak=peak, victim=victim):
            lanes = e.twin.snapshot["junctions"]["J2"]["lanes"]
            for lane_id in victim:
                peak["victim_wait"] = max(
                    peak["victim_wait"], lanes[lane_id]["wait_time_max_single_vehicle"]
                )

        stats = run_episode(env, controller.act, horizon_s=horizon_s, on_step=track)
        env.close()
        stats["victim_wait"] = peak["victim_wait"]
        results[label] = stats
        report(label, stats)

    a, b = results["A  validator OFF"], results["B  validator ON"]
    ceiling_overrides = [r for r in b["overrides"] if r["rule"] == "starvation_ceiling"]
    print(f"\n  {RULE[:60]}")
    print("  CONTRAST — J2 north (the deliberately starved approach)")
    print(f"    max wait, validator OFF : {a['victim_wait']:8.1f}s")
    print(f"    max wait, validator ON  : {b['victim_wait']:8.1f}s")
    print(f"    reduction               : {100 * (1 - b['victim_wait'] / max(1e-9, a['victim_wait'])):8.1f}%")
    print(f"    ceiling overrides in B  : {len(ceiling_overrides)}")
    applied = sum(1 for r in ceiling_overrides if r["outcome"] == "applied")
    deferred = sum(1 for r in ceiling_overrides if r["outcome"] == "deferred_min_green")
    print(f"        applied={applied}  deferred_min_green={deferred}")
    print(f"    ceiling triggers at {STARVATION_CEILING_S:.0f}s; observed max is higher by")
    print(f"    detection granularity + yellow + min-green deference + discharge (§10.1)")
    return results


# --------------------------------------------------------------------------
# B4 — random baseline re-measured with the validator on
# --------------------------------------------------------------------------
def b4():
    print(RULE)
    print("B4 — random-action baseline RE-MEASURED with the validator ON")
    print(RULE)
    print("  §16's recorded baseline predates the validator. The trained agent will")
    print("  run inside the shield, so Checkpoints 1 and 2 need a shielded baseline")
    print("  to compare against.")
    env = PsychoFlowEnv(scenario_config=ScenarioConfig(lane_counts=(4, 3, 2)), seed=7)
    stats = run_episode(env, random_picker(np.random.default_rng(7)))
    env.close()
    report("random masked actions, validator ON (corridor 4/3/2, seed 7)",
           stats, baseline=PHASE3_BASELINE)
    return stats


def main() -> None:
    args = set(sys.argv[1:])
    run_all = not args or "--all" in args
    if run_all or "--measure-scale" in args:
        measure_scale()
        print()
    if run_all or "--b1" in args:
        b1()
        print()
    if run_all or "--b2" in args:
        b2()
        print()
    if run_all or "--b3" in args:
        b3()
        print()
    if run_all or "--b4" in args:
        b4()
        print()


if __name__ == "__main__":
    main()
