"""Audit 27 historical ETF checkpoints and construct provisional reference-adjusted former-stock OHLC.
Never writes an accepted PIT membership or a certified total-return series."""
import json,gzip,hashlib,time,re
from pathlib import Path
from calendar import monthrange
from datetime import date
import numpy as np
import pandas as pd
import requests
from pit_reconcile import extract, A, GROUPS
from pit_checkpoint_audit import expected_at
from historical_holdings_audit import URL
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-expanded-output"
OLD=ROOT/"pit-retired-input"
NEW=ROOT/"pit-reconcile-input"
CHECK=ROOT/"pit-checkpoint-input"
def adjusted(frame,events,calendar):
    d=frame.loc[(frame.status=="traded")&(frame.volume>0)].copy().sort_values("date").reset_index(drop=True)
    if d.empty or d.date.duplicated().any():raise ValueError("empty/duplicate traded rows")
    prices=d.close.astype(float).to_numpy()
    factors=np.ones(len(d));errors=[];event_log=[];seen=set()
    pos={x:i for i,x in enumerate(d.date)}
    for event in events:
        day=event["date"];f=event["fields"]
        if day<d.date.iloc[0] or day>d.date.iloc[-1]:continue
        try:
            if day in seen:raise ValueError("duplicate ex date")
            seen.add(day)
            if day not in pos or pos[day]==0:raise ValueError("missing ex-date or prior trade")
            i=pos[day];before=float(str(f["除權息前收盤價"]).replace(",",""));ref=float(str(f["除權息參考價"]).replace(",",""))
            if not (0<before and 0<ref):raise ValueError("nonpositive reference")
            if abs(prices[i-1]-before)>max(.011,before*.0005):raise ValueError("prior close mismatch")
            factors[i]=ref/before
            event_log.append({"date":day,"prior":before,"reference":ref,"factor":float(factors[i]),"type":f.get("權/息")})
        except Exception as exc:errors.append({"date":day,"error":str(exc),"fields":f})
    # Ratio chaining with last traded price anchor. Only factors strictly after the row.
    scale=np.ones(len(d))
    for i in range(len(d)-2,-1,-1):scale[i]=scale[i+1]*factors[i+1]
    for c in ["open","high","low","close"]:d["reference_adjusted_"+c]=d[c].astype(float)*scale
    raw=d.close.astype(float).pct_change();adj=d.reference_adjusted_close.pct_change()
    d["reference_factor"]=factors
    gaps=sorted(set(x for x in calendar if d.date.iloc[0]<=x<=d.date.iloc[-1])-set(d.date))
    jumps=[{"date":d.date.iloc[i],"raw_return":float(raw.iloc[i]),"adjusted_return":float(adj.iloc[i])} for i in range(1,len(d)) if abs(adj.iloc[i])>.13]
    return d,{"events_applied":event_log,"event_errors":errors,"unexplained_abs_return_over_13pct":jumps,"missing_benchmark_sessions":gaps,"rows":len(d),"first":d.date.iloc[0],"last":d.date.iloc[-1],"status":"PROVISIONAL_REFERENCE_ADJUSTED_NOT_TOTAL_RETURN"}
def checkpoint(q,events,anchor):
    y,k=q;month=k*3;label=f"{y}Q{k}";day=date(y,month,monthrange(y,month)[1]).isoformat()
    cached=CHECK/f"{label}.html.gz"
    if cached.exists():raw=gzip.decompress(cached.read_bytes())
    else:
        payload={"encodeURIComponent":"1","step":"1","firstin":"1","off":"1","keyword4":"","code1":"","TYPEK2":"","checkbtn":"","queryName":"co_id","inpuType":"co_id","TYPEK":"all","isnew":"false","co_id":"","year":str(y-1911),"season":f"{k:02d}","type":"05"}
        r=requests.post(URL,data=payload,headers={"User-Agent":"Mozilla/5.0","Referer":"https://mopsov.twse.com.tw/mops/web/t78sb35_new"},timeout=45);r.raise_for_status();raw=r.content
        time.sleep(2)
    (OUT/"raw"/f"{label}.html.gz").write_bytes(gzip.compress(raw))
    roster=extract(raw);expected=expected_at(day,anchor,events)
    return {"quarter":label,"comparison_asof":day,"source_sha256":hashlib.sha256(raw).hexdigest(),"groups":{g:{"count":len(roster[g]),"matched":len(set(roster[g])&expected[g]),"missing":sorted(expected[g]-set(roster[g])),"extra":{c:roster[g][c] for c in sorted(set(roster[g])-expected[g])}} for g in GROUPS}}
def main():
    OUT.mkdir(exist_ok=True);(OUT/"raw").mkdir(exist_ok=True)
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","adjusted":{},"checkpoints":[]}
    events=json.loads((NEW/"provisional_events.json").read_text())
    ex=json.loads((NEW/"ex_rights_events.json").read_text())
    old_audit=json.loads((OLD/"audit.json").read_text());new_audit=json.loads((NEW/"audit.json").read_text())
    if len(events)!=32 or new_audit["reverse"]!={"passed":32,"blocked":False}:raise ValueError("missing verified reconstruction artifact")
    if any(v.get("status")!="parsed" for v in new_audit["ex_rights"].values()) or len(new_audit["ex_rights"])!=8:raise ValueError("incomplete ex-rights source")
    bench=pd.read_csv(ROOT/"pit-benchmark"/"daily_nav.csv",index_col=0,parse_dates=True).index
    cal=[x.date().isoformat() for x in bench]
    for c,a in A.items():
        path=(NEW if c in ("2448","2809") else OLD)/f"{c}_raw.csv"
        try:
            d=pd.read_csv(path,dtype={"date":str});v,r=adjusted(d,[e for e in ex if e["code"]==c],cal)
            if r["last"]!=a["last"]:raise ValueError("last trade mismatch")
            v.to_csv(OUT/f"{c}_reference_adjusted.csv",index=False)
            report["adjusted"][c]=r
        except Exception as exc:report["adjusted"][c]={"error":str(exc)}
    # Include Q1 2020 as an extra historical checkpoint; earlier events may be missing.
    pool=json.loads((ROOT/"exploratory_pool_20261125.json").read_text())
    anchor={g:[x["code"] for x in pool["ranking"] if x["group"]==g] for g in GROUPS}
    qs=[(y,k) for y in range(2020,2027) for k in range(1,5) if (y,k)<=(2026,3)]
    for q in qs:
        try:r=checkpoint(q,events,anchor)
        except Exception as exc:r={"quarter":f"{q[0]}Q{q[1]}","error":str(exc)}
        report["checkpoints"].append(r);print("EXPANDED_CHECKPOINT",json.dumps(r,ensure_ascii=False),flush=True)
    report["matched_expected_members"]=sum(g["matched"] for r in report["checkpoints"] for g in r.get("groups",{}).values())
    report["expected_members_compared"]=sum(50 if k=="0050" else 100 for r in report["checkpoints"] for k in r.get("groups",{}))
    report["checkpoint_exact_groups"]=sum(not g["missing"] and not g["extra"] for r in report["checkpoints"] for g in r.get("groups",{}).values())
    report["checkpoint_groups"]=sum(len(r.get("groups",{})) for r in report["checkpoints"])
    report["remaining_requirements"]=["Independent official index roster; clarify ETF holdings dates and all discrepancies.","Verify all corporate actions outside TWT49U, no-trade sessions, and settlement/receivable dates.","Reference adjustment is not exact dividend/rights total return; use consistent return convention with other stocks and separate real raw trade execution.","Validate surviving historical vendor series against official data, and actual corporate action transitions before formal backtest."]
    (OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print("PIT_EXPANDED_SUMMARY",json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
