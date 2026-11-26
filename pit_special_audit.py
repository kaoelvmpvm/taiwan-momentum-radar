"""Official special-action source discovery and independent factor reconciliation.
Only accept dated, structurally validated source ranges. Never infer action factors from vendor residuals."""
import json,re,time,hashlib
from pathlib import Path
import requests
import pandas as pd
import pit_factor_audit as prior
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-special-output"
E=json.loads((ROOT/"backtest/pit_special_evidence.json").read_text())
BASE="https://www.twse.com.tw"
ROUTES={
 "face":["/rwd/zh/reducation/TWTB7U","/rwd/zh/reduction/TWTB7U","/rwd/zh/announcement/TWTB7U","/exchangeReport/TWTB7U"],
 "reduction":["/rwd/zh/reducation/TWTAUU","/rwd/zh/reduction/TWTAUU","/exchangeReport/TWTAUU"]
}
def num(v):return float(str(v).replace(",","").replace("%",""))
def iso(s):
    s=str(s).strip()
    m=re.fullmatch(r"(\d{2,4})[年/-](\d{1,2})[月/-](\d{1,2})日?",s)
    if not m:raise ValueError("unknown date "+s)
    y,m,d=map(int,m.groups())
    return f"{y+1911 if y<1911 else y:04d}-{m:02d}-{d:02d}"
def request(kind,route,y,mode):
    begin=f"{y}0101";end=f"{y}1231" if y<2026 else "20261125"
    p={"response":"json","startDate":begin,"endDate":end}
    if mode=="legacy":p={"response":"json","strDate":begin,"endDate":end}
    elif mode=="date":p={"response":"json","date":begin,"selectType":"ALL"}
    r=requests.get(BASE+route,params=p,headers={"User-Agent":"Mozilla/5.0"},timeout=35)
    r.raise_for_status();raw=r.content
    name=f"{kind}_{y}_{route.split('/')[-2]}_{mode}.json"
    (OUT/"raw"/name).write_bytes(raw)
    x=r.json()
    if x.get("stat")!="OK":raise ValueError(str(x.get("stat")))
    title=str(x.get("title",""))
    dates=re.findall(r"\d{2,4}年\d{1,2}月\d{1,2}日",title)
    if len(dates)!=2 or [iso(v).replace("-","") for v in dates]!=[begin,end]:raise ValueError("range not verified: "+title)
    fields=x["fields"];rows=[]
    for r in x["data"]:
        f=dict(zip(fields,r));c=str(f["股票代號"]).strip();day=iso(f["恢復買賣日期"])
        if not begin<=day.replace("-","")<=end:raise ValueError("out-of-range date")
        if kind=="face":
            old=num(f["變更前面額"]);new=num(f["變更後面額"])
            if min(old,new)<=0:raise ValueError("invalid face value")
            fact=new/old
        else:
            before=num(f["停止買賣前收盤價格"]);ref=num(f["恢復買賣參考價"])
            if min(before,ref)<=0:raise ValueError("invalid reduction reference")
            fact=ref/before
        rows.append({"code":c,"date":day,"factor":fact,"source":BASE+route,"fields":f,"kind":kind})
    return rows,{"sha256":hashlib.sha256(raw).hexdigest(),"title":title,"rows":len(rows),"route":route,"mode":mode}
def main():
    OUT.mkdir(exist_ok=True);(OUT/"raw").mkdir(exist_ok=True)
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","probes":[],"coverage":{}}
    all_events=[]
    for kind,routes in ROUTES.items():
        route_mode=None;probe_y=2025
        for route in routes:
            for mode in ("range","legacy","date"):
                try:
                    rows,meta=request(kind,route,probe_y,mode)
                    # Face probe must reproduce independently sourced 2025 changes.
                    if kind=="face" and not {"4763","6919","2327"}<=set(x["code"] for x in rows):raise ValueError("known events missing")
                    route_mode=(route,mode);all_events+=rows;report["coverage"][kind]={"2025":meta};break
                except Exception as exc:report["probes"].append({"kind":kind,"route":route,"mode":mode,"error":str(exc)})
                time.sleep(.6)
            if route_mode:break
        if not route_mode:continue
        for y in range(2019,2027):
            if y==probe_y:continue
            try:
                rows,meta=request(kind,route_mode[0],y,route_mode[1]);all_events+=rows;report["coverage"][kind][str(y)]=meta
            except Exception as exc:report["coverage"][kind][str(y)]={"error":str(exc)}
            time.sleep(1.2)
    # Use previous immutable inputs and known confirmed actions, supplement official rows only.
    codes=set(pd.read_csv(ROOT/"pit-price-input"/"adjusted_close.csv.gz",nrows=1).columns[1:])
    special={c:dict(e) for c,e in E["confirmed_splits"].items()}
    old=prior.official_events(codes)
    keys={(c,e["date"]):e for c,es in old.items() for e in es}
    conflicts=[];added=[]
    candidates=[{"code":c,**v} for c,v in special.items()]+all_events
    for e in candidates:
        c=e["code"];d=e["date"]
        if c not in codes:continue
        k=(c,d)
        if k in keys:
            if keys[k]["factor"] is None or abs(keys[k]["factor"]/e["factor"]-1)>.01:conflicts.append({"code":c,"date":d,"existing":keys[k],"new":e})
            continue
        old.setdefault(c,[]).append(e);keys[k]=e;added.append(e)
    report["new_events"]=added;report["conflicts"]=conflicts
    report["complete_source_years"]={k:sum("rows" in x for x in v.values()) for k,v in report["coverage"].items()}
    (OUT/"special_events.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    # Repeat exact previous rank comparison; change only official reference action factors.
    prior.OUT=OUT/"reconciliation"
    prior.official_events=lambda _:old
    prior.main()
    after=json.loads((prior.OUT/"audit.json").read_text())
    report["reconciliation"]={k:after[k] for k in ("return_comparison_counts","top5_identical_months","top10_identical_months","total_months","largest_residuals","flagged_codes")}
    (OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print("PIT_SPECIAL_SUMMARY",json.dumps({k:v for k,v in report.items() if k not in ("new_events",)},ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
