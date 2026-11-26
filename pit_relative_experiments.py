"""P3/P4: independent relative-momentum opportunity-cost tests.
Fixed P1 signal membership, no parameter search. Provisional PIT data."""
import json,math
from pathlib import Path
import pandas as pd
import numpy as np
import pit_historical_backtest as pit
import pit_persistence_experiments as prev
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-relative-output"
KEYS=("S0","B2330","P1","P3","P4")
def relative_target(w,scores,fill):
    keep={c:v for c,v in w.items() if scores[c]>scores[fill]}
    rem=1-sum(keep.values())
    if rem>1e-12:keep[fill]=keep.get(fill,0)+rem
    return keep
def make_plans(cl,records):
    plans={k:{} for k in KEYS};audit=[]
    for x in records:
        p=pd.Timestamp(x["signal"]);d=pd.Timestamp(x["execution"]);b=pd.Timestamp(x["base6"])
        if not b<p<d:raise ValueError("signal chronology")
        w=x["weights"]["P1"]
        scores={c:float(cl.at[p,c]/cl.at[b,c]-1) for c in set(w)|{"0050","2330"}}
        if not all(math.isfinite(v) for v in scores.values()):raise ValueError("missing momentum benchmark")
        fill="2330" if scores["2330"]>scores["0050"] else "0050"
        t={"S0":{"0050":1.},"B2330":{"2330":1.},"P1":w,"P3":relative_target(w,scores,"0050"),"P4":relative_target(w,scores,fill)}
        for k,v in t.items():plans[k][d]=(p,v)
        audit.append({"signal":x["signal"],"execution":x["execution"],"scores":scores,"fallback":fill,"weights":t})
    return plans,audit
def same(a,b):return all(abs(a.get(c,0)-b.get(c,0))<1e-10 for c in set(a)|set(b))
def main():
    OUT.mkdir(exist_ok=True);pit.OUT=OUT/"boundary";(pit.OUT/"official_execution").mkdir(parents=True,exist_ok=True)
    pit.quote=prev.cached_quote
    dates,op,cl,raw=pit.load_prices()
    source=ROOT/"pit-persistence-input"
    records=json.loads((source/"signals.json").read_text());prior=json.loads((source/"summary.json").read_text())
    if len(records)!=76:raise ValueError("unexpected signal count")
    plans,audit=make_plans(cl,records)
    anoms={c:cl[c].dropna().pct_change().abs().loc[lambda x:x>.35] for c in cl}
    start=min(plans["P1"]);end=pit.END
    def run(a,z,fee=1):
        return {k:pit.simulate(dates,op,cl,anoms,plans[k],a,z,fee,k in ("S0","B2330")) for k in KEYS}
    full=run(start,end);double=run(start,end,2)
    pre=run(start,dates[dates.searchsorted(pd.Timestamp("2025-12-31"),side="right")-1])
    stress=run(dates[dates.searchsorted(pd.Timestamp("2026-01-01"))],end)
    for k in ("S0","P1"):
        if abs(full[k]["summary"]["terminal"]-prior["summary"][k]["terminal"])>.01:raise ValueError("prior baseline mismatch")
    report={"status":"PROVISIONAL_RESEARCH_NOT_CERTIFIED","period":[str(start.date()),str(end.date())],"signals":len(audit),"full":{k:r["summary"] for k,r in full.items()},"pre2026":{k:r["summary"] for k,r in pre.items()},"stress2026":{k:r["summary"] for k,r in stress.items()},"double_cost":{k:r["summary"] for k,r in double.items()},"audits":{k:r["audit"] for k,r in full.items()},"changes":{k:[x for x in audit if not same(x["weights"][k],x["weights"]["P1"])] for k in ("P3","P4")},"concentration":{k:{"max_target_2330":max(x["weights"][k].get("2330",0) for x in audit),"months_2330_gt50":sum(x["weights"][k].get("2330",0)>.5 for x in audit),"max_target_0050":max(x["weights"][k].get("0050",0) for x in audit)} for k in ("P1","P3","P4")}}
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    (OUT/"signals.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    (OUT/"trades.json").write_text(json.dumps({k:r["trades"] for k,r in full.items()},ensure_ascii=False,indent=2))
    pd.DataFrame({k:r["nav"] for k,r in full.items()}).to_csv(OUT/"daily_nav.csv")
    report["execution"]=prev.execution(full,op,cl)
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print("PIT_RELATIVE_RESULTS",json.dumps({k:v for k,v in report.items() if k!="audits"},ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
