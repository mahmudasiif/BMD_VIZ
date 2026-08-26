"""
BMD Weather Visualization — FastAPI backend.

Endpoints:
  GET /                                              HTML frontend
  GET /health                                        Health check
  GET /runs/latest                                   Most recent run manifest
  GET /runs                                          All available run manifests
  GET /layer/{variable}/{run_date}/{run_hour}/{step} PNG overlay
  GET /wind/{run_date}/{run_hour}/{step}             u/v wind grid JSON
  GET /lightning/recent                              Recent strikes (JSON)
  GET /point/{run_date}/{run_hour}/{step}            Point value query (?lat=&lon=)
"""

import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import xarray as xr
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / 'data' / 'viz'
LIGHTNING_DB = ROOT / 'data' / 'lightning' / 'strikes.db'
FRONTEND_HTML = ROOT / 'bmd_weather_viz_light.html'
BOUNDARY_GEOJSON = ROOT / 'data' / 'bd_boundary.geojson'
OUTLINE_GEOJSON = ROOT / 'data' / 'bd_outline.geojson'

BD_LAT_MAX = 27.0
BD_LAT_MIN = 20.0
BD_LON_MIN = 87.5
BD_LON_MAX = 93.2

VARIABLES = {'rain', 'temp', 'humidity', 'cloud', 'windspeed'}

app = FastAPI(title='BMD Weather Viz', docs_url='/docs')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],  # Restrict to BMD network/VPN in production
    allow_methods=['GET'],
    allow_headers=['*'],
)

# Module-level dataset cache keyed by nc_path string
_ds_cache: dict[str, xr.Dataset] = {}


def _get_dataset(nc_path: Path) -> xr.Dataset:
    key = str(nc_path)
    if key not in _ds_cache:
        _ds_cache[key] = xr.open_dataset(nc_path)
    return _ds_cache[key]


def _find_nc(run_date: str, run_hour: str) -> Optional[Path]:
    """Locate the source NetCDF for a given run. Checks the project root and the
    ingest archive (where watch_ingest.py moves processed files)."""
    hour_int = run_hour.rstrip('z')
    search_dirs = [ROOT, ROOT / 'data' / 'incoming' / 'archive', ROOT / 'data' / 'incoming']
    for d in search_dirs:
        if not d.exists():
            continue
        for candidate in d.glob('*.nc'):
            if run_date in candidate.name and f'_{hour_int}.' in candidate.name:
                return candidate
    return None


def _load_run_manifests() -> list[dict]:
    manifests = []
    for mf in sorted(DATA_DIR.glob('*/*/manifest.json'), reverse=True):
        manifests.append(json.loads(mf.read_text()))
    return manifests


def _manifest(run_date: str, run_hour: str) -> dict:
    mf = DATA_DIR / run_date / run_hour / 'manifest.json'
    if not mf.exists():
        raise HTTPException(404, f'Run {run_date}/{run_hour} not found. Run process_ecmwf.py first.')
    return json.loads(mf.read_text())


def _png_path(variable: str, run_date: str, run_hour: str, step: int) -> Path:
    return DATA_DIR / run_date / run_hour / variable / f'{step:03d}.png'


def _wind_path(run_date: str, run_hour: str, step: int) -> Path:
    return DATA_DIR / run_date / run_hour / 'wind' / f'{step:03d}.json'


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get('/')
def frontend():
    if not FRONTEND_HTML.exists():
        raise HTTPException(404, 'Frontend HTML not found.')
    return FileResponse(FRONTEND_HTML, media_type='text/html')


@app.get('/boundary')
def boundary():
    if not BOUNDARY_GEOJSON.exists():
        raise HTTPException(404, 'Boundary GeoJSON not found.')
    return JSONResponse(
        content=json.loads(BOUNDARY_GEOJSON.read_text()),
        headers={'Cache-Control': 'public, max-age=86400'},
    )


@app.get('/outline')
def outline():
    """Dissolved Bangladesh country outline (single MultiPolygon) — used to clip
    the data layers so they only render over Bangladesh."""
    if not OUTLINE_GEOJSON.exists():
        raise HTTPException(404, 'Outline GeoJSON not found.')
    return JSONResponse(
        content=json.loads(OUTLINE_GEOJSON.read_text()),
        headers={'Cache-Control': 'public, max-age=86400'},
    )


@app.get('/health')
def health():
    runs = _load_run_manifests()
    return {'status': 'ok', 'available_runs': len(runs)}


@app.get('/runs/latest')
def runs_latest():
    runs = _load_run_manifests()
    if not runs:
        raise HTTPException(404, 'No processed runs found. Run process_ecmwf.py first.')
    return runs[0]


@app.get('/runs')
def runs_all():
    return _load_run_manifests()


@app.get('/layer/{variable}/{run_date}/{run_hour}/{step}')
def layer_png(variable: str, run_date: str, run_hour: str, step: int):
    if variable not in VARIABLES:
        raise HTTPException(400, f'Unknown variable. Choose from: {sorted(VARIABLES)}')
    _manifest(run_date, run_hour)  # 404 if run not found
    path = _png_path(variable, run_date, run_hour, step)
    if not path.exists():
        raise HTTPException(404, f'PNG not found for step {step}.')
    return FileResponse(path, media_type='image/png',
                        headers={'Cache-Control': 'public, max-age=3600'})


@app.get('/wind/{run_date}/{run_hour}/{step}')
def wind_json(run_date: str, run_hour: str, step: int):
    _manifest(run_date, run_hour)
    path = _wind_path(run_date, run_hour, step)
    if not path.exists():
        raise HTTPException(404, f'Wind JSON not found for step {step}.')
    return JSONResponse(
        content=json.loads(path.read_text()),
        headers={'Cache-Control': 'public, max-age=3600'},
    )


@app.get('/lightning/recent')
def lightning_recent(hours: float = Query(1.0, ge=0.1, le=48.0)):
    if not LIGHTNING_DB.exists():
        return {'strikes': [], 'count': 0}

    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(LIGHTNING_DB))
    try:
        rows = conn.execute(
            '''SELECT timestamp, lat, lon, intensity_ka, polarity, stroke_type
               FROM strikes WHERE timestamp >= ? ORDER BY timestamp DESC''',
            (since,),
        ).fetchall()
    finally:
        conn.close()

    strikes = [
        {'timestamp': r[0], 'lat': r[1], 'lon': r[2],
         'intensity_ka': r[3], 'polarity': r[4], 'stroke_type': r[5]}
        for r in rows
    ]
    return {'strikes': strikes, 'count': len(strikes)}


@app.get('/point/{run_date}/{run_hour}/{step}')
def point_query(
    run_date: str,
    run_hour: str,
    step: int,
    lat: float = Query(..., ge=BD_LAT_MIN, le=BD_LAT_MAX),
    lon: float = Query(..., ge=BD_LON_MIN, le=BD_LON_MAX),
):
    manifest = _manifest(run_date, run_hour)
    if step < 0 or step >= manifest['n_steps']:
        raise HTTPException(400, f'step must be 0..{manifest["n_steps"]-1}')

    nc_path = _find_nc(run_date, run_hour)
    if nc_path is None:
        raise HTTPException(404, 'Source NetCDF not found for this run.')

    ds = _get_dataset(nc_path)
    ds_pt = ds.sel(
        latitude=lat, longitude=lon,
        method='nearest',
    ).isel(time=step)

    rain_acc = float(ds_pt['precipitation_amount_acc'].squeeze().values)
    # Detect metres-of-water units same as in process_ecmwf.py
    if rain_acc < 5.0 and rain_acc > 0:
        rain_acc_mm = rain_acc * 1000.0
    else:
        rain_acc_mm = rain_acc

    # Interval rain: diff with previous step if possible
    if step > 0:
        ds_prev = ds.sel(latitude=lat, longitude=lon, method='nearest').isel(time=step - 1)
        prev_acc = float(ds_prev['precipitation_amount_acc'].squeeze().values)
        if prev_acc < 5.0 and prev_acc > 0:
            prev_acc = prev_acc * 1000.0
        rain_mm = max(0.0, rain_acc_mm - prev_acc)
    else:
        rain_mm = max(0.0, rain_acc_mm)

    temp_c = float(ds_pt['air_temperature_2m'].squeeze().values) - 273.15
    rh_pct = float(
        ds_pt['relative_humidity_pl'].sel(pressure=850, method='nearest').values
    )
    cloud_pct = float(ds_pt['cloud_area_fraction'].squeeze().values) * 100.0
    u = float(ds_pt['x_wind_10m'].squeeze().values)
    v = float(ds_pt['y_wind_10m'].squeeze().values)
    wind_speed = math.sqrt(u * u + v * v)
    wind_dir = (math.degrees(math.atan2(-u, -v)) + 360) % 360  # from-direction

    return {
        'lat': lat,
        'lon': lon,
        'step': step,
        'step_hour': manifest['step_hours'][step],
        'rain_mm': round(rain_mm, 1),
        'temp_c': round(temp_c, 1),
        'humidity_pct': round(rh_pct, 0),
        'cloud_pct': round(cloud_pct, 0),
        'wind_speed_ms': round(wind_speed, 1),
        'wind_dir_deg': round(wind_dir, 0),
    }
