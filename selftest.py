"""Offline analytic and financial tests. Fake prices are never included in release data."""
import math
import json
import tempfile
import sys
from pathlib import Path
from datetime import date,timedelta

import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import analysis as a
import update_fundamentals as fin
import update_prices as price
from config import TICKERS,SOFTWARE,HARDWARE


def synthetic(n=2520, market='up'):
    rng=np.random.default_rng(42)
    days=pd.bdate_range('2016-01-01',periods=n)
    tt={}
    for t in TICKERS:
        base=.0003
        if t=='IGV':base=.0005 if market=='up' else -.001
        if t=='SMH':base=.0003 if market=='up' else -.0016
        if t in SOFTWARE:base=.00065 if market=='up' else -.001
        if t in HARDWARE:base=.00048 if market=='up' else -.0013
        vol=.011 if t in ['SPY','IGV','SMH','QQQ'] else .016
        returns=rng.normal(base,vol,n)
        tt[t]=pd.Series(100*np.exp(np.cumsum(returns)),index=days)
    return pd.DataFrame(tt,index=days)


def test_returns_and_relative():
    assert a.percent(110,100)==pytest.approx(10)
    assert a.percent(90,100)==pytest.approx(-10)
    assert a.percent(10,0) is None
    p=pd.DataFrame({'IGV':[100,105,110],'SMH':[100,99,100]},index=pd.bdate_range('2024-01-01',periods=3))
    assert a.relative(p,'IGV','SMH',2,2)==pytest.approx(10)


def test_sma_no_future_and_no_forward_fill():
    p=synthetic(1020)
    old=a.sma(p,'IGV',50,600)
    p.iloc[601:,p.columns.get_loc('IGV')]*=50
    assert a.sma(p,'IGV',50,600)==pytest.approx(old)
    p.loc[p.index[595],'IGV']=float('nan')
    assert a.sma(p,'IGV',50,600) is None


def test_score_comparable_and_breadth_is_watchlist_only():
    p=synthetic()
    result=a.produce(p)
    assert result['tickers']==23
    assert len(result['stocks'])==19
    assert len([k for k,v in result['stocks'].items() if v['group']=='HW'])==6
    assert 0<=result['stocks']['NVDA']['score']<=100
    assert result['stocks']['ANET']['benchmark']=='QQQ'  # networking != semiconductor
    assert result['sectors']['SW']['count']==13
    assert result['sectors']['HW']['count']==6
    assert isinstance(result['events'],list)
    assert result['backtest']['status']=='ok'


def test_both_negative_outperformance_is_not_positive_leadership():
    p=synthetic(market='down')
    i=len(p)-1
    # Strong deterministic final downturn across both ETFs, IGV falls less.
    for t,mult in [('IGV',.90),('SMH',.76),('SPY',.85),('QQQ',.82)]:
        k=p.columns.get_loc(t)
        p.iloc[-70:,k] *= np.linspace(1,mult,70)
    res=a.produce(p)
    sw,hw=res['sectors']['SW'],res['sectors']['HW']
    assert sw['r60']<0 and hw['r60']<0
    assert sw['vsOther60']>0
    assert res['market']['label']=='방어·관찰'


def test_leader_has_no_missing_fundamental_shortcut():
    p=synthetic()
    m=a.metrics(p,'CRWD',len(p)-1)
    assert set(['vsSPY60','vsQQQ60','vsOwn60','above50','above200','score','risk60','leader']).issubset(m)
    assert m['score']%10==0
    assert not ('ForwardPE' in m or 'revenueYoY' in m)


def test_missing_and_duplicate_prices_rejected(tmp_path):
    rows=[['2026-01-01','SPY',100,101,1],['2026-01-01','SPY',100,101,1]]
    file=tmp_path/'p.csv';file.write_text('Date,Ticker,AdjClose,Close,Volume\n'+'\n'.join(','.join(map(str,row)) for row in rows))
    with pytest.raises(ValueError,match='Duplicate'):a.load_prices(file)


def test_no_lookahead_in_entry_decision():
    p=synthetic()
    i=1500
    orig=a._target(p,i-1)
    q=p.copy()
    q.loc[q.index[i]:,['SPY','IGV','SMH']]*=100
    assert a._target(q,i-1)==orig
    # Trading at same close using current day's return would violate this test.


def test_fee_reduces_returns_on_switch():
    p=synthetic()
    base=a.backtest(p)
    assert base['status']=='ok'
    assert base['assumptions']['oneWayCostBps']==15
    assert base['periods']['전체']['rotation']['rebalanceCount']>=1
    assert base['periods']['후반30%']['SPY']['days']>300
    assert base['periods']['전체']['rotation']['mddPct']<=0


def test_export_and_json_strict(tmp_path):
    p=synthetic()
    dest=tmp_path/'analysis.json'
    a.write_json(a.produce(p),dest)
    obj=json.loads(dest.read_text())
    assert obj['backtest']['periods']['후반30%']['SMH']['cagrPct'] is not None
    assert obj['date']==str(p.index[-1].date())


def test_quote_missing_returns_null_not_estimates():
    class DummyYF:
        @staticmethod
        def Ticker(_):raise ValueError('Yahoo 429')
    q=fin.try_yahoo('NET',DummyYF)
    assert q['forwardPE'] is None and q['marketCapUSD'] is None


def fake_financial_facts():
    # Choose dated fiscal periods where last year comparison is unambiguous.
    def entry(st,en,v,filed,form='10-Q'):
        return {'start':st,'end':en,'val':v,'filed':filed,'form':form}
    return {'us-gaap':{'RevenueFromContractWithCustomerExcludingAssessedTax':{'units':{'USD':[
        entry('2024-01-01','2024-03-31',100,'2024-05-05'),
        entry('2025-01-01','2025-03-31',120,'2025-05-05'),
        entry('2025-04-01','2025-06-30',122,'2025-08-05'),
        entry('2025-07-01','2025-09-30',125,'2025-11-05'),
        entry('2025-10-01','2025-12-31',130,'2026-02-05','10-K'),
    ]}}}}


def test_sec_extraction_yoy_and_ttm():
    f=fin.extract_sec(fake_financial_facts())
    assert f['secStatus']=='ok'
    assert f['revenueMetric']=='분기'
    assert f['revenueTTMUSD']==497
    # latest Q4 2025 has no comparable Q4 2024 -> explicitly missing, not fabricated.
    assert f['revenueYoY'] is None
    assert f['revenueFiled']=='2026-02-05'


def test_sec_missing_terms_no_fake_values():
    f=fin.extract_sec({'us-gaap':{}})
    assert f['secStatus']=='unavailable'
    assert f['revenueYoY'] is None and f['fcfMarginAnnual'] is None


def test_sec_user_agent_requirement_without_live_network():
    class DummyYF:
        @staticmethod
        def Ticker(_):raise ValueError('offline')
    r=fin.build_financials(ua='',yf=DummyYF)
    assert r['secConfigured'] is False
    assert len(r['stocks'])==19
    assert all(v['secStatus']=='not-configured' for v in r['stocks'].values())


def test_price_frame_validation_missing_current_day():
    p=synthetic(830)
    frames={t:pd.DataFrame({'Adj Close':p[t],'Close':p[t],'Volume':100000}) for t in TICKERS}
    rows,last,n=price.validate_frames(frames, today=p.index[-1].date()+timedelta(days=1))
    assert len(rows)==len(p)*23
    assert last==str(p.index[-1].date())
    frames['NET']=frames['NET'].iloc[:-1]
    with pytest.raises(ValueError,match='NET latest'):
        price.validate_frames(frames, today=p.index[-1].date()+timedelta(days=1))


def test_price_jump_rejected_instead_of_misleading_60d_rs():
    p=synthetic(830)
    frames={t:pd.DataFrame({'Adj Close':p[t],'Close':p[t],'Volume':100000}) for t in TICKERS}
    frames['NVDA'].iloc[-1,0]*=2
    with pytest.raises(ValueError,match='unusual'):
        price.validate_frames(frames,today=p.index[-1].date()+timedelta(days=1))


def test_fixed_backtest_benchmark_buy_and_hold_cost():
    p=synthetic(1400)
    bt=a.backtest(p)
    # Matched ETF calendar. Benchmark initial purchase incurs estimated one-way cost.
    initial=252;v=p['SPY']
    expected=((v.iloc[-1]/v.iloc[initial])*(1-a.FEE_ONE_WAY)-1)*100
    assert bt['periods']['전체']['SPY']['returnPct']==pytest.approx(expected,abs=.015)


def test_stale_price_data_rejected():
    p=synthetic(830)
    frames={t:pd.DataFrame({'Adj Close':p[t],'Close':p[t],'Volume':100000}) for t in TICKERS}
    with pytest.raises(ValueError,match='stale/future'):
        price.validate_frames(frames, today=p.index[-1].date()+timedelta(days=12))


def test_price_multilevel_yfinance_layout():
    p=synthetic(830)
    import itertools
    keys=list(itertools.product(TICKERS,['Adj Close','Close','Volume']))
    data=pd.DataFrame(index=p.index,columns=pd.MultiIndex.from_tuples(keys),dtype=float)
    for t in TICKERS:
        data[(t,'Adj Close')]=p[t]
        data[(t,'Close')]=p[t]
        data[(t,'Volume')]=100000.0
    frames=price._coerce(data,pd)
    assert len(frames)==23
    rows,latest,n=price.validate_frames(frames,today=p.index[-1].date()+timedelta(days=1))
    assert n==830


def test_annual_capex_source_stays_separate_from_ai_capex():
    # No field in SEC automatically identifies 'AI-only' capital expenditure.
    annual={'us-gaap':{'PaymentsToAcquirePropertyPlantAndEquipment':{'units':{'USD':[
        {'start':'2024-01-01','end':'2024-12-31','filed':'2025-02-01','val':10_000,'form':'10-K'},
        {'start':'2025-01-01','end':'2025-12-31','filed':'2026-02-01','val':12_000,'form':'10-K'}]}}}}
    cap=fin.pick_facts(annual,fin.TAGS['capex'])
    recent=fin.latest_year(cap['data'])
    assert recent['val']==12_000
    assert fin.pct_new_over_old(recent,fin.comparable_previous(cap['data'],recent))==20


def test_yahoo_download_uses_supported_start_not_invalid_custom_period(monkeypatch):
    import types
    # End near today's UTC date, not an invented market price.
    d=date.today()-timedelta(days=1)
    days=pd.bdate_range(end=pd.Timestamp(d),periods=830)
    import itertools
    keys=list(itertools.product(TICKERS,['Adj Close','Close','Volume']))
    table=pd.DataFrame(index=days,columns=pd.MultiIndex.from_tuples(keys),dtype=float)
    for t in TICKERS:
        table[(t,'Adj Close')]=100.0
        table[(t,'Close')]=100.0
        table[(t,'Volume')]=100000.0
    used={}
    class MockYF:
        @staticmethod
        def download(*args,**kwargs):
            used.update(kwargs)
            return table
    monkeypatch.setitem(sys.modules,'yfinance',MockYF())
    rows,last,n=price.fetch()
    assert 'period' not in used
    assert used['start'].count('-')==2
    assert n==830
