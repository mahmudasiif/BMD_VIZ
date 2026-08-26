#!/usr/bin/env python3
"""
Process a fimex-fetched ECMWF NetCDF file into PNG overlays and wind JSON.

Confirmed variable mapping (from bangladesh_atmos_*.nc):
  rain     -> precipitation_amount_acc (Mg/m² metadata, treated as mm; differenced per step)
  temp     -> air_temperature_2m       (K -> degrees C)
  humidity -> relative_humidity_pl     (%, selected at 850 hPa)
  cloud    -> cloud_area_fraction      (fraction -> %)
  wind u   -> x_wind_10m               (m/s, eastward)
  wind v   -> y_wind_10m               (m/s, northward)

Dimension names: time (54 steps, mixed 3h/6h), surface (singleton), pressure, latitude, longitude
Lat is descending (40 -> -5 at 0.25 deg); lon ascending (45 -> 110 at 0.25 deg).

Usage:
  python scripts/process_ecmwf.py --input bangladesh_atmos_20260823_12.nc
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.colors as mcolors
from PIL import Image
import xarray as xr

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / 'data' / 'viz'

# Domain tightly around Bangladesh. The data is clipped to the country in the
# frontend, so there's no reason to cover a wide area — and a small latitude
# span keeps Web-Mercator distortion negligible, so the (linearly-stretched)
# image overlay aligns with the Mercator-projected boundary lines.
# Bangladesh extent is lat 20.5–26.7, lon 88.0–92.7; add a small margin.
LAT_MAX = 27.0
LAT_MIN = 20.0
LON_MIN = 87.5
LON_MAX = 93.2

# Output PNG — upsampled to high resolution for smooth appearance (portrait)
TARGET_W = 720   # longitude direction (pixels)
TARGET_H = 900   # latitude direction  (pixels)


# ── Vivid colormaps via control-point interpolation ────────────────────────────
def _interp_rgba(values: np.ndarray, ctrl: np.ndarray) -> np.ndarray:
    """
    ctrl shape: (N, 5) → [t, R, G, B, A] each row a control point, t in [0,1].
    Returns uint8 RGBA array of the same shape as values.
    """
    out = np.empty((*values.shape, 4), dtype=np.uint8)
    for ch in range(4):
        out[..., ch] = np.clip(
            np.interp(values, ctrl[:, 0], ctrl[:, ch + 1]), 0, 255
        ).astype(np.uint8)
    return out


def rain_rgba(data: np.ndarray, vmax: float = 40.0) -> np.ndarray:
    t = np.clip(data / vmax, 0.0, 1.0)
    ctrl = np.array([
        # t     R    G    B    A    ← yr.no radar BLUE palette
        # Zero-point RGB matches light-rain blue to prevent bilinear halos.
        [0.000, 150, 205, 255,   0],   # 0 mm      transparent
        [0.005, 150, 205, 255, 150],   # 0.2 mm    very light blue
        [0.025, 105, 175, 248, 180],   # 1 mm      light blue
        [0.070,  60, 135, 235, 200],   # 2.8 mm    medium blue
        [0.140,  15,  90, 215, 212],   # 5.6 mm    blue
        [0.260,   0,  50, 190, 222],   # 10.4 mm   deep blue
        [0.420,   0,  18, 160, 230],   # 16.8 mm   dark blue
        [0.620,   0,   5, 125, 237],   # 24.8 mm   very dark blue
        [1.000,   0,   0,  80, 245],   # 40 mm+    deep navy
    ])
    rgba = _interp_rgba(t, ctrl)
    rgba[data < 0.2, 3] = 0
    return rgba


def wind_speed_rgba(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    speed = np.sqrt(u * u + v * v)
    vmax = 24.0  # m/s
    t = np.clip(speed / vmax, 0.0, 1.0)
    ctrl = np.array([
        # t     R    G    B    A    ← yr.no wind speed palette
        [0.000, 175, 228, 200,   0],   # 0 m/s     transparent
        [0.050, 175, 228, 200,  70],   # 1.2 m/s   faint mint
        [0.160, 100, 210, 150, 140],   # 3.8 m/s   light green
        [0.230,  50, 190, 130, 165],   # 5.5 m/s   teal-green
        [0.340,  15, 162, 142, 180],   # 8.2 m/s   teal
        [0.460,   0, 118, 178, 192],   # 11 m/s    blue-teal
        [0.580,   0,  68, 198, 202],   # 13.9 m/s  blue
        [0.700,  10,  18, 185, 210],   # 16.8 m/s  deep blue
        [0.830,  55,   0, 162, 218],   # 19.9 m/s  blue-purple
        [1.000,  95,   0, 130, 225],   # 24 m/s+   dark purple
    ])
    return _interp_rgba(t, ctrl)


def temp_rgba(data: np.ndarray, vmin: float = 18.0, vmax: float = 42.0) -> np.ndarray:
    t = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
    ctrl = np.array([
        # t     R    G    B    A   ← yr.no full spectral scale mapped to Bangladesh
        # yr.no's global rainbow: blue (cold) → cyan → green → yellow → orange → red
        # For 18-42°C Bangladesh range, start at cyan-blue (coolest, e.g. mountains)
        # through green → yellow → orange → red (hottest plains in summer).
        [0.000,  20, 120, 220, 185],   # 18°C  steel blue (cool uplands)
        [0.165,   0, 200, 200, 195],   # 22°C  cyan-teal
        [0.330,  20, 190,  60, 202],   # 26°C  green
        [0.460, 160, 215,   0, 208],   # 29°C  yellow-green
        [0.580, 245, 205,   0, 213],   # 32°C  vivid yellow
        [0.720, 255, 120,   0, 218],   # 35°C  orange
        [0.860, 220,  20,   0, 222],   # 38°C  red-orange
        [1.000, 130,   0,   0, 228],   # 42°C  dark red
    ])
    return _interp_rgba(t, ctrl)


def humidity_rgba(data: np.ndarray, vmin: float = 30.0, vmax: float = 100.0) -> np.ndarray:
    t = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
    ctrl = np.array([
        # t     R    G    B    A   ← tan/brown → green → teal → deep blue (Ventusky RH scale)
        [0.000, 180, 140,  60, 165],   # 30%  warm tan (dry)
        [0.300, 100, 180,  80, 188],   # 51%  yellow-green
        [0.560,  20, 180, 130, 205],   # 68%  muted teal
        [0.780,   0, 110, 200, 218],   # 80%  medium blue
        [1.000,   0,  50, 160, 230],   # 100% deep blue (saturated)
    ])
    return _interp_rgba(t, ctrl)


def cloud_rgba(data: np.ndarray) -> np.ndarray:
    t = np.clip(data / 100.0, 0.0, 1.0)
    ctrl = np.array([
        # t     R    G    B    A   — Ventusky cloud: transparent→light grey-blue
        [0.000, 180, 195, 215,   0],   # 0%   transparent (same hue, no halo)
        [0.150, 180, 195, 215,  55],   # 15%  very faint
        [0.400, 155, 172, 195, 115],   # 40%  light blue-grey
        [0.700, 120, 138, 165, 165],   # 70%  medium slate
        [1.000,  85, 105, 140, 210],   # 100% deeper slate (not too dark)
    ])
    return _interp_rgba(t, ctrl)


def save_layer_png(rgba_uint8: np.ndarray, out_path: Path) -> None:
    """Upsample small grid RGBA to TARGET_W × TARGET_H and save."""
    small = Image.fromarray(rgba_uint8, 'RGBA')
    large = small.resize((TARGET_W, TARGET_H), Image.BILINEAR)
    large.save(out_path, optimize=True)


def process_file(nc_path: Path) -> None:
    print(f'Opening {nc_path.name} ...')
    ds = xr.open_dataset(nc_path)

    ref_time = ds['forecast_reference_time'].values
    ref_dt = datetime.utcfromtimestamp(int(ref_time) / 1e9)
    run_date = ref_dt.strftime('%Y%m%d')
    run_hour_int = ref_dt.hour
    run_hour = f'{run_hour_int:02d}z'

    print(f'  Run: {run_date} {run_hour}  |  {len(ds.time)} time steps')

    out_root = DATA_DIR / run_date / run_hour
    for var in ('rain', 'temp', 'humidity', 'cloud', 'windspeed'):
        (out_root / var).mkdir(parents=True, exist_ok=True)
    (out_root / 'wind').mkdir(parents=True, exist_ok=True)

    ds_bd = ds.sel(
        latitude=slice(LAT_MAX, LAT_MIN),
        longitude=slice(LON_MIN, LON_MAX),
    )

    times = ds_bd['time'].values
    n_steps = len(times)
    step_hours = [int((int(t) - int(ref_time)) / 3_600_000_000_000) for t in times]

    rain_acc = ds_bd['precipitation_amount_acc'].squeeze('surface').values
    if float(np.nanmax(rain_acc)) < 5.0:
        print('  Note: rain values appear to be in metres; converting to mm')
        rain_acc = rain_acc * 1000.0
    rain_interval = np.zeros_like(rain_acc)
    rain_interval[0] = rain_acc[0]
    rain_interval[1:] = np.diff(rain_acc, axis=0)
    rain_interval = np.clip(rain_interval, 0.0, None)

    temp_arr  = ds_bd['air_temperature_2m'].squeeze('surface').values - 273.15
    rh_arr    = ds_bd['relative_humidity_pl'].sel(pressure=850, method='nearest').values
    cloud_arr = ds_bd['cloud_area_fraction'].squeeze('surface').values * 100.0
    u10       = ds_bd['x_wind_10m'].squeeze('surface').values
    v10       = ds_bd['y_wind_10m'].squeeze('surface').values

    lats = ds_bd['latitude'].values
    lons = ds_bd['longitude'].values
    n_lat, n_lon = len(lats), len(lons)

    print(f'  Bangladesh grid: {n_lat} lat × {n_lon} lon  →  PNG: {TARGET_H}×{TARGET_W}')
    print(f'  Processing {n_steps} steps ...')

    for step_idx in range(n_steps):
        s = f'{step_idx:03d}'

        save_layer_png(rain_rgba(rain_interval[step_idx]),        out_root / 'rain'     / f'{s}.png')
        save_layer_png(temp_rgba(temp_arr[step_idx]),             out_root / 'temp'     / f'{s}.png')
        save_layer_png(humidity_rgba(rh_arr[step_idx]),           out_root / 'humidity' / f'{s}.png')
        save_layer_png(cloud_rgba(cloud_arr[step_idx]),           out_root / 'cloud'    / f'{s}.png')

        u_step = u10[step_idx].astype(np.float32)
        v_step = v10[step_idx].astype(np.float32)

        # Wind-speed colour background (drawn under the white streamlines)
        save_layer_png(wind_speed_rgba(u_step, v_step),           out_root / 'windspeed' / f'{s}.png')
        (out_root / 'wind' / f'{s}.json').write_text(json.dumps({
            'u': u_step.flatten().tolist(),
            'v': v_step.flatten().tolist(),
            'nLat': n_lat,
            'nLon': n_lon,
            'latMax': float(lats[0]),
            'latMin': float(lats[-1]),
            'lonMin': float(lons[0]),
            'lonMax': float(lons[-1]),
        }, separators=(',', ':')))

        if (step_idx + 1) % 10 == 0 or step_idx == n_steps - 1:
            print(f'  [{step_idx + 1}/{n_steps}] done')

    (out_root / 'manifest.json').write_text(json.dumps({
        'run_date': run_date,
        'run_hour': run_hour,
        'n_steps': n_steps,
        'step_hours': step_hours,
        'processed_at': datetime.utcnow().isoformat() + 'Z',
        'source': nc_path.name,
    }, indent=2))
    print(f'Done. Output: {out_root}')
    ds.close()


def main():
    parser = argparse.ArgumentParser(description='Process ECMWF NetCDF -> PNG + wind JSON')
    parser.add_argument('--input', required=True, help='Path to the NetCDF file')
    args = parser.parse_args()
    nc_path = Path(args.input).resolve()
    if not nc_path.exists():
        print(f'Error: file not found: {nc_path}', file=sys.stderr)
        sys.exit(1)
    process_file(nc_path)


if __name__ == '__main__':
    main()
