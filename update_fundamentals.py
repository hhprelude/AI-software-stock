#!/usr/bin/env python3
"""Weekly FREE financial-data enrichment with explicit provenance and nulls.

Authoritative: SEC companyfacts (after SEC_USER_AGENT is supplied).
Indicative ONLY: yfinance forward PE, market cap, PS. Not audited.
Never backfill today's reported financials into historical strategy tests.
SEC does not support CORS, so run on GitHub Actions rather than the browser.
"""
from __future__ import annotations
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from config import SOFTWARE,HARDWARE

DEST=Path(__file__).resolve().parent/'data'/'fundamentals_latest.json'
STOCKS=[*SOFTWARE,*HARDWARE]
HYPERSCALERS=('MSFT','GOOG','AMZN','META')  # company-wide CAPEX, NOT AI-only CAPEX
TAGS={
 'revenue':['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','SalesRevenueGoodsNet','RevenueFromContractWithCustomerIncludingAssessedTax'],
 'opIncome':['OperatingIncomeLoss'],
 'ocf':['NetCashProvidedByUsedInOperatingActivities'],
 'capex':['PaymentsToAcquirePropertyPlantAndEquipment']
}


def good_num(x,limit=1e18):
    return isinstance(x,(int,float)) and math.isfinite(x) and abs(x)<limit


def pick_facts(facts,tags,unit='USD'):
    """Use one taxonomy tag at a time; never combine incompatible revenue tags."""
    collections=[]
    for namespace in ('us-gaap','ifrs-full'):
        group=facts.get(namespace,{})
        for tag in tags:
            entries=group.get(tag,{}).get('units',{}).get(unit,[])
            seq=[]
            for o in entries:
                try:
                    start=date.fromisoformat(o['start']);end=date.fromisoformat(o['end']);filed=date.fromisoformat(o['filed'])
                except (KeyError,ValueError,TypeError):continue
                if o.get('form') not in ('10-Q','10-K','20-F','40-F','6-K'):continue
                if filed>date.today() or filed < end:continue
                days=(end-start).days
                if not 65<=days<=390 or not good_num(o.get('val')):continue
                seq.append({'start':str(start),'end':str(end),'filed':str(filed),'val':float(o['val']),
                            'days':days,'form':o.get('form'),'fy':o.get('fy'),'fp':o.get('fp')})
            if seq:
                collections.append((len([x for x in seq if 70<=x['days']<=110]),len(seq),namespace,tag,seq))
    if not collections:return None
    # prefer tag with more direct fiscal quarter facts, then more coverage
    collections.sort(reverse=True,key=lambda x:(x[0],x[1]))
    _,_,ns,tag,rows=collections[0]
    dedup={}
    for o in sorted(rows,key=lambda x:x['filed']):
        dedup[(o['start'],o['end'])]=o  # latest amendment filed for identical period
    return {'tag':ns+':'+tag,'data':list(dedup.values())}


def latest_year(seq):
    yr=[o for o in seq if 330<=o['days']<=390]
    return max(yr,key=lambda x:(x['end'],x['filed'])) if yr else None


def latest_quarter(seq):
    q=[o for o in seq if 70<=o['days']<=110]
    return max(q,key=lambda x:(x['end'],x['filed'])) if q else None


def comparable_previous(rows,record):
    a=date.fromisoformat(record['end'])
    candidates=[x for x in rows if x['days']>=record['days']-15 and x['days']<=record['days']+15 and
                330 <= (a-date.fromisoformat(x['end'])).days <= 410]
    return min(candidates,key=lambda x:abs((a-date.fromisoformat(x['end'])).days-365)) if candidates else None


def pct_new_over_old(cur,old):
    if not cur or not old or not good_num(cur['val']) or not good_num(old['val']):return None
    if old['val']<=0:return None
    return round((cur['val']/old['val']-1)*100,2)


def ttm_four(rows):
    """Require 4 real distinct ~quarter-length observations with contiguous dates."""
    qs=sorted([r for r in rows if 70<=r['days']<=110],key=lambda r:r['end'],reverse=True)
    picked=[]
    for q in qs:
        if not picked:
            picked.append(q)
        elif q['end']==picked[-1]['end']:
            continue
        elif 75 <= (date.fromisoformat(picked[-1]['end'])-date.fromisoformat(q['end'])).days <= 110:
            picked.append(q)
        else:
            break
        if len(picked)==4:break
    return sum(x['val'] for x in picked) if len(picked)==4 else None


def extract_sec(facts):
    """SEC supports GAAP/IFRS public facts, but definitions may not be strictly comparable."""
    results={}
    for name,taglist in TAGS.items():
        results[name]=pick_facts(facts,taglist)
    rv=results['revenue'];oper=results['opIncome'];ocf=results['ocf'];capex=results['capex']
    out={'secStatus':'unavailable','revenueYoY':None,'revenuePeriod':'',
         'revenueFiled':'','revenueMetric':'','revenueTTMUSD':None,
         'revenueAnnualUSD':None,'opMarginAnnual':None,'fcfMarginAnnual':None,'secSource':None}
    if not rv:return out
    out['secStatus']='ok'
    annual=latest_year(rv['data']);quarter=latest_quarter(rv['data']);recent=quarter or annual
    if recent:
        comp=comparable_previous(rv['data'],recent)
        if not comp and annual and annual!=recent:
            comp=comparable_previous(rv['data'],annual)
            if comp: recent=annual
        out['revenueYoY']=pct_new_over_old(recent,comp)
        out['revenuePeriod']=recent['end'];out['revenueFiled']=recent['filed']
        out['revenueMetric']='분기' if recent['days']<=110 else '연간'
    if annual:
        out['revenueAnnualUSD']=round(annual['val'])
    out['revenueTTMUSD']=round(ttm_four(rv['data'])) if ttm_four(rv['data']) is not None else None
    if annual and annual['val']>0:
        for metric,key in [(oper,'opMarginAnnual'),(ocf,'fcfMarginAnnual')]:
            if metric:
                match=next((x for x in metric['data'] if x['start']==annual['start'] and x['end']==annual['end']),None)
                if match and key=='opMarginAnnual':out[key]=round(100*match['val']/annual['val'],2)
                if match and key=='fcfMarginAnnual' and capex:
                    cap=next((x for x in capex['data'] if x['start']==annual['start'] and x['end']==annual['end']),None)
                    if cap and cap['val']>=0:out[key]=round(100*(match['val']-cap['val'])/annual['val'],2)
    out['secTag']=rv['tag']
    return out


def fetch_sec_by_cik(cik,headers,requests_mod):
    url=f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
    r=requests_mod.get(url,headers=headers,timeout=22);r.raise_for_status()
    return r.json(),url


def fetch_map(headers,requests_mod):
    r=requests_mod.get('https://www.sec.gov/files/company_tickers.json',headers=headers,timeout=25)
    r.raise_for_status()
    obj=r.json()
    return {row['ticker'].upper():int(row['cik_str']) for row in obj.values()}


def try_yahoo(t,yf):
    """Quote metrics are UNVERIFIED and can disappear/rate-limit without notice."""
    try:
        info=yf.Ticker(t).info
        def bounded(key,lo,hi):
            x=info.get(key)
            return round(float(x),2) if good_num(x) and lo<float(x)<hi else None
        return {'forwardPE':bounded('forwardPE',0,500),
                'yahooPS':bounded('priceToSalesTrailing12Months',0,500),
                'marketCapUSD':round(float(info['marketCap'])) if good_num(info.get('marketCap'),1e17) and info['marketCap']>0 else None,
                'quoteSource':'Yahoo Finance (unofficial; estimates not verified)',
                'quoteAsOfUTC':datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return {'forwardPE':None,'yahooPS':None,'marketCapUSD':None,
                'quoteSource':'Yahoo unavailable','quoteError':str(exc)[:110], 'quoteAsOfUTC':None}


def build_financials(previous=None,requests_mod=None,yf=None,ua=''):
    previous=previous or {}
    if requests_mod is None:import requests as requests_mod
    if yf is None:import yfinance as yf
    # An identifiable SEC UA is required. No fake or synthetic identity.
    enabled=bool(ua and '@' in ua and ' ' in ua)
    heads={'User-Agent':ua,'Accept-Encoding':'gzip, deflate','Accept':'application/json'} if enabled else None
    cik={}; notes=[]
    if enabled:
        try:cik=fetch_map(heads,requests_mod)
        except Exception as e:notes.append('SEC ticker map failed: '+str(e)[:130])
    else: notes.append('SEC_USER_AGENT not configured: official SEC feed disabled')
    result={}
    sec_cache={}
    for t in STOCKS:
        old=previous.get('stocks',{}).get(t,{})
        row={'ticker':t,'group':'SW' if t in SOFTWARE else 'HW',
             'secStatus':'not-configured' if not enabled else 'unavailable',
             'revenueYoY':None,'revenuePeriod':'','revenueFiled':'','revenueMetric':'',
             'revenueTTMUSD':None,'revenueAnnualUSD':None,'opMarginAnnual':None,
             'fcfMarginAnnual':None,'secSource':None,'forwardPE':None,'yahooPS':None,
             'marketCapUSD':None,'quoteSource':'none','quoteAsOfUTC':None}
        if enabled and t in cik:
            try:
                obj,url=fetch_sec_by_cik(cik[t],heads,requests_mod)
                sec_cache[t]=(obj,url)
                row.update(extract_sec(obj.get('facts',{})))
                row['secSource']=url
            except Exception as e:
                row['secStatus']='error';row['secError']=str(e)[:130]
        if row['secStatus']!='ok' and old.get('secStatus') in ('ok','cached'):
            # preserve stale official observations with original filing period/date
            for key in ('revenueYoY','revenuePeriod','revenueFiled','revenueMetric','revenueTTMUSD',
                        'revenueAnnualUSD','opMarginAnnual','fcfMarginAnnual','secSource','secTag'):
                row[key]=old.get(key)
            row['secStatus']='cached'
        row.update(try_yahoo(t,yf))
        # SEC TTM USD and Yahoo market cap are different sources: label as approximate.
        if (t!='TSM' and good_num(row.get('revenueTTMUSD')) and row['revenueTTMUSD']>0 and
                good_num(row.get('marketCapUSD')) and row['marketCapUSD']>0):
            row['estimatedPS']=round(row['marketCapUSD']/row['revenueTTMUSD'],2)
            row['psSource']='SEC 4 direct quarters + Yahoo unofficial quote market cap; indicative'
        else:
            row['estimatedPS']=row.get('yahooPS')
            row['psSource']='Yahoo Finance unofficial PS estimate' if row['estimatedPS'] is not None else None
        # No false precision: TSM is an ADR/IFRS reporting issuer.
        if t=='TSM':row['adrWarning']='TSM은 ADR·IFRS 자료. USD·원주식 비율 단순 환산 금지.'
        result[t]=row
        time.sleep(.15) # respect SEC fair-access policy
    capex_data={}
    if enabled:
        for t in HYPERSCALERS:
            if t not in cik:
                capex_data[t]={'status':'missing CIK'};continue
            try:
                obj,url=sec_cache[t] if t in sec_cache else fetch_sec_by_cik(cik[t],heads,requests_mod)
                capex=pick_facts(obj.get('facts',{}),TAGS['capex'])
                yr=latest_year(capex['data']) if capex else None
                prev=comparable_previous(capex['data'],yr) if capex and yr else None
                capex_data[t]={'status':'ok' if yr else 'missing',
                               'annualCapexUSD':round(yr['val']) if yr else None,
                               'annualYoY':pct_new_over_old(yr,prev),
                               'periodEnd':yr['end'] if yr else None,
                               'filed':yr['filed'] if yr else None,'source':url}
            except Exception as e:capex_data[t]={'status':'error','error':str(e)[:120]}
            time.sleep(.15)
    return {'version':'3.0','generatedUTC':datetime.now(timezone.utc).isoformat(),
            'secConfigured':enabled,'notes':notes,'stocks':result,'hyperscalers':capex_data,
            'disclaimer':'SEC filing data (historical reported) vs Yahoo (unverified forward estimates); never used in historical backtest'}


def main():
    previous={}
    if DEST.exists():
        try:previous=json.loads(DEST.read_text(encoding='utf-8'))
        except (ValueError,OSError):pass
    data=build_financials(previous,ua=os.environ.get('SEC_USER_AGENT',''))
    DEST.parent.mkdir(exist_ok=True,parents=True)
    tmp=DEST.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    os.replace(tmp,DEST)
    sec_count=sum(x['secStatus']=='ok' for x in data['stocks'].values())
    quote_count=sum(x.get('forwardPE') is not None for x in data['stocks'].values())
    print(f'FINANCIALS: SEC OK {sec_count}/{len(STOCKS)}; Yahoo forwardPE indicative {quote_count}/{len(STOCKS)}; SEC UA configured {data["secConfigured"]}')

if __name__=='__main__':main()
