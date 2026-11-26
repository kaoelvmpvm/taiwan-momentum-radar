"""Predeclared long-only adaptations of Chen et al. (2023, 2018).
Same provisional historical membership and price matrices; no parameter search.
All signals use known dates only. Not a replication of the papers' long-short, overlapping portfolios."""
import json,math,gzip,hashlib
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
import pit_historical_backtest as pit
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-persistence-output"
CACHE=ROOT/"pit-prior-backtest"/"official_execution"
KEYS=("S0","M3","P1","P0_6","P0_7","A1","A0","A2")
def gamma(prices):
    # Scale invariant price-level curvature, not log-return regression.
    v=np.asarray(prices,dtype=float)
    if len(v)<200 or not np.isfinite(v).all() or np.min(v)<=0:raise ValueError("invalid curvature prices")
    t=np.linspace(0.,1.,len(v))
    return float(np.linalg.lstsq(np.column_stack((np.ones(len(v)),t,t*t)),v/v[0],rcond=None)[0][2])
def persistent(current,previous):
    a=current[:math.ceil(.30*len(current))]
    b=set(previous[:math.ceil(.30*len(previous))])
    return [c for c in a if c in b][:10]
def baseline(dates,p,months):
    q=p-pd.DateOffset(months=months)
    i=dates.searchsorted(q,side="right")-1
    if i<0 or (q-dates[i]).days>10:raise ValueError("missing baseline")
    return dates[i]
def ordered(cl,dates,p,months,eligible,anoms,minquotes):
    b=baseline(dates,p,months)
    scores={}
    for c in eligible:
        if pd.isna(cl.at[b,c]) or pd.isna(cl.at[p,c]) or cl.loc[b:p,c].count()<minquotes or len(anoms[c].loc[b:p]):continue
        scores[c]=float(cl.at[p,c]/cl.at[b,c]-1)
    return sorted(scores,key=lambda c:(-scores[c],c)),b
def make_plans(dates,cl,records,base,anoms):
    plans={k:{} for k in KEYS};audit=[];previous=None;previous_day=None
    for rec in records:
        p=pd.Timestamp(rec["signal"]);d=pd.Timestamp(rec["execution"])
        eligible=[c for c in rec["rank"] if c!="0050"]
        rank6,b6=ordered(cl,dates,p,6,eligible,anoms,100)
        rank7,b7=ordered(cl,dates,p,7,eligible,anoms,115)
        if previous is not None:
            chosen=persistent(rank6,previous)
            n=len(chosen)
            if n>len(rank7):raise ValueError("matched P0_7 insufficient")
            # Same target exposure, including when there are fewer than ten persistent winners.
            pt=lambda names:{c:.1 for c in names}
            # Skip most recent month for both acceleration and matched price-only control.
            # As-of p membership and eligibility known at p; lagged price window ends previous month.
            lag=previous_day
            rank12,b12=ordered(cl,dates,lag,12,eligible,anoms,200)
            pool=rank12[:math.ceil(.20*len(rank12))]
            if not pool:raise ValueError("empty acceleration pool")
            g={c:gamma(cl.loc[b12:lag,c].dropna().to_numpy()) for c in pool}
            count=math.ceil(.20*len(pool))
            hi=sorted(pool,key=lambda c:(-g[c],c))[:count]
            lo=sorted(pool,key=lambda c:(g[c],c))[:count]
            target={"S0":base["S0"][d][1],"M3":base["M3"][d][1],"P1":pt(chosen),"P0_6":pt(rank6[:n]),"P0_7":pt(rank7[:n]),"A1":pit.weights(hi),"A0":pit.weights(pool[:count]),"A2":pit.weights(lo)}
            for k,v in target.items():plans[k][d]=(p,v)
            audit.append({"signal":str(p.date()),"execution":str(d.date()),"persistency_prior":str(previous_day.date()),"base6":str(b6.date()),"base7":str(b7.date()),"acceleration_end":str(lag.date()),"acceleration_base":str(b12.date()),"rank6_count":len(rank6),"persistent_count":n,"acceleration_pool":len(pool),"acceleration_selected_count":count,"gamma":g,"weights":target})
        previous=rank6;previous_day=p
    if len(audit)!=76:raise ValueError("unexpected common signal count")
    return plans,audit
def cached_quote(day):
    path=CACHE/f"{day}.json.gz"
    if not path.exists():return original_quote(day)
    raw=gzip.decompress(path.read_bytes());x=json.loads(raw)
    if x.get("stat")!="OK" or str(x.get("date"))!=day.replace("-",""):raise ValueError("cached quote wrong date")
    fs={"證券代號","開盤價","收盤價","成交股數"}
    ts=[t for t in x["tables"] if fs<=set(t.get("fields",[]))]
    if len(ts)!=1:raise ValueError("cached quote ambiguous")
    t=ts[0];idx=[t["fields"].index(k) for k in ("證券代號","開盤價","收盤價","成交股數")]
    rows={}
    for row in t["data"]:
        c=str(row[idx[0]]).strip()
        try:o,z,v=[float(str(row[j]).replace(",","")) for j in idx[1:]]
        except (ValueError,TypeError):continue
        if min(o,z,v)>0:rows[c]=(o,z,v)
    if "0050" not in rows or len(rows)<500:raise ValueError("incomplete cached market")
    (pit.OUT/"official_execution"/path.name).write_bytes(path.read_bytes())
    return rows,hashlib.sha256(raw).hexdigest()
original_quote=pit.quote
def execution(paths,op,cl):
    # Reuse cached verified trading-day data. Fetch newly needed dates only.
    dates=sorted({t["date"] for r in paths.values() for t in r["trades"]})
    q={};errors={}
    for d in dates:
        try:q[d]=cached_quote(d)[0]
        except Exception as e:errors[d]=str(e)
    result={}
    for k,r in paths.items():
        counts=Counter();issues=[]
        for t in r["trades"]:
            d=t["date"];p=pd.Timestamp(d)
            for c,v in t["moves"].items():
                counts["orders"]+=1
                if d not in q or c not in q[d]:
                    counts["unverified"]+=1;issues.append({"date":d,"code":c,"reason":"missing_official_trade"});continue
                o,z,vol=q[d][c]
                residual=float((op.at[p,c]/cl.at[p,c])/(o/z)-1)
                if abs(residual)>.001:
                    counts["ratio_mismatch"]+=1;issues.append({"date":d,"code":c,"residual":residual})
                else:counts["ratio_match"]+=1
                if abs(v)/(o*vol)>.01:
                    counts["volume_proxy_flag"]+=1;issues.append({"date":d,"code":c,"reason":"notional_over_1pct_daily_volume_proxy"})
        result[k]={"counts":dict(counts),"issues":issues}
    return {"dates":len(dates),"source_errors":errors,"strategies":result}
def diagnostics(full,pre,stress,double):
    pairs=[("P1","P0_6"),("P1","P0_7"),("A1","A0"),("A1","A2")]
    results=[]
    for a,b in pairs:
        if any(s[k]["summary"]["status"]!="PROVISIONAL_COMPLETE" for s in (full,pre,stress,double) for k in (a,b)):
            results.append({"test":a,"control":b,"status":"incomplete"});continue
        x=full[a]["summary"];y=full[b]["summary"]
        criteria={"full_sharpe_higher":x["sharpe"]>y["sharpe"],"full_mdd_not_worse":x["mdd"]>=y["mdd"],"full_cagr_higher":x["cagr"]>y["cagr"],"pre2026_sharpe_higher":pre[a]["summary"]["sharpe"]>pre[b]["summary"]["sharpe"],"double_cost_cagr_higher":double[a]["summary"]["cagr"]>double[b]["summary"]["cagr"],"stress_mdd_not_worse":stress[a]["summary"]["mdd"]>=stress[b]["summary"]["mdd"]}
        results.append({"test":a,"control":b,"criteria":criteria,"all_pass":all(criteria.values()),"cagr_difference":x["cagr"]-y["cagr"],"sharpe_difference":x["sharpe"]-y["sharpe"]})
    return results
def main():
    OUT.mkdir(exist_ok=True);pit.OUT=OUT/"boundary";(pit.OUT/"official_execution").mkdir(parents=True,exist_ok=True)
    pit.quote=cached_quote
    dates,op,cl,raw=pit.load_prices()
    rosters=json.loads((pit.T/"provisional_monthly_rosters.json").read_text())
    base,records,anoms=pit.build(dates,cl,rosters,"historical")
    # Reproduce previous baseline before evaluating new rules.
    reproduced=pit.simulate(dates,op,cl,anoms,base["M3"],pit.START,pit.END)
    if reproduced["summary"]["status"]!="PROVISIONAL_COMPLETE" or abs(reproduced["summary"]["terminal"]-601690.9885569563)>1.:raise ValueError("previous historical M3 did not reproduce")
    plans,audit=make_plans(dates,cl,records,base,anoms)
    start=min(plans["P1"]);end=pit.END
    def run(a,z,fee=1):
        return {k:pit.simulate(dates,op,cl,anoms,plans[k],a,z,fee,k=="S0") for k in KEYS}
    full=run(start,end);double=run(start,end,2)
    pre=run(start,dates[dates.searchsorted(pd.Timestamp("2025-12-31"),side="right")-1])
    stress=run(dates[dates.searchsorted(pd.Timestamp("2026-01-01"))],end)
    results={"status":"PROVISIONAL_RESEARCH_NOT_CERTIFIED","period":[str(start.date()),str(end.date())],"signals":len(audit),"baseline_reproduced":reproduced["summary"]["terminal"],"summary":{k:r["summary"] for k,r in full.items()},"pre2026":{k:r["summary"] for k,r in pre.items()},"stress2026":{k:r["summary"] for k,r in stress.items()},"double_cost":{k:r["summary"] for k,r in double.items()},"audits":{k:r["audit"] for k,r in full.items()},"diagnostic":diagnostics(full,pre,stress,double),"counts":{"P1_min":min(x["persistent_count"] for x in audit),"P1_max":max(x["persistent_count"] for x in audit),"A1_min":min(x["acceleration_selected_count"] for x in audit),"A1_max":max(x["acceleration_selected_count"] for x in audit)},"limitations":["Long-only monthly adaptation, not original overlapping 6-month holding strategy.","Historical roster remains provisional; mixed adjustment conventions, 83 candidate returns lack official endpoint evidence.","Acceleration normalized to starting price to avoid nominal price-scale confounding; original paper's price-level coefficient not identical.","No historical daily shares-outstanding dataset: turnover experiment not run.","In-sample exploration, not independent holdout. No live strategy changes."]}
    (OUT/"summary.json").write_text(json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False))
    (OUT/"signals.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False))
    (OUT/"trades.json").write_text(json.dumps({k:r["trades"] for k,r in full.items()},ensure_ascii=False,allow_nan=False))
    pd.DataFrame({k:r["nav"] for k,r in full.items()}).to_csv(OUT/"daily_nav.csv")
    small={k:v for k,v in results.items() if k!="audits"}
    small["audits"]={k:{"blocked":r["audit"]["blocked"],"delayed_sessions":r["audit"]["delayed_sessions"],"stale_marks":len(r["audit"]["stale_marks"]),"held_jumps":r["audit"]["held_jumps"]} for k,r in full.items()}
    print("PIT_PERSISTENCE_RESULTS",json.dumps(small,ensure_ascii=False,allow_nan=False),flush=True)
    ex=execution(full,op,cl);results["execution"]=ex
    (OUT/"summary.json").write_text(json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False))
    print("PIT_PERSISTENCE_EXECUTION",json.dumps({"dates":ex["dates"],"errors":ex["source_errors"],"strategies":{k:v["counts"] for k,v in ex["strategies"].items()}},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
