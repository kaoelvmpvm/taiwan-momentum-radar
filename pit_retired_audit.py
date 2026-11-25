"""Restore five former members' official raw OHLCV and audit corporate-action evidence.
No synthetic prices; no merger settlement marked as executable. Separate from live radar."""
import csv,hashlib,json,re,time
from datetime import date
from pathlib import Path
import requests
import pandas as pd
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-retired-output"
DAYURL="https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
EXURL="https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
START=pd.Timestamp("2019-06-01")
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json"}
def roc(s):
    m=re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})",str(s).strip())
    if not m:raise ValueError("bad ROC date "+str(s))
    y,mo,d=map(int,m.groups());return date(y+1911,mo,d).isoformat()
def num(x):
    return float(str(x).strip().replace(",",""))
def fetch(url,params,path):
    err=None
    for attempt in range(2):
        try:
            r=requests.get(url,params={**params,"response":"json"},headers=HEADERS,timeout=25)
            r.raise_for_status()
            path.write_bytes(r.content)
            return r.json(),hashlib.sha256(r.content).hexdigest()
        except Exception as e:
            err=e;time.sleep(2+attempt*2)
    raise ValueError(str(err))
def parse_day(x,code,month):
    if x.get("stat")!="OK":raise ValueError("source stat "+str(x.get("stat")))
    title=str(x.get("title",""))
    if not re.search(r"(?<![0-9])"+code+r"(?![0-9])",title):raise ValueError("wrong stock title "+title)
    if str(x.get("date",""))[:6]!=month.replace("-",""):raise ValueError("wrong response month")
    fields=x.get("fields",[])
    needed=["日期","成交股數","開盤價","最高價","最低價","收盤價"]
    if any(k not in fields for k in needed):raise ValueError("unrecognized fields "+str(fields))
    idx=[fields.index(k) for k in needed]
    rows=[]
    for row in x.get("data",[]):
        d=roc(row[idx[0]])
        if not d.startswith(month):raise ValueError("out-of-month row "+d)
        try:
            v,o,h,l,c=[num(row[i]) for i in idx[1:]]
        except ValueError:
            if str(row[idx[1]]).replace(",","").strip()=="0":
                rows.append({"date":d,"volume":0,"open":None,"high":None,"low":None,"close":None,"status":"nontrading"})
                continue
            raise
        if v<0 or min(o,h,l,c)<=0 or h<max(o,l,c) or l>min(o,h,c):raise ValueError("invalid OHLCV "+d)
        rows.append({"date":d,"volume":int(v),"open":o,"high":h,"low":l,"close":c,"status":"traded" if v>0 else "nontrading"})
    if not rows or len({r["date"] for r in rows})!=len(rows):raise ValueError("empty or duplicate dates")
    return rows
def parse_ex(x,y):
    if x.get("stat")!="OK":raise ValueError("source stat "+str(x.get("stat")))
    title=str(x.get("title",""))
    if not (re.search(rf"{y-1911}年0?1月0?1日",title) and re.search(rf"{y-1911}年12月31日",title)):
        raise ValueError("date range not verified "+title)
    fields=x.get("fields",[])
    code_idx=next((i for i,k in enumerate(fields) if k in ("股票代號","證券代號")),None)
    date_idx=next((i for i,k in enumerate(fields) if k in ("資料日期","除權息日期","除權除息日期","日期")),None)
    if code_idx is None or date_idx is None:raise ValueError("unknown ex-right fields "+str(fields))
    rows=[]
    for r in x.get("data",[]):
        if not str(r[code_idx]).strip() in ACTIONS:continue
        d=roc(str(r[date_idx]).replace("年","/").replace("月","/").replace("日",""))
        if not d.startswith(str(y)):raise ValueError("wrong ex date")
        rows.append({"code":str(r[code_idx]).strip(),"date":d,"fields":dict(zip(fields,r))})
    return rows
ACTIONS=json.loads((ROOT/"backtest/pit_retired_actions.json").read_text())
def main():
    OUT.mkdir(exist_ok=True);raw=OUT/"raw";raw.mkdir(exist_ok=True)
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","start":str(START.date()),"stocks":{},"ex_rights":{},"actions":ACTIONS}
    benchmark_path=ROOT/"pit-benchmark"/"daily_nav.csv"
    bench=pd.read_csv(benchmark_path,index_col=0,parse_dates=True).index
    for c,a in ACTIONS.items():
        end=pd.Timestamp(a["last"])
        months=list(pd.period_range(START,end,freq="M"))
        # Probe the known final trading month first. Do not flood unavailable endpoints.
        order=[months[-1]]+months[:-1];data=[];errors=[];hashes={}
        for i,m in enumerate(order):
            label=str(m)
            try:
                x,sha=fetch(DAYURL,{"stockNo":c,"date":label.replace("-","")+"01"},raw/f"{c}_{label}.json")
                rows=parse_day(x,c,label);data+=rows;hashes[label]=sha
            except Exception as e:
                errors.append({"month":label,"error":str(e)})
                if i==0:break
            time.sleep(1.4)
        data=sorted(data,key=lambda r:r["date"])
        traded=[r for r in data if r["status"]=="traded"]
        actual={r["date"] for r in traded}
        expected={str(d.date()) for d in bench if START<=d<=end}
        gaps=sorted(expected-actual)
        complete=not errors and len(hashes)==len(months) and bool(traded) and traded[-1]["date"]==a["last"]
        if data:
            with (OUT/f"{c}_raw.csv").open("w",newline="",encoding="utf-8") as f:
                w=csv.DictWriter(f,fieldnames=["date","volume","open","high","low","close","status"]);w.writeheader();w.writerows(data)
        report["stocks"][c]={"expected_months":len(months),"successful_months":len(hashes),"errors":errors,"monthly_response_sha256":hashes,"traded_rows":len(traded),"first":traded[0]["date"] if traded else None,"last":traded[-1]["date"] if traded else None,"last_close":traded[-1]["close"] if traded else None,"raw_months_complete":complete,"benchmark_sessions_without_trade_since_2020_07":gaps,"gap_count":len(gaps),"status":"raw_only_not_adjusted"}
        print("RETIRED_STOCK",json.dumps({"code":c,**{k:v for k,v in report["stocks"][c].items() if k not in ("monthly_response_sha256","benchmark_sessions_without_trade_since_2020_07")}},ensure_ascii=False),flush=True)
    ex=[]
    for y in range(2019,2027):
        try:
            x,sha=fetch(EXURL,{"strDate":f"{y}0101","endDate":f"{y}1231"},raw/f"ex_{y}.json")
            rows=parse_ex(x,y);ex+=rows
            report["ex_rights"][str(y)]={"status":"parsed","rows":len(rows),"source_sha256":sha}
        except Exception as e:report["ex_rights"][str(y)]={"status":"blocked","error":str(e)}
        time.sleep(1.4)
    (OUT/"ex_rights_events.json").write_text(json.dumps(ex,ensure_ascii=False,indent=2))
    report["raw_complete_count"]=sum(x["raw_months_complete"] for x in report["stocks"].values())
    report["remaining_requirements"]=["Verify every no-trade day, corporate actions including reductions and ex-rights completeness; construct adjusted return series only after verification.","Confirm actual cash/share delivery dates and preferred security IDs; separate nontradable receivable from tradable position.","Independent official membership roster and price verification for other former members."]
    (OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print("PIT_RETIRED_SUMMARY",json.dumps({**report,"stocks":{c:{k:v for k,v in s.items() if k not in ("monthly_response_sha256","benchmark_sessions_without_trade_since_2020_07")} for c,s in report["stocks"].items()}},ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
