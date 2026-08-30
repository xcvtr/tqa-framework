#!/usr/bin/env python3
"""LSR-CROSS direction analysis — LONG vs SHORT PnL breakdown.

Usage:
    cd /home/user/projects/tqa-framework
    source ~/.hermes/hermes-agent/venv/bin/activate
    python scripts/analyze_dir.py [--days 1095]
"""
from __future__ import annotations
import argparse, itertools, json, logging, sys, time
from collections import OrderedDict
from datetime import datetime
from statistics import mean, stdev
import numpy as np
import requests as _req

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("dir")
sys.path.insert(0, "/home/user/projects/tqa-framework")
from tqa_framework.engine.detect import load_m1_from_ch

CH_HOST, CH_DB = "http://10.0.0.60:8123", "crypto"
TICKERS = ["ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","NEARUSDT","APTUSDT","ARBUSDT","OPUSDT"]
EXCLUDE_SYMS = {"SOLUSDT"}
SYM_RISK = {"ADAUSDT":1.3,"ARBUSDT":1.2,"OPUSDT":1.1,"AVAXUSDT":1.05,"DOGEUSDT":1.0,"XRPUSDT":1.0,"ETHUSDT":1.0,"BNBUSDT":1.0,"LINKUSDT":1.0,"NEARUSDT":0.9,"APTUSDT":0.9}

def parse_ts(val):
    if isinstance(val, datetime): return val.replace(tzinfo=None) if val.tzinfo else val
    return datetime.fromisoformat(str(val).replace("Z","+00:00")).replace(tzinfo=None)

def get_end_time():
    r = _req.get(CH_HOST, params={"query":f"SELECT max(timestamp) FROM {CH_DB}.klines WHERE symbol='ETHUSDT' AND interval='5m' FORMAT TabSeparated"}, timeout=15)
    return r.text.strip()

def load_lsr_data(symbol, hours=24):
    end = get_end_time()
    r = _req.get(CH_HOST, params={"query":f"""
    SELECT timestamp, ratio FROM crypto.long_short_ratio WHERE symbol='{symbol}' AND source='bybit_global'
    AND timestamp>=toDateTime64('{end}',3,'UTC')-INTERVAL {hours} HOUR
    AND timestamp<=toDateTime64('{end}',3,'UTC') ORDER BY timestamp FORMAT JSONEachRow"""}, timeout=30)
    raw = r.text.strip()
    if not raw: return []
    lsr = [json.loads(l) for l in raw.split('\n') if l.strip()]
    if not lsr: return []
    ratios = [row['ratio'] for row in lsr]
    zs = [0.0]*len(ratios)
    for i in range(len(ratios)):
        start = max(0,i-720+1)
        if i-start+1>=100:
            s=ratios[start:i+1]; m=mean(s); sd=stdev(s) if len(s)>1 else 0.001
            zs[i]=(ratios[i]-m)/sd if sd>0 else 0
    sigs=[]
    for i in range(1,len(zs)):
        if zs[i-1]<2 and zs[i]>=2: sigs.append({'ts':lsr[i]['timestamp'],'symbol':symbol,'direction':'SHORT','zscore':zs[i]})
        elif zs[i-1]>-2 and zs[i]<=-2: sigs.append({'ts':lsr[i]['timestamp'],'symbol':symbol,'direction':'LONG','zscore':zs[i]})
    return sigs

def preload(days, end_time):
    np5,np1,pm,ev={},{},{},[]
    for sym in TICKERS:
        if sym in EXCLUDE_SYMS: continue
        b5=load_m1_from_ch(sym,days*24,CH_HOST,CH_DB,end_time=end_time,source="bars",interval="5m")
        b1=load_m1_from_ch(sym,days*24,CH_HOST,CH_DB,end_time=end_time,source="bars",interval="1m")
        if not b5 or len(b5)<200: continue
        b1=b1 or b5
        ls=load_lsr_data(sym,days*24+720)
        if not ls: continue
        def _ts(v):
            if isinstance(v,str): return datetime.fromisoformat(v.replace("Z","+00:00").rsplit("+")[0]).timestamp()
            return v.timestamp()
        np5[sym]=(np.array([_ts(b["ts"]) for b in b5],dtype=np.float64),np.array([b["close"] for b in b5],dtype=np.float64))
        np1[sym]=(np.array([_ts(b["ts"]) for b in b1],dtype=np.float64),np.array([b["high"] for b in b1],dtype=np.float64),np.array([b["low"] for b in b1],dtype=np.float64),np.array([b["close"] for b in b1],dtype=np.float64))
        pm[sym]=OrderedDict((parse_ts(b["ts"]),b["close"]) for b in b1)
        for s in ls:
            td=parse_ts(s['ts'])
            if td>=parse_ts(end_time)-__import__('datetime').timedelta(hours=days*24):
                ev.append((td,sym,1 if s['direction']=='LONG' else -1,s['direction']))
    logger.info(f"Preloaded: {len(np5)} symbols, {len(ev)} events")
    return np5,np1,pm,ev

def simulate_trade(symbol,event_ts,direction_int,np5,np1,sl=0.05,tp=0,hold_h=168,pyr_tr=0.05,pyr_add=0.5,tr_act=0.04,tr_dist=0.015,tr_lock=0):
    if symbol not in np5 or symbol not in np1: return None,None,None
    pt5,pc5=np5[symbol]; pt,ph,pl,pc=np1[symbol]
    ei=np.searchsorted(pt5,parse_ts(event_ts).timestamp(),side="right")
    if ei+1>=len(pc5): return None,None,None
    entry=float(pc5[ei]); si=np.searchsorted(pt,pt5[ei]+(pt5[1]-pt5[0]),side="right")
    if si+1>=len(pc): return None,None,None
    end=min(si+int(hold_h*60),len(pc)-1)
    d=direction_int
    sp=entry*(1-sl) if d==1 else entry*(1+sl)
    tp_px=entry*(1+tp) if d==1 else entry*(1-tp)
    pp=entry*(1+pyr_tr) if d==1 else entry*(1-pyr_tr)
    ex_px=None; exit_ts=None; br=1.0; added=False; trail=False; pk=entry
    for j in range(si,end+1):
        hi,lo=float(ph[j]),float(pl[j])
        if d==1:
            if not added and pyr_tr>0 and hi>=pp: added=True; br+=pyr_add
            if tr_act>0:
                if not trail and hi>=entry*(1+tr_act): trail=True; pk=hi; sp=entry
                if trail:
                    if hi>pk: pk=hi
                    ns=float(pk*(1-tr_dist))
                    if tr_lock>0: ns=max(ns,entry*(1+tr_lock))
                    sp=max(sp,ns)
            if lo<=sp: ex_px=float(sp); exit_ts=pt[j]; break
            if not trail and tp>0 and hi>=tp_px: ex_px=float(tp_px); exit_ts=pt[j]; break
        else:
            if not added and pyr_tr>0 and lo<=pp: added=True; br+=pyr_add
            if tr_act>0:
                if not trail and lo<=entry*(1-tr_act): trail=True; pk=lo; sp=entry
                if trail:
                    if lo<pk: pk=lo
                    ns=float(pk*(1+tr_dist))
                    if tr_lock>0: ns=min(ns,entry*(1-tr_lock))
                    sp=min(sp,ns)
            if hi>=sp: ex_px=float(sp); exit_ts=pt[j]; break
            if not trail and tp>0 and lo<=tp_px: ex_px=float(tp_px); exit_ts=pt[j]; break
    if ex_px is None: ex_px=float(pc[end]); exit_ts=pt[end]
    comm=0.0010+0.0005
    pnl=(ex_px-entry)/entry-comm if d==1 else (entry-ex_px)/entry-comm
    return pnl*br, entry, exit_ts

def portfolio_bt(events,np5,np1,pm,risk=0.08,conc=6,lev=1,eq0=1000,**kw):
    trades=[]
    for td,sym,d,dir_str in events:
        if sym not in np5: continue
        pnl_eff,entry_px,exit_ts_float = simulate_trade(sym,td,d,np5,np1,**kw) or (None,None,None)
        if pnl_eff is None: continue
        exit_ts_dt = datetime.utcfromtimestamp(exit_ts_float) if exit_ts_float else td
        trades.append({'ts':td,'sym':sym,'dir':d,'entry':entry_px,'exit_ts':exit_ts_dt,'pnl':pnl_eff,'eq_at':None,'opened':False})
    trades.sort(key=lambda x:x['ts'])
    all_ts=sorted(set().union(*[pm[s].keys() for s in pm]))
    eq,cash,peak,mdd=eq0,eq0,eq0,0.0
    nxt,act=0,[]
    for bt_ts in all_ts:
        while nxt<len(trades):
            p=trades[nxt]
            if p['ts']<=bt_ts:
                if len(act)<conc: p['eq_at']=float(eq); p['opened']=True; act.append(p)
                nxt+=1
            else: break
        still=[]
        for p in act:
            if p['eq_at'] is None: continue
            exit_ts=p.get('exit_ts',p['ts'])
            if exit_ts is not None and exit_ts < bt_ts:
                r=risk*SYM_RISK.get(p['sym'],1.0)
                cash+=p['eq_at']*r*lev*p['pnl']
                p['eq_at']=None
            else: still.append(p)
        act=still
        pv=cash
        for p in act:
            if p['eq_at'] is None: continue
            px=pm[p['sym']].get(bt_ts)
            if px is not None:
                r=risk*SYM_RISK.get(p['sym'],1.0)
                mtm=(px-p['entry'])/p['entry'] if p['dir']==1 else (p['entry']-px)/p['entry']
                pv+=p['eq_at']*r*lev*mtm
        eq=pv
        if eq>peak: peak=eq
        mdd=max(mdd,(peak-eq)/peak*100)
    for p in trades:
        if p['opened'] and p['eq_at'] is not None:
            r=risk*SYM_RISK.get(p['sym'],1.0)
            cash+=p['eq_at']*r*lev*p['pnl']
    ret=(cash-eq0)/eq0*100
    return ret,mdd,sum(1 for p in trades if p['opened']),trades

if __name__=="__main__":
    days=1095
    end=get_end_time()
    logger.info(f"End: {end}, days={days}")
    data=preload(days,end)
    np5,np1,pm,events=data

    best_conf=dict(sl=0.05,tp=0,hold_h=168,pyr_tr=0.05,pyr_add=0.5,tr_act=0.04,tr_dist=0.015,tr_lock=0)

    # Direction analysis
    long_ev=[e for e in events if e[3]=='LONG']
    short_ev=[e for e in events if e[3]=='SHORT']

    logger.info(f"\nTotal events: {len(events)} — LONG: {len(long_ev)}, SHORT: {len(short_ev)}")

    # Per-direction PnL (raw, no portfolio)
    def raw_pnl(evs):
        results=[simulate_trade(s,td,d,np5,np1,**best_conf) for td,s,d,_ in evs]
        pnls=[r[0] for r in results if r[0] is not None]
        if not pnls: return 0,0,0,0,0
        return len(pnls), sum(pnls), mean(pnls), sum(1 for p in pnls if p>0)/len(pnls)*100, stdev(pnls) if len(pnls)>1 else 0

    logger.info("\nRaw PnL (no portfolio):")
    for label,evs in [("LONG",long_ev),("SHORT",short_ev),("ALL",events)]:
        n,tot,avg,wr,sd=raw_pnl(evs)
        logger.info(f"  {label:>6}: n={n:>4} tot={tot:>+8.4f} avg={avg:>+8.4f} wr={wr:>5.1f}% sd={sd:.4f}")

    # Portfolio MTM per direction
    logger.info(f"\nPortfolio MTM (risk=0.08, conc=6, best_conf):")
    for label,evs in [("LONG",long_ev),("SHORT",short_ev),("ALL",events)]:
        ret,mdd,nt,_=portfolio_bt(evs,np5,np1,pm,0.08,6,**best_conf)
        cagr=(1+ret/100)**(1/3)-1
        logger.info(f"  {label:>6}: ret={ret:>+8.2f}% CAGR={cagr*100:>5.1f}% DD={mdd:>5.1f}% trades={nt}")

    # Direction asymmetry: try LONG-only with higher risk
    logger.info(f"\nLONG-only risk sweep:")
    for r in [0.10,0.12,0.15,0.20]:
        ret,mdd,nt,_=portfolio_bt(long_ev,np5,np1,pm,r,6,**best_conf)
        cagr=(1+ret/100)**(1/3)-1
        s="✓" if mdd<=15 else ""
        logger.info(f"  risk={r:.2f} LONG: ret={ret:>+8.2f}% CAGR={cagr*100:>5.1f}% DD={mdd:>5.1f}% trades={nt} {s}")

    logger.info(f"\nSHORT-only risk sweep:")
    for r in [0.10,0.12,0.15,0.20]:
        ret,mdd,nt,_=portfolio_bt(short_ev,np5,np1,pm,r,6,**best_conf)
        cagr=(1+ret/100)**(1/3)-1
        s="✓" if mdd<=15 else ""
        logger.info(f"  risk={r:.2f} SHORT: ret={ret:>+8.2f}% CAGR={cagr*100:>5.1f}% DD={mdd:>5.1f}% trades={nt} {s}")

    # Per-symbol direction asymmetry
    logger.info(f"\nDirection asymmetry by symbol:")
    logger.info(f"{'Symbol':>8} {'LONG_n':>6} {'LONG_avg':>9} {'LONG_WR':>7} {'SHORT_n':>7} {'SHORT_avg':>9} {'SHORT_WR':>8}")
    logger.info("-"*60)
    for sym in sorted(set(e[1] for e in events)):
        lp=[simulate_trade(s,td,d,np5,np1,**best_conf) for td,s,d,_ in long_ev if s==sym]
        sp=[simulate_trade(s,td,d,np5,np1,**best_conf) for td,s,d,_ in short_ev if s==sym]
        lp=[x[0] for x in lp if x[0] is not None]; sp=[x[0] for x in sp if x[0] is not None]
        if not lp and not sp: continue
        ln,la,lw=len(lp),mean(lp) if lp else 0,sum(1 for x in lp if x>0)/len(lp)*100 if lp else 0
        sn,sa,sw=len(sp),mean(sp) if sp else 0,sum(1 for x in sp if x>0)/len(sp)*100 if sp else 0
        logger.info(f"{sym:>8} {ln:>6} {la:>+9.4f} {lw:>5.1f}% {sn:>7} {sa:>+9.4f} {sw:>5.1f}%")
