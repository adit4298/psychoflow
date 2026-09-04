# PsychoFlow Operator Console — Design Spec

Source of truth for the frontend build (Phase 10). Tokens, layout, screens,
and motion below are firm. Everything called out under **Latitude** is the
implementer's call — match a coherent whole, don't transcribe.

The console is used by **one traffic officer in a booth**, on a desktop
monitor, for a whole shift. It is scanned and operated, not read. Calm,
legible, fast. It must not look like a generic AI dashboard.

---

## 1. What changed (design review history — read once)

Three mockup rounds were rejected. The binding corrections:

- **Genuine light theme.** Soft, low-chroma off-white ground — *not* pure
  white, *not* a grey so deep it reads as dark. Dark theme is a real option,
  toggled, but light is the default and the one that must feel finished.
- **Not one long page.** Four separate screens (routes). Overview stays
  uncluttered; detail lives on Junctions and Logs. "Neat, finished layout —
  not features scattered around."
- **Sidebar = vertically-centred icon rail**, like the reference SaaS
  dashboard. Brand mark pinned top, nav icon group centred in the remaining
  height, settings + theme + avatar pinned bottom.
- **Nav hover cue:** resting nav icon sits on white; on hover the tile fills
  a slightly greyer tint (`--surface-hover`) to signal "click selects this";
  the selected item holds a firmer tint (`--surface-active`) + primary-ink
  icon. One cue per state, no glow, no bounce.
- **Manual control is genuinely interactive** — a draggable 60-second cycle
  editor with a live sweeping playhead and hold/skip actions. **Not** a
  static coloured lane-cross or a decorative coloured donut. The interaction
  is the point; colour is secondary.
- **No "Greedy" mode.** Two modes only: **Auto** and **Manual**. We are not
  framing this against another team's system.
- **Assistant is agentic.** A docked chat + voice panel where the officer
  types or speaks a decision ("hold N–S green at J2 for 20 seconds") and it
  executes as if he had clicked it — echoed as a chat turn *and* as an
  action card with Undo.

## 2. Latitude (Opus owns these — decide for contrast and coherence)

- Exact spacing rhythm within the scale, panel-to-panel visual weight, how
  prominent each Overview panel is relative to the others.
- Whether a second typeface earns its place (see §4) — if not, don't force one.
- Precise shadow values, border vs. no-border per surface, icon set choice
  (Lucide is a safe default).
- Micro-copy and empty/loading states.
- Chart/sparkline styling detail, as long as it stays quiet.
- Dark-theme fine-tuning beyond the starting tokens in §4.

Do **not** re-open: the four-screen split, the centred rail, the light-first
default, Auto/Manual-only, the interactive cycle editor, the agentic assistant.

## 3. Stack

- **Vite + React + TypeScript.**
- **Routing:** React Router (4 routes).
- **State:** a small store (Zustand) holding the current frame + a rolling
  window of recent frames.
- **Styling:** plain CSS. One `src/styles/tokens.css` defines every custom
  property in §4; component styles are co-located `*.module.css`. **No
  Tailwind** — do not introduce a utility config or invent token values
  mid-build.
- **Primitives:** run `/pick-ui-library` in this session before hand-rolling
  any dialog, popover, tooltip, dropdown, or slider. Default to **Base UI**
  (unstyled, origin-aware popovers/tooltips the motion spec needs) or Radix
  primitives. The cycle-editor drag is a small custom pointer handler
  (pointer capture, velocity-aware) — a headless slider is acceptable if it
  supports multiple thumbs cleanly.
- **Data:** dev runs against `frontend/fixtures/recorded_session.json`
  (200 frames) replayed at ~2 fps. A `FrameSource` abstraction has two
  implementations — `FixtureSource` (default, no backend) and
  `WebSocketSource` (connects to the §13.2 stream when a URL is supplied).
  The UI never knows which is live.
- **Icons:** Lucide (or equivalent line set). Never a rounded-square icon
  tile stamped above every heading.

## 4. Design tokens

### Colour — light (`:root`)

```css
:root {
  --ground:         #ECEEF3;  /* app background behind panels */
  --surface:        #FFFFFF;  /* panels, rail, cards */
  --surface-2:      #F5F6FA;  /* insets, table stripes, input fills */
  --surface-hover:  #E9EBF1;  /* nav icon hover fill */
  --surface-active: #E1E4EE;  /* nav icon selected fill */
  --line:           #E5E7EF;  /* hairline */
  --line-strong:    #D4D7E2;  /* divider that must read */

  --ink:            #1E202A;  /* primary text — tinted near-black, never #000 */
  --ink-2:          #666B7B;  /* secondary */
  --ink-3:          #9A9EAA;  /* tertiary, axis labels */

  --primary:        #4361EE;  /* actions, focus ring, "now" markers */
  --primary-ink:    #FFFFFF;
  --primary-wash:   #EEF1FD;  /* selected row, assistant card tint */

  /* signal semantics — a separate system from --primary */
  --go:    #2FA84F;  --go-wash:   #E6F4EA;
  --stop:  #E0554C;  --stop-wash: #FBEAE9;
  --wait:  #E39B26;  --wait-wash: #FBF0DD;

  --shadow-panel: 0 1px 2px rgba(24,26,42,.04), 0 10px 30px rgba(24,26,42,.06);
  --shadow-pop:   0 6px 16px rgba(24,26,42,.10), 0 2px 6px rgba(24,26,42,.06);
}
```

### Colour — dark

Redefine **only** these tokens in two guarded blocks so both the OS setting
and the explicit toggle win:

```css
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { /* dark values below */ }
}
:root[data-theme="dark"] { /* same dark values */ }
```

```
--ground:#131419  --surface:#1C1D24  --surface-2:#23242D
--surface-hover:#262832  --surface-active:#2F323E
--line:#2A2C36  --line-strong:#3A3D49
--ink:#ECEDF2  --ink-2:#A2A6B4  --ink-3:#6E7280
--primary:#6E86F7  --primary-wash:#20233A
--go:#43B966  --go-wash:#16281D
--stop:#E8695F  --stop-wash:#2E1E1D
--wait:#E7A94A  --wait-wash:#2C2417
```

`body { background: var(--ground); color: var(--ink); }` — always from tokens.

### Type

- **Figtree** (Google Fonts), weights 400 / 500 / 600 — all UI and body text.
  Fallback: `Figtree, ui-sans-serif, "Segoe UI", Roboto, sans-serif`.
- **IBM Plex Mono**, 400 / 500 — every number that is data: counts, wait
  seconds, timers, coordinates, sim clock, log timestamps.
- A second display face is optional (Latitude). If it doesn't clearly earn
  contrast, ship Figtree alone.

Scale (`font-size / line-height / weight`):

| Token | Value | Use |
|---|---|---|
| `--fs-title` | 22 / 1.25 / 600 | screen title (one per screen) |
| `--fs-h2` | 16 / 1.30 / 600 | panel heading |
| `--fs-body` | 14 / 1.45 / 400 | body |
| `--fs-sm` | 13 / 1.40 / 400 | secondary, table cells |
| `--fs-label` | 11 / 1.35 / 500 | uppercase label, `letter-spacing: .05em` |
| `--fs-data` | 13 mono / 500 | inline data |
| `--fs-data-lg` | 20 mono / 500 | stat-tile value |

### Spacing / radius

Spacing scale (px): **4 8 12 16 20 24 32 40 48**. Lay groups out with
flex/grid + `gap`, not per-element margins.

```
--r-panel: 18px;  --r-control: 12px;  --r-chip: 8px;  --r-pill: 999px;
```

## 5. Layout shell

```
┌──┬───────────────────────────────────────────────┐
│  │  top bar (64px, transparent over --ground)     │
│R ├───────────────────────────────────────────────┤
│A │                                               │
│I │   content — max-width 1240, 24px gutter,       │
│L │   20px grid gap, panels on --surface           │
│  │                                               │
└──┴───────────────────────────────────────────────┘
```

### Rail — 72px, `--surface`, full height

- **Top:** brand mark, 24px from top, 28px square, `--primary`.
- **Centre:** the nav icon group, **vertically centred** in the space between
  brand and footer (`flex: 1; display:flex; flex-direction:column;
  justify-content:center; gap:8px`). Items: Overview, Junctions, Manual,
  Logs. Each item = 40px square, `--r-control`, icon 20px `--ink-2`.
  - `:hover` → `background: var(--surface-hover)`, icon `--ink`.
  - `[aria-current]` → `background: var(--surface-active)`, icon `--primary`.
  - transition: `background 150ms var(--ease)`.
- **Bottom:** theme toggle, settings, operator avatar (28px), 24px from bottom.

### Top bar — 64px, no panel background

- **Left:** screen title (`--fs-title`) + a thin context line
  (`--fs-sm --ink-2`), e.g. `Overview · corridor live`.
- **Right cluster:** the **Auto / Manual** segmented control (see §7.2);
  an incident status chip (`--stop-wash` bg / `--stop` text when an
  `incident_alerts` entry is live, else a calm `--go` "clear" chip);
  a notifications bell; the avatar is in the rail, not here.

### Content grid

Panels: `background: var(--surface)`, `border-radius: var(--r-panel)`,
`box-shadow: var(--shadow-panel)`, padding 20–24px. **No card inside a
card** — the Overview 3-card row sits directly on `--ground`, not nested in
a wrapper panel.

## 6. Data contract (`frontend/fixtures/recorded_session.json`)

Array of 200 frames. Per-frame top-level keys:

| Key | Type | Notes |
|---|---|---|
| `sim_time` | float | sim seconds, ≠ wall time |
| `digital_twin` | obj | `corridor_adjacency`, `junctions {J1,J2,J3}` each `{lanes[], vision, current_phase, lane_count}`, `active_incidents[]`, `weather{state}`, `v2x_messages_recent[]` (≤50 vehicles: `vehicle_id, position{x,y}, speed, heading, timestamp, delay_ms, dropped`) |
| `decision` | obj | `{sim_time, junction_id, phase_selected, score_breakdown{halted_count,wait_time,starvation_bonus}, alternative_scores, reason, lane_id, direction, lane_slot}` |
| `narration` | str | **contains UTF-8 mojibake** (`J2 � Lane 2`) — sanitise/replace `�` with `·` on render |
| `metrics_snapshot` | obj | `{wait_time_variance_across_lanes, mean_wait_max, starvation_events_total, throughput_total}` |
| `agent_activity` | list | `{agent, role, wraps, kind, said, at, step, detail}` — agents: Detection, Vision, Prediction, IncidentPriority, Control, Supervisor |

One lane object: `{lane_id, approach, vehicle_count, halted_count,
type_composition{bike,auto,car,truck,ambulance}, wait_time_current,
wait_time_max_single_vehicle, starvation_flag}`.

**Additive — present only on some frames; UI must not assume them:**

| Key | First frame | Shape / trap |
|---|---|---|
| `incident_alerts` | 40 | `[{type, junction, approach, lane_index, distance_m, distance_confidence, severity, detected_at, source}]`. **`distance_m` is `null`** until vision calibration lands — render "range calibrating", not "0 m". |
| `predictions` | 8 | `{spillover:[{from_junction, to_junction, horizon_s, predicted_queue_delta, confidence}]}` |
| `responder_messages` | 94 | emergency-clearance rows. **Trap:** operator-triggered rows carry `clearance_time_s: 0.0`, `improvement_pct: 100.0`, `served_on_arrival`. **Never** surface 100 % as a headline claim. Prefer `source: "detected"` rows; show clearance as e.g. `≈ 3 s`, and only when `override_fired` and not `served_on_arrival`. |

**Live-only — absent from the fixture entirely.** Panels that consume them
degrade to a quiet "no signal" state, never an error:
- `iot_sensors` — roadside sensor readings.
- `shadow_advisor` — the MARL read-only recommendation. If shown at all,
  label it *"what the alternative architecture would do"* — it is the
  **worse** policy, not a suggestion being ignored. Not required for Phase 10.

## 7. Screens (build in this order)

### 7.1 Overview — route `/`

The screen that must look finished at rest. Three bands on `--ground`:

**Band A — 4 stat tiles** (one row, equal). Each: `--fs-label` caption,
`--fs-data-lg` value, a 24px inline SVG sparkline (last ~30 frames), a
delta chip vs. 5 min ago (`--go`/`--stop` text, no coloured background).
Tiles: **Mean wait** (s) · **Fairness** (wait variance across lanes; lower
better) · **Throughput** (vehicles cleared) · **Starvation events** (count;
`--stop` when > 0).

**Band B — two panels, ~2fr / 1fr:**
- **Signal timeline** (2fr). Three rows J1 / J2 / J3. Each row = a horizontal
  track of phase blocks over the last 90 s of `sim_time`, coloured
  `--go` / `--wait` / `--stop`. A 1px `--primary` "now" edge at the right,
  moving `linear`. Axis ticks −90 / −60 / −30 / now. Hover a block → tooltip
  with that decision's `reason` + `phase_selected`. This replaces any
  lane-cross diagram.
- **Corridor map** (1fr). Small top-down schematic J1→J2→J3 from
  `corridor_adjacency`. Vehicle dots positioned from
  `v2x_messages_recent[].position` (x 26–892, y 30–266 → fit the viewBox;
  coordinates are already in the netconvert-shifted frame, use as-is).
  Ambulance dot `--stop` and slightly larger. Ambient, not the centrepiece.

**Band C — 3 cards on `--ground` (not nested):**
- **Recent decisions.** Scrolling list; row = `HH:MM:SS` (mono) · junction ·
  phase · one-line `reason`. A 6px dot marker coloured by reason class
  (normal / starvation-ceiling / emergency / manual).
- **Live detection — J2.** A 16:9 frame (fixture: a placeholder still; live:
  the camera). YOLO boxes drawn as absolutely-positioned `<div>`s over the
  frame with a class label + confidence. Per-approach counts E / W / N / S
  from `type_composition` sums. A `source` tag: `vision_mock` (fixture) or
  `camera` (live). When an `incident_alerts` entry is live: a top banner —
  `type` · `approach` lane `lane_index` · **distance to stop line** (or
  "range calibrating") · `severity`.
- **Assistant** — see §7.5.

### 7.2 Auto / Manual — the mode control

A segmented control in the top bar, always visible. **Auto** = the RL policy
drives; Manual controls on `/manual` are disabled with a hint. **Manual** =
switching here navigates to `/manual` and shows the "you have control"
banner. Maps to `control_api` `set_mode("auto"|"manual")`.

### 7.3 Manual control — route `/manual`

Only interactive when mode = Manual; otherwise the controls render disabled
with "Switch to Manual to take control."

**Banner:** `You have control — Auto is paused. Changes take effect on the
next 60-second cycle.` + a live mono countdown to the next cycle boundary.

**Per junction (J1 / J2 / J3) — a control card containing:**

1. **The 60-second cycle editor** (the interactive centrepiece):
   - A single horizontal track = one 60 s cycle, full card width, ~44px tall,
     `--r-control`. Divided into ordered segments: `E–W green · amber ·
     N–S green · amber`, filled `--go` / `--wait` / `--go` / `--wait`.
   - **Draggable dividers** between segments (pointer capture; snap to 1 s;
     resist below `MIN_GREEN_S` = 7 s; amber fixed at 4 s, not draggable).
   - A **playhead** — 2px `--primary` vertical line sweeping left→right,
     `linear`, position = `(sim_time mod 60) / 60`.
   - Live readout below in mono: `E–W 32s · amber 4s · N–S 20s · amber 4s`.
2. **Quick actions:** `Hold E–W green` / `Hold N–S green` (toggle pins, held
   until released) → `force_phase` / `clear_override`. `Skip to N–S now`
   → `force_phase` one-shot.
3. **Priority lane** dropdown → `set_lane_bias(lane_id, weight, duration_s)`
   (weight 0.1–10.0, duration 10–900 s — enforce in the UI).

**Card footer:** `Apply to J2` · `Apply to whole corridor` · `Return to Auto`.

Every action is **optimistic**: show the intended result immediately, plus a
small action card in the Assistant log with **Undo**. See §10 for the
cycle-plan endpoint gap.

### 7.4 Junctions — route `/junctions`

Tabbed J1 / J2 / J3. Per junction:
- **Lane table:** approach · vehicle count · halted · type mix (tiny stacked
  bar from `type_composition`) · current wait (mono) · max wait (mono) ·
  starvation flag (`--stop` pip). Row stripe `--surface-2`.
- **Phase history** for this junction (a taller version of the timeline row).
- **Detection feed** for this junction's `vision`.
- **Active incidents** at this junction from `active_incidents`.

This screen is where detail lives so Overview stays calm.

### 7.5 Assistant (Overview card + expandable)

Docked card, `background: var(--primary-wash)` over `--surface` (a 6 % tint,
**not** a purple→blue gradient). Contents:
- A small orb mark, heading `How can I help, officer?`
- **Quick-action pills:** `Hold a phase` · `Set a cycle timer` ·
  `Corridor status` · `Switch to Manual`.
- **Input row:** text field + **mic button**. Text and voice converge on one
  path: `raw text → local intent parse → dispatch`. The mic only produces the
  raw text.
- **STT provider (voice → text):** Sarvam AI STT (free tier, Indian-language
  coverage incl. code-switched Kannada/Hindi/English), with the browser
  `SpeechRecognition` (Web Speech API) as fallback and local Whisper as the
  offline fallback — `--stt {sarvam,webspeech,whisper}`. STT is transcription
  only; it is not in the reasoning path, so a free cloud STT is allowed under
  the same rule that already permits Web Speech API. **No paid/cloud model
  ever does the intent parsing or the decision.**
- **Intent parsing (text → action):** a real local model call — Gemma via
  Ollama — given the `control_api.CONTROL_FUNCTIONS` allowlist + each
  function's arg schema as its system prompt, told to emit
  `{function, args}` JSON or `{"unparsed": true}`. It genuinely interprets
  ("north–south is backing up, give it another 15 seconds" →
  `set_lane_bias` / a cycle-plan intent on the N–S lanes). The ~0.5–3 s it
  takes on CPU is **correct and visible** — a real parse has latency; an
  instant reply means a lookup table, which is the thing being replaced.
  Fixture build: a small rule-based parser over the known verbs stands in.
- On a parsed intent: the phrase appears as a chat turn, then an **action
  card** — e.g. `Hold N–S green · J2 · 20 s` with `Undo` — and the same
  `control_api.dispatch()` call fires that the Manual button would. Fail
  closed: `{"unparsed": true}`, or a name not on the allowlist, →
  `Didn't catch a command — try "hold N–S green at J2 for 20 seconds".`,
  logged, no action. Never guess a junction or a value. `dispatch()` already
  rejects unknown names before arg binding.
- Reconcile lane numbering explicitly: the narrator renders `{lane}` as the
  raw 0-based SUMO index; a spoken "lane 3" must not silently mean slot 3.

### 7.6 Logs — route `/logs`

The full decision log, virtualised. Filters: junction, reason class, time
range. Row expands to show `score_breakdown` and `alternative_scores` as a
small bar list. Two side feeds: **Predictions** (`predictions.spillover` —
`from → to`, `predicted_queue_delta`, `confidence`) and **Responder
messages** (`responder_messages`, following the §6 trap rules).

## 8. Motion spec

`--ease: cubic-bezier(0.23, 1, 0.32, 1)` (enter / hover)
`--ease-io: cubic-bezier(0.77, 0, 0.175, 1)` (on-screen movement)

| Element | Duration | Easing | Properties |
|---|---|---|---|
| Nav icon hover fill | 150ms | `--ease` | `background` |
| Button `:active` | 120ms | `--ease` | `transform: scale(0.97)` |
| Chip / tooltip enter | 160ms | `--ease` | `opacity`, `transform` (origin-aware; skip delay on subsequent hovers) |
| Panel / route enter | 220ms | `--ease` | `opacity 0→1`, `transform: translateY(6px)→0` |
| Drawer / expandable | 260ms | `--ease-io` | `transform` |
| Timeline "now" edge, cycle playhead | — | `linear` | `transform` |
| Live number change (counts, waits) | 180ms | `--ease` | tween value, **no bounce** |

Rules: animate **only** `transform` / `opacity`. Never `scale(0)` — enter
from `scale(0.97) + opacity 0`. No bounce / elastic / spring on UI chrome.
`@media (prefers-reduced-motion: reduce)` → drop panel/route/enter motion to
a plain opacity fade or instant; the "now" edge and playhead still move
(they carry information) but without any easing flourish. Everything meant to
be read is visible at rest — nothing parked at `opacity: 0` waiting on a
scroll observer.

## 9. Anti-patterns (non-negotiable — from build-phase-prompt.md)

- No Inter / Arial / system-default as the actual UI face (fallback stacks OK).
- No purple→blue gradient hero or card.
- No grey text on a coloured fill.
- No pure `#000` / neutral grey — every neutral is tinted (tokens already are).
- No card nested inside a card.
- No bounce / elastic easing.
- No rounded-square icon tile stamped above every heading.
- Do not invent colour or spacing values outside §4. If something's missing,
  add it to `tokens.css` deliberately, not inline.

## 10. Accessibility

- Visible keyboard focus everywhere: `outline: 2px solid var(--primary);
  outline-offset: 2px`.
- Cycle-editor dividers are keyboard-operable (arrow keys ±1 s, respect the
  min-green clamp) with `role="slider"` + `aria-valuenow/min/max`.
- Signal state is never colour-only — pair with a label or glyph (`▲ go`,
  `● stop`).
- Target contrast ≥ 4.5:1 for text on its surface in both themes.
- `prefers-reduced-motion` honoured (§8).

## 11. Out of scope / known stubs (do not block on these)

- **Cycle-plan endpoint gap.** `control_api` has `force_phase`,
  `clear_override`, `set_lane_bias`, `set_mode` — but **no** "set 60-second
  cycle plan" function yet. For this build the cycle editor emits a
  `CyclePlanIntent` object and shows the optimistic result; actually wiring
  it is a later additive backend call + integration pass. Build the control
  fully; stub only the send.
- `iot_sensors` / `shadow_advisor` panels: build the empty state, wire the
  data later.
- Live detection source + local YOLO + local voice intent: the fixture build
  uses the placeholder still and a rule-based parser. At integration the
  detection panel is fed by a **public traffic-camera dataset clip**
  (UA-DETRAC, or GRAM-RTM "Urban1" — self-recorded footage is barred by
  event rules) run through local YOLO, with hand-drawn per-approach regions
  supplying the lane counts and a marked stop-line supplying the
  metres-per-pixel calibration that fills `distance_m`. The `FrameSource` /
  `SpeechRecognition` seams must exist so the live swap is drop-in.
- No auth. The console is a local demo surface, loopback only.
