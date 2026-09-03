# `sim/media/` — real camera footage for the vision detector (§7.2, reopened §2)

Video files are **gitignored** (large binaries). This README is the tracked record
of what belongs here and why. Nothing in this directory is on any measured path —
no checkpoint, no training run and no recorded number depends on it.

## Status

**EMPTY — no footage has been added yet.** A human has to download it; see below.

## What to put here

Two clips is enough. Name them `traffic_01.mp4`, `traffic_02.mp4`.

### Hard requirement: a FIXED camera

The jury ask is an agent reporting **lane + distance**. Both are only recoverable
from a static viewpoint:

- **Lane** is assigned by testing a detection's foot-point against hand-drawn lane
  polygons in image space. Those polygons are only valid while the camera does not
  move.
- **Distance** needs a homography from four image points to four ground points
  (a lane width, a stop-line, a dash spacing). The homography is invalidated by any
  pan, zoom or handheld drift.

So: **elevated/overhead, tripod-static, no pan or zoom.** A moving or handheld shot
makes both outputs meaningless and no amount of detector quality recovers it.
CCTV/traffic-cam framing is ideal; dashcam and drone-orbit footage are not.

### Also wanted

- 720p or 1080p (yolov8n letterboxes to 640 anyway — 4K just costs decode time)
- 20-60s is plenty; loop it for the demo
- Visible queueing at a signal — the demo beat is queue-front behaviour, so footage
  of free-flowing traffic shows nothing
- Mixed traffic (two-wheelers + cars + autos) if you can get it, to match the
  corridor's vehicle mix

## Where to get it (free, no login)

- **Pexels** — <https://www.pexels.com/search/videos/traffic/> — free licence, no
  attribution required, direct MP4 download. Filter to fixed-camera intersection
  shots. Best first stop.
- **Pixabay** — <https://pixabay.com/videos/search/traffic/> — same deal.
- **Videvo** / **Coverr** — smaller libraries, same licence model.

For research-grade traffic surveillance footage (registration required, heavier):
**UA-DETRAC**, **MIO-TCD**, **VisDrone**. Overkill for a 45h build.

Do **not** scrape YouTube for demo footage — the licence is wrong for a public demo.

## Known gap: COCO classes do not cover this project's vehicle taxonomy

`yolov8n.pt` is COCO-pretrained. Measured on the downloaded weights, the relevant
classes are exactly:

    0 person   1 bicycle   2 car   3 motorcycle   5 bus   7 truck

The project's five types (§7.1 `type_composition`) are `bike / auto / car / truck /
ambulance`. Mapping:

| project type | COCO source            | status |
|---|---|---|
| `bike`      | `bicycle` + `motorcycle` | OK |
| `car`       | `car`                    | OK |
| `truck`     | `truck` + `bus`          | OK |
| `auto`      | — **no COCO class**      | **GAP** (auto-rickshaw) |
| `ambulance` | — **no COCO class**      | **GAP** |

`auto` and `ambulance` cannot come from stock YOLOv8 and need either a custom-trained
model or an explicit heuristic. Decide which before wiring the detector to
`type_composition`, and whichever way it goes, say so out loud per §17 — an
auto-rickshaw silently counted as a `car` is a wrong number on the dashboard, not a
rounding error.
