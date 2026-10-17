# Marshal on Three.js: a browser replacement for CARLA

For Faris. This explains exactly what Marshal uses CARLA for, so the same demo can run in a Three.js world
without a GPU VM. Everything below comes from the code in this repo; file references are at the end.

**Goal.** A Three.js "plant" that can stand in for CARLA behind Marshal's existing driving-system boundary.
Marshal and Gemini stay as they are, and every flow in the pitch video keeps working:

- the gate check
- the lamp check
- route choice
- Gemini parking
- the yard re-plan
- a blocked car asking for help

**Not the goal.** Photorealism or physics fidelity. Gemini has to be able to *read* the frames (car colour,
lamps on or off, a box and a person in the lane), and the cars have to drive believably at walking pace.
That is the bar.

---

## 1. How Marshal talks to a simulator (the contract)

Marshal never steers a car. It sends **missions**; the driving system drives them and reports back. This
boundary is `DrivingSystem` in `src/marshal_ai/driving/api.py`, with the messages in
`src/marshal_ai/driving/messages.py`. CARLA is one implementation (`carla_backend.py`); a laptop
stand-in is another (`fake.py`). Three.js will be a third.

### Marshal → simulator

| message | fields | meaning |
|---|---|---|
| `mission.go` | `mission_id, vin, waypoints[], max_speed_kmh=10, routine?, routine_steps?` | Drive through these named map points in order. If `routine` (e.g. `"R-12"`) is set, perform the test-track routine on reaching `test_track`. |
| `mission.hold` | `vin, duration_s?, reason` | Stop and stay stopped (until `mission.resume`, or for `duration_s`). |
| `mission.resume` | `vin` | Carry on after a hold. |
| `assist.choose` | `case_id, vin, option, chosen_by` | A human picked one of the options the car offered when it asked for help. |

### Simulator → Marshal

| event | fields | when |
|---|---|---|
| `status` | `vin, location, x, y, speed_kmh, mission_id, progress, held, stopped_for_assist` | Every car, **once per simulated second**. `location` is the last map point passed. |
| `mission.done` | `mission_id, vin, location` | The car reached the last waypoint. |
| `track.step` | `vin, mission_id, routine, step, name, telemetry{}` | One routine step finished on the test track, with what the car's electronics report. |
| `assist.request` | `case_id, vin, location, reason, options[], clip_uri` | The car stopped because something is in the lane, and asks a human what to do. |

There is deliberately **no** message for steering, braking or overriding the car's own safety stops.

### Extra calls the demo scripts and flows make on the backend

On top of the protocol above, the CARLA backend exposes some methods the flows, dashboard and demo
scripts call. The Three.js backend needs the same ones (names from `carla_backend.py`):

| method | used for |
|---|---|
| `spawn(vin, at="line_end", colour="r,g,b", behind_m=0)` | Put a car on the map; `behind_m` queues cars back along the point's heading. |
| `despawn(vin)` | Remove a car and its cameras. |
| `faults[vin] = {"headlights"}` | The "dead bulb": the car **reports** the lamp ON in telemetry but it stays **dark** on camera. |
| `repair(vin, defect_types)` | Rework fixes the car (clears the fault). |
| `transform(point)` / `reference_xy(point)` / `position_xy(vin)` | Where a map point or a car is. |
| `assist_media(case_id)` | The frames from before the stop: Gemini reads them, and the dashboard shows them. |
| `raise_assist(vin, case_id, reason, options)` | Force a help request (scripted scenes). |
| `passes[vin]` | km/h at the moment the car crossed the gate (the "drive-through at 5 km/h" claim). |
| `assist_outcomes[case_id]` | What the car did with the chosen option (waited N s, passed at 3 km/h with X m clearance, contacts). |
| `parking_log[vin]` | Each Gemini parking session (decisions, outcome, final pose). |
| `store_artifact(uri, bytes)` | Upload a frame to Cloud Storage (Marshal already handles this). |
| `realtime_ratio()` | Simulated seconds per real second (only reported). |

There are also two camera objects, `GateCamera.capture(vin, views)` and `TrackCamera.clip/sequence(vin, step, name)`;
see §5.

---

## 2. Proposed architecture

```
 ┌──────────────── Marshal (Python, unchanged) ────────────────┐
 │ flows (gate, lamp, route, parking, yard, assist) · Gemini    │
 │ MissionClient ──► DrivingSystem protocol                     │
 └──────────────────────────────┬──────────────────────────────┘
                                │  in-process
                 ┌──────────────▼──────────────┐
                 │ threejs_backend.py (Python) │  ← we write this (mirror of carla_backend.py)
                 │ WebSocket server            │
                 └──────────────┬──────────────┘
                                │  JSON + base64 JPEG over WebSocket
                 ┌──────────────▼──────────────┐
                 │ Three.js plant (browser)    │  ← Faris builds this
                 │ world · cars · cameras ·    │
                 │ driving · lamps · props     │
                 └─────────────────────────────┘
```

**Lock-step, like CARLA's synchronous mode.** Python owns the clock. Each tick it sends `{"op": "tick", "dt": 0.05}`.
The browser advances the world by exactly 0.05 simulated seconds and replies with the state of every car.
While Gemini is thinking, Python just doesn't tick, so the world pauses and no car moves unobserved.
Marshal relies on that pause: the gate photographs a car and gives it its next mission while the world is
stopped.

**Where it runs.** Either:

- **A browser tab on the presenter's laptop** (simplest, uses its GPU); or
- **Headless Chrome on a CPU VM** (`--use-angle=swiftshader`). Rendering will be slow, but lock-step makes
  speed a matter of patience rather than correctness.

Recommendation: build for the laptop tab first.

**Who writes what.**

| Faris (Three.js) | Rey (Python) |
|---|---|
| World, cars, lamps, props, people | `threejs_backend.py`: the protocol, cameras, scenario helpers |
| Driving: lane following, queuing, stopping for objects, lane changes, pulling into slots | Translating missions to sim commands; status / done / track.step / assist events |
| Cameras rendering to JPEG on request | Parking: Python runs Gemini's session and sends a control per tick |
| A demo view (chase, top-down, overlays) for filming | Dashboard, Gemini, rules: already done |

### Wire protocol (proposal; we can adjust together)

Python → browser:

```jsonc
{"op":"load_world"}                                       // build the plant (§3)
{"op":"spawn","vin":"VIN-P1","at":"line_end","colour":[0,80,200],"behind_m":9}
{"op":"despawn","vin":"VIN-P1"}
{"op":"route","vin":"VIN-P1","waypoints":["inspection_gate","test_track"],"max_speed_kmh":10,
  "precise":["inspection_gate"], "drive_through":["inspection_gate"]}
{"op":"brake","vin":"VIN-P1","on":true}                   // hold / assist / routine step
{"op":"lights","vin":"VIN-P1","state":["LowBeam"]}        // routine steps; [] = all off
{"op":"control","vin":"VIN-P1","speed_mps":-0.6,"wheel":0.5,"brake":false} // parking & manoeuvres, per tick
{"op":"set_pose","vin":"VIN-P1","x":-30.75,"y":-68,"yaw":90}             // "square up" in a slot
{"op":"lane_change","vin":"VIN-P1","side":"left","run_m":7,"back_after_m":6,"kmh":3}   // pass the box
{"op":"spawn_prop","id":"box-1","kind":"box","x":..,"y":..,"yaw":..}     // also "walker", "truck", "cone"...
{"op":"remove","id":"box-1"}
{"op":"sun","altitude_deg":15}
{"op":"capture","cam":"gate.front","w":1280,"h":720}      // → {"jpeg":"<base64>"}
{"op":"tick","dt":0.05}                                   // → state of every car and actor
```

Browser → Python (reply to `tick`):

```jsonc
{"t": 123.45,
 "cars":[{"vin":"VIN-P1","x":32.1,"y":5.25,"yaw":0.3,"speed_mps":1.39,
          "route_done":false,"arrived":"inspection_gate","blocked_by":null,
          "contacts":[]}],
 "actors":[{"id":"box-1","kind":"box","x":..,"y":..,"yaw":..,"len":0.6,"wid":0.6,"h":0.6,"moving":false}]}
```

Keep coordinates in **CARLA's frame** on the wire (§3.1), so the existing map file, parking geometry and
offsets all work unchanged. Convert only inside Three.js.

---

## 3. The world: the plant

Our CARLA world is not a city map. It is a custom plant built from OpenDRIVE (`scripts/plant_xodr.py`):
a one-way loop shaped like a car plant, with a connector across the middle (lane C). All numbers below
are in metres.

### 3.1 Coordinate frame (important)

CARLA is **left-handed, z up**: x forward/east, y to the **right** (south on a top-down view), yaw in
degrees, positive yaw turns from +x towards +y (clockwise seen from above).

Three.js is right-handed, y up. To show the same top-down picture:

```js
// CARLA (x, y, z, yawDeg)  ->  Three.js
mesh.position.set(x, z, y);
mesh.rotation.y = -THREE.MathUtils.degToRad(yawDeg);
// a CARLA forward vector for yaw θ is (cos θ, sin θ) in (x, y)  ->  (cos θ, 0, sin θ) in Three.js (x, z)
// right vector for yaw θ is the forward vector of θ + 90°
```

### 3.2 Road geometry

The loop runs counter-clockwise on a top-down view: east along the bottom, then up, west along the top,
and down. Roads in order (`ROADS` in `plant_xodr.py`):

| road | shape | start (ref line) | heading | length |
|---|---|---|---|---|
| 1 | straight ("south straight") | (0, 0) | 0° (+x) | 120 |
| 2 | quarter bend left, radius 22 | (120, 0) | 0° → −90° | 34.6 |
| 3 | straight ("east", rework bays) | (142, −22) | −90° (−y) | 60 |
| 4 | quarter bend, radius 22 | (142, −82) | −90° → 180° | 34.6 |
| 5 | straight ("north", finishing) | (120, −104) | 180° (−x) | 120 |
| 6 | quarter bend, radius 22 | (0, −104) | 180° → 90° | 34.6 |
| 7 | straight ("west", the yard) | (−22, −82) | 90° (+y) | 60 |
| 8 | quarter bend, radius 22 | (−22, −22) | 90° → 0° | 34.6 |

**Lane C** is a two-lane connector that leaves road 1 at s = 102 with a 10 m-radius left turn. It runs
straight up the middle along x ≈ 112 (lane centre x = 117.25 heading −90°) and joins road 5 the same
way, with a 4 m junction at each end. It's the other way to the yard, skipping the finishing corner.
Exact geometry: `xodr()` in `plant_xodr.py`.

**Lane layout**, the same on every loop road, measured from the reference line to its right (the outside
of the loop):

| lane | width | type | use |
|---|---|---|---|
| −1 | 3.5 | driving (broken line) | inner driving lane |
| −2 | 3.5 | driving (solid edge) | **the lane cars drive in**; most points are on it |
| −3 | 3.5 | shoulder (road 7: parking) | rework bays (road 3), yard slots Y1–Y8 (road 7) |
| −4 | 3.0 | walkway | |
| −5 | 5.5 | apron (road 7: a second parking row for the lot) | |
| −6 | 8.0 | apron to the perimeter fence | |
| −7 | 40 | paving beyond the fence | |
| +1 (left) | 20 | inner apron (shoulder, flat) | inside the loop |

So on road 1 (heading +x) lane −2's centre is at y = +5.25 and the shoulder's centre at y = +8.75. The
middle of the loop is paved (road 9, a filler) and holds a storage compound of parked cars.

### 3.3 Named points (the plant map)

Missions only ever name points. These are the CARLA poses from `src/marshal_ai/plant/plant_map_world.yaml`;
use them as-is. `pull_over` means the spot is off the driving lanes, on the shoulder: a car drives to the
lane beside it and then pulls over (§4.3).

| point | zone | x | y | yaw | notes |
|---|---|---|---|---|---|
| line_end | line_end | 8.0 | 5.25 | 0 | cars queue here, 9 m apart behind it |
| buffer_zone_b | buffer | 18.0 | 5.25 | 0 | only connects to line_end |
| inspection_gate | gate | 32.0 | 5.25 | 0 | 4-camera drive-through gate, 5 km/h |
| test_track | test track | 62.0 | 5.25 | 0 | routine R-12 is performed here |
| report_point | test track | 100.0 | 5.25 | 0 | decision point: corner or lane C |
| finishing_corner.p1 | corner | 146.5 | −88.36 | −103.5 | blind corner (hazards staged here) |
| finishing_corner.p2 | corner | 139.27 | −101.27 | −135 | |
| finishing_corner.p3 | corner | 126.36 | −108.5 | −166.5 | |
| finishing | finishing | 90.0 | −109.25 | 180 | |
| lane_c | finishing | 117.25 | −50.0 | −90 | the middle of lane C |
| rework.mechanical | rework | 150.75 | −30.0 | −90 | pull_over |
| rework.electrical | rework | 150.75 | −40.0 | −90 | pull_over (lamp faults go here) |
| rework.body | rework | 150.75 | −50.0 | −90 | pull_over |
| rework.paint | rework | 150.75 | −60.0 | −90 | pull_over |
| production_control | rework | 150.75 | −72.0 | −90 | pull_over (wrong colour goes here) |
| yard.entry | yard | −27.25 | −80.0 | 90 | |
| yard.Y1 … Y8 | yard | −30.75 | −68.0 … −29.5 | 90 | pull_over, 5.5 m apart (Y1 −68, Y2 −62.5, Y3 −57, Y4 −51.5, Y5 −46, Y6 −40.5, Y7 −35, Y8 −29.5) |
| loading_bay | yard | −27.25 | −26.0 | 90 | trucks load here |

**Drivable segments** (`segments:` in the same file; both ways unless one-way):

```
line_end–inspection_gate, line_end–buffer_zone_b, inspection_gate–test_track, test_track–report_point,
report_point–corner.p1–p2–p3–finishing, report_point–lane_c–finishing,
report_point→rework.paint (one way), rework.paint–body–electrical–mechanical–production_control,
rework.paint→inspection_gate (one way: repaired cars are re-inspected),
finishing–yard.entry, yard.entry–Y1…Y8, yard.entry–loading_bay
```

The simulator needs real drivable lane polylines between these points. The easiest approach is to
generate lane-centre polylines from the road table above, sampled every 1 m, and route over that graph.
Marshal checks missions against the segment graph before sending them, so the sim only ever gets valid
waypoint sequences.

### 3.4 Dressing (so frames look like a plant and Gemini has context)

From `scripts/dress_plant.py` (`dress_world`). Nothing may stand on a lane a car drives or parks on, and
lane C's corridor stays clear.

- **Perimeter fence:** street barriers every 4 m on lane −6, 3.2 m out.
- **Yard:** a row of parked finished cars on road 7 lane −5 every 6 m, and two rows on the inner apron
  opposite (±4 m), in the plant colours.
- **Loading area:** articulated trucks (European HGV) on road 5's inner apron every 13 m, 8 m in.
- **Storage compound** in the middle: rows of parked cars every 9 m, four rows.
- **Line end:** stacked boxes and two barrels.
- **Gate:** two calibration boards either side of the mark (±4 m), a warning board 10 m before it, and cones
  along the shoulder.
- **Each rework bay:** two barrels, a box, an iron plank, a "construction" sign on the walkway beside it.
- **Plant colours** (RGB): blue `0,80,200`, white `230,230,230`, grey `120,120,120`, black `20,20,20`,
  red `200,20,20`.

Low-poly glTF is fine: Kenney's car and city kits, Quaternius, or CC0 models from Sketchfab or Poly Pizza.
What matters is that a car's colour, lamps and outline read clearly at 1280×720.

---

## 4. Actors and driving behaviour

### 4.1 The plant's car

| property | value | why it matters |
|---|---|---|
| model | Lincoln MKZ 2020 in CARLA (any sedan is fine) | |
| length × width | **4.9 × 1.9 m** | parking geometry assumes it (`parking.py`) |
| wheelbase | **2.85 m** | kinematic bicycle model |
| max front-wheel angle used for parking | 30° | `STEER_DEG` |
| colour | set at spawn from the build sheet (blue; red for the wrong-colour car) | the gate check compares it |
| lights | low beams (front, pair), brake (rear bar), reverse (rear, white), left/right blinker, position | the lamp check reads them from photos |

**Lights must be visibly lit in the render:** emissive material, a small point or spot light, and bloom if
you can afford it. Off must look clearly off. Gemini checks "both headlights lit" from a front photo and
"the full width of the rear light bar is lit" from a rear one. CARLA's asset has one continuous rear bar,
which is why the prompt is worded that way. A modelled car with separate tail lamps is fine; tell us and
we'll adjust the expected-on-camera text in `routines.py`.

**A fault** (`faults[vin] = {"headlights"}`) means: when the routine turns the headlights on, **don't light
them**. The car still *reports* `headlights: "ON"`. That disagreement is the whole lamp-check story.

### 4.2 Normal driving (what CARLA's BehaviorAgent did for us)

The sim needs a simple autonomous driver per car:

1. **Route.** Given waypoints, plan along the lane graph (lane −2 by default) and follow it with pure-pursuit
   steering. One mission can cross several segments.
2. **Speed.** Hold `max_speed_kmh` (10 km/h by default, 8 km/h in the stuck scene), with gentle
   acceleration and braking.
3. **Arrival.** BehaviorAgent counted itself arrived within about 3–4 m. That's fine for normal points but not
   for these:
   - **Precise stops** (`inspection_gate`, and every `pull_over` point) need **±0.4 m** along the heading,
     because cameras are aimed at the mark. Creep onto it at walking pace, and give up after 30 s.
   - **Drive-through** (`inspection_gate`, on by default): don't stop. Hold the mission speed (5 km/h at the
     gate), and when the car's centre crosses the mark, record `passes[vin] = km/h` and report arrival.
     Python pauses the world there, photographs the car and usually sends the next mission, so the car
     carries straight on. If no new mission comes, brake.
4. **Queuing.** Keep a gap behind any vehicle in the same lane: about 6 m standing, more when moving.
   Waiting behind **another plant car** is queuing and must **not** trigger a help request.
5. **Ignore parked cars.** A car standing in a slot or bay (on the shoulder) is not in the lane, so drive
   past it. On CARLA this caused real jams before it was handled.
6. **Status** at 1 Hz, and `mission.done` when the last waypoint is reached (§1).

### 4.3 Pulling into a slot or bay (`pull_over` points)

Cars don't route on the shoulder. To reach a slot:

1. Route to the driving lane **12 m before** the spot (`PULL_OVER_RUN_M`).
2. Steer for a point **4 m beyond** the spot along its heading (`PULL_OVER_AIM_M`), at walking pace, braking for
   anything in front.
3. Once within 3 m, **square up**: set the car exactly on the spot, straight (`_square_up`). That's allowed;
   the CARLA stand-in did the same.
4. **Leaving a slot:** start the route from the lane beside it, 5 m ahead (`PULL_OUT_M`).
5. **Slot-to-slot moves** for a yard re-plan go along the shoulder at creep speed: forward if the new slot is
   ahead, reverse if it's behind (up to 30 m), or reverse down the lane and turn in 7 m before the slot.
   Wait if a car is between the two slots.

When **Gemini parks** (`MARSHAL_PARKING=gemini`), steps 2–3 are replaced by §7.

### 4.4 Stopping for objects, and asking for help (flow 2)

- **Stop for props and people** (boxes, barrels, walkers) in the path: within `9 m + v²/(2·4)` ahead and
  1.8 m to either side of the car's centreline. BehaviorAgent didn't brake for props in CARLA, so the backend
  added this. Vehicles are handled by queuing (§4.2).
- **Blocked:** if the car has been below 0.6 km/h for 5 s with a **non-plant** obstacle within 14 m ahead
  (and within 3 m laterally), it raises `assist.request`:
  - `case_id = "AC-<vin>-<n>"`
  - `reason = "unknown object in lane: an object (static.prop.box01) 9.0 m ahead"`
  - `options`: a subset of `["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"]`, offering only what can
    be driven from here. "Wait" is always offered. A pass needs a same-direction lane to the left and enough
    room past the object before the next stop.
  - the clip: the last **10 s** from the car's forward camera, **8 frames** (§5). Gemini describes the scene
    and returns **bounding boxes** for the box and the person (`box_2d`), which the dashboard draws.
- **Doing what the human chose** (`assist.choose`):
  - `wait_for_removal`: stay braked until nothing but plant traffic is ahead, then resume. Record the seconds
    waited.
  - `pass_left_3kmh`: change into the left lane over 7 m, pass at **3 km/h**, return to the own lane once the
    car's centre is 6 m past the object, and be back in lane at least 5 m before the next stop. Measure and
    report the minimum distance to the object, the speed alongside, and **contacts** (a collision check during
    the detour).
  - `reroute_lane_c`: the same, but into lane C at mission speed.
  - Anything that can't be driven after all becomes waiting.

### 4.5 Other actors used in scenes

| actor | CARLA blueprint | Three.js stand-in |
|---|---|---|
| box in the lane | `static.prop.box01` (also box02/03, creasedbox02) | a cardboard box, about 0.6 m |
| worker | `walker.pedestrian.0030` (any pedestrian) | a person in hi-vis, standing |
| lorry unloading (the "forklift" hazard) | `vehicle.carlamotors.european_hgv`, parked, position lights on | a box truck |
| cones, warning sign, barrels, street barriers, calibration boards | `static.prop.*` | simple meshes |
| parked finished cars (scenery) | `vehicle.lincoln.mkz_2020`, physics off | the same car model, static |

Every actor must be reportable as a box: centre, yaw, length, width, height, and whether it's moving. The
parking and obstacle logic in Python needs exactly that.

---

## 5. Cameras (Gemini's eyes)

All Marshal cameras are RGB, rendered on demand to **JPEG**. Evidence frames are **1280×720** (at 640×360
Gemini couldn't tell a lit headlight from a dark one). Poses are given relative to a map point or to the car,
in CARLA's frame: `forward` along the heading, `right` to its right, `z` up, `yaw` offset, and `pitch`
(negative looks down).

### 5.1 Gate: four fixed cameras around `inspection_gate` (`GATE_CAMERAS`)

| view | forward | right | height | yaw offset | pitch | FOV |
|---|---|---|---|---|---|---|
| front | +7.0 | 0 | 1.5 | 180° (looks back at the car's face) | −5° | 90° |
| rear | −7.0 | 0 | 1.5 | 0° | −5° | 90° |
| left | 0 | −5.0 | 1.2 | +90° | −5° | 90° |
| right | 0 | +5.0 | 1.2 | −90° | −5° | 90° |

They are taken as the car's centre crosses the gate mark (drive-through) or once it stops on it. Marshal sends
all four to Gemini (screen per view, then build-sheet match). Files: `gs://…/gate/<vin>/<view>.jpg`.

### 5.2 Cameras on the car (`_attach_onboard`)

| camera | pose (forward, z, pitch, yaw) | FOV | buffer | used for |
|---|---|---|---|---|
| assist (chase) | x −6.0, z 3.0, pitch −12°, yaw 0 | 90° | last **10 s**, **8** frames | the help request clip |
| evidence rear | x −7.0, z 1.6, pitch −5°, yaw 0 (behind, looking forward at the car's back) | **38°** | last **4 s**, **8** frames | brake lights, reverse lamp, indicators |
| evidence front | x +7.5, z 1.6, pitch −5°, yaw 180° (ahead, looking back) | **38°** | last 4 s, 8 frames | headlights |

They're attached to the car because the car moves during the routine, and a camera fixed to the map lost it.
The FOV is narrow so the car's end fills the frame. All cameras render every **0.2 simulated s**
(`CAMERA_TICK_S`), not every tick. Each one keeps a rolling buffer, so "the last 4 s as 8 frames" is a sample
every 0.5 s.

Step → view: headlights → front; indicators, brake lights and reverse → rear.

**Implementation tip.** One `WebGLRenderTarget` per camera, rendered on its 0.2 s schedule. Read the pixels
(`renderer.readRenderTargetPixels`) or draw to an `OffscreenCanvas`, then `convertToBlob({type:"image/jpeg",
quality:0.9})`, keeping the last N blobs per camera. Python asks for `latest` or `clip` (N frames, oldest
first). **No overlays or text in evidence frames:** Gemini reads them.

### 5.3 Parking doesn't need a camera

Gemini's parking view is a bird's-eye diagram that Python draws from poses and boxes (`parking.render`). The
sim only provides the car's pose and the obstacle boxes within 15 m of the slot (§7).

### 5.4 The demo view (for filming, not for Gemini)

The film used:

- a chase camera;
- a cinematic low camera;
- a top-down "mini-map" inset;
- text notes like "GEMINI · VIN-P1 parking Y8: path B (0.81)".

In Three.js this is just the main canvas with an HTML overlay. Python can push notes (`{"op":"note","text":..,"kind":"gemini"}`)
and you render a feed in a corner.

---

## 6. The test-track routine (R-12) and the lamp check

From `src/marshal_ai/driving/routines.py` and `_routine_step` / `_manoeuvre` / `_full_lock`. On reaching
`test_track` with `routine="R-12"`, the car performs these steps in order (or only `routine_steps`, e.g. `[1]`):

| # | name | what the car does | lights | duration | telemetry reported | camera |
|---|---|---|---|---|---|---|
| 1 | headlights | stand braked | LowBeam | 8 s | `{"headlights":"ON"}` | front evidence |
| 2 | indicators | stand braked; **alternate left/right every 1.2 s** | LeftBlinker ↔ RightBlinker | 10 s | `{"indicator_left":"BLINK","indicator_right":"BLINK"}` | rear (telemetry-only on CARLA) |
| 3 | brake_lights | stand braked | Brake | 8 s | `{"brake_signal":true,"brake_lamps":"ON"}` | rear evidence |
| 4 | reverse | **actually reverse 3 m**, wheel straight, ≤4 km/h; wait if anything is within 4 m behind | Reverse | until done (≤25 s) | measured: `{"gear":"R","distance_m":3.02,"lateral_m":0.05,"reverse_lamp":"ON"}` | rear evidence |
| 5 | brake_test | accelerate to 20 km/h (run-up ≤12 m), full brake, **measure stopping distance** | | until done | measured: `{"start_speed_kmh":..,"stopping_distance_m":..,"run_up_m":..}` | telemetry only |
| 6 | full_lock | drive full-lock circles: left 90°, right 360°, left 270°, at 5 km/h (only if the area to the left is empty) | | ≤90 s | measured steering and wheel angles, radii | telemetry only |

At the end of each step, send `track.step` with the telemetry, then switch the lights for the next step (all
off after the last). **Telemetry is always "healthy"**: a car with a headlights fault still reports
`"headlights":"ON"`. Only the camera shows the truth.

Marshal's rules then check the telemetry (`TELEMETRY_CHECKS`: reverse 3 ± 0.5 m and ≤0.5 m sideways, brake
test ≤5 m from ≥18 km/h…). They ask Gemini whether the camera shows the expected lamp. If telemetry says ON
and the camera says OFF, the result is FAIL: the car goes to `rework.electrical`, is repaired (`repair()`
clears the fault), and is re-checked on the track.

For the demo, steps 1, 3 and 4 matter most. Step 1 with a headlights fault is the scene in the video.

---

## 7. Gemini parks the car (Python drives, the sim follows)

`src/marshal_ai/driving/parking.py`, `_gemini_park` in the backend. In the default **paths** mode:

1. Python plans up to 3 paths into the slot (weighted A*). Gemini sees them drawn and picks one, or "parked",
   or "ask a person".
2. The car drives the **first segment** of that path at walking pace: 0.7 m/s forward, 0.6 m/s reverse.
3. The car stops, Python re-plans, and Gemini chooses again, until the car is parked.

A safety check stops any move whose next 0.4 m would bring the car within 0.2 m of anything.

Everything is in the **slot's frame**: x along the slot (the way a parked car faces), y to its left.

What the sim provides each tick (Python converts to the slot frame):
- the car's pose (x, y, yaw), and its signed speed along its heading;
- every vehicle, walker and prop within 15 m of the slot as a box (centre, yaw, length, width), with `moving`
  set if it's driving or one of ours still parking; props flatter than 0.15 m are ignored.

What Python sends each tick: `{"op":"control","vin":..,"speed_mps":±target,"wheel":-1..1,"brake":bool}`.
Wheel ±1 = 30° front-wheel angle. **A kinematic bicycle model is exactly what the planner assumes**
(`parking.advance`):

```js
// per tick dt, wheelbase L = 2.85, steer δ = wheel * 30° (positive = left, in the slot frame's convention)
x   += v * Math.cos(yaw) * dt;
y   += v * Math.sin(yaw) * dt;
yaw += v / L * Math.tan(δ) * dt;
```

Mind the sign. In CARLA a positive steer turns **right**, so the backend sends `steer = -wheel * 0.45`. In
your sim, just use the bicycle model in CARLA's frame and agree the sign with us once.

**Parked** means: the whole car inside the slot's lines, 0.3 m clear at the sides, and the heading within 8°.
Python decides that; the sim doesn't need to know.

---

## 8. The demo scenes, and what each needs from the sim

These are the runs in the pitch video, from the `scripts/demo_carla_*.py` files.

### Scene A: a shift, several cars at once (`demo_carla_plant.py`)
- **Setup:**
  - Spawn 4–5 cars at `line_end`, queued 9 m apart (`behind_m`), all blue (`0,80,200`) except car 2, which is
    **red** (`200,20,20`) against a blue build sheet.
  - Two trucks, T-1 and T-2.
  - Optionally stage a **hazard** at the finishing corner (`stage_hazard`) and report it to Marshal as
    `finishing_corner:forklift unloading pallets`.
- **Hazard staging,** relative to the corner's middle point (forward/side in metres, side positive to the
  right of the road heading):
  - lorry at (0, 5.5), parked, position lights on;
  - boxes at (−4.2, 4.6), (−4.8, 5.6), (−4.4, 6.5), (−5.6, 4.9);
  - warning sign at (−12, 3.4), turned 180°;
  - cones at (−7, 3.3) and (3, 3.3);
  - worker at (−3, 3.6), turned 90°.
  - Everything sits beside the lane, not in it.
- **What happens:**
  1. Each car drives through the gate at ~5 km/h: four photos, then PASS. The red car FAILs on colour and goes
     to production_control.
  2. The track routine runs (step 1).
  3. Report.
  4. Route choice at `report_point`: Gemini sends cars via **lane C** around the reported hazard (probability
     1.00).
  5. Each car parks in a yard slot (by truck): Gemini parks, or pull-over + square-up.
  6. Ship.
- **The sim must:** handle several cars concurrently, queue behind the plant's own cars without asking for
  help, and support drive-through photographs while the world is paused.

### Scene B: a fault only the camera sees (`demo_carla_day4.py`)
- **Setup:** spawn `VIN-DARK` at `test_track`, with `faults["VIN-DARK"] = {"headlights"}`. Also spawn a
  healthy `VIN-OK` for comparison.
- **What happens:**
  1. Step 1: VIN-DARK reports headlights ON, but the front evidence frame shows them dark, so FAIL.
  2. A supervisor confirms.
  3. The car drives to `rework.electrical` (pull-over) and `repair()` clears the fault.
  4. The car goes back to the track and is re-checked: PASS.
- **The sim must:** light lamps convincingly at dusk (the film used a low sun) and leave them dark when
  faulty.

### Scene C: a car that needs help (`demo_carla_day5.py`)
- **Setup:**
  - Spawn `VIN-STUCK` (blue) at `buffer_zone_b`.
  - Put a **box** in the lane, **26 m ahead** along the point's heading.
  - Put a **worker** 1 m beyond the box and **2.6 m to the side**.
  - Mission at 8 km/h to `report_point`, past them. The car stops about 9 m short of the box.
- **What happens:**
  1. The car stops short of the box, stays stopped for 5 s, then raises `assist.request` with the 8-frame
     clip.
  2. Gemini describes the scene and returns boxes around the box and the person, and recommends waiting. The
     rules never allow passing a person.
  3. On the dashboard, a supervisor clicks **wait for removal**. The worker and box are removed (a scripted
     `remove`), the lane clears, and the car carries on.
- **The sim must:** stop for props and people, detect being blocked, keep the 10 s forward-camera buffer, and
  resume when the lane is clear.

### Scene D: a late truck, a yard re-plan (`demo_carla_yard.py`, and the plant run)
- **Setup:** cars parked in the yard for T-1 and T-2. T-1 calls in 40 minutes late.
- **What happens:**
  1. Gemini proposes moves, e.g. VIN-P1 Y8→Y6, VIN-P3 Y7→Y5, VIN-P2 Y1→Y8.
  2. The rules check the plan and a supervisor approves.
  3. The cars move along the shoulder. **P2 waits until P1 has left Y8.**
- **The sim must:** do slot-to-slot moves on the shoulder (forward/reverse), wait for the slot to be free,
  and end each car within 0.5 m of its new slot, straight.

### Scene E: Gemini parks (`MARSHAL_PARKING=gemini`, any scene ending in the yard)
- See §7. One car parking into Y7 behind a parked car in Y8 is the shot in the video.

### Not simulated: damage
CARLA can't render dents or scratches, so the damage check runs on a public photo dataset (no sim involved).
Nothing to build here.

---

## 9. What we learned on CARLA (save yourself the trouble)

1. **Aim cameras at a precise mark.** A car stopping 3 m short meant the rear camera saw its roof, and
   Gemini reported a "panel gap" on a car it could barely see. Drive-through or ±0.4 m.
2. **Narrow FOV for lamps** (38°). At 90° a lamp was a handful of pixels.
3. **Fixed cameras lose a car that moves.** Evidence cameras ride on the car.
4. **One frame can't show a change** (indicators switching sides, a car stopping), so those steps use a short
   frame sequence or telemetry only.
5. **Only claim what a camera can see.** The gate cameras are 1.2–1.5 m high and can't see a sunroof, so the
   build sheet doesn't declare one.
6. **Parked cars aren't in the lane,** and **queuing behind a plant car isn't being stuck.** Both caused jams
   and false help requests.
7. **Waypoint order on a one-way loop:** a stop behind the car sends it all the way round. Marshal now only
   sends forward stops, but the graph should make that natural.
8. **Lock-step matters:** while Gemini judges (about 1–2 s) the world must be paused, or cars drive past the
   moment being judged.

---

## 10. Suggested milestones

| # | deliverable | done when |
|---|---|---|
| 1 | Static plant: loop, lane C, slots, bays, dressing, sun, one car model with working lights | screenshot from each gate camera pose looks right |
| 2 | Lane graph + one car driving a mission (route, speed, arrival, drive-through, precise stop) | `line_end → inspection_gate → test_track → report_point` at 10 km/h, crosses the gate at ~5 km/h |
| 3 | WebSocket lock-step + status / mission.done + gate capture | Marshal's gate flow runs end to end against the browser |
| 4 | Several cars: queuing, ignore parked cars, pull-over + square-up into slots | a 4-car shift ends with each car in its slot, no deadlock |
| 5 | Routine R-12: lights per step, faults, reverse 3 m, onboard evidence buffers | VIN-DARK fails the headlights step, VIN-OK passes |
| 6 | Props/walkers, stop-for-object, blocked → assist.request with 8-frame clip, wait/pass/reroute | scene C works from the dashboard |
| 7 | Parking control channel (bicycle model) + obstacle boxes | Gemini parks a car in Y7 |
| 8 | Demo view: chase / top-down / notes feed | we can screen-record the pitch scenes |

The Python bridge (`threejs_backend.py`) is built alongside: a mock socket can be tested against
`fake.py`'s behaviour, and milestone 3 is where the two meet.

---

## 11. Reference: where everything lives

| file | what |
|---|---|
| `src/marshal_ai/driving/messages.py` | the mission API messages (§1) |
| `src/marshal_ai/driving/api.py` | `DrivingSystem` protocol and `MissionClient` (the rule checks) |
| `src/marshal_ai/driving/carla_backend.py` | the CARLA implementation; the constants quoted here are at the top |
| `src/marshal_ai/driving/fake.py` | the laptop stand-in, the simplest example of a backend |
| `src/marshal_ai/driving/routines.py` | routine R-12 and the telemetry checks |
| `src/marshal_ai/driving/parking.py` | Gemini parking: geometry, planner, safety check, bicycle model |
| `src/marshal_ai/plant/plant_map_world.yaml` | named points with CARLA poses, and the segment graph |
| `scripts/plant_xodr.py` | the plant road network (roads, lanes, lane C) |
| `scripts/dress_plant.py` | the dressing (`dress_world`) |
| `scripts/demo_carla_plant.py` | scene A, and `stage_hazard` |
| `scripts/demo_carla_day4.py` / `day5.py` / `demo_carla_yard.py` | scenes B, C, D |
| `src/marshal_ai/cameras.py` | the camera protocols |
| `src/marshal_ai/runtime.py` | where a backend is chosen (`MARSHAL_DRIVING=fake|carla`; add `threejs`) |

Questions: ask Rey. The fastest way to see any behaviour is to run the fake backend locally
(`MARSHAL_DRIVING=fake`) and watch the dashboard.
