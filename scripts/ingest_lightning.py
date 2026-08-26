#!/usr/bin/env python3
"""
Ingest a lightning CSV file into SQLite.

COLUMN_MAP below must be updated once a real sample CSV is available.
The current keys are placeholders -- match them to the actual header names
from the lightning sensor network feed.

Usage:
  python scripts/ingest_lightning.py --input /path/to/strikes.csv
  python scripts/ingest_lightning.py --input /path/to/strikes.csv --watch  # poll every 60s
"""

import argparse
import csv
import sqlite3
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / 'data' / 'lightning' / 'strikes.db'

# *** ACTION REQUIRED ***
# Replace these key strings with the actual column headers from the real CSV feed.
# The values on the right are what we store internally -- do not change those.
COLUMN_MAP = {
    'timestamp':    'timestamp',    # ISO8601 datetime string
    'lat':          'lat',          # float, decimal degrees north
    'lon':          'lon',          # float, decimal degrees east
    'intensity_kA': 'intensity_ka', # float, peak current in kA (positive=CG, negative=CC)
    'polarity':     'polarity',     # '+' or '-'
    'stroke_type':  'stroke_type',  # e.g. 'CG', 'CC', 'IC'
}


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS strikes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT    NOT NULL,
            lat          REAL    NOT NULL,
            lon          REAL    NOT NULL,
            intensity_ka REAL,
            polarity     TEXT,
            stroke_type  TEXT,
            ingested_at  TEXT    NOT NULL
        )
    ''')
    # Unique constraint prevents duplicates from re-ingesting the same file
    conn.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_strike
        ON strikes (timestamp, lat, lon)
    ''')
    conn.commit()
    return conn


def ingest_csv(csv_path: Path, conn: sqlite3.Connection) -> int:
    inserted = 0
    now = datetime.utcnow().isoformat() + 'Z'
    with open(csv_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                conn.execute(
                    '''
                    INSERT OR IGNORE INTO strikes
                      (timestamp, lat, lon, intensity_ka, polarity, stroke_type, ingested_at)
                    VALUES (?,?,?,?,?,?,?)
                    ''',
                    (
                        row.get(COLUMN_MAP['timestamp'], '').strip(),
                        float(row.get(COLUMN_MAP['lat'], 0)),
                        float(row.get(COLUMN_MAP['lon'], 0)),
                        float(row.get(COLUMN_MAP['intensity_kA'], 0) or 0),
                        row.get(COLUMN_MAP['polarity'], '').strip(),
                        row.get(COLUMN_MAP['stroke_type'], '').strip(),
                        now,
                    ),
                )
                if conn.execute('SELECT changes()').fetchone()[0]:
                    inserted += 1
            except (ValueError, KeyError) as exc:
                print(f'  Skipping bad row: {exc}  row={dict(row)}')
    conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser(description='Ingest lightning CSV into SQLite')
    parser.add_argument('--input', required=True, help='Path to CSV file')
    parser.add_argument('--db', default=str(DB_PATH), help='SQLite database path')
    parser.add_argument('--watch', action='store_true',
                        help='Poll the CSV file every 60 seconds')
    parser.add_argument('--interval', type=int, default=60,
                        help='Poll interval in seconds (with --watch)')
    args = parser.parse_args()

    csv_path = Path(args.input)
    db_path = Path(args.db)

    conn = init_db(db_path)
    print(f'DB: {db_path}')

    while True:
        if not csv_path.exists():
            print(f'Warning: CSV not found: {csv_path}')
        else:
            n = ingest_csv(csv_path, conn)
            print(f'[{datetime.utcnow().isoformat()}Z] Ingested {n} new strikes from {csv_path.name}')

        if not args.watch:
            break
        time.sleep(args.interval)

    conn.close()


if __name__ == '__main__':
    main()
