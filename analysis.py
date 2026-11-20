#!/usr/bin/env python3
"""Point-in-time-safe PRICE dashboard calculations and executable ETF strategy test.

All indicators use <= as-of close, all portfolio selections use prior-close
information and execute at NEXT trading day's close. No current fundamentals
are ever injected into historical portfolio simulations.
"""
from __future__ import annotations
import csv
import json
import math
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from config import ETF, SOFTWARE, HARDWARE, TICKERS, GROUPS, COMPARATOR

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / 'data' / 'ai_prices.csv'
OUTPUT = ROOT / 'data' / 'analysis_latest.json'
H = (20, 60, 120, 250)
FEE_ONE_WAY = .0015  # commission + slippage estimate, NOT an actual broker quote


def percent(p, prev):
    if p is None or prev is None or not (math.isfinite(p) and math.isfinite(prev)) or prev <= 0: return None
    return (p / prev - 1) * 100


def finite(x):
    return x is not None and isinstance(x,(int,float)) and math.isfinite(x)


def number(x, n=4):
    return round(float(x),n) if finite(x) else None


def load_prices(path=INPUT):
    df = pd.read_csv(path, parse_dates=['Date'])
    req={'Date','Ticker','AdjClose','Close','Volume'}
    if not req.issubset(df.columns): raise ValueError('Expected header Date,Ticker,AdjClose,Close,Volume; first run update_prices.py')
    if df.duplicated(['Date','Ticker']).any(): raise ValueError('Duplicate date/ticker rows')
    if not df.Ticker.isin(TICKERS).all(): raise ValueError('Unexpected ticker(s)')
    if df[['AdjClose','Close']].isna().any().any() or (df[['AdjClose','Close']] <= 0).any().any():
        raise ValueError('Invalid adjusted / raw close')
    p=df.pivot(index='Date',columns='Ticker',values='AdjClose').sort_index()
    p=p.reindex(columns=list(TICKERS))
    if p['SPY'].isna().any(): raise ValueError('SPY benchmark calendar incomplete')
    if len(p) < 800: raise ValueError('Insufficient dates for live analysis')
    for t in TICKERS:
        if not finite(p[t].iloc[-1]): raise ValueError(f'Missing most recent observation {t}')
    return p


def value(p,t,i):
    if i<0 or i>=len(p) or t not in p: return None
    x=p[t].iloc[i]
    return float(x) if pd.notna(x) and math.isfinite(x) else None


def ret(p,t,n,i):
    return percent(value(p,t,i),value(p,t,i-n))


def relative(p,t,b,n,i):
    a,c=ret(p,t,n,i),ret(p,b,n,i)
    return a-c if finite(a) and finite(c) else None


def sma(p,t,n,i):
    if i<n-1: return None
    a=p[t].iloc[i-n+1:i+1]
    if a.isna().any(): return None
    return float(a.mean())


def above(p,t,n,i):
    avg=sma(p,t,n,i); x=value(p,t,i)
    return x>avg if finite(avg) and finite(x) else None


def ratio_high(p,t,b,n,i):
    if i<n:return None
    x=p[t].iloc[i-n:i+1] / p[b].iloc[i-n:i+1]
    if x.isna().any(): return None
    return bool(x.iloc[-1] > x.iloc[:-1].max())


def risk(p,t,n,i,b='SPY'):
    if i<n:return {'vol':None,'mdd':None,'infoRatio':None}
    x=p[t].iloc[i-n:i+1];y=p[b].iloc[i-n:i+1]
    if x.isna().any() or y.isna().any(): return {'vol':None,'mdd':None,'infoRatio':None}
    r=x.pct_change().dropna(); active=r-y.pct_change().dropna()
    annual_vol=float(r.std(ddof=1))*math.sqrt(252)*100 if len(r)>1 else None
    peak=x.cummax(); mdd=float((x/peak-1).min())*100
    ir=float(active.mean()/active.std(ddof=1)*math.sqrt(252)) if len(active)>1 and active.std(ddof=1)>0 else None
    return {'vol':number(annual_vol,2),'mdd':number(mdd,2),'infoRatio':number(ir,2)}


def metrics(p,t,i):
    b=COMPARATOR[t] if t in COMPARATOR else 'SPY'
    ma20=sma(p,t,20,i);ma50=sma(p,t,50,i);ma200=sma(p,t,200,i)
    returns={f'r{n}':number(ret(p,t,n,i),2) for n in H}
    spy=relative(p,t,'SPY',60,i); qqq=relative(p,t,'QQQ',60,i)
    sector=relative(p,t,b,60,i);sp20=relative(p,t,'SPY',20,i)
    above20=above(p,t,20,i);above50=above(p,t,50,i);above200=above(p,t,200,i)
    # fixed scoring rubric works across groups; NOT predictive/probabilistic
    inputs=[above20, above50, above200,
            ma50>ma200 if finite(ma50) and finite(ma200) else None,
            spy>0 if finite(spy) else None, qqq>0 if finite(qqq) else None,
            sector>0 if finite(sector) else None,
            sp20>0 if finite(sp20) else None,
            returns['r20']>0 if finite(returns['r20']) else None,
            returns['r60']>0 if finite(returns['r60']) else None]
    strength=sum(10 for x in inputs if x) if all(x is not None for x in inputs) else None
    isleader=all([finite(returns['r60']) and returns['r60']>0,
                  finite(returns['r120']) and returns['r120']>0,
                  finite(spy) and spy>0, finite(qqq) and qqq>0,
                  finite(sector) and sector>0,
                  above50 is True,above200 is True,
                  finite(ma50) and finite(ma200) and ma50>ma200])
    recent_high=ratio_high(p,t,b,60,i)
    status=('선도 후보' if isleader else
            '전환 관찰' if returns['r20'] is not None and returns['r20']>0 and above50 is True and finite(sector) and sector>0 else
            '약세' if returns['r60'] is not None and returns['r60']<0 and above50 is False else '관찰')
    return {'ticker':t,'name':(SOFTWARE.get(t) or HARDWARE.get(t) or (ETF.get(t),''))[0],
         'group':GROUPS.get(t,'ETF'),'subsector':(SOFTWARE.get(t) or HARDWARE.get(t) or ('',''))[1],
         'benchmark':b,'latest':number(value(p,t,i),4),**returns,
         'vsSPY60':number(spy,2),'vsQQQ60':number(qqq,2),'vsOwn60':number(sector,2),'vsSPY20':number(sp20,2),
         'ma20':number(ma20,4),'ma50':number(ma50,4),'ma200':number(ma200,4),
         'above20':above20,'above50':above50,'above200':above200,
         'ma50Above200':ma50>ma200 if finite(ma50) and finite(ma200) else None,
         'rsHigh60':recent_high,'score':strength,'leader':bool(isleader),
         'status':status,'risk60':risk(p,t,60,i)}


def sector_summary(p,sector,stockmetrics,etfmetric):
    member=[m for m in stockmetrics if m['group']==sector]
    leader=sum(m['leader'] for m in member)
    above50=sum(m['above50'] is True for m in member)
    benchmark= 'IGV' if sector=='SW' else 'SMH'
    peer='SMH' if sector=='SW' else 'IGV'
    e=etfmetric[benchmark]
    r20,r60=e['r20'],e['r60'];bs=e['vsSPY60'];bq=e['vsQQQ60']
    # "주도" never based on negative absolute performance or outperformance alone.
    strong=(all(finite(x) and x>0 for x in (r20,r60,bs,bq)) and
            e['above50'] is True and e['above200'] is True and len(member)>0 and leader/len(member)>=.30)
    if strong: stage='독립 강세 확인'
    elif finite(r60) and r60<0 and finite(relative(p,benchmark,peer,60,len(p)-1)) and relative(p,benchmark,peer,60,len(p)-1)>0: stage='상대적 방어'
    elif finite(r60) and r60<0 and e['above50'] is False: stage='업종 약세'
    else: stage='관찰'
    return {'ticker':benchmark,'group':sector,'count':len(member),'leaders':leader,'above50Count':above50,
            'watchlistBreadthPct':round(100*leader/len(member),1) if member else None,
            'stage':stage,'strong':strong,'r20':r20,'r60':r60,
            'vsSPY60':bs,'vsQQQ60':bq,'vsOther60':number(relative(p,benchmark,peer,60,len(p)-1),2),
            'above50':e['above50'],'above200':e['above200']}


def regime(sw,hw,etf):
    # Explicitly resolve both strong; neither false exclusive 'winner' if both fall.
    if not sw['strong'] and not hw['strong']:
        if finite(sw['r60']) and finite(hw['r60']) and sw['r60']<0 and hw['r60']<0:
            return {'label':'방어·관찰','reason':'IGV·SMH 모두 음의 수익률: 상대적 방어는 상승 주도가 아님'}
        return {'label':'전환 관찰','reason':'독립 상승 추세와 시장 대비 초과수익 조건 미충족'}
    if sw['strong'] and hw['strong']:
        return {'label':'양 업종 동반 강세','reason':'IGV·SMH 모두 절대·시장 대비 상승; 우위 여부는 수익률 차이로 별도 판단'}
    return {'label':'소프트웨어 독립 강세' if sw['strong'] else '하드웨어 독립 강세',
            'reason':'양 업종 중 하나만 절대 상승·시장 대비 초과·선도주 확산 기준 충족'}


def changes(prev, cur):
    res=[]
    for t,x in cur.items():
        a=prev.get(t)
        if not a:continue
        if not a['leader'] and x['leader']:
            res.append({'ticker':t,'kind':'진입','message':f'{t}: 신규 선도 후보'})
        if a['leader'] and not x['leader']:
            res.append({'ticker':t,'kind':'이탈','message':f'{t}: 선도 후보 이탈'})
        if a['above50'] is False and x['above50'] is True:
            res.append({'ticker':t,'kind':'회복','message':f'{t}: 50일선 상향 돌파'})
        if a['above50'] is True and x['above50'] is False:
            res.append({'ticker':t,'kind':'주의','message':f'{t}: 50일선 하향 이탈'})
        if not a['rsHigh60'] and x['rsHigh60']:
            res.append({'ticker':t,'kind':'강세','message':f'{t}: 60일 상대가격 신고가'})
    return res


def _target(p,i):
    """Choose using data up to index i only. First NEXT trading-day close will trade."""
    if i<250:return 'CASH'
    if above(p,'SPY',200,i) is not True:return 'CASH'
    candidates=[]
    for t in ('IGV','SMH'):
        a50=sma(p,t,50,i);a200=sma(p,t,200,i);r20=ret(p,t,20,i);rs=relative(p,t,'SPY',60,i)
        if all((above(p,t,50,i) is True,finite(a50) and finite(a200) and a50>a200,
                finite(r20) and r20>0,finite(rs) and rs>0)):
            candidates.append((rs,t))
    return max(candidates)[1] if candidates else 'SPY'


def stats(curve, dateindex, start, end, flips, initial_base=None):
    vals=curve[start:end+1]
    if len(vals)<50:return None
    v0=initial_base if initial_base is not None else vals[0]
    normalized=[x/v0 for x in vals]
    dr=[normalized[i]/normalized[i-1]-1 for i in range(1,len(normalized))]
    years=(dateindex[end]-dateindex[start]).days/365.25
    annual=(normalized[-1]**(1/years)-1)*100 if years>0 else None
    mx=0;dd=0
    for x in normalized: mx=max(mx,x);dd=min(dd,x/mx-1)
    vol=statistics.stdev(dr)*math.sqrt(252)*100 if len(dr)>1 else None
    shp=(statistics.mean(dr)/statistics.stdev(dr)*math.sqrt(252)) if len(dr)>1 and statistics.stdev(dr)>0 else None
    return {'returnPct':number((normalized[-1]-1)*100,2),'cagrPct':number(annual,2),
            'mddPct':number(dd*100,2),'volPct':number(vol,2),'sharpeZeroCash':number(shp,2),
            'rebalanceCount':flips,'days':len(vals)}


def backtest(p):
    """Full ETF period and final 30% chronological holdout; no parameter fitting.

    15 bps estimated one-way cost per leg. Prior close signal, next close execution.
    Benchmarks buy+hold, cash 0%, dividends reflected approximately via AdjClose.
    """
    keys=('SPY','IGV','SMH','QQQ')
    v=p[list(keys)].dropna()
    if len(v)<1000: return {'status':'insufficient','note':'Need at least 1000 shared ETF closes'}
    # reindex only ETF dates; never fill
    first=252;n=len(v);split=first+round((n-first)*.70)
    curves={k:[1.0]*n for k in ('rotation','SPY','IGV','SMH','QQQ','cash')}
    pos='CASH';trades=[];month=''
    # Same estimated initial one-way execution cost for every buy-and-hold ETF.
    for k in ('SPY','IGV','SMH','QQQ'):
        curves[k][first]*=(1-FEE_ONE_WAY)
    for i in range(first,n):
        if i>first:
            for k in ('SPY','IGV','SMH','QQQ'):
                curves[k][i]=curves[k][i-1] * (v[k].iloc[i]/v[k].iloc[i-1])
            curves['cash'][i]=curves['cash'][i-1]
            curves['rotation'][i]=curves['rotation'][i-1]*(1 if pos=='CASH' else v[pos].iloc[i]/v[pos].iloc[i-1])
        monthnow=v.index[i].strftime('%Y-%m')
        if monthnow!=month:
            # Computed at previous day's close; no current day's prices in target.
            tgt=_target(v,i-1)
            if tgt!=pos:
                legs=2 if pos!='CASH' and tgt!='CASH' else 1
                curves['rotation'][i] *= (1-FEE_ONE_WAY)**legs
                trades.append({'date':str(v.index[i].date()),'from':pos,'to':tgt,'costBps':int(legs*FEE_ONE_WAY*10000)})
                pos=tgt
            month=monthnow
    output={}
    for label,begin,end in [('전체',first,n-1),('후반30%',split,n-1)]:
        counts=sum(begin<=int(v.index.get_indexer([pd.Timestamp(x['date'])])[0])<=end for x in trades)
        output[label]={k:stats(curves[k],v.index,begin,end,counts if k=='rotation' else 0, initial_base=1.0 if begin==first else None) for k in curves}
        output[label]['from']=str(v.index[begin].date());output[label]['to']=str(v.index[end].date())
    return {'status':'ok','assumptions':{'signal':'month-end close, next session close execution','oneWayCostBps':15,
            'cashReturn':'0%','universe':'ETF only (IGV SMH SPY QQQ)','holdout':'final 30% chronological; rules fixed but not fully independent'},
            'periods':output,'tradesLast12':trades[-12:],
            'chart':[{'date':str(v.index[i].date()),'rotation':number(curves['rotation'][i]/curves['rotation'][first]*100,2),
                      'SPY':number(curves['SPY'][i]/curves['SPY'][first]*100,2),
                      'IGV':number(curves['IGV'][i]/curves['IGV'][first]*100,2),
                      'SMH':number(curves['SMH'][i]/curves['SMH'][first]*100,2)}
                     for i in range(first,n,10)] + [{'date':str(v.index[-1].date()),
                     **{k:number(curves[k][-1]/curves[k][first]*100,2) for k in ['rotation','SPY','IGV','SMH']}}]}


def produce(p):
    end=len(p)-1;prior=end-1
    e={t:metrics(p,t,end) for t in ETF}
    stocks={t:metrics(p,t,end) for t in (*SOFTWARE,*HARDWARE)}
    prev={t:metrics(p,t,prior) for t in stocks}
    sw=sector_summary(p,'SW',list(stocks.values()),e)
    hw=sector_summary(p,'HW',list(stocks.values()),e)
    names={**{t:ETF[t] for t in ETF},**{t:x[0] for t,x in SOFTWARE.items()},**{t:x[0] for t,x in HARDWARE.items()}}
    chronology=[]
    for i in range(max(0,end-260),end+1):
        item={'date':str(p.index[i].date())}
        for t in ('IGV','SMH','SPY','QQQ'):
            item[t]=number(percent(value(p,t,i),value(p,t,max(0,end-260))),2)
        rs=relative(p,'IGV','SMH',60,i)
        item['rs60']=number(rs,2)
        chronology.append(item)
    warnings=['가격은 Yahoo Finance 비공식 조정종가; 배당 재투자 성과의 근사치.',
              'IGV는 AI 전용이 아니며, SMH는 반도체 ETF입니다. ANET은 QQQ 비교.',
              '선도주 확산율은 임의 선정 19종목 관찰목록의 비율이며 업종 전체 대표값이 아닙니다.',
              '강세 점수는 고정 규칙 충족 개수×10이며 매수 확률·예상수익률이 아닙니다.']
    return {'version':'3.0','date':str(p.index[-1].date()),'generatedUTC':datetime.now(timezone.utc).isoformat(),
            'priceSource':'Yahoo Finance/yfinance (unofficial, adjusted close)','tickers':len(TICKERS),
            'sessions':len(p),'classifications':{'SW':list(SOFTWARE),'HW':list(HARDWARE)},
            'etfs':e,'stocks':stocks,'sectors':{'SW':sw,'HW':hw},
            'market':regime(sw,hw,e),'events':changes(prev,stocks),
            'chart':chronology,'backtest':backtest(p),'warnings':warnings}


def write_json(obj,destination=OUTPUT):
    destination.parent.mkdir(exist_ok=True,parents=True)
    temp=destination.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    os.replace(temp,destination)


def main():
    p=load_prices()
    res=produce(p)
    write_json(res)
    print(f'ANALYSIS OK: {res["date"]}, {res["sessions"]} sessions, market={res["market"]["label"]}, backtest={res["backtest"]["status"]}')

if __name__=='__main__':main()
