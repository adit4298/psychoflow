# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + Vite. Inferred from existing repo evidence — `backend/main.py`'s CORS
allowlist assumes a Vite dev server, and CLAUDE.md references a component
file `IntersectionView.jsx` — and confirmed by the user during init.

## Users

**Primary:** a city traffic-operations engineer/operator responsible for a
signalized arterial corridor, working from a live operations dashboard during
their shift. Their job: monitor real-time corridor state, understand why the
system chose a given signal phase, intervene when needed (manual override,
lane bias, forced emergency clearance, incident reporting), and trust that a
safety floor holds even when the learned policy is wrong.

**Secondary:** a dispatch/emergency-response coordinator, who needs a clear
read on whether an ambulance's corridor has been cleared and how much time
that saved, as decision support — not integrated into a real CAD/dispatch
system.

## Product Purpose

PsychoFlow is an adaptive traffic-signal control system for a linear
multi-junction corridor. It replaces fixed-timing or hand-tuned actuated
signals with a policy that continuously re-times phases based on live queue
state, under a hard, rule-based safety floor that no learned behavior can
override: no lane is ever allowed to wait past a defined ceiling, and an
approaching emergency vehicle preempts everything else automatically.
Success means: lower average and worst-case wait than a naive controller,
provably fair service (no lane starves), demonstrably faster emergency-vehicle
clearance, and an operator who can ask "why did you do that?" and get a real,
specific answer rather than a black box.

## Positioning

The differentiating claim is fairness as a **structural guarantee**, not a
training-time hope: a safety/policy validator sits between every proposed
signal decision and the physical signal, and it is the only thing that can
override the learned policy. A neighboring "AI traffic light" pitch that just
wraps a black-box model around a signal controller cannot truthfully make this
claim — there is no independent, rule-based floor beneath its own model, so if
the model is wrong nothing catches it. PsychoFlow also exposes its reasoning
at every decision (a structured decision log, plain-language narration, and
"why" queries), and lets an operator watch a naive greedy controller and
PsychoFlow run side by side on identical traffic, so the fairness claim is
falsifiable on screen rather than asserted in a pitch.

## Operating Context

- Runs against a live traffic simulation of a 3-junction linear corridor
  (J1→J2→J3), with independently configurable lane counts (2/3/4) per
  junction.
- An operator watches a real-time dashboard (state pushed every simulation
  step): a digital-twin view of the corridor, a metrics panel, a technical
  decision log with narration, and controls to switch modes (auto/manual),
  bias a lane, force/clear a phase override, inject an incident, force an
  emergency clearance, and swap topology or baseline controller.
- Voice commands are a secondary input surface: local intent parsing (no
  cloud/paid model in the runtime path) maps spoken commands onto the same
  control API the dashboard buttons use; an unparseable command fails closed
  rather than guessing.
- The system distinguishes and surfaces, to the operator, what is rule-based,
  what is learned (RL), and what is simulated-input vs. real-input — so trust
  in any given signal is calibrated to its actual source.

## Capabilities and Constraints

- **Control policy:** primarily a trained single-agent PPO policy (a
  multi-agent, graph-attention-coordinated alternative is built and
  benchmarked but is not the deployed driver); a rule-based fairness-first
  controller is the manual-mode fallback and comparison baseline.
- **Safety floor (locked, non-negotiable):** an independent rule-based
  validator sits between every proposed action and the road. It enforces a
  hard starvation ceiling and an unconditional emergency-vehicle override;
  nothing in the system can disable this validator on a live/operator-facing
  path.
- **Vision input:** works from a simulated mock feed by default (re-emits
  ground-truth counts through a camera-shaped data contract), with an
  optional real local object-detection feed behind an explicit
  `--vision-source` flag — both share one interface, so downstream code is
  agnostic to which is live. A real detector reads recorded footage, not the
  live corridor; it does not drive the control loop.
- **V2X-style connected-vehicle data:** simulated/shaped (noise, drop-rate,
  jitter applied to ground truth) — not a full ITS/V2X protocol stack.
- **Incident & prediction:** incidents are structured reports (not free
  text); the system forecasts downstream spillover risk and incident delay
  impact ahead of it becoming visibly congested.
- **Corridor is fixed to a 3-junction linear topology** — not a general
  road-network product; a merge/diverge ("Y") topology is explicitly out of
  current scope.
- **No paid or cloud inference in the runtime control/voice path** — a hard
  budget/architecture constraint, not a style preference. Speech-to-text may
  use a browser API that is not itself local, but intent parsing and every
  control decision are.
- **Undecided:** exact target corridor size/scale for a first real
  deployment; whether/how emergency-clearance messaging integrates with a
  real dispatch (CAD) system (currently decision-support only, not
  integrated).

## Brand Commitments

The name "PsychoFlow" is the only established brand fact. No logo, color
system, or typography commitment exists yet.

## Evidence on Hand

Extensive internal benchmark data comparing the deployed policy against a
rule-based baseline and a naive greedy controller on measured fairness/wait/
throughput metrics — real measured data from the project's own simulation
runs. No external testimonials, press, pilot deployments, or customer case
studies exist; future work must not fabricate any of these.

## Product Principles

1. **The safety floor is structural, not statistical.** Any claim about
   fairness or emergency response has to be true because a rule enforces it,
   not because the model usually behaves.
2. **Every source of truth is labeled.** Rule-based vs. learned, real vs.
   simulated input, measured vs. estimated — the operator, and this document,
   should never blur these.
3. **Falsifiable over persuasive.** Prefer a side-by-side, on-screen
   comparison an operator can watch fail or succeed over an assertion about
   how good the system is.
4. **Explain every decision, on demand, in the system's own terms** — not a
   generic AI-explainability bolt-on, but the actual score or rule that
   produced this specific action.
5. **No paid/cloud dependency in the control path.** This is load-bearing for
   the product's deployability story, not incidental.

## Accessibility & Inclusion

No product-specific accessibility requirement has been established yet;
treat general WCAG-level web accessibility as the default floor for the
operator dashboard until stated otherwise.
