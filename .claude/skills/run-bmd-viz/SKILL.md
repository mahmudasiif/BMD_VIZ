---
name: run-bmd-viz
description: Run, screenshot, and verify the BMD Weather Visualization tool. Use when asked to run, start, launch, screenshot, verify, or test the BMD viz app or weather visualization.
---

# BMD ATMOS — Run Skill

FastAPI backend + single-file Leaflet frontend with a futuristic dark / glassmorphism design.
Driven by a Playwright script at `.claude/skills/run-bmd-viz/driver.mjs`.
All commands verified on this machine.

**Paths are relative to project root:** `/home/mics02/bmd_viz/`

---

## Prerequisites

Python (user-installed via `~/.local/bin/pip`):
```bash
~/.local/bin/pip install -r requirements.txt   # fastapi uvicorn xarray netCDF4 Pillow matplotlib geopandas
```

Node / Playwright (installed at project root):
```bash
node --version   # v22.22.0
# playwright is in node_modules/ — no global install needed
npx playwright install chromium   # one-time download of headless Chromium
```

---

## Data pipeline — run once per new NC file

```bash
python3 scripts/process_ecmwf.py --input bangladesh_atmos_20260823_12.nc
```

- Domain is **tight around Bangladesh**: lat 20–27°N, lon 87.5–93.2°E (data is clipped to the country anyway; a small lat span keeps Mercator distortion negligible)
- Outputs 720×900 RGBA PNGs (rain/temp/humidity/cloud/**windspeed**) + wind JSON for all 54 time steps
- Runtime: ~60 s
- Skip if `data/viz/*/manifest.json` already exists

### Regular data — automatic ingest (cron)

ECMWF runs 2×/day (00z/12z). Drop-folder based, scheduled by **cron**:

```bash
# Your fimex/OPeNDAP pipeline drops new runs here:
cp bangladesh_atmos_YYYYMMDD_HH.nc data/incoming/

# cron processes pending files + prunes old runs (every 15 min):
*/15 * * * * cd /home/mics02/bmd_viz && python3 scripts/watch_ingest.py --keep 6 >> data/ingest.log 2>&1
```

- **No archiving** — raw `.nc` files stay in `data/incoming/`; the `/point` endpoint reads them for exact values. A file is skipped once `data/viz/{date}/{hour}z/manifest.json` exists (derived from the `YYYYMMDD_HH` filename), so re-running is idempotent ("nothing new").
- Waits for a file to stop growing before processing (avoids half-copied uploads).
- `--keep N` prunes `data/viz/` to the N newest runs **and deletes their raw `.nc`** from `data/incoming/`, so the folder never holds more than N files.
- The frontend **auto-refreshes**: polls `/runs/latest` every 5 min and hot-swaps to a newer run (clears the per-step wind cache, re-renders at the same step, shows a "↻ New run loaded" toast) — no page reload.
- `scripts/watch_ingest.py --watch` exists for a standalone daemon, but with cron set you don't need it. `start.sh` runs one ingest pass then starts the server (cron keeps it fresh after).

Also exports the district GeoJSON + dissolved country outline once (already done):
```bash
python3 -c "
import geopandas as gpd, json
gdf = gpd.read_file('Shape_bd/bd_dist.shp').to_crs('EPSG:4326')
json.dump(json.loads(gdf.to_json()), open('data/bd_boundary.geojson','w'))
# Dissolved single-country outline for clipping the data layers
diss = gdf.dissolve().geometry.iloc[0].simplify(0.0005, preserve_topology=True)
feat = {'type':'Feature','properties':{},
        'geometry': json.loads(gpd.GeoSeries([diss]).to_json())['features'][0]['geometry']}
json.dump(feat, open('data/bd_outline.geojson','w'))
# Division polygons for the 5-day outlook. The shapefile predates the 2015
# reorg, so split Mymensingh out of Dhaka and modernise Chittagong's spelling.
gdf.loc[gdf['FIRST_DIST'].isin(['MYMENSINGH','JAMALPUR','NETRAKONA','SHERPUR']), 'Division'] = 'Mymensingh'
gdf.loc[gdf['Division']=='Chittagong', 'Division'] = 'Chattogram'
dv = gdf.dissolve(by='Division').reset_index()[['Division','geometry']]
dv['geometry'] = dv['geometry'].simplify(0.005, preserve_topology=True)
o = json.loads(dv.to_json())
for f in o['features']: f['properties'] = {'division': f['properties']['Division']}
json.dump(o, open('data/bd_divisions.geojson','w'))   # 8 divisions
"
```

---

## Start the server

```bash
~/.local/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning &
until curl -sf http://localhost:8000/health >/dev/null; do sleep 1; done
```

Stop:
```bash
fuser -k 8000/tcp
```

Or use the all-in-one startup script:
```bash
bash start.sh
```

---

## Agent path — driver.mjs

### Screenshot all layers

```bash
node .claude/skills/run-bmd-viz/driver.mjs screenshot
```

Produces timestamped PNGs in `.claude/skills/run-bmd-viz/screenshots/`:
One colour field at a time (mutually exclusive) with the **animated white wind
streamlines always flowing on top of every layer** (Ventusky look). Default: rain.
Produces timestamped PNGs in `.claude/skills/run-bmd-viz/screenshots/`:
| File | Shows |
|---|---|
| `*-01-wind.png` | Wind speed colour field (green→blue→purple); streamlines on all layers |
| `*-02-rain.png` | Precipitation (yr.no radar blue, transparent where dry) |
| `*-03-temp.png` | Temperature (blue→cyan→green→yellow→orange→red) |
| `*-04-humidity.png` | RH 850 hPa (tan→green→teal→deep blue) |
| `*-05-cloud.png` | Total cloud cover (transparent→slate) |
| `*-06-point-query.png` | White forecast card over Dhaka |
| `*-07-playback.png` | After 5-step auto-play |

Screenshot a specific step (0–53):
```bash
node .claude/skills/run-bmd-viz/driver.mjs screenshot 20
```

### Health check

```bash
node .claude/skills/run-bmd-viz/driver.mjs health
# → {"status":"ok","available_runs":1}
```

### Point query

```bash
node .claude/skills/run-bmd-viz/driver.mjs point 23.72 90.41
# → { "rain_mm": 0.3, "temp_c": 29.2, "humidity_pct": 94, "cloud_pct": 98, "wind_speed_ms": 6.0, ... }
```

### Direct API smoke (no browser)

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/runs/latest | python3 -m json.tool | head -10
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/layer/rain/20260823/12z/004
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/wind/20260823/12z/004
curl -s "http://localhost:8000/point/20260823/12z/5?lat=23.72&lon=90.41"
curl -s http://localhost:8000/boundary | python3 -m json.tool | head -5
```

---

## UI layout

yr.no-style light theme: white cards, `#005B9A` blue accent, flat shadows,
Esri Light Gray Canvas basemap (no API key); place labels in a top pane so they
stay readable over the data. On load the map `fitBounds` to Bangladesh's extent
(≈ zoom 7.4, `zoomSnap: 0.1`) so the country fills the viewport for presentation,
re-fitting on window resize.

```
┌──────────────────────────────────────────────────┐
│  BMD WEATHER  ECMWF IFS · 2026-08-23 12z  T+048h  08-24 · 12:00 UTC │  ← header
├──────────────────────────────────────────────────┤
│                                          [WIND]  │
│     light basemap + data overlay         [RAIN]  │  ← layer rail (right, centred)
│     white wind streamlines               [TEMP]  │     mutually exclusive
│                                          [RH]    │
│                                          [CLOUD] │
├──────────────────────────────────────────────────┤
│ LEGEND (bar+ticks) │ ▶ ──●── 08-24·12:00 · ⏱──● 4× │  ← timeline + speed (bottom)
└──────────────────────────────────────────────────┘
```

Click anywhere on the map → white point-forecast card.

**5-Day Outlook** button (top-left) opens a right-docked panel with a 8-division ×
5-day grid (condition icon, rain mm, temp max/min) for presentations. The map pans
left to clear the panel; hovering a division row highlights that division polygon on
the map. Computed by `scripts/compute_outlook.py` (area-averages the forecast over
the grid cells inside each division polygon), served/cached via `/outlook`.

The timeline has a **playback-speed slider** (0.5×–4×, `#speed-slider`) next to the
step scrubber. Playback uses a self-scheduling `setTimeout` reading
`BASE_PLAY_MS / state.speed` each tick, so speed changes apply on the next frame
without restarting the timer.

---

## Endpoints reference

| Endpoint | Description |
|---|---|
| `GET /` | Frontend HTML |
| `GET /health` | Status + run count |
| `GET /runs/latest` | Latest run manifest (n_steps, step_hours, …) |
| `GET /runs` | All available runs |
| `GET /boundary` | Bangladesh district GeoJSON (64 features) |
| `GET /outline` | Dissolved country outline (1 MultiPolygon) — used to clip layers |
| `GET /divisions` | 8 division polygons (for the outlook highlight) |
| `GET /outlook/{date}/{hour}` | 5-day per-division forecast summary (computed once, cached to `outlook.json`) |
| `GET /layer/{var}/{date}/{hour}/{step}` | 880×660 RGBA PNG overlay |
| `GET /wind/{date}/{hour}/{step}` | u/v wind grid JSON (for streamline particles) |
| `GET /lightning/recent?hours=N` | Lightning strikes (empty — feed not yet wired) |
| `GET /point/{date}/{hour}/{step}?lat=&lon=` | All variables at one point |

Variables: `rain` `temp` `humidity` `cloud` `windspeed`

The `wind` button in the UI loads the `windspeed` PNG (speed colour field) **and**
the `/wind/...` JSON (u/v grid) which drives the white streamline particles. The
streamlines animate on top of **every** layer (their own map pane at z-index 500,
above the colour field at 400, below labels at 650) — the `wind` button only
switches the base colour field to wind speed.

---

## Colormaps (yr.no-accurate)

| Layer | Scale |
|---|---|
| Wind speed | Mint → green → teal → blue → purple (0 → 24+ m/s) + white streamlines |
| Rain | yr.no radar blue: light blue → blue → deep navy (transparent where dry) |
| Temp | Steel blue → cyan → green → yellow → orange → red (18 → 42 °C) |
| Humidity | Warm tan (dry) → yellow-green → teal → deep blue (saturated) |
| Cloud | Transparent (0%) → faint blue-grey → slate (100%, max ~82% opacity) |

Zero-value cells are fully transparent so the labelled basemap always shows through.
All zero-point RGB values match the first visible colour so bilinear upsampling
never produces dark halos at layer boundaries.

**Data is clipped to Bangladesh** — layers only render over the country; surrounding
regions show the plain basemap (focus-country effect). **Frames crossfade smoothly**
(400 ms opacity) via a double-buffered pair of image overlays.

---

## Gotchas

- **`+run_hour` on `"12z"` → NaN** — always `parseInt(state.run.run_hour)` in JS. `"12z"` as unary `+` = NaN → `Date.UTC` → Invalid Date → `toISOString()` throws → outer try/catch shows "API OFFLINE" even though the API is healthy.
- **Basemap must be key-free** — CartoDB `basemaps.cartocdn.com` now stamps "API KEY REQUIRED" across every tile (their free no-key tier was discontinued). Use **Esri Light Gray Canvas** instead: `World_Light_Gray_Base` (land, in the tile pane) + `World_Light_Gray_Reference` (labels, in a custom `labels` pane at z-index 650 so names sit above the data). No key needed. Note Esri uses `{z}/{y}/{x}` order (y before x).
- **One colour field at a time; streamlines always on** — `state.activeLayer` holds one of `windspeed|rain|temp|humidity|cloud|null` (the colour field). The white wind streamlines animate over **all** of them — `windLayer` is added once at init and never removed, living in its own `wind` map pane (z-index 500) so it always sits above the colour field. `loadWind()` runs on every step regardless of active layer. Don't re-gate it behind `activeLayer === 'windspeed'`.
- **Rain must be blue, not green/red** — yr.no radar precipitation is a light-blue → navy scale. A green→red scale reads as "temperature/severity" and confuses forecasters. Match yr.no.
- **Neon/electric colormaps look cartoonish** — use perceptual scales with moderate saturation, matching yr.no's meteorological convention.
- **Wind streamlines are white** — visible over both the light basemap and the coloured speed field. Dark navy streamlines vanish over the blue/purple high-speed regions.
- **Clip-to-Bangladesh uses `clipPathUnits="objectBoundingBox"`** — the data overlays cover a *fixed* geographic bbox (`BD.*`) and the browser scales them, so a clip-path in normalised 0–1 coords clips correctly at every zoom with **zero recomputation** on pan/zoom. X is linear (`x=(lon-lonMin)/lonSpan`); **Y must be Web-Mercator**: `y=(mercY(latMax)-mercY(lat))/mercYspan` where `mercY(lat)=ln(tan(π/4+lat·π/360))`. A *linear*-latitude Y sits ~7 km off near the northern edge and lets data bleed across the border. Applied to both the scalar `<img>` overlays and the wind `<canvas>` via the `.bd-clip-target` class + `applyClipAll()`.
- **Clip outline must be dissolved AND high-res** — `/boundary` has 64 separate district polygons; clipping needs the single-country union (`geopandas .dissolve()` → `data/bd_outline.geojson`). Simplify only lightly (**0.0005°**, ~15k pts): coarser tolerances (e.g. 0.01°) straighten the border's concave notches (Tripura) and the data bleeds into India. The delta yields ~133 MultiPolygon parts (islands) — fine as one SVG path.
- **Smooth frame transitions need double-buffering** — a single `imageOverlay.setUrl()` hard-cuts. Two overlays (`scalarBufs[0..1]`) crossfade: load the back buffer, then on its `load` event fade it in and the front out. CSS `.scalar-overlay { transition: opacity .4s }`.
- **Don't re-spawn wind particles on step change** — `setData()` swaps the vector field in place and keeps particles so they flow into the new field (no visible pop). Only spawn when the particle array is empty (first load / after resize).
- **`pkill -f uvicorn` exits 144** — use `fuser -k 8000/tcp` instead.
- **Canvas white box** — `fillStyle rgba(255,255,255,…)` fades trails to white and blocks scalar overlays. Fixed: `globalCompositeOperation = 'destination-out'` fades to transparent.
- **Wind canvas invisible** — positioned at NE corner with negative width. Fixed: position at NW corner `L.point(sw.x, ne.y)`, `width = ne.x − sw.x`.
- **Data misaligned with boundary (spills north)** — `L.imageOverlay` stretches the PNG *linearly* across its geographic bbox, but Leaflet draws vector boundaries in *Web Mercator*. Over a wide latitude span (the old 13–35°N domain) this mismatch reached ~80 km, so the data spilled past the country outline. Fixed by shrinking the domain to a tight 20–27°N band — Mercator distortion over 7° is only a few km. (A fully correct fix would Mercator-warp the PNG rows, but the tight domain makes it unnecessary.)
- **PNG halo artifacts** — bilinear upsampling of RGBA with black at alpha=0 creates dark fringes. Fixed: zero-point RGB must match the first visible colour; only alpha interpolates.
- **Playwright not finding `playwright` package** — run driver from project root where `node_modules/` exists.
- **NC file units "Mg/m²"** for precipitation is a labelling error; values are in metres. Auto-detected (max < 5) and × 1000 → mm.
- **Humidity variable** is `relative_humidity_pl` (pressure-level, 850 hPa), not a surface field.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Loading spinner stays / "API OFFLINE" | Check `run_hour` date math — must use `parseInt`, not `+` |
| Address already in use | `fuser -k 8000/tcp` |
| 404 on `/layer/…` | Run `process_ecmwf.py --input <nc_file>` first |
| Blank rain at T+0 | Expected — interval precipitation at step 0 is 0 mm |
| Point query 404 | Ensure `bangladesh_atmos_*.nc` is in project root |
| `Cannot find package 'playwright'` | Run from `/home/mics02/bmd_viz/`, not `/tmp` or elsewhere |
