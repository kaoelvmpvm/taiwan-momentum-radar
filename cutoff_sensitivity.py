"""Cutoff sensitivity on the immutable saved daily NAVs of the original runs."""
import json, math
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"cutoff-output"
START=pd.Timestamp("2020-07-01")
END=pd.Timestamp("2025-12-31")
INITIAL=100000.
def read(path):
    d=pd.read_csv(path,index_col=0,parse_dates=True)
    if d.index.has_duplicates or not d.index.is_monotonic_increasing:raise ValueError("invalid dates")
    if START not in d.index or END not in d.index:raise ValueError("missing endpoints")
    d=d.loc[START:END]
    if d.isna().any().any() or (d<=0).any().any():raise ValueError("bad NAV")
    return d
def stats(s):
    r=s.pct_change();r.iloc[0]=s.iloc[0]/INITIAL-1
    peak=s.cummax().clip(lower=INITIAL)
    dd=s/peak-1
    trough=dd.idxmin()
    prior=s.loc[:trough]
    top=prior.idxmax() if prior.max()>INITIAL else START-pd.Timedelta(days=1)
    recovery=s.loc[trough:][s.loc[trough:]>=peak.at[trough]]
    y=s.resample("YE").last()
    years=(END-START).days/365.2425
    cagr=(s.iloc[-1]/INITIAL)**(1/years)-1
    return {"terminal":float(s.iloc[-1]),"cagr":float(cagr),"mdd":float(dd.min()),"sharpe":float(r.mean()/r.std(ddof=1)*math.sqrt(252)),"vol":float(r.std(ddof=1)*math.sqrt(252)),"calmar":float(cagr/abs(dd.min())),"peak":str(top.date()),"trough":str(trough.date()),"recovery":str(recovery.index[0].date()) if len(recovery) else None,"annual":{str(k.year):float(v) for k,v in (y/y.shift(fill_value=INITIAL)-1).items()}}
def main():
    a=read(ROOT/"cutoff-input"/"first"/"daily_nav.csv")
    b=read(ROOT/"cutoff-input"/"second"/"daily_nav.csv")
    if not a.index.equals(b.index):raise ValueError("dates differ")
    for k in ("S0","M3"):
        if ((a[k]-b[k]).abs()/a[k]).max()>1e-4:raise ValueError("baseline differs")
    combined=pd.concat([a[["M1","M2","M4","M5"]],b],axis=1)
    result={k:stats(combined[k]) for k in combined}
    # Fixed exposure controls C6-C9 were fitted on full 2020-26 period,
    # so exclude them from pre-2026 ranking, but retain separately for audit.
    controls={k:result.pop(k) for k in list(result) if k.startswith("C")}
    report={"start":str(START.date()),"end":str(END.date()),"observations":len(combined),"source_runs":[45622458773,45632267524],"method":"truncate saved daily NAV without recalibration","summary":result,"full_period_fitted_controls_not_valid_for_cutoff_comparison":controls}
    OUT.mkdir(exist_ok=True)
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    combined.to_csv(OUT/"daily_nav.csv")
    print("CUTOFF_SUMMARY",json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
