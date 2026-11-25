"""M8 diagnosis using immutable original artifacts."""
import json
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parent
P=ROOT/"m8-input"
OUT=ROOT/"m8-output"
def load(n):return json.loads((P/n).read_text())
def iso(d):return str(d.date())
def draw(s):
    peak=s.cummax().clip(lower=100000)
    dd=s/peak-1
    t=dd.idxmin();p=s.loc[:t].idxmax()
    rec=s.loc[t:][s.loc[t:]>=peak.at[t]]
    return {"peak":iso(p),"trough":iso(t),"peak_nav":float(peak.at[t]),"trough_nav":float(s.at[t]),"mdd":float(dd.at[t]),"recovery":iso(rec.index[0]) if len(rec) else None,"end_drawdown":float(dd.iloc[-1])}
def main():
    n=pd.read_csv(P/"daily_nav.csv",index_col=0,parse_dates=True)
    e=pd.read_csv(P/"daily_exposure.csv",index_col=0,parse_dates=True)
    sig=load("signals.json");trades=load("trades.json")
    pool=json.loads((ROOT/"exploratory_pool_20261125.json").read_text())
    names={r["code"]:r["name"] for r in pool["ranking"]}
    if not n.index.equals(e.index) or not n.index.is_unique or not n.index.is_monotonic_increasing:raise ValueError("bad alignment")
    if n.index[-1]!=pd.Timestamp("2026-11-25"):raise ValueError("bad cutoff")
    dd={k:draw(n[k]) for k in ("S0","M3","M8","C8")}
    p=pd.Timestamp(dd["M8"]["peak"]);t=pd.Timestamp(dd["M8"]["trough"])
    window=n.loc[p:t]
    mr=n.resample("ME").last().pct_change()
    rows=[]
    for s in sig:
        if s["date"]<"2026-01-01":continue
        key=pd.Timestamp(s["date"]).to_period("M")
        q=mr.loc[mr.index.to_period("M")==key].iloc[0]
        x=e.loc[e.index.to_period("M")==key].mean()
        a=s["weights"]["M3"];b=s["weights"]["M8"]
        omitted=[{"code":c,"name":names.get(c,"")} for c in a if c not in b]
        kept=[{"code":c,"name":names.get(c,"")} for c in b]
        fills={k:[u["date"] for u in trades[k] if u["signal"]==s["signal"]] for k in ("M3","M8")}
        rows.append({"month":str(key),"signal":s["signal"],"planned":s["date"],"M3_return":float(q.M3),"M8_return":float(q.M8),"S0_return":float(q.S0),"C8_return":float(q.C8),"M8_minus_M3":float(q.M8-q.M3),"M8_exposure":float(x.M8),"C8_exposure":float(x.C8),"kept":kept,"omitted":omitted,"fills":fills})
    wret={k:float(window[k].iloc[-1]/window[k].iloc[0]-1) for k in ("M3","M8","S0","C8")}
    d=n[["M3","M8","C8","S0"]].pct_change().dropna()
    d["diff"]=d.M8-d.M3
    worst=[{"date":iso(k),**{c:float(v) for c,v in x.items()}} for k,x in d.loc["2026"].nsmallest(8,"diff").iterrows()]
    result={"source_run":45632267524,"drawdown":dd,"M8_peak_to_trough_same_dates":wret,"peak_to_trough_days":int((t-p).days),"2026_months":rows,"worst_relative_days":worst,"limitations":["No stock-level price path in original artifact; omitted-stock counterfactual requires separate price validation.","C8 exposure fitted on whole period; diagnostic only.","November partial month."]}
    OUT.mkdir(exist_ok=True)
    (OUT/"diagnosis.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    print("M8_DIAGNOSIS",json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
