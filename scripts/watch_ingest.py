#!/usr/bin/env python3
"""
Ingest processor — turns dropped ECMWF NetCDF files into viz layers.

Designed to run from cron (ECMWF updates 2×/day, so a tick every 15 min is
plenty). Each run it processes any *.nc in `data/incoming/` whose run hasn't
been generated yet, and prunes old runs so disk stays bounded.

No archiving: raw files stay in `data/incoming/` (the /point endpoint reads them
for exact point values). Pruning removes both an old run's viz output AND its
raw .nc together, so `data/incoming/` never holds more than `--keep` files.

Cron example (every 15 min):
  */15 * * * * cd /home/mics02/bmd_viz && python3 scripts/watch_ingest.py --keep 6 >> data/ingest.log 2>&1

Drop new data with:  cp bangladesh_atmos_YYYYMMDD_HH.nc data/incoming/
"""

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from process_ecmwf import process_file, DATA_DIR  # noqa: E402

INCOMING = ROOT / 'data' / 'incoming'

# bangladesh_atmos_YYYYMMDD_HH.nc  ->  (YYYYMMDD, HH)
_NAME_RE = re.compile(r'(\d{8})_(\d{2})\.nc$')


def _run_of(nc: Path):
    """Derive (run_date, run_hour) from the filename, e.g. ('20260827','00z')."""
    m = _NAME_RE.search(nc.name)
    return (m.group(1), f'{m.group(2)}z') if m else (None, None)


def _already_done(nc: Path) -> bool:
    date, hour = _run_of(nc)
    if not date:
        return False   # unknown naming -> let process_file decide the run
    return (DATA_DIR / date / hour / 'manifest.json').exists()


def _is_stable(path: Path, settle_s: float = 3.0) -> bool:
    """True once the file stops growing — avoids processing a half-copied drop."""
    try:
        s1 = path.stat().st_size
        time.sleep(settle_s)
        return path.stat().st_size == s1 and s1 > 0
    except FileNotFoundError:
        return False


def process_pending() -> int:
    INCOMING.mkdir(parents=True, exist_ok=True)
    done = 0
    for nc in sorted(INCOMING.glob('*.nc')):
        if _already_done(nc):
            continue                      # this run is already generated
        if not _is_stable(nc):
            print(f'  {nc.name}: still being written, skipping this pass')
            continue
        try:
            print(f'Processing {nc.name} ...')
            process_file(nc)              # writes data/viz/{date}/{hour}z/
            done += 1
        except Exception as exc:          # keep going on a bad file
            print(f'  ERROR processing {nc.name}: {exc}', file=sys.stderr)
    return done


def prune_runs(keep: int) -> int:
    """Keep the `keep` newest runs; delete older viz output AND their raw .nc."""
    if keep <= 0:
        return 0
    runs = []  # (sortkey, run_date, run_hour, hour_dir, date_dir)
    for manifest in DATA_DIR.glob('*/*/manifest.json'):
        hour_dir = manifest.parent
        date_dir = hour_dir.parent
        runs.append((f'{date_dir.name}{hour_dir.name}', date_dir.name,
                     hour_dir.name, hour_dir, date_dir))
    runs.sort(reverse=True)

    import shutil
    removed = 0
    for _, run_date, run_hour, hour_dir, date_dir in runs[keep:]:
        shutil.rmtree(hour_dir, ignore_errors=True)
        if date_dir.exists() and not any(date_dir.iterdir()):
            date_dir.rmdir()
        # Delete the matching raw .nc from data/incoming/
        hh = run_hour.rstrip('z')
        for nc in INCOMING.glob('*.nc'):
            d, h = _run_of(nc)
            if d == run_date and h == run_hour:
                nc.unlink(missing_ok=True)
        print(f'  pruned old run {run_date}/{run_hour} (+ its .nc)')
        removed += 1
    return removed


def run_once(keep: int) -> None:
    n = process_pending()
    r = prune_runs(keep)
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{stamp}] processed {n}, pruned {r}' if (n or r)
          else f'[{stamp}] nothing new')


def main():
    ap = argparse.ArgumentParser(description='Process new ECMWF runs dropped in data/incoming/')
    ap.add_argument('--watch', action='store_true', help='Poll continuously instead of one pass (cron does not need this)')
    ap.add_argument('--interval', type=int, default=300, help='Poll interval seconds (with --watch)')
    ap.add_argument('--keep', type=int, default=6, help='How many newest runs to keep (0 = keep all)')
    args = ap.parse_args()

    if not args.watch:
        run_once(args.keep)
        return

    print(f'Watching {INCOMING} every {args.interval}s (Ctrl-C to stop) ...')
    try:
        while True:
            run_once(args.keep)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    main()
