#!/usr/bin/env python3
"""Daily, split/dividend-adjusted prices for the AI RS dashboard.

Designed for GitHub Actions after the US market closes. No brokerage API keys.
Yahoo Finance via yfinance is an unofficial feed; a failed/stale download blocks
publication rather than silently publishing an incomplete leaderboard.

Local usage:
    python -m pip install -r requirements.txt
    python update_prices.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import sys
import time

TICKERS = (
    'IGV', 'SMH', 'SPY',  # sector/market benchmarks
    'NET', 'CRWD', 'MSFT', 'IOT', 'SNOW', 'PLTR', 'DDOG', 'NOW',
    'CRM', 'ESTC', 'PATH', 'APP', 'PANW',  # software companies
    'AMD', 'ANET', 'AVGO', 'NVDA', 'MU', 'TSM',  # hardware companies
)
REQUIRED = set(TICKERS)
DEST = Path(__file__).resolve().parent / 'data' / 'ai_rs_latest.csv'


def fetch():
    import pandas as pd
    import yfinance as yf

    source = yf.download(
        tickers=' '.join(TICKERS),
        period='18mo',
        interval='1d',
        auto_adjust=True,
        progress=False,
        group_by='ticker',
        threads=True,
    )
    if source.empty:
        raise RuntimeError('Empty Yahoo Finance response')

    closes = {}
    for ticker in TICKERS:
        cols = source.columns
        if isinstance(cols, pd.MultiIndex):
            col = (ticker, 'Close') if (ticker, 'Close') in cols else ('Close', ticker)
            if col not in cols:
                raise RuntimeError(f'Missing symbol/Close data: {ticker}')
            series = source[col]
        elif len(TICKERS) == 1 and 'Close' in cols:
            series = source['Close']
        else:
            raise RuntimeError('Unexpected yfinance response shape')

        series = pd.to_numeric(series, errors='coerce').dropna()
        series.index = pd.DatetimeIndex(series.index).tz_localize(None).normalize()
        series = series[(series > 0) & (~series.index.duplicated(keep='last'))]
        if len(series) < 180:
            raise RuntimeError(f'Insufficient observations for {ticker}: {len(series)}')
        closes[ticker] = series

    if set(closes) != REQUIRED:
        raise RuntimeError('Incomplete universe')

    # Use only dates on which every ticker has a true observation.
    # No forward fills (which would create fictitious zero returns).
    common = sorted(set.intersection(*(set(series.index) for series in closes.values())))
    if len(common) < 180:
        raise RuntimeError(f'Only {len(common)} common trading days available')

    latest_common = common[-1]
    latest_etf = min(closes[t].index.max() for t in ('IGV', 'SMH', 'SPY'))
    if latest_common < latest_etf:
        raise RuntimeError(f'Incomplete latest date: common={latest_common.date()}, ETF={latest_etf.date()}')

    age_days = (datetime.now(timezone.utc).date() - latest_common.date()).days
    if age_days < 0 or age_days > 6:
        raise RuntimeError(f'Unexpected or stale last trading day: {latest_common.date()} ({age_days} days old)')

    # Include a full calendar-year boundary for YTD and at least six months
    # of history, even during the first half of the next year.
    common = common[-380:]
    rows = []
    for date in common:
        for ticker in TICKERS:
            price = float(closes[ticker].loc[date])
            rows.append((date.strftime('%Y-%m-%d'), ticker, round(price, 6)))
    return rows, str(common[-1].date()), len(common)


def write_csv(rows):
    import csv

    DEST.parent.mkdir(parents=True, exist_ok=True)
    temp = DEST.with_suffix('.csv.tmp')
    with temp.open('w', encoding='utf-8', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(('Date', 'Ticker', 'AdjClose'))
        writer.writerows(rows)
    os.replace(temp, DEST)
    return DEST


def main():
    last_exc = None
    for attempt in range(1, 4):
        try:
            rows, latest, days = fetch()
            path = write_csv(rows)
            print(f'OK: {len(TICKERS)} tickers · {days} days · latest {latest} · {path}')
            return 0
        except Exception as exc:
            last_exc = exc
            print(f'Attempt {attempt}/3 failed: {exc}', file=sys.stderr)
            if attempt < 3:
                time.sleep(20 * attempt)
    print(f'FAILED: prior published CSV left unchanged ({last_exc})', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
