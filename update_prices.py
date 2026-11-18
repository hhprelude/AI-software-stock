#!/usr/bin/env python3
"""Fetch adjusted daily closes for the AI sector dashboard (no paid API / AI tokens).

Fail closed: missing / stale ticker prices do NOT silently replace previously
published data. All price observations are real shared trading dates, with no
forward fill. Yahoo/yfinance is an unofficial price source; validate externally.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import csv
import os
import sys
import time

ETF = ('IGV', 'SMH', 'SPY', 'QQQ')
SOFTWARE = ('NET', 'CRWD', 'MSFT', 'IOT', 'SNOW', 'PLTR', 'DDOG', 'NOW',
            'CRM', 'ESTC', 'PATH', 'APP', 'PANW')
HARDWARE = ('AMD', 'ANET', 'AVGO', 'NVDA', 'MU', 'TSM')
TICKERS = ETF + SOFTWARE + HARDWARE
DEST = Path(__file__).resolve().parent / 'data' / 'ai_rs_latest.csv'
MIN_HISTORY = 600
KEEP_HISTORY = 950  # approx. 3.8 calendar years: MA200 and 60d forward tests


def fetch():
    import pandas as pd
    import yfinance as yf

    data = yf.download(
        tickers=' '.join(TICKERS), period='5y', interval='1d',
        auto_adjust=True, group_by='ticker', progress=False,
        threads=True, timeout=25,
    )
    if data.empty:
        raise RuntimeError('Yahoo Finance returned an empty price table')

    closes = {}
    for ticker in TICKERS:
        columns = data.columns
        if isinstance(columns, pd.MultiIndex):
            col = ((ticker, 'Close') if (ticker, 'Close') in columns else
                   ('Close', ticker))
            if col not in columns:
                raise RuntimeError(f'No Close column for {ticker}')
            values = data[col]
        else:
            raise RuntimeError('Unexpected yfinance response layout')
        values = pd.to_numeric(values, errors='coerce').dropna()
        index = pd.DatetimeIndex(values.index)
        if index.tz is not None:
            index = index.tz_convert(None)
        values.index = index.normalize()
        values = values[(values > 0) & (~values.index.duplicated(keep='last'))]
        if len(values) < MIN_HISTORY:
            raise RuntimeError(f'Insufficient history for {ticker}: {len(values)} days')
        closes[ticker] = values

    # A real observation is required for every ticker on every published day.
    # Do not forward fill absent prices into artificial zero-return sessions.
    common = sorted(set.intersection(*(set(s.index) for s in closes.values())))
    if len(common) < MIN_HISTORY:
        raise RuntimeError(f'Insufficient common trading days: {len(common)}')

    latest = common[-1]
    latest_bench = min(closes[t].index.max() for t in ETF)
    if latest != latest_bench:
        raise RuntimeError(f'Missing latest ticker observations: common={latest.date()}, benchmarks={latest_bench.date()}')
    lag = (datetime.now(timezone.utc).date() - latest.date()).days
    if lag < 0 or lag > 6:
        raise RuntimeError(f'Stale or future latest day {latest.date()} ({lag} calendar days)')

    days = common[-KEEP_HISTORY:]
    rows = [(day.strftime('%Y-%m-%d'), ticker,
             f'{float(closes[ticker].loc[day]):.6f}')
            for day in days for ticker in TICKERS]
    return rows, str(latest.date()), len(days)


def write(rows):
    DEST.parent.mkdir(parents=True, exist_ok=True)
    temporary = DEST.with_suffix('.tmp')
    with temporary.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(('Date', 'Ticker', 'AdjClose'))
        writer.writerows(rows)
    os.replace(temporary, DEST)


def main():
    for attempt in range(1, 4):
        try:
            rows, last, count = fetch()
            write(rows)
            print(f'OK: {len(TICKERS)} symbols × {count} days; latest={last}; CSV={DEST}')
            return 0
        except Exception as exc:
            print(f'Price attempt {attempt}/3 failed: {exc}', file=sys.stderr)
            if attempt < 3:
                time.sleep(20 * attempt)
    print('FAILED: kept previously published prices unchanged', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
