"""Four fixed theory-driven quality experiments. No parameter search or live trades."""
import json,math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import minimize
import exploratory_rotation as original
from regime_experiments import simulate,weights,START,END,make_plans,compare,WARM
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"quality-output"
KEYS=("S0","M3","M8","Q1","Q2","Q3","Q4")
WINDOW=126

def info_discreteness(prices):
    r=prices.pct_change(fill_method=None).dropna()
    if len(r)<180:return None
    cr=prices.iloc[-1]/prices.iloc[0]-1
    return float(np.sign(cr)*((r<0).mean()-(r>0).mean()))

def allocation(returns):
    cov=returns.cov().to_numpy()
    sig=np.sqrt(np.diag(cov))
    if not np.isfinite(cov).all() or np.any(sig<=0):raise ValueError("invalid covariance")
    iv=1/sig;iv/=iv.sum()
    corr=cov/np.outer(sig,sig)
    # Equivalent convex formulation: minimize y'Corr y on simplex, then w ∝ y/sigma.
    # Long-only maximum diversification; no fitted returns or tuned regularization.
    n=len(sig)
    fit=minimize(lambda y:float(y@corr@y),np.ones(n)/n,jac=lambda y:2*corr@y,
        bounds=[(0.,1.)]*n,constraints=[{"type":"eq","fun":lambda y:y.sum()-1,"jac":lambda y:np.ones(n)}],
        method="SLSQP",options={"ftol":1e-12,"maxiter":500})
    if not fit.success or abs(fit.x.sum()-1)>1e-7 or np.min(fit.x)<-1e-7:raise ValueError("diversification optimization failed")
    y=np.maximum(fit.x,0);md=y/sig;md/=md.sum()
    def dr(w):return float((w@sig)/np.sqrt(w@cov@w))
    if dr(md)+1e-7<dr(iv):raise ValueError("MD below feasible IV")
    return iv,md,{"iv_dr":dr(iv),"md_dr":dr(md),"ew_dr":dr(np.ones(n)/n),"mean_corr":float(corr[np.triu_indices(n,1)].mean())}

def quality_plans(dates,cl,anoms,sigs,base):
    plans={k:{} for k in KEYS};audit=[]
    for day,(p,rank) in sigs.items():
        if day<START:continue
        b=dates[dates.searchsorted(p-pd.DateOffset(years=1),side="right")-1]
        top=rank[:10]
        pool=rank[:math.ceil(len(rank)*.2)]
        # Keep original 12m momentum eligibility; rerank top quintile only.
        ids={c:info_discreteness(cl.loc[b:p,c]) for c in pool}
        valid=[c for c in pool if ids[c] is not None]
        if len(valid)<10:raise ValueError("Q1 insufficient ID coverage")
        q1=sorted(valid,key=lambda c:(ids[c],rank.index(c)))[:10]
        highs={c:float(cl.at[p,c]/cl.loc[b:p,c].max()) for c in pool}
        q2=sorted(pool,key=lambda c:(-highs[c],rank.index(c)))[:10]
        r=cl.loc[:p,top].tail(WINDOW+1).pct_change(fill_method=None).iloc[1:].dropna()
        fallback=len(r)<100
        if fallback:
            iv=md=np.ones(10)/10
            diag={"fallback":"fewer than 100 common valid daily returns"}
        else:
            iv,md,diag=allocation(r)
        targets={"S0":base["S0"][day][1],"M3":base["M3"][day][1],"M8":base["M8"][day][1],
                 "Q1":weights(q1),"Q2":weights(q2),"Q3":dict(zip(top,iv.tolist())),
                 "Q4":{c:float(w) for c,w in zip(top,md) if w>0}}
        for k,t in targets.items():plans[k][day]=(p,t)
        audit.append({"date":str(day.date()),"signal":str(p.date()),"id_count":len(valid),"candidate_count":len(pool),
                      "Q1_id":{c:ids[c] for c in q1},"Q2_high_ratio":{c:highs[c] for c in q2},
                      "common_returns":len(r),"covariance":diag,"weights":targets})
    return plans,audit

def suite(dates,op,cl,anoms,plans,market,a,z,fee=1):
    result={k:simulate(dates,op,cl,anoms,plans[k],a,z,fee,k=="S0") for k in KEYS}
    summary=compare(result,market)
    return result,summary

def main():
    OUT.mkdir(exist_ok=True)
    snapshot=json.loads(original.SNAPSHOT.read_text());codes=sorted(x["code"] for x in snapshot["ranking"])
    if len(codes)!=151 or len(set(codes))!=151:raise ValueError("invalid fixed pool")
    original.BEGIN="2018-01-01"
    frames,fail=original.download(codes)
    print("DOWNLOAD_FAILURES",json.dumps(fail,ensure_ascii=False),flush=True)
    if fail:raise ValueError("incomplete download")
    op,cl,anoms,ca=original.prepare(frames)
    dates=pd.DatetimeIndex(cl["0050"].dropna().index).sort_values()
    op=op.reindex(dates);cl=cl.reindex(dates)
    warm=dates[dates.searchsorted(WARM)]
    sigs,coverage=original.signals(dates,cl,anoms,codes,warm,END)
    warmplan={d:(p,weights(rank[:10])) for d,(p,rank) in sigs.items()}
    wr=simulate(dates,op,cl,anoms,warmplan,warm,END)["nav"].pct_change().dropna()
    base,market,_=make_plans(dates,cl,anoms,sigs,wr)
    plans,audit=quality_plans(dates,cl,anoms,sigs,base)
    if len(audit)!=77:raise ValueError("unexpected signals")
    result,summary=suite(dates,op,cl,anoms,plans,market,START,END)
    if abs(summary["M3"]["terminal"]/2434591.378406042-1)>.0001 or abs(summary["M8"]["terminal"]/1879750.5126193273-1)>.0001:
        raise ValueError("original baseline changed; review vendor data")
    _,double=suite(dates,op,cl,anoms,plans,market,START,END,2)
    periods={}
    for label,lo,hi in [("2020H2-2023","2020-07-01","2023-12-31"),("2024-2025","2024-01-01","2025-12-31"),("2026_stress","2026-01-01","2026-11-25"),("pre2026","2020-07-01","2025-12-31")]:
        a=dates[dates.searchsorted(pd.Timestamp(lo))];z=dates[dates.searchsorted(pd.Timestamp(hi),side="right")-1]
        _,periods[label]=suite(dates,op,cl,anoms,plans,market,a,z)
    enddiff={}
    b=dates[dates.searchsorted(END-pd.DateOffset(years=1),side="right")-1]
    for row in snapshot["ranking"]:
        c=row["code"]
        if pd.notna(cl.at[b,c]) and pd.notna(cl.at[END,c]):
            diff=float((cl.at[END,c]/cl.at[b,c]-1)*100-row["momentum"])
            if abs(diff)>.5:enddiff[c]=diff
        else:enddiff[c]="missing endpoint"
    for k in KEYS:
        w=[x["weights"][k] for x in audit]
        summary[k]["avg_effective_positions"]=float(np.mean([1/sum(v*v for v in t.values()) for t in w if t]))
        summary[k]["max_target_weight"]=max(max(t.values(),default=0.) for t in w)
        summary[k]["avg_target_exposure"]=float(np.mean([sum(t.values()) for t in w]))
    report={"audit":{"kind":"survivor_biased_exploration","pool_asof":snapshot["asof"],"signals":len(audit),"downloaded":len(frames),"start":str(START.date()),"end":str(END.date()),"end_diff":enddiff,"risk_weight_fallbacks":sum("fallback" in x["covariance"] for x in audit),"unresolved_jumps":{c:{str(d.date()):float(x) for d,x in v.items()} for c,v in anoms.items() if len(v)}},
            "summary":summary,"double_cost":double,"periods":periods}
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    (OUT/"signals.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False))
    pd.DataFrame({k:v["nav"] for k,v in result.items()}).to_csv(OUT/"daily_nav.csv")
    (OUT/"trades.json").write_text(json.dumps({k:v["trades"] for k,v in result.items()},ensure_ascii=False))
    # compact log, full metrics in artifact
    small={k:{a:v[a] for a in ("terminal","cagr","mdd","sharpe","vol","calmar","exposure","cost","held_jumps")} for k,v in summary.items()}
    compact={"audit":report["audit"],"summary":small,"weights":{k:{a:summary[k][a] for a in ("avg_effective_positions","max_target_weight","avg_target_exposure")} for k in KEYS},"double_cost":{k:{a:v[a] for a in ("cagr","mdd","sharpe")} for k,v in double.items()},"periods":{p:{k:{a:v[a] for a in ("terminal","cagr","mdd","sharpe")} for k,v in s.items()} for p,s in periods.items()}}
    print("QUALITY_SUMMARY",json.dumps(compact,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
