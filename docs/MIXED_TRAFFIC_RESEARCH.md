# Mixed-Traffic Driving Model — Research Basis and Reasoned Defaults

**Scope.** This file records the evidence base behind the demo-only mixed-traffic
driving model (`sim/networks/vehicle_types_demo.add.xml`) so a fresh session does
not have to re-derive it. It covers the STEP 1 build (2026-08-31) and the STEP 1
REFINEMENT that follows it.

**Status of this file:** reference material, not a decision log. Decisions go to
`docs/BUILD_LOG.md`; standing rules go to `CLAUDE.md` §8. Read those first.

---

## 0. PROVENANCE — read before citing anything below

The findings in §1 were **supplied to the build session in the 2026-08-31
refinement brief.** They were **not** independently retrieved, and no paper was
read, downloaded, or fetched during the session that wrote this file. That
matters because this project's standing discipline is that a number's provenance
travels with it.

So the honest status of §1 is: **claims sourced from Indian traffic-engineering
literature, relayed second-hand, recorded as directional evidence rather than as
figures this repo has verified.** They are strong enough to justify a design
direction. They are not strong enough to cite as measured results, and no number
in §1 should ever be quoted to a judge as something PsychoFlow measured.

Everything in §2 is a **reasoned default chosen for this project** — an
engineering starting point for tuning. §2 contains **no sourced proportions at
all**, and must never be presented as one.

Everything in §3 **was measured in this repo**, and carries the harness and the
raw-data path that produced it. Only §3 numbers are PsychoFlow's own.

---

> **HARNESS LOCATION (2026-09-02).** Every script named in this file now lives
> in **`sim/mixed_traffic/`**, together with the pinned `measure.rou.xml`, the
> comparison-arm vType tables and the raw JSON under `data/`. They were
> previously in a session-scoped OS temp scratchpad, i.e. one directory-clear
> from unreproducible. See `sim/mixed_traffic/README.md`.

---

## 1. SOURCED FINDINGS (relayed — see §0)

**1.1 Weak lane discipline is a studied phenomenon, not a stereotype.**
Heterogeneous, weak-lane-discipline traffic is formally studied in Indian traffic
engineering literature as a distinct traffic regime with its own models. This is
the justification for modelling it at all, and for §17's honest-boundary wording:
we are approximating a documented regime, not caricaturing one.

**1.2 Overtaking is driven by RELATIVE SPEED to the vehicle ahead.**
Overtaking decisions are governed by the speed differential between the
overtaking and overtaken vehicle — a driver seeks to pass when there is a real
speed gain available, not indiscriminately. This maps directly onto SUMO's
`lcSpeedGain` mechanism, and it is the behavioural claim item 2 of the refinement
is written to test. **See §3.1 — this is where the current tuning was found to be
half-right and half-wrong.**

**1.3 Two-wheeler overtaking splits roughly 62% lane-sharing / 38% lane-change.**
Field studies of motorised two-wheeler overtaking found the majority of
manoeuvres are same-lane "lane sharing" — passing without taking a full lane —
with the minority being discrete lane changes. **This is a checkable target**, and
§3.2 checks the model against it. Treat 62/38 as a target *ratio to aim near*,
not a tolerance to hit exactly: it is relayed, the study's road geometry is not
this corridor's, and a sublane simulation is not a field observation.

**1.4 Two-wheelers accept the smallest time gaps of any class.**
Multiple Indian studies find motorised two-wheelers have the minimum desired
time-gap of any vehicle class. Already reflected in STEP 1's `tau` ordering
(bike lowest, truck highest) — see the hard numerical constraint in §2.4.

**1.5 Red-light running should be a MINORITY behaviour.**
One detailed crash-causation study attributed **72.5%** of contributing factors to
over-speeding against **0.7%** for red-light jumping. Note what this is and is
not: it is a breakdown of *crash contributing factors*, **not** a measured
red-light-jump rate, and it must not be quoted as one. Used only directionally,
and the direction is clear: signal violation is an occasional behaviour of a
minority of drivers, not a universal one. This is the basis for gating the `jm*`
junction-model group to the aggressive tier only (refinement item 4).

**1.6 Driver heterogeneity via `<vTypeDistribution>` is standard SUMO practice.**
Splitting a vehicle class into weighted aggressiveness sub-types under a
`<vTypeDistribution>` is an established SUMO modelling pattern with real
precedent, not an invention of this project. This is the mechanism refinement
item 1 uses. **Verified to work on this repo's exact file layout — see §3.3.**

**1.7 Further reading, parameter table NOT retrieved.**
Mathew & Radhakrishnan calibrated SUMO specifically for Indian heterogeneous
traffic at a Chennai intersection. Cited as the closest published precedent for
what this model attempts. **Its parameter table was not retrieved**, so nothing in
`vehicle_types_demo.add.xml` is derived from it and it must not be described as
"calibrated per Mathew & Radhakrishnan". It is a pointer for anyone who wants to
do this properly later.

---

## 2. REASONED DEFAULTS — chosen for this project, NOT sourced

**Everything in this section is an engineering starting point.** None of these
proportions or values appears in any study. They are stated here explicitly so a
future reader cannot mistake them for findings.

**2.1 Aggressiveness-tier split ratios.** The per-type cautious/normal/aggressive
proportions are a **reasoned starting point for tuning, not a sourced
proportion**:

| type | cautious | normal | aggressive | basis |
|---|---|---|---|---|
| bike | 20% | 45% | 35% | reasoned default |
| auto | 25% | 50% | 25% | reasoned default |
| car | — | 90% | 10% | reasoned default |
| truck | single type | | | unchanged from STEP 1 |
| ambulance | single type | | | unchanged from STEP 1 |

The only part with any grounding is the *ordering* — bike most permissive, auto
next, car a small minority, truck/ambulance none — which follows §1.2/§1.4/§1.5
directionally. The specific percentages do not.

Car deliberately gets a non-zero aggressive minority: "cars do it too, just less
often" is the honest reading of §1.1/§1.5, and setting it to zero would model a
cleaner separation between classes than the literature supports.

**2.2 The per-tier parameter values themselves** (the `lc*`, `jm*`, `tau`, `minGapLat`
numbers in the add-file) are reasoned settings arrived at by measurement against
this corridor, not transcribed from any source. STEP 1's own tuning history is in
`docs/BUILD_LOG.md`'s 2026-08-31 STEP 1 entry.

**2.3 The AGGRESSIVE tier reuses STEP 1's existing table.** STEP 1's measured,
signed-off parameter set becomes the aggressive tier; cautious and normal are
scaled back toward SUMO's stock defaults. This is a deliberate choice so the
refinement cannot make the *most* aggressive behaviour worse than what was
already verified — the tiers only add heterogeneity below the existing ceiling.

**2.4 `tau >= STEP_LENGTH_S` is a NUMERICAL constraint, not a behavioural one.**
Krauss's safe-velocity cannot guarantee a collision-free follow below the
simulation step size (1.0 s). STEP 1 measured **14.55% of vehicles in a
collision** at bike `tau=0.5` / auto `tau=0.6`, versus 0.00% after raising them.
**No tier may set `tau` below 1.0 for a behavioural reason** — §1.4 says
two-wheelers accept the smallest gaps, and this constraint bounds how far that
can be expressed. Realism loses to numerical validity here.

> **RESOLVED by the refinement (2026-08-31).** Every tier now sets `tau >= 1.0`;
> SUMO's load-time warning is gone. Measured cost: `bike_over_car` 36.29% →
> 35.67% (−0.62 pts) with the bike/car ratio *improving* 2.13x → 5.37x, and
> collisions unchanged at 0.00% — see §6.1/§6.2. The original text follows.
>
> **Known discrepancy, flagged not resolved:** STEP 1 ships bike `tau=0.9`, which
> is *below* 1.0 and therefore violates the rule its own BUILD_LOG entry states.
> SUMO warns about it explicitly on load
> (`Value of tau=0.90 in vehicle type 'bike' lower than simulation step size may
> cause collisions`). Measured collision rate is nonetheless **0.00%** (§3.4), so
> it is not currently causing harm. The refinement should raise bike's cautious
> and normal tiers to `tau >= 1.0` and decide deliberately whether the aggressive
> tier keeps 0.9.

---

## 3. MEASURED IN THIS REPO (PsychoFlow's own numbers)

Harness: `measure_overtake.py` (`sim/mixed_traffic/`, standalone raw SUMO — netconvert's
static TLS, 1 s resolution, corridor 4/3/2, seed 7, 1200 s, 1868 vehicles). It
constructs no `PsychoFlowEnv`, loads no checkpoint, and touches no reward or
validator code, so it measures the driving model and cannot perturb Stage 4.
Raw data: `ot_baseline.json`, `ot_demo_step1.json`.

### 3.1 Item 2 — the wish IS speed-gated, and so is the execution

> **CORRECTED 2026-08-31, same session. The conclusion originally recorded in
> this section — "the wish is speed-gated but the EXECUTION is not" — is
> WITHDRAWN. It was a measurement artifact of my own making.** The outcome view
> bucketed each completed pass by the speed delta measured when the following
> relationship OPENED. Median relationship duration is **16–17 s** in the `≤ 0`
> bucket against **6–7 s** in the `> 6` bucket, so a low-delta relationship
> persists while the leader slows, and the pass it eventually produces was
> being attributed to a state that no longer held.
>
> Re-bucketed by the delta **at the pass instant**, the outcome curve is cleanly
> monotonic and **exactly zero passes occur in the `≤ 0` bucket — in BOTH arms**:
>
> | delta (m/s) | STEP 1 demo, per 1k | tiered, per 1k |
> |---|---|---|
> | ≤ 0 | **0.00** | **0.00** |
> | 0–1 | 2.58 | 4.19 |
> | 1–2 | 14.49 | 9.05 |
> | 2–4 | 18.84 | 17.51 |
> | 4–6 | 26.80 | 27.91 |
> | > 6 | **64.04** | **58.19** |
>
> So the model has **never** overtaken a leader that was already at or above the
> follower's desired speed — not in STEP 1, and not now. The `lcAssertive` /
> `lcSublane` / `lcImpatience` "execution is ungated" diagnosis was wrong, and
> the `lcTimeToImpatience` 5 → 20 change made on the strength of it is **not
> validated as a fix, because there was nothing there to fix.** It is retained
> as harmless (the mechanism span widened 23.6× → 27.4×), not as a correction.
>
> **This is the same failure shape this repo has now recorded five times** —
> `j1=3`, `0.885 vs 0.778`, D1 "collapse", the 15/15 emergency matrix, and now
> this: *a measurement that could not have detected the alternative, read as
> though it had.* The defence remains the recorded one — before writing a
> comparative claim, ask which regions the sampling would have had to cover for
> the opposite conclusion to be visible. Here, that region was "the same pass,
> measured at a different instant."
>
> **What remains true and unexplained by the model:** the demo completes roughly
> **twice as many passes as the baseline** (1617 / 1527 vs 818 in 1200 s). A
> live viewer sees overtaking constantly. That is a VOLUME observation, and it
> is real — it is just not the same claim as "overtakes regardless of speed".
>
> The original text is kept below unedited, because the reasoning it contains is
> what the tier design was actually built against.

Two independent views were measured, because either alone passes while proving
nothing.

**(A) MECHANISM** — the lane-change model's own stated wish, read via
`getLaneChangeState(vid, dir)[0] & LCA_SPEEDGAIN`, bucketed by
`delta = desired_speed(follower) − speed(leader)`. Moving followers only (a
follower stopped in a queue is not making an overtaking decision, and pooling
those steps flattens the very profile being measured).

| `delta` (m/s) | baseline rate | **STEP 1 demo rate** |
|---|---|---|
| ≤ 0 | 0.0104 | **0.0126** |
| 0–1 | 0.0187 | 0.0363 |
| 1–2 | 0.0419 | 0.0836 |
| 2–4 | 0.0409 | 0.1875 |
| 4–6 | 0.0927 | 0.2671 |
| > 6 | 0.0678 | **0.2973** |

**The demo profile rises monotonically, 0.0126 → 0.2973, a 23.6× span, and the
`≤ 0` bin — where there is no speed to gain — sits near zero at 1.26%.** So
`lcSpeedGain` is working as §1.2 describes. **The hypothesis that the model
overtakes regardless of lead speed is NOT confirmed at the mechanism level.**

**(B) OUTCOME** — completed passes per 1000 moving follower-steps, bucketed by the
same `delta` measured when the following relationship opened.

| `delta` (m/s) | baseline | **STEP 1 demo** |
|---|---|---|
| ≤ 0 | 7.71 | **23.88** |
| 0–1 | 8.37 | 17.11 |
| 1–2 | 13.16 | 28.24 |
| 2–4 | 14.90 | 29.82 |
| 4–6 | 23.92 | 25.66 |
| > 6 | 13.26 | **31.20** |

**This is flat — 17 to 31 across the whole range, with no trend, and the `≤ 0`
bin (23.88) as high as the `4–6` bin (25.66).** Total passes 1617 demo vs 818
baseline. **This is the behaviour the GUI watch identified**, and it is real.

**Diagnosis — the two views disagree, and the disagreement is the finding.** The
model correctly *wants* to move in proportion to available speed gain, and then
executes essentially every wish it forms, including the low-value ones. The
evidence is the blocked rate: **baseline 0.63–0.71, demo 0.007–0.19.** In the
demo a lane-change wish is almost never refused.

**So the lever is NOT `lcSpeedGain`** — it is already doing its job. The lever is
everything that lets an unmerited wish through:
`lcAssertive` (2.8 / 2.2 — gap acceptance), `lcSublane` (0.2 / 0.3 — near-zero
reluctance to reposition laterally), and above all
`lcImpatience=1.0` with `lcTimeToImpatience=5.0`, which drives the model to
maximum impatience after five seconds and erodes the speed-gain threshold it just
computed. Fixing the balance per tier is the correct response; adding custom
overtaking logic is not, and would be wrong on this evidence.

### 3.2 Item 3 — lane-sharing share, against §1.3's 62/38 target

A pass is opened when follower and leader are in the **same lane** of a
non-internal edge and closed when the follower is ahead. Classified by whether the
follower's lane index ever changed.

| arm | follower | n passes | **sharing (strict)** | change |
|---|---|---|---|---|
| baseline | all | 818 | **0.00%** | 100% |
| demo | bike | 487 | **48.46%** | 51.54% |
| demo | auto | 456 | 37.72% | 62.28% |
| demo | car | 674 | 39.02% | 60.98% |
| demo | all | 1617 | 41.50% | 58.50% |

**Against §1.3's ~62% target, bike sharing is 48.5% — about 13 points low**, i.e.
the model takes a full lane more often than field observation suggests. Same root
cause as §3.1: over-eager execution converts what should be a lateral squeeze into
a discrete lane change.

**Metric-validity note, and how a bug was caught.** The **strict** definition
(neither vehicle left the shared lane) is the one quoted. The first version of the
classifier opened a relationship on *same edge* rather than *same lane*, and the
baseline control promptly reported **22.19% lane sharing under LC2013 — a model
with no sublane, where same-lane passing is impossible by construction.** The
cause was that vehicles in parallel lanes were being counted as a following pair.
The baseline arm exists precisely as this control, and the strict metric now reads
**0.00%** there, which is the correct answer and validates it.

A looser follower-only variant (`mode`: did *the follower* change lane) is also
computed and reads bike 57.91%, closer to 62% — **do not quote it.** It counts
cases where the *leader* moved aside, which LC2013 shows happens 8–25% of the
time even with no sublane at all.

**Also flagged: `car` completes the MOST passes of any type (674, above bike's
487).** Cars out-overtaking two-wheelers inverts §1.2/§1.4's ordering. The tier
split (§2.1, ~10% aggressive cars) is the intended correction.

### 3.3 Nested `<vTypeDistribution>` works on this repo's exact layout

`sim/scenario_generator.py` writes `<vTypeDistribution id="mixed"
vTypes="bike auto car truck">` into every route file, including training's.
**Verified on SUMO 1.27.1** (`probe_dist.py`) that `mixed` resolves correctly when
`bike` is itself a distribution: the tier split reproduced (0.16/0.48/0.36 against
a 0.20/0.45/0.35 target at n=25) and bike's share of the overall mix was preserved
(0.140 against the 0.15 target). **`scenario_generator.py` therefore does not need
to change, and the training route-file writer is untouched.**

### 3.4 Collisions: 0.00% in both arms

`collisions_distinct_vehicles = 0` of 1868 vehicles, baseline and demo alike,
under `--collision.action warn --collision.mingap-factor 0`. Reproduces STEP 1's
recorded result. See §2.4's flagged `tau=0.9` discrepancy — it is not currently
producing collisions.

---

## 4. STRUCTURAL CONSEQUENCE THE TIER SPLIT CREATES — must be handled

**`traci.vehicle.getTypeID()` returns the CONCRETE SUB-TYPE id** (`bike.normal`),
not the distribution id. Measured, not assumed (§3.3's probe).

`perception/lane_sensor.py:84` classifies by
`getTypeID().split("@")[0]` against
`VEHICLE_TYPES = ("bike","auto","car","truck","ambulance")`. A `bike.normal`
therefore falls into the `unknown_types` diagnostic set and contributes **zero** to
`type_composition["bike"]`.

**Why this is load-bearing, not cosmetic:** `type_composition` feeds §9.2's
observation features `LF_TYPE_START+0..4`. Under the demo driving model the
deployed policy would see the bike/auto/car type channels **silently zeroed** —
this repo's named failure mode, a run that works while proving nothing. Nothing
raises.

**Not affected:** `vehicle_count`, `halted_count`, `wait_time_*` and
`starvation_flag` are lane-level TraCI reads and never touch the type id. **The
ambulance stays a single type**, so §10's emergency detection and §9.4's emergency
term are untouched.

**Fix:** resolve a dotted sub-type id back to its base in `read_lane`. Verified
that `split(".")[0]` recovers all three tiers. The change is provably **inert on
the default file** — `sim/networks/vehicle_types.add.xml` contains no dotted ids,
so no training or evaluation path can observe a difference, and no recorded number
moves.

---

## 4b. Weather — the SECOND silent breakage, found by the required audit

Condition 1 of the refinement approval required grepping the **entire repo** for
`getTypeID()` call sites rather than assuming `lane_sensor.py:84` was the only
one. That audit found `perception/lane_sensor.py` is indeed the only
`getTypeID()` site — **but it also surfaced a second module affected the same
way through a different API.**

`perception/weather.py` drove §7.4 entirely through `traci.vehicletype.getTau
("bike")` / `setTau("bike", …)` — addressing vTypes **by id**. Under tiering
`bike` is a distribution id, and measured on SUMO 1.27.1:

- it does **not** raise;
- a read of `getTau("bike")` returned **bike.aggressive's** value (0.900);
- the very next `setTau("bike", 9.99)` landed on **bike.normal**;
- `1 of 3` tiers was reached. The other two kept clear-weather dynamics.

So SUMO resolves a distribution id to **one randomly sampled member**, per call.
§7.4's contract ("behaviour genuinely shifts, not just a label") would have
become roughly one-third true, silently, non-reproducibly, with the twin still
reporting `heavy_rain`.

**Fix:** `WeatherModel._resolve_members()` expands each base to its concrete
`"<base>.<tier>"` members when any exist, and uses the bare id otherwise.
`current_vtype_params()` reads the same resolved ids, so its stated job —
evidence that the write landed — cannot report a tier the write never touched.
**Inert on the default file:** every base resolves to exactly `[base]`.

---

## 5. Standing prohibitions carried forward from STEP 1

- **Never re-measure a checkpoint under the demo driving model and compare the
  result to a recorded number.** Every figure in this project's record — §16's
  baselines, the 4a bake-off, Tier 0's 41.0 s worst wait — was measured on
  `vehicle_types.add.xml` with SUMO's default lane-disciplined LC2013 model. The
  demo model describes a different world.
- `training/train.py` asserts the default vtype file and
  `lateral_resolution is None` before `model.learn()`, so a training run cannot
  pick the demo model up by accident.
- **Say "approximates mixed traffic using SUMO's sublane model", not "lane-free
  driving"** (§17). The road is still lanes with centre-lines and lane-to-lane
  connections; SL2015 adds a continuous lateral position.

---

## 6. REFINEMENT RESULTS (measured, 2026-08-31)

All three arms on ONE route file (corridor 4/3/2, seed 7, 1200 s, 1868
vehicles), so the comparison is internally controlled.

**Harness-trust caveat, stated rather than buried.** STEP 1's recorded baseline
did **not** reproduce bit-for-bit here: 1868 vehicles against its recorded 1870,
and `bike_over_car` 5.61% against its recorded 4.17%. The route file this session
generates is therefore not byte-identical to STEP 1's, and its recorded **37.13%**
demo figure is consequently **not a valid direct comparator**. STEP 1's own
parameter table was reconstructed and re-run here instead, so every number below
is same-route-file.

### 6.1 Condition 2 — bike filtering, and the tau fix

| metric | baseline | STEP 1 (tau 0.9) | STEP 1 (tau 1.0) | **TIERED** |
|---|---|---|---|---|
| `bike_over_car` | 5.61% | 36.29% | 35.67% | **31.73%** |
| `car_over_bike` | 26.47% | 17.05% | 6.64% | 10.81% |
| bike/car ratio | 0.21x | 2.13x | **5.37x** | **2.94x** |
| bike mean advancement | −1.699 | 1.569 | 1.82 | 1.717 |
| collisions | 0.00% | 0.00% | 0.00% | **0.107%** |

**The tau 0.9 → 1.0 fix is not the cause of the drop** — it costs 0.62 points on
`bike_over_car` and *improves* the ratio 2.13x → 5.37x, while silencing SUMO's
load-time warning. The remaining 3.94-point drop is **tiering, working as
designed**: only 35% of bikes are aggressive now, and the per-tier split confirms
the intent rather than a flattening —

| tier | mean advancement | n |
|---|---|---|
| bike.aggressive | **2.500** | 36 |
| bike.normal | 1.462 | 52 |
| bike.cautious | 0.889 | 18 |
| auto.aggressive | 0.333 | 87 |
| auto.normal | −0.157 | 140 |
| car.normal | −0.158 | 530 |
| truck | −0.627 | 142 |

`bike.aggressive` at **2.500** filters *harder* than STEP 1's uniform bike
(1.569). The population rate falls only because two thirds of bikes are now
deliberately calmer. Per-tier speeds separate the same way (bike.aggressive 31.3
km/h vs bike.cautious 27.5).

### 6.2 Collisions — a real 0.00% → 0.107% regression

Two vehicles of 1868, on-lane on `J2_J1_0`, t=252–265, **truck-on-truck** — and
truck's parameters are byte-identical to STEP 1's. Two hypotheses were tested
rather than asserted, per STEP 1's own precedent that guessing here wastes
passes:

- **`actionStepLength=1.5` on truck** — REFUTED. Removing it entirely leaves the
  result unchanged (still 2 vehicles, 0.107%).
- **the bike tau change** — REFUTED. STEP 1's table with only bike tau raised to
  1.0 gives **0.00%**.

At the time this was written it was reported as "attributable to the changed
traffic MIX around unchanged trucks" — **that was a plausible story, not a
confirmed mechanism, and was reported as such.** §6.6 below is the follow-up
that actually pinned it down: the real cause, and the fix.

### 6.3 Condition 3a — mechanism vs outcome

Mechanism span widened, and stays cleanly monotonic:

| delta | STEP 1 | TIERED |
|---|---|---|
| ≤ 0 | 0.0126 | 0.0100 |
| 2–4 | 0.1875 | 0.1820 |
| > 6 | 0.2973 | 0.2737 |
| span | 23.6x | **27.4x** |

The outcome curve **does** track it — but see §3.1's correction: it already did
in STEP 1, once measured at the pass instant. **Condition 3a's hypothesis is
therefore not confirmed, and the `lcTimeToImpatience` change is not validated as
a fix.**

### 6.4 Condition 3b — lane sharing against the 62% target

Strict same-lane classifier (the one whose baseline control reads 0.00% under
LC2013, as it must):

| follower | STEP 1 | **TIERED** | §1.3 target |
|---|---|---|---|
| bike | 48.46% | **51.38%** | ~62% |
| auto | 37.72% | 38.65% | — |
| car | 39.02% | 43.15% | — |

Bike moved **+2.92 points closer** and remains **~10.6 points short**. Per tier:
bike.aggressive 54.59%, bike.cautious 52.54%, bike.normal 47.89%.

### 6.5 Condition 4 — red-running population, computed not asserted

Aggressive-tier share x the route file's own mix
(`MIX_PROBABILITIES` = bike .15 / auto .25 / car .50 / truck .10). Only
aggressive tiers set `jmDriveAfterRedTime >= 0`; every other tier is `-1`.

| type | mix p | aggressive share | expected | observed (n=1868) |
|---|---|---|---|---|
| bike | 0.15 | 0.35 | 5.25% | 5.57% |
| auto | 0.25 | 0.25 | 6.25% | 6.80% |
| car | 0.50 | 0.10 | 5.00% | 4.82% |
| truck | 0.10 | 0.00 | 0.00% | 0.00% |
| **total** | | | **16.50%** | **17.18%** |

### 6.6 Collision follow-up (2026-09-01) — root-caused, not "attributable to"

§6.2 left the 0.00%→0.107% regression as a plausible story. This is the
bounded follow-up that pulled the real collision report and a per-step state
trace for both vehicles, the same way §4/§4b's bugs and §3.1's withdrawal were
run down — evidence first, conclusion second.

**Correction to the original framing.** `--collision.action warn` re-logs an
overlapping pair on every step the overlap persists — it does not report one
event per incident. The two trucks (`f_r_ew.36`, `f_r_ew.28`), both on-lane on
`J2_J1_0`, generated **14 warning events across t=246–265s: ~19 seconds of
continuous physical contact**, not one instantaneous graze. Vehicle count (2)
and location (`J2_J1_0`) were accurate; duration was not previously measured
and is corrected here.

**The trace.** Both vehicles' lane, longitudinal position, speed, lateral
offset (`getLateralLanePosition`), and same-lane neighbors were logged every
step from insertion to collision (`diagnose_collision.py`,
`diagnose_collision2.py`, `diagnose_collision3.py`, all in `sim/mixed_traffic/`). J2_J1's two
lanes are 3.2m wide with centers 3.2m apart, i.e. adjacent lanes share a
boundary with zero nominal gap; each truck is 2.4m wide (half-width 1.2m), so
any vehicle sitting more than 0.4m off its own lane center already extends
into the neighbor lane.

**What the trace shows.** `f_r_ew.28` performs an ordinary SUMO **strategic**
lane change starting at simulation time ≈155s — the router pre-positioning it
for a later turn. This has nothing to do with tiering, heterogeneity, or any
interaction with a neighbor: `sameLaneNear` is empty (no vehicle within 15m)
for the entire maneuver. At truck's lateral capability
(`maxSpeedLat=0.5`, `lcAccelLat=0.6`) the lane change takes roughly 24 seconds
to complete (observed lateral offset climbing from 0 to +1.6m — well past the
0.4m a truck can occupy while staying fully inside one lane). By the time SUMO
reassigns the vehicle to its new lane (t≈179s) its lateral offset is still
**~1.2m off that lane's center** — about a third of the truck's own width
sitting in the neighboring lane — and this residual offset does **not**
continue correcting at any meaningful rate while driving normally afterward:
it is essentially unchanged (still ~1.2m off-center) some 65 seconds later
when the truck reaches the J2 signal queue. With
`--collision.mingap-factor 0` (this project's standing convention, so the
lateral safety margin used during a lane change is reduced to its
mathematical minimum) there is no buffer left to absorb that residual
overlap. When `f_r_ew.36` — a second truck, sitting normally and fully within
its own lane the entire time, no fault of its own — happens to be alongside
at the same longitudinal position, the two bodies physically overlap. SUMO's
own collision report confirms the geometry: `gap=-0.07` (a ~0.07m longitudinal
overlap — essentially a rear-quarter graze, not a T-bone) and `latGap=-0.00`
(zero lateral clearance).

**Why tiering triggered it and STEP 1's arm did not, given byte-identical
truck parameters.** The vulnerability is a property of `truck`'s own lateral
parameters, present in STEP 1's file too. Tiering consumes extra draws from
the shared RNG stream (one per bike/auto/car for tier selection), which shifts
the exact insertion timing and gaps for every vehicle drawn after the first
tiered one — including which trucks happen to be adjacent to which, and when.
This is a timing coincidence exposing a pre-existing hazard, not a behavioral
change in trucks themselves.

**The fix, tested before it was applied to the shipped file.** Raised truck
`maxSpeedLat` 0.5→**0.9** and `lcAccelLat` 0.6→**1.2**, doubling the lateral
recentring rate so the same class of strategic lane change finishes with road
to spare instead of arriving still off-center. `lcMaxSpeedLatStanding` is
untouched at 0.0, so a truck still cannot creep sideways while queued —
queue-front filtering by bikes/autos (the mechanism the whole demo model was
built around) is unaffected. Every other truck parameter is unchanged from
STEP 1.

| | before (shipped, unfixed) | after (fixed) |
|---|---|---|
| collision warn-events (same route/seed) | 14 (~19s contact) | **0** |
| vehicles arrived | 1737 | 1737 (unchanged) |
| emergency-braking warnings | 0 | 1 |

The one emergency-braking warning after the fix is background noise, not a
new problem: STEP 1's own uniform-aggressive arm produces **2** such warnings
on this same route/seed with **0** collisions, so occasional hard-brake events
are normal stochastic behaviour (`sigma`-driven imperfect following) present
in every arm, unrelated to this fix.

**What this fix does not touch, and why that's safe to assert rather than
re-measure.** `truck` is excluded from `FOLLOWER_BASES` in
`measure_overtake.py`, and `maxSpeedLat`/`lcAccelLat` govern only how fast a
vehicle recentres laterally — no bike/auto/car parameter references them, and
truck's own `jm*` red-running parameters are untouched. So §6.1/6.3/6.4/6.5's
figures are unaffected by construction; the collision and throughput numbers
in the table above are the fix's actual, direct re-measurement.

Applied to the shipped `sim/networks/vehicle_types_demo.add.xml`; the `truck`
`vType`'s doc comment records this mechanism and fix in full.

**ELIGIBLE is not OFFENDING:** `jmDriveAfterRedTime` is the window after the
light turns red in which the vehicle will still enter, and `jmIgnoreFoeProb` is
a per-encounter probability, so actual red entries are a fraction of 16.50%.
Observed within-type tier splits land on target (car exactly 0.100/0.900 at
n=897; bike 0.395 aggressive against 0.35 at n=263, sampling noise).
