#!/usr/bin/env python3
"""
Compute a 5-day, per-division forecast outlook from an ECMWF NetCDF.

For each of Bangladesh's divisions (dissolved district polygons in
data/bd_divisions.geojson) and each of the next N days, area-averages the
forecast over the grid cells inside that division and derives a simple
condition label — the summary a presenter narrates day by day.

Used by the /outlook endpoint, which caches the result to
data/viz/{date}/{hour}z/outlook.json.
"""

import sys
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import xarray as xr
from shapely.geometry import shape, Point
from shapely.prepared import prep

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from process_ecmwf import LAT_MAX, LAT_MIN, LON_MIN, LON_MAX  # noqa: E402

DIVISIONS_GEOJSON = ROOT / 'data' / 'bd_divisions.geojson'


def _condition(rain_mm: float, cloud_pct: float) -> str:
    if rain_mm >= 20:  return 'rain-heavy'
    if rain_mm >= 5:   return 'rain'
    if rain_mm >= 0.5: return 'drizzle'
    if cloud_pct >= 70: return 'cloudy'
    if cloud_pct >= 30: return 'partly'
    return 'clear'


def compute_outlook(nc_path: Path, n_days: int = 5) -> dict:
    ds = xr.open_dataset(nc_path)
    ref_time = ds['forecast_reference_time'].values
    ref_dt = datetime.utcfromtimestamp(int(ref_time) / 1e9)

    ds_bd = ds.sel(latitude=slice(LAT_MAX, LAT_MIN), longitude=slice(LON_MIN, LON_MAX))
    lats = ds_bd['latitude'].values
    lons = ds_bd['longitude'].values
    times = ds_bd['time'].values

    # Variables (same derivations as process_ecmwf)
    rain_acc = ds_bd['precipitation_amount_acc'].squeeze('surface').values
    if float(np.nanmax(rain_acc)) < 5.0:
        rain_acc = rain_acc * 1000.0
    rain_int = np.zeros_like(rain_acc)
    rain_int[0] = rain_acc[0]
    rain_int[1:] = np.clip(np.diff(rain_acc, axis=0), 0, None)

    temp_c = ds_bd['air_temperature_2m'].squeeze('surface').values - 273.15
    cloud = ds_bd['cloud_area_fraction'].squeeze('surface').values * 100.0
    u = ds_bd['x_wind_10m'].squeeze('surface').values
    v = ds_bd['y_wind_10m'].squeeze('surface').values
    wspd = np.sqrt(u * u + v * v)

    # Division masks: which grid cells fall inside each division polygon
    divisions = json.loads(DIVISIONS_GEOJSON.read_text())['features']
    lon2d, lat2d = np.meshgrid(lons, lats)
    div_masks = []
    for feat in divisions:
        geom = prep(shape(feat['geometry']))
        mask = np.zeros(lat2d.shape, dtype=bool)
        for i in range(lat2d.shape[0]):
            for j in range(lat2d.shape[1]):
                if geom.contains(Point(lon2d[i, j], lat2d[i, j])):
                    mask[i, j] = True
        if not mask.any():
            # tiny division vs coarse grid — fall back to nearest cell to centroid
            c = shape(feat['geometry']).centroid
            i = int(np.abs(lats - c.y).argmin())
            j = int(np.abs(lons - c.x).argmin())
            mask[i, j] = True
        div_masks.append((feat['properties']['division'], mask))

    # Group forecast steps by UTC calendar date, take the first n_days
    dates = [datetime.utcfromtimestamp(int(t) / 1e9).date() for t in times]
    ordered = []
    for d in dates:
        if d not in ordered:
            ordered.append(d)
    day_list = ordered[:n_days]
    day_idx = {d: [k for k, dd in enumerate(dates) if dd == d] for d in day_list}

    def area_mean(arr2d, mask):
        return float(np.nanmean(arr2d[mask]))

    result_divisions = []
    for name, mask in div_masks:
        days = []
        for d in day_list:
            steps = day_idx[d]
            # daily rain total = sum over the day of area-mean interval precip
            rain_mm = float(sum(area_mean(rain_int[s], mask) for s in steps))
            tmeans = [area_mean(temp_c[s], mask) for s in steps]
            cmean = float(np.mean([area_mean(cloud[s], mask) for s in steps]))
            wmean = float(np.mean([area_mean(wspd[s], mask) for s in steps]))
            days.append({
                'date': d.isoformat(),
                'dow': d.strftime('%a'),
                'rain_mm': round(rain_mm, 1),
                'tmax': round(max(tmeans), 1),
                'tmin': round(min(tmeans), 1),
                'cloud_pct': round(cmean, 0),
                'wind_ms': round(wmean, 1),
                'condition': _condition(rain_mm, cmean),
            })
        result_divisions.append({'division': name, 'days': days})

    ds.close()
    return {
        'run_date': ref_dt.strftime('%Y%m%d'),
        'run_hour': f'{ref_dt.hour:02d}z',
        'days': [{'date': d.isoformat(), 'dow': d.strftime('%a'),
                  'label': d.strftime('%d %b')} for d in day_list],
        'divisions': result_divisions,
    }


if __name__ == '__main__':
    nc = Path(sys.argv[1])
    print(json.dumps(compute_outlook(nc), indent=2))
