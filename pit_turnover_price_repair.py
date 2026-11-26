"""Fill observed vendor coverage gaps for T1 only using dated exchange quotes.
No fabricated prices, date deferral, replacement names or modified turnover ranks.
If two matching surrounding price-basis anchors cannot establish adjustment, fail closed.
"""
import gzip,hashlib,json,shutil
from pathlib import Path
from collections import defaultdict
import pandas as pd
import numpy as np
import pit_turnover_experiment as t
ROOT=Path(__file__).resolve().parent
INPUT=ROOT/"pit-turnover-input"
PRIOR=ROOT/"pit-turnover-repaired-input"
OUT=t.OUT
OUT.mkdir(exist_ok=True)
shutil.copy2(PRIOR/"2023"/"audit.json",INPUT/"2023"/"audit.json")
shutil.copy2(PRIOR/"2023"/"signals.json",INPUT/"2023"/"signals.json")
original=json.loads((PRIOR/"summary.json").read_text())
if original["blocked_signals"] or len(original["coverage"])!=76:raise ValueError("unexpected signal scope")
original_load=t.pit.load_prices
quotes={}
def quote(day):
    day=str(pd.Timestamp(day).date())
    if day not in quotes:
        # Recover verified dated market responses from original collector or price audit.
        paths=[t.pit.P/"official"/f"{day}.json.gz",ROOT/"pit-prior-backtest"/"official_execution"/f"{day}.json.gz"]
        paths += [INPUT/str(y)/"raw"/f"{day}_volume.json.gz" for y in range(2020,2027)]
        p=next((p for p in paths if p.exists()),None)
        dest=OUT/"gap_official"/f"{day}.json.gz";dest.parent.mkdir(exist_ok=True)
        if p is None:
            t.prev.original_quote(day)
            p=t.pit.OUT/"official_execution"/f"{day}.json.gz"
        raw=gzip.decompress(p.read_bytes());x=json.loads(raw)
        if x.get("stat")!="OK" or not t.date_ok(x,day):raise ValueError("wrong official date "+day)
        tab=t.table(x,{"證券代號","開盤價","收盤價","成交股數"})
        rows={}
        for row in tab["data"]:
            f=dict(zip(tab["fields"],row));c=str(f["證券代號"]).strip()
            try:
                v=t.num(f["成交股數"]);o=t.num(f["開盤價"]);z=t.num(f["收盤價"])
            except (ValueError,TypeError):continue
            rows[c]=(o,z,v)
        if len(rows)<500 or not rows.get("0050",(0,0,0))[2]:raise ValueError("incomplete official market "+day)
        if not dest.exists():dest.write_bytes(gzip.compress(raw))
        quotes[day]=(rows,hashlib.sha256(raw).hexdigest())
    return quotes[day]
def repair():
    dates,op,cl,raw=original_load()
    targets=defaultdict(set);boundaries=set()
    for item in original["coverage"]:
        start=pd.Timestamp(item["execution"]);end=dates[dates.to_period("M")==start.to_period("M")][-1]
        names=list(item["selected"])
        for day in dates[(dates>=start)&(dates<=end)]:
            for c in names:
                if day==start or day==end:boundaries.add((day,c))
                if pd.isna(cl.at[day,c]) or (day==start and pd.isna(op.at[day,c])):
                    targets[c].add(day)
    report={"repaired":[],"untraded":[],"unresolved":[],"source_hashes":{}}
    for c,days in sorted(targets.items()):
        for d in sorted(days):
            q,h=quote(d);v=q.get(c)
            if v is None or not all(x is not None and x>0 for x in v):
                entry={"date":str(d.date()),"code":c,"official_row":v,"boundary":(d,c) in boundaries}
                if (d,c) in boundaries:report["unresolved"].append(entry)
                else:report["untraded"].append(entry)
                continue
            # Choose anchors from the unchanged archive, excluding every missing date for that code.
            valid=cl[c].notna() & raw[c].notna() & ~cl.index.isin(days)
            prior=cl.index[valid & (cl.index<d)];after=cl.index[valid & (cl.index>d)]
            if len(prior)==0 or len(after)==0:
                report["unresolved"].append({"date":str(d.date()),"code":c,"reason":"missing_anchor"});continue
            a,b=prior[-1],after[0];scales=[]
            for p in (a,b):
                pq,_=quote(p);pv=pq.get(c)
                if pv is None or pv[1] is None or abs(raw.at[p,c]-pv[1])>max(.011,.0001*pv[1]):break
                scales.append(float(cl.at[p,c]/pv[1]))
            if len(scales)!=2 or abs(scales[0]/scales[1]-1)>.0001:
                report["unresolved"].append({"date":str(d.date()),"code":c,"reason":"anchor_basis_mismatch","anchors":[str(a.date()),str(b.date())],"scales":scales});continue
            if pd.isna(op.at[d,c]):op.at[d,c]=v[0]*scales[0]
            if pd.isna(cl.at[d,c]):cl.at[d,c]=v[1]*scales[0]
            if pd.isna(raw.at[d,c]):raw.at[d,c]=v[1]
            report["repaired"].append({"date":str(d.date()),"code":c,"official_open":v[0],"official_close":v[1],"scale":scales[0],"anchors":[str(a.date()),str(b.date())],"hash":h})
    report["source_hashes"]={d:h for d,(_,h) in quotes.items()}
    (OUT/"price_gap_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print("TURNOVER_PRICE_GAPS",json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)
    if report["unresolved"]:raise ValueError("unresolved traded quote or exact execution price; no full result")
    return dates,op,cl,raw
t.pit.load_prices=repair
t.aggregate()
s=json.loads((OUT/"summary.json").read_text())
if s["coverage"]!=original["coverage"]:raise ValueError("turnover ranks changed")
if any(s[key]["T1"]["status"]!="PROVISIONAL_COMPLETE" for key in ("full","double_cost","pre2026","stress2026")):raise ValueError("T1 incomplete")
print("TURNOVER_PRICE_REPAIR_COMPLETE",flush=True)
