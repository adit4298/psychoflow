# NOTES-FOR-INTEGRATION — `hackathon/vision-iot`

Written by the branch that owns `iot/`, `perception/vision_detector.py`,
`perception/vision_source.py`, `perception/incident_detector.py` and `tests/`.

Everything in this file is a change **outside those paths**. None of it has been
made. Each item says what the change is, why the owning branch could not make it,
and what breaks if it is skipped.

---

## 0. What this branch added, in one paragraph

Three things, all additive, none of them on any existing measured path:

| what | where | done-bar |
|---|---|---|
| Local MQTT ingestion (amqtt broker + paho clients) | `iot/` | `python -m tests.test_iot` -> 32/32 |
| Real YOLOv8n detector + `mock`/`detector` factory | `perception/vision_detector.py`, `perception/vision_source.py` | `python -m tests.test_vision_detector` -> 28/28 |
| Incident classification from tracks + flow state | `perception/incident_detector.py` | `python -m perception.incident_detector` -> 34/34 |

**No file under `env/`, `agents/`, `safety/`, `prediction/`, `coordinator/`,
`explainability/`, `twin/` or `backend/` was touched.** `perception/vision_mock.py`
is byte-for-byte unchanged and remains the default vision source.

---

## 1. `twin/digital_twin.py` — the vision source is hardwired to the mock

**This is the largest integration item and the one with a real design decision in
it. Read all of it before wiring anything.**

`DigitalTwin.__init__` currently does `self.vision = VisionMock(seed=seed)`
(`twin/digital_twin.py:73`) and `update()` calls
`self.vision.observe_all(readings)` (`twin/digital_twin.py:154`), where `readings`
is keyed by real SUMO `lane_id`.

Swapping in the factory is one line:

```python
from perception.vision_source import get_vision_source
self.vision = get_vision_source(vision_mode, seed=seed)          # mode="mock" default
```

**But do not do only that.** The two sources are not interchangeable in the way
the shared method signature suggests:

### 1a. A camera does not know a SUMO `lane_id`

`VisionMock.observe(reading)` re-emits the exact `lane_id` it was handed, because
it is reading the same TraCI ground truth. `VisionDetector` sees image-space ROI
polygons, one per **approach**. Its native output (`FrameObservation`) is keyed by
approach, and `observe(reading)` hands that approach's aggregate back under the
caller's `lane_id` with **`lane_fanout: True`** set on the observation.

That flag is the whole point: the per-lane split is **declared, not observed**.
Four lanes on the north approach will each report the approach's full count.

Three options, in order of honesty:

1. **Keep the detector as a parallel feed** (recommended). Leave `§7.1` /
   `lane_sensor` driving the twin, and surface the camera feed as its own panel.
   This is what BUILD_LOG's 2026-09-03 §1 scope boundary already committed to:
   *"a second, parallel perception source for the jury beat, not a replacement for
   §7.1."* Nothing below is needed.
2. **Fan out with a declared split ratio.** Add an explicit per-approach
   `lane_split` to the config and divide the aggregate by it. Still not a
   measurement, but at least the ratio is a stated assumption rather than an
   implied uniform one.
3. **Divide evenly and say nothing.** Do not. It puts an invented per-lane number
   on the dashboard with nothing marking it as invented.

### 1b. Three fields the detector cannot measure, and says so

The detector emits every `LaneReading` key (that is asserted by
`tests.test_vision_detector` D1), but three of them are structural zeros:

| field | detector value | why |
|---|---|---|
| `wait_time_current` | `0.0` | a camera cannot see accumulated waiting time |
| `wait_time_max_single_vehicle` | `0.0` | ditto |
| `starvation_flag` | `False` | derived from the above, so also not measured |

The observation carries **`wait_times_measured: False`** beside them. **Any
consumer that reads those three without checking that flag will read "not
observable from here" as "observed zero"** — and §9.1's starvation bonus and
§9.4's starvation penalty both key off exactly these. That is why the detector
must not drive `PsychoFlowEnv`: it would silently zero the fairness signal.

A fourth field is conditionally unmeasured. `halted_count` comes from the queue
estimate, which needs per-track speeds, which need a previous frame to difference
against. On the **first frame of a clip** — and any frame where the tracker
produced no ids — it falls back to the raw count, so `halted_count ==
vehicle_count`: every vehicle reported as queued. That is the right fallback (a
`0` would claim "no queue", a stronger claim than the truth), but it reads
identically to a genuinely stopped lane, so **`queue_measured: False`** rides
beside it. Check `queue_measured` before trusting `halted_count`, the same way
you check `wait_times_measured`.

### 1c. `type_composition` will always report `auto: 0, ambulance: 0`

Measured, not assumed: COCO's 80 classes give `person / bicycle / car /
motorcycle / bus / truck` and nothing else. There is no auto-rickshaw class and no
ambulance class. `bike <- bicycle+motorcycle`, `car <- car`, `truck <- truck+bus`;
`auto` and `ambulance` are present-and-zero by construction and listed in the
observation's `undetectable_types`.

This is the same class of silent breakage as the `base_vtype` bug: §9.2's
observation features `LF_TYPE_START+0..4` would take two permanently-zero channels.
The detector must not feed those features.

---

## 2. `perception/incident_intake.py` — no home for three detector fields

`incident_detector.detect_incident()` returns
`{type, junction, approach, lane_index, distance_m, distance_confidence, severity,
detected_at, source}`. `Incident` (§7.3) has none of `distance_m`,
`distance_confidence` or `lane_index`.

`incident_detector.to_intake_kwargs(out, lane_id=..., estimated_duration_s=...)`
already bridges this and is asserted end-to-end against the real
`IncidentIntake.report()` (`tests.test_incident_detector` F8). It makes two
mappings explicit that do **not** line up on their own:

- **`type`.** §7.3's enum is `lane_blocked / accident / roadworks`. The detector's
  kinds are `breakdown / accident / major_congestion`. `INTAKE_TYPE_FOR_KIND` folds
  `breakdown` and `major_congestion` onto `lane_blocked`.
- **`lane_id`.** The detector knows an approach and a lane index, never a SUMO lane
  id, so **the caller must supply it**. There is no derivation that would be
  correct.

`distance_m` / `distance_confidence` / `lane_index` are **dropped** by that mapping.
If they should survive into the twin — and for §11.2's responder messaging they
probably should, since "breakdown, north approach, 40m short of the J2 stop line"
is a materially better dispatch than "lane_blocked at J2" — then `Incident` needs
three new optional fields. That is an edit to `perception/incident_intake.py`,
which this branch does not own.

**If you add them, keep `distance_confidence` beside `distance_m`.** It is not
decoration: the detector reports `distance_m = None, distance_confidence = 0.0`
when no geometry was supplied, and a consumer that drops the confidence will
render an approximation from a junction-centre fallback identically to an exact
stop-line measurement.

---

## 3. `backend/sim_runner.py` — where the MQTT feed would attach

Not required for the demo; recorded so the shape is not re-derived.

`iot/subscriber.py` gives `latest_counts()` (keyed by `lane_id`) and
`latest_camera()` (keyed by `junction_id`), both newest-wins over a bounded buffer.
`LaneCountsPayload.to_lane_reading_dict()` emits exactly `LaneReading.to_dict()`'s
key set, so it drops into anything that consumes a §7.1 reading.

**The standing TraCI-single-thread rule still applies.** `iot/subscriber.py` runs
paho's own network thread; it must publish into a lock-protected cache that the
sim thread drains between decision steps, exactly as `ControlState.pending`
already does. **Never call `env.step()` / `reset()` or anything TraCI-touching
from an MQTT callback.**

---

## 4. Security posture of `iot/` — matched to `backend/`'s, and it has the same limit

The broker mirrors `backend/main.py`'s rules deliberately:

- **Loopback by default.** `IoTBroker` refuses a non-loopback bind unless
  `allow_lan=True`; `python -m iot.broker` refuses `--host` without `--allow-lan`
  and prints a warning banner when given both.
- **Everything inbound is validated.** `iot/schema.py`'s `decode()` enforces a
  64KiB cap before parsing, rejects non-UTF-8, non-JSON and non-object bodies,
  **rejects unknown fields rather than ignoring them**, validates every field
  against §7.1/§7.3/§7.4's enums, and refuses a message whose body names a
  different junction or lane than its own topic.
- **Topic levels are allowlisted** (`iot/topics.py::validate_segment`) before any
  id is interpolated, so a `junction_id` of `#` or `J1/+` cannot widen a
  subscription or forge a level.

**The limit is the same as §13's and must be said out loud the same way: the
broker runs anonymous auth with no TLS and is a LOCAL DEMO SURFACE.** Any process
on the machine can publish to any `psychoflow/` topic. The decoder is what makes
that survivable; it is not authentication and does not pretend to be. If this ever
needs to leave the machine, that is a real auth design, not an `--allow-lan` flag.

---

## 4b. `.gitignore` needs three more entries — a stray commit already tripped this

A commit on this branch, `8926dcc` ("iot"), swept **39 unrelated tooling files**
into version control alongside this branch's work: `.agents/skills/**`,
`.claude/skills/**` and `skills-lock.json`, ~13,000 lines of agent-skill markdown
that has nothing to do with PsychoFlow. This branch's own commit untracks them
(`git rm --cached`, files left on disk), but **nothing stops the next `git add -A`
from doing it again**, and `8926dcc` is still in the history that reaches
`hackathon/integration`.

`.gitignore` is outside this branch's ownership, so the lines were not added.
They should be, next to the existing `ECC/` entry, which exists for exactly this
reason (a third-party clone that a `git add -A` would have swallowed):

```gitignore
# Agent-skill tooling — not part of PsychoFlow, same reason as ECC/ above.
.agents/
.claude/skills/
skills-lock.json
```

Note `.claude/settings.local.json` is already ignored; `.claude/skills/` is not,
so ignoring the whole of `.claude/` would be a wider change than needed.

---

## 5. Dependency pins

`ultralytics 8.4.138`, `opencv-python 5.0.0`, `paho-mqtt 2.1.0`, `amqtt 0.12.0`
are installed in the venv and pinned **nowhere**. There is no `requirements.txt`
or `pyproject.toml` at the repo root. A fresh clone cannot reproduce this branch.
Whoever owns dependency manifests should add these four plus the existing stack.

Two amqtt/paho facts worth carrying, both learned by measurement, both documented
in `iot/broker.py`'s docstring:

- `amqtt.broker.Broker` **must be constructed inside a running event loop** — its
  `__init__` calls `asyncio.get_running_loop()`.
- **`plugins={}` does not mean "defaults"** — it removes `AnonymousAuthPlugin` and
  the broker then refuses every client with `Not authorized`. The failure is a
  clean CONNACK rejection with no broker-side error, so it reads as a client bug.

---

## 6. `perception/vision_detector.py` is 968 lines — over the 800-line ceiling

Stated as a deliberate, temporary exception rather than left for a reviewer to
find. The house rule (`coding-style.md`, `code-review.md`) puts a soft
maintainability ceiling at 800 lines and rates an *unexplained* overrun MEDIUM.

The measured split is **997 total = ~590 executable + ~210 docstring + ~65 comment
+ ~132 blank**, so most of the overrun is the "why" documentation this repo runs
on. That does not make it fine — ~590 executable lines in one module is still
large.

**The obvious fix was not taken, on purpose.** The file has three clean seams and
the config layer (`VisionConfigError`, `ApproachROI`, `VisionConfig`, ~200 lines)
would lift straight out into `perception/vision_config.py`. That file is not on
this branch's stated ownership list, so it was not created — the boundary is worth
more during a parallel build than the refactor is, and the split is mechanical and
behaviour-preserving whenever someone with the wider remit wants it.

Suggested split, if you take it:

| new file | contents |
|---|---|
| `perception/vision_config.py` | `VisionConfigError`, `ApproachROI`, `VisionConfig` |
| `perception/vision_geometry.py` (optional) | `foot_point`, `point_in_polygon`, `assign_to_approaches`, `density_for`, `queue_estimate` |
| `perception/vision_detector.py` | label mapping, `ApproachAggregate`, `FrameObservation`, `VisionDetector`, CLI |

`perception/incident_detector.py` at 599 lines (279 executable) is inside the
ceiling and needs nothing.

---

## 7. Docs that should learn these modules exist

Neither `CLAUDE.md` nor `docs/PsychoFlow_Master_Plan.md` mentions `iot/`, MQTT,
`vision_detector` or `incident_detector` — grepped, zero matches. Given how much
this project leans on `CLAUDE.md` as the module-boundary source of truth, someone
with edit rights should fold in a §7.2b (real detector, as an addition to §7.2's
mock) and a §7.7 (MQTT transport) once the hackathon branches reconcile.

The commands worth adding to `CLAUDE.md` §8:

```
python -m tests.test_iot                      # 32 assertions, no SUMO, no camera
python -m tests.test_vision_detector          # 28 assertions, generates its own clip
python -m perception.incident_detector        # 34 assertions, no SUMO
python -m iot.broker                          # local broker, loopback:1883
python -m iot.publisher --with-broker --steps 5
python -m iot.subscriber --seconds 10
python -m perception.vision_detector --make-sample --frames 100   # self-contained
```

---

## 8. Known gaps this branch is NOT claiming to have closed

- **No real camera footage exists.** `sim/media/` is empty but for its README; the
  ROI polygons shipped in `VisionConfig.default()` are **placeholders**, marked
  `is_placeholder=True` in the data itself and warned about on the CLI. They were
  never measured against a real camera. Nobody should read a per-approach number
  off placeholder geometry as real.
- **The done-bar clip is synthetic.** `make_sample_video()` composites a real
  photographed bus (shipped inside the `ultralytics` wheel, no download) so the
  pipeline is exercised with genuine non-zero detections — an earlier version drew
  rectangles, ran 100 clean frames and detected **zero vehicles in every one**,
  which is this repo's named "passes while proving nothing" failure mode. It is
  still one photo on a flat background, not a footage substitute.
- **`emergency_vehicle_flag` is a behavioural heuristic, not a classification.**
  COCO has no ambulance class. It flags a vehicle sustaining several times its
  neighbours' median speed in congested traffic. It is wrong on a motorcycle
  filtering hard and says nothing in free-flowing traffic. It rides with
  `emergency_flag_is_experimental: True` and is forbidden from incrementing
  `type_composition["ambulance"]`. **It must never reach §10's emergency override.**
- **The incident thresholds are reasoned defaults, not measured.**
  `STATIONARY_MIN_S=15`, `ACCIDENT_CLUSTER_RADIUS_PX=90`,
  `SPEED_COLLAPSE_RATIO=0.25`, `CONGESTION_QUEUE_MIN_M=60` are engineering starting
  points chosen to satisfy the hand-scored scenarios, in the sense
  `docs/MIXED_TRAFFIC_RESEARCH.md` §2 uses. `ACCIDENT_CLUSTER_RADIUS_PX` in
  particular is in **pixels** and so is frame-scale dependent — it will need
  retuning against whatever real footage arrives.

---

## 9. Reconciliation against `hackathon/agents-backend`'s §A1/§A2/§A3

Added 2026-09-04. That branch recorded three **assumed** Track A shapes while
Track A did not yet exist in its tree, and asked to "either match them or tell
us to change." This section is that answer. Nothing below changes any code on
this branch — all three are the consuming side's to apply.

**Both branches carry a file named `NOTES-FOR-INTEGRATION.md` and they are
different documents.** They will conflict on merge; the resolution is to keep
both, not to pick one.

### 9.1 §A2 — the factory name does not match. This one breaks at import.

| | assumed by agents-backend | **shipped here** |
|---|---|---|
| name | `make_vision_source` | **`get_vision_source`** |
| signature | `(kind: str, *, seed: int \| None)` | **`(mode: str = "mock", **kwargs)`** |

`get_vision_source` is the name Track A was specified to build, so the code is
right and §A2's assumption is what needs correcting. `detector` additionally
**requires** `source=<video path or camera index>` and raises `ValueError`
without it — there is no default camera.

Compatible as-is: §A2's duck-type is `observe_all(readings) -> dict[str, dict]`,
and both `VisionMock` and `VisionDetector` expose `observe()` **and**
`observe_all()` with that shape.

§A2's "**on `mock` perform no swap at all**" rule still holds and should be kept
— `get_vision_source("mock")` constructs a *fresh* `VisionMock`, so assigning it
onto the twin would reseed it and perturb recorded numbers, which is precisely
what that rule exists to prevent. Call the factory only for `detector`.

### 9.2 §A1 — the detector's emergency key is named differently, and routing it is a decision, not a rename.

The detector emits **`emergency_vehicle_flag`**, never `emergency`. Passed
through unmapped, §A1 falls back to `type_composition["ambulance"] > 0`, and the
detector can never set that (COCO has no ambulance class, see §8) — so a
detector-sourced emergency silently reads as **always false**. That fails
closed, which is the safe direction, but it is silent.

**Do not fix it with a rename.** The flag is a behavioural heuristic that rides
with `emergency_flag_is_experimental: True`, and §8 forbids it reaching §10's
emergency override. Mapping it onto §A1's `emergency` routes an experimental
signal into the priority agent's emergency class. That is a cross-track call for
the user, not an adapter detail — Track A's position is that it should not.

Otherwise §A1 matches: `lane_id`, `vehicle_count`, `type_composition`,
`confidence` and `source` are all emitted in the §7.2 envelope.
`perception/incident_detector.py` returns a **different** shape (§A1's own table
of `{type, junction, approach, lane_index, distance_m, distance_confidence,
severity, detected_at, source}`) — §A1 already states the adapter is
`backend/sim_runner.py`'s job, which is the correct split.

### 9.3 §A3 — topics match exactly; `fresh_s` is the consumer's to compute.

All four topic strings match `iot/topics.py` character for character.

`fresh_s` is **not a field this branch produces** and does not appear anywhere
in `iot/`. It is derivable: `SensorCountsPayload` carries **`sim_time`**, so the
frame's `{"source": ..., "fresh_s": ...}` is `now_sim_time - payload.sim_time`,
computed at the point the frame is built. `source` is on the payload already
(defaults to `"iot_sensor"`).
