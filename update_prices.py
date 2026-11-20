#!/usr/bin/env python3
"""Download adjustment-aware daily data. No synthetic fills, no partial publish.

Yahoo Finance is an unofficial source. This script fails closed if a current symbol
is missing, prices are stale, or a suspicious raw/adjusted jump appears.
"""
from __future__ import annotations
import csv
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from config import TICKERS

DEST = Path(__file__).resolve().parent / 'data' / 'ai_prices.csv'
MIN_DAYS = 800   # 3+ years for ETFs; stocks with shorter trading history require at least 180 sessions.
KEEP_DAYS = 2850 # ~11 years, ETF backtest where history exists.


def _coerce(raw, pd):
    """Coerce both common yfinance MultiIndex layouts to DataFrame per ticker."""
    if raw.empty:
        raise ValueError('Empty provider response')
    output = {}
    cols = raw.columns
    if not isinstance(cols, pd.MultiIndex):
        raise ValueError('Unexpected yfinance column layout')
    for t in TICKERS:
        if (t, 'Close') in cols:
            frame = raw[t].copy()
        elif ('Close', t) in cols:
            frame = raw.xs(t, axis=1, level=1, drop_level=True).copy()
        else:
            raise ValueError(f'Missing {t} Close column')
        if 'Adj Close' not in frame or 'Close' not in frame or 'Volume' not in frame:
            raise ValueError(f'Missing adjusted/raw/volume fields for {t}')
        frame = frame[['Adj Close', 'Close', 'Volume']].apply(pd.to_numeric, errors='coerce')
        frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
        frame = frame[~frame.index.duplicated(keep='last')].sort_index()
        frame = frame[(frame['Adj Close'] > 0) & (frame['Close'] > 0) & (frame['Volume'] >= 0)].dropna()
        output[t] = frame
    return output


def validate_frames(frames, today=None):
    """Validate observations on a shared SPY calendar, reject stale/poisoned data.

    Returns validated (Date, Ticker, AdjClose, Close, Volume) rows, latest date,
    observation count. Non-ETF IPO history is allowed to begin later.
    """
    import pandas as pd
    today = today or datetime.now(timezone.utc).date()
    for t in ('SPY','QQQ','IGV','SMH'):
        if len(frames[t]) < MIN_DAYS:
            raise ValueError(f'Insufficient benchmark history: {t}')
    market = frames['SPY'].index[-KEEP_DAYS:]
    if len(market) < MIN_DAYS:
        raise ValueError('Insufficient common benchmark calendar')
    last = market.max()
    age = (today-last.date()).days
    if age < 0 or age > 6:
        raise ValueError(f'SPY stale/future: {last.date()} ({age} days)')
    for t, df in frames.items():
        if len(df) < (MIN_DAYS if t in ('SPY','QQQ','IGV','SMH') else 180):
            raise ValueError(f'Insufficient {t} history: {len(df)}')
        # No stale ticker is published as current; exchange-aligned observations required.
        if df.index[-1] != last:
            raise ValueError(f'{t} latest {df.index[-1].date()} != SPY {last.date()}')
        recent = df.loc[df.index.isin(market[-61:])]
        if len(recent) < 59:
            raise ValueError(f'{t} missing too many recent sessions: {len(recent)}')
        # A huge adjusted jump can be a bad split adjustment or genuinely huge return.
        # Require manual provider cross-check rather than produce misleading signals.
        changes = recent['Adj Close'].pct_change().abs().dropna()
        if (changes > .65).any():
            raise ValueError(f'{t} unusual >65% daily adjusted change; verify manually')
        if (recent['Adj Close'] > 1e9).any():
            raise ValueError(f'{t} implausible adjusted close')
    out = []
    market_set = set(market)
    for t, df in frames.items():
        for dt, row in df.iterrows():
            if dt not in market_set: continue
            out.append((str(dt.date()), t,
                f'{float(row["Adj Close"]):.6f}', f'{float(row["Close"]):.6f}',
                str(int(row['Volume']))))
    return out, str(last.date()), len(market)


def fetch():
    import pandas as pd
    import yfinance as yf
    # Yahoo only officially accepts certain period tokens (e.g. 10y, max).
    # Arbitrary '12y' can fail. Use an explicit start date instead.
    start=(datetime.now(timezone.utc).date()-timedelta(days=365*12+12)).isoformat()
    response = yf.download(' '.join(TICKERS), start=start, interval='1d',
        auto_adjust=False, group_by='ticker', progress=False, threads=False, timeout=30)
    return validate_frames(_coerce(response,pd))


def atomic_csv(rows):
    DEST.parent.mkdir(exist_ok=True, parents=True)
    tmp = DEST.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        w=csv.writer(f);w.writerow(['Date','Ticker','AdjClose','Close','Volume']);w.writerows(rows)
    os.replace(tmp, DEST)


def main():
    for n in range(3):
        try:
            rows,last,days=fetch()
            atomic_csv(rows)
            print(f'PRICE OK: {len(TICKERS)} symbols; {days} market sessions; latest {last}; rows {len(rows)}')
            return 0
        except Exception as e:
            print(f'Price fetch/QA {n+1}/3 failed: {e}',file=sys.stderr)
            if n<2: time.sleep(12*(n+1))
    print('FAIL-CLOSED: did not modify published prices',file=sys.stderr)
    return 1

if __name__=='__main__': raise SystemExit(main())
