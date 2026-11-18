#!/usr/bin/env python3
"""Optional FRED H.15 US Treasury 2y/10y yield data, percentage points.

FRED graph CSV downloads are publicly accessible without an API key.
If this OPTIONAL feed fails, keep existing file; the price dashboard can still
publish successfully. Stock signals never depend on yield availability.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import csv
import io
import os
import sys
import time
import urllib.request

OUT = Path(__file__).resolve().parent / 'data' / 'us_treasury_latest.csv'
IDS = ('DGS2', 'DGS10')


def get_series(sid):
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2024-01-01'
    request = urllib.request.Request(url, headers={'User-Agent': 'AI-sector-research-dashboard/2.0'})
    with urllib.request.urlopen(request, timeout=28) as rsp:
        raw = rsp.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(raw))
    if sid not in (reader.fieldnames or []):
        raise RuntimeError(f'Unexpected FRED CSV headers for {sid}: {reader.fieldnames}')
    values = {}
    for row in reader:
        d, v = row.get('observation_date', row.get('DATE', '')), row.get(sid, '')
        if not v or v == '.':
            continue
        try:
            f = float(v)
            if 0 <= f <= 25:
                values[d] = f
        except ValueError:
            pass
    if len(values) < 100:
        raise RuntimeError(f'Insufficient FRED daily values for {sid}: {len(values)}')
    return values


def main():
    try:
        datasets = {key: get_series(key) for key in IDS}
        common = sorted(set.intersection(*(set(a.keys()) for a in datasets.values())))
        if len(common) < 100:
            raise RuntimeError('Insufficient shared 2-year and 10-year observations')
        latest = common[-1]
        age = (datetime.now(timezone.utc).date() - datetime.strptime(latest, '%Y-%m-%d').date()).days
        if age < 0 or age > 15:
            raise RuntimeError(f'Stale FRED H.15 rates dated {latest}')
        OUT.parent.mkdir(parents=True, exist_ok=True)
        temp = OUT.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8', newline='') as out:
            w = csv.writer(out)
            w.writerow(['Date', 'DGS2', 'DGS10'])
            for day in common[-320:]:
                w.writerow([day, f'{datasets["DGS2"][day]:.3f}', f'{datasets["DGS10"][day]:.3f}'])
        os.replace(temp, OUT)
        print(f'FRED: {len(common[-320:])} observations through {latest}')
        return 0
    except Exception as exc:
        print(f'WARNING: optional FRED rate refresh failed ({exc}); kept previous file', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
