"""Apply evidenced delayed index changes and retired-stock action repairs; no certified backtest."""
import copy,json,gzip,hashlib
from pathlib import Path
import pandas as pd
from pit_reconcile import extract,GROUPS
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-timing-output"
E=json.loads((ROOT/"backtest/pit_timing_evidence.json").read_text())
def empty():return {g:{"add":[],"remove":[]} for g in GROUPS}
def corrected(events,delays):
    out=copy.deepcopy(events)
    for x in delays:
        found=[e for e in out if e["effective"]==x["scheduled"]]
        if len(found)!=1:raise ValueError("scheduled event ambiguous")
        n={"effective":x["effective"],"source":x["source"],"groups":empty(),"kind":"delayed_index_change"}
        for g,side in x["changes"]:
            found[0]["groups"][g][side].remove(x["code"])
            n["groups"][g][side].append(x["code"])
        out.append(n)
    return sorted(out,key=lambda x:x["effective"])
def members(day,anchor,events,delays):
    s={g:set(anchor[g]) for g in GROUPS}
    for e in reversed(events):
        if e["effective"]<=day:continue
        for g in GROUPS:
            a=set(e["groups"][g]["add"]);b=set(e["groups"][g]["remove"])
            if not a<=s[g] or b&s[g]:raise ValueError("reverse conflict "+e["effective"]+" "+g)
            s[g]=(s[g]-a)|b
        if s["0050"]&s["0051"]:raise ValueError("cross-index overlap")
    expected={"0050":50,"0051":100}
    for x in delays:
        if x["scheduled"]<=day<x["effective"]:
            for g,side in x["changes"]:expected[g]+=(-1 if side=="add" else 1)
    if {g:len(s[g]) for g in GROUPS}!=expected:raise ValueError("unexpected count "+day)
    return s
def reduction_adjust(d,x):
    day=x["effective"];ratio=x["share_ratio"]
    if not 0<ratio<1 or x["cash_return"]!=0:raise ValueError("unsupported action")
    dates=list(d.date);i=dates.index(day)
    if i==0 or not dates[i-1]<x["halt_start"]<=day:raise ValueError("bad reduction boundary")
    for k in ("open","high","low","close"):
        d.loc[d.date<day,"reference_adjusted_"+k]/=ratio
    d.loc[d.date==day,"reference_factor"]/=ratio
    return {"date":day,"before_date":dates[i-1],"before_close":float(d.close.iloc[i-1]),"after_close":float(d.close.iloc[i]),"raw_return":float(d.close.iloc[i]/d.close.iloc[i-1]-1),"share_adjusted_return":float(d.close.iloc[i]*ratio/d.close.iloc[i-1]-1),"share_ratio":ratio}
def main():
    OUT.mkdir(exist_ok=True)
    old=ROOT/"pit-expanded-input";rec=ROOT/"pit-reconcile-input"
    prior=json.loads((old/"audit.json").read_text());orig=json.loads((rec/"provisional_events.json").read_text())
    if len(orig)!=32:raise ValueError("wrong prior event set")
    ev=corrected(orig,E["delays"])
    p=json.loads((ROOT/"exploratory_pool_20261125.json").read_text())
    anchor={g:[r["code"] for r in p["ranking"] if r["group"]==g] for g in GROUPS}
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","event_count":len(ev),"checkpoints":[],"price_repairs":{}}
    for e in ev:
        members(e["effective"],anchor,ev,E["delays"])
    for r in prior["checkpoints"]:
        if "groups" not in r:raise ValueError("missing prior quarter")
        raw=gzip.decompress((old/"raw"/(r["quarter"]+".html.gz")).read_bytes())
        if hashlib.sha256(raw).hexdigest()!=r["source_sha256"]:raise ValueError("hash mismatch")
        actual=extract(raw);s=members(r["comparison_asof"],anchor,ev,E["delays"])
        report["checkpoints"].append({"quarter":r["quarter"],"groups":{g:{"expected":len(s[g]),"matched":len(s[g]&set(actual[g])),"missing":sorted(s[g]-set(actual[g])),"extra":sorted(set(actual[g])-s[g])} for g in GROUPS}})
    for c,r in prior["adjusted"].items():
        d=pd.read_csv(old/f"{c}_reference_adjusted.csv",dtype={"date":str})
        r=copy.deepcopy(r)
        if c=="3682":
            r["reduction"]=reduction_adjust(d,E["reduction"])
        if c=="2823":
            first=d.date.iloc[0]
            boundary=[e for e in r["event_errors"] if e["date"]==first and e["error"]=="missing ex-date or prior trade"]
            if len(boundary)!=1:raise ValueError("unexpected boundary")
            r["outside_observed_return_interval"]=boundary
            r["event_errors"]=[e for e in r["event_errors"] if e not in boundary]
        gaps=set(r["missing_benchmark_sessions"])
        verified=[]
        for x in E["suspensions"]:
            if x["code"]!=c:continue
            if x["date"] not in gaps or x["resumed"] not in set(d.date):raise ValueError("suspension mismatch "+c)
            gaps.remove(x["date"]);verified.append(x)
        r["verified_suspensions"]=verified;r["unresolved_benchmark_sessions"]=sorted(gaps)
        v=d.reference_adjusted_close.pct_change()
        r["unexplained_abs_return_over_13pct"]=[{"date":d.date.iloc[i],"return":float(v.iloc[i])} for i in range(1,len(d)) if abs(v.iloc[i])>.13]
        d.to_csv(OUT/f"{c}_reference_adjusted.csv",index=False)
        report["price_repairs"][c]=r
    report["matched_expected_members"]=sum(g["matched"] for r in report["checkpoints"] for g in r["groups"].values())
    report["expected_members_compared"]=sum(g["expected"] for r in report["checkpoints"] for g in r["groups"].values())
    report["exact_groups"]=sum(not g["missing"] and not g["extra"] for r in report["checkpoints"] for g in r["groups"].values())
    # Provisional signal-date membership only: no claim of accepted roster or execution.
    idx=pd.read_csv(ROOT/"pit-benchmark"/"daily_nav.csv",index_col=0,parse_dates=True).index
    months=pd.Series(idx,index=idx).groupby(idx.to_period("M")).max()
    days=["2020-06-30"]+[d.date().isoformat() for d in months if d.to_period("M")<pd.Period("2026-11",freq="M")]
    rows=[]
    for day in days:
        s=members(day,anchor,ev,E["delays"])
        rows.append({"signal_date":day,"groups":{g:sorted(s[g]) for g in GROUPS},"status":"PROVISIONAL_NOT_CERTIFIED"})
    report["provisional_months"]=len(rows)
    (OUT/"provisional_events.json").write_text(json.dumps(ev,ensure_ascii=False,indent=2))
    (OUT/"provisional_monthly_rosters.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    (OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    short={**report,"checkpoints":[r for r in report["checkpoints"] if any(g["missing"] or g["extra"] for g in r["groups"].values())],"price_repairs":{c:{k:v for k,v in r.items() if k!="events_applied"} for c,r in report["price_repairs"].items()}}
    print("PIT_TIMING_SUMMARY",json.dumps(short,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
