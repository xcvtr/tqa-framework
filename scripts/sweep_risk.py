#!/usr/bin/env python3
"""LSR-CROSS risk sweep on ALL portfolio with best pure trail config.

Runs portfolio MTM at risk=0.08/0.10/0.12/0.15/0.20 for ALL events.
Outputs real CAGR + DD. Target: CAGR>100% with DD<15%.
"""
import sys, json, logging, time
from collections import OrderedDict
from datetime import datetime
from statistics import mean, stdev
import numpy as np
import requests as _req
logging.basicConfig(level=logging.INFO, format="%(message)s")
sys.path.insert(0,"/home/user/projects/tqa-framework")
from tqa_framework.engine.detect import load_m1_from_ch

CH_HOST,CH_DB="http://10.0.0.60:8123","crypto"
TICKERS=["ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","NEARUSDT","APTUSDT","ARBUSDT","OPUSDT"]
EXCLUDE_SYMS={"SOLUSDT"}
SYM_RISK={"ADAUSDT":1.3,"ARBUSDT":1.2,"OPUSDT":1.1,"AVAXUSDT":1.05}
def parse_ts(v):
    if isinstance(v,datetime): return v.replace(tzinfo=None) if v.tzinfo else v
    return datetime.fromisoformat(str(v).replace("Z","+00:00")).replace(tzinfo=None)

def load_data():
    end=_req.get(CH_HOST,params={"query":f"SELECT max(timestamp) FROM {CH_DB}.klines WHERE symbol='ETHUSDT' AND interval='5m' FORMAT TabSeparated"},timeout=15).text.strip()
    logger.info(f"End: {end}")
    np5,np1,pm,ev={},{},{},[]
    for sym in TICKERS:
        if sym in EXCLUDE_SYMS: continue
        b5=load_m1_from_ch(sym,1095*24,CH_HOST,CH_DB,end_time=end,source="bars",interval="5m")
        b1=load_m1_from_ch(sym,1095*24,CH_HOST,CH_DB,end_time=end,source="bars",interval="1m")
        if not b5 or len(b5)<200: continue
        b1=b1 or b5
        # LSR signals
        r=_req.get(CH_HOST,params={"query":f"SELECT timestamp,ratio FROM crypto.long_short_ratio WHERE symbol='{sym}' AND source='bybit_global' AND timestamp>=toDateTime64('{end}',3,'UTC')-INTERVAL {1095*24+720} HOUR AND timestamp<=toDateTime64('{end}',3,'UTC') ORDER BY timestamp FORMAT JSONEachRow"},timeout=30)
        raw=r.text.strip()
        if not raw: continue
        ls=[json.loads(l) for l in raw.split('\n') if l.strip()]
        ratios=[row['ratio'] for row in ls]
        zs=[0.0]*len(ratios)
        for i in range(len(ratios)):
            s=ratios[max(0,i-720+1):i+1]
            if len(s)>=100:
                m=mean(s); sd=stdev(s) if len(s)>1 else 0.001
                zs[i]=(ratios[i]-m)/sd if sd>0 else 0
        def _ts(v):
            if isinstance(v,str): return datetime.fromisoformat(v.replace("Z","+00:00").rsplit("+")[0]).timestamp()
            return v.timestamp()
        np5[sym]=(np.array([_ts(b["ts"]) for b in b5],dtype=np.float64),np.array([b["close"] for b in b5],dtype=np.float64))
        np1[sym]=(np.array([_ts(b["ts"]) for b in b1],dtype=np.float64),np.array([b["high"] for b in b1],dtype=np.float64),np.array([b["low"] for b in b1],dtype=np.float64),np.array([b["close"] for b in b1],dtype=np.float64))
        pm[sym]=OrderedDict((parse_ts(b["ts"]),b["close"]) for b in b1)
        end_dt=parse_ts(end)
        for i in range(1,len(zs)):
            td=parse_ts(ls[i]['timestamp'])
            if td<end_dt-__import__('datetime').timedelta(hours=1095*24): continue
            if zs[i-1]<2 and zs[i]>=2: ev.append((td,sym,-1))
            elif zs[i-1]>-2 and zs[i]<=-2: ev.append((td,sym,1))
    logger.info(f"Loaded: {len(np5)} syms, {len(ev)} events")
    return np5,np1,pm,ev

def simulate(sym,ts,d,np5,np1,sl=0.05,tp=0,hold=168,pt=0.05,pa=0.5,ta=0.04,td=0.015,tl=0):
    if sym not in np5: return None,None,None
    pt5,pc5=np5[sym]; pt,ph,pl,pc=np1[sym]
    ei=np.searchsorted(pt5,parse_ts(ts).timestamp(),side="right")
    if ei+1>=len(pc5): return None,None,None
    e=float(pc5[ei]); si=np.searchsorted(pt,pt5[ei]+(pt5[1]-pt5[0]),side="right")
    if si+1>=len(pc): return None,None,None
    end=min(si+int(hold*60),len(pc)-1)
    sp=e*(1-sl) if d==1 else e*(1+sl)
    pp=e*(1+pt) if d==1 else e*(1-pt)
    ex,ex_ts=None,None; br=1.0; added=False; trail=False; pk=e
    for j in range(si,end+1):
        hi,lo=float(ph[j]),float(pl[j])
        if d==1:
            if not added and pt>0 and hi>=pp: added=True; br+=pa
            if ta>0:
                if not trail and hi>=e*(1+ta): trail=True; pk=hi; sp=e
                if trail:
                    if hi>pk: pk=hi
                    ns=float(pk*(1-td))
                    if tl>0: ns=max(ns,e*(1+tl))
                    sp=max(sp,ns)
            if lo<=sp: ex=float(sp); ex_ts=pt[j]; break
        else:
            if not added and pt>0 and lo<=pp: added=True; br+=pa
            if ta>0:
                if not trail and lo<=e*(1-ta): trail=True; pk=lo; sp=e
                if trail:
                    if lo<pk: pk=lo
                    ns=float(pk*(1+td))
                    if tl>0: ns=min(ns,e*(1-tl))
                    sp=min(sp,ns)
            if hi>=sp: ex=float(sp); ex_ts=pt[j]; break
    if ex is None: ex=float(pc[end]); ex_ts=pt[end]
    pnl=((ex-e)/e-0.0015) if d==1 else ((e-ex)/e-0.0015)
    return pnl*br, e, ex_ts

def bt(evs,np5,np1,pm,risk=0.08,conc=6,conf={}):
    trades=[]
    for ts,sym,d in evs:
        pnl,e,ex=simulate(sym,ts,d,np5,np1,**conf)
        if pnl is None: continue
        trades.append({'ts':ts,'sym':sym,'dir':d,'entry':e,'exit':datetime.utcfromtimestamp(ex),'pnl':pnl,'eq':None,'op':False})
    trades.sort(key=lambda x:x['ts'])
    all_ts=sorted(set().union(*[pm[s].keys() for s in pm]))
    eq=cash=1000; peak=1000; mdd=0; nxt=0; act=[]
    for bt_ts in all_ts:
        while nxt<len(trades):
            p=trades[nxt]
            if p['ts']<=bt_ts:
                if len(act)<conc: p['eq']=float(eq); p['op']=True; act.append(p)
                nxt+=1
            else: break
        still=[]
        for p in act:
            if p['eq'] is None: continue
            if p['exit']<bt_ts:
                cash+=p['eq']*risk*SYM_RISK.get(p['sym'],1.0)*p['pnl']
                p['eq']=None
            else: still.append(p)
        act=still
        pv=cash
        for p in act:
            px=pm[p['sym']].get(bt_ts)
            if px is not None and p['eq'] is not None:
                mtm=(px-p['entry'])/p['entry'] if p['dir']==1 else (p['entry']-px)/p['entry']
                pv+=p['eq']*risk*SYM_RISK.get(p['sym'],1.0)*mtm
        eq=pv
        if eq>peak: peak=eq
        dd=(peak-eq)/peak*100
        if dd>mdd: mdd=dd
    for p in trades:
        if p['op'] and p['eq'] is not None:
            cash+=p['eq']*risk*SYM_RISK.get(p['sym'],1.0)*p['pnl']
    ret=(cash-1000)/1000*100
    nt=sum(1 for p in trades if p['op'])
    cagr=((1+ret/100)**(1/3)-1)*100
    return ret,cagr,mdd,nt

logger=logging.getLogger("sweep")
data=load_data()
np5,np1,pm,ev=data

# Best pure trail configs
confs=[
    ("Pure trail 0.015",dict(sl=0.05,tp=0,hold=168,pt=0.05,pa=0.5,ta=0.04,td=0.015,tl=0)),
    ("Pure trail 0.03 ",dict(sl=0.05,tp=0,hold=168,pt=0.05,pa=0.5,ta=0.04,td=0.03,tl=0)),
    ("ATR-trail 0.03 ",dict(sl=0.05,tp=0.18,hold=120,pt=0.05,pa=0.5,ta=0.04,td=0.03,tl=0)),
]

for name,conf in confs:
    logger.info(f"\n{'='*60}")
    logger.info(f"{name} risk sweep (ALL events):")
    logger.info(f"{'='*60}")
    logger.info(f"{'Risk':>6} {'CAGR':>7} {'DD':>7} {'Ret':>9} {'Trades':>7}")
    logger.info("-"*40)
    for risk in [0.08,0.10,0.12,0.15,0.20,0.25]:
        t0=time.time()
        ret,cagr,mdd,nt=bt(ev,np5,np1,pm,risk,6,conf)
        safe="✓" if mdd<=15 else ""
        logger.info(f"{risk:.2f}  {cagr:>6.1f}% {mdd:>6.1f}% {ret:>+8.2f}% {nt:>6} {safe}  [{time.time()-t0:.0f}s]")

# Also test SHORT-only with best conf
logger.info(f"\n{'='*60}")
logger.info(f"SHORT-only, Pure trail 0.015, risk sweep:")
logger.info(f"{'='*60}")
short_ev=[e for e in ev if e[2]==-1]
logger.info(f"{'Risk':>6} {'CAGR':>7} {'DD':>7} {'Ret':>9} {'Trades':>7}")
logger.info("-"*40)
for risk in [0.08,0.10,0.12]:
    t0=time.time()
    ret,cagr,mdd,nt=bt(short_ev,np5,np1,pm,risk,6,confs[0][1])
    safe="✓" if mdd<=15 else ""
    logger.info(f"{risk:.2f}  {cagr:>6.1f}% {mdd:>6.1f}% {ret:>+8.2f}% {nt:>6} {safe}  [{time.time()-t0:.0f}s]")

# Test without sym_risk
logger.info(f"\n{'='*60}")
logger.info(f"ALL, Pure trail 0.015, NO sym_risk (flat 1.0):")
logger.info(f"{'='*60}")
SYM_RISK_BAK=SYM_RISK.copy()
for k in SYM_RISK: SYM_RISK[k]=1.0
for risk in [0.08,0.10,0.12,0.15]:
    t0=time.time()
    ret,cagr,mdd,nt=bt(ev,np5,np1,pm,risk,6,confs[0][1])
    safe="✓" if mdd<=15 else ""
    logger.info(f"{risk:.2f}  {cagr:>6.1f}% {mdd:>6.1f}% {ret:>+8.2f}% {nt:>6} {safe}  [{time.time()-t0:.0f}s]")