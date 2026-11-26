"""Resolve additional confirmed splits and audit missing TWSE endpoints on TPEx.
Diagnostic only: never label a provisional roster or return series as certified."""
import json,re,gzip,time,hashlib,math
from pathlib import Path
from collections import Counter,defaultdict
import requests
import pandas as pd
import pit_factor_audit as prior
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-market-output"
P=ROOT/"pit-price-input"
SPECIAL=ROOT/"pit-special-input"
SPLITS={"6415":{"date":"2022-07-13","factor":.25,"last_old":"2022-07-05","source":"https://news.cnyes.com/news/print/4901924"},"6531":{"date":"2021-10-18","factor":.5,"last_old":"2021-10-06","source":"https://ec.ltn.com.tw/article/breakingnews/3674168"}}
def norm_date(x):
    s=str(x).strip()
    if re.fullmatch(r"\d{8}",s):return s
    m=re.fullmatch(r"(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})",s)
    if not m:return ""
    y,m,d=map(int,m.groups());return f"{y+1911 if y<1911 else y:04d}{m:02d}{d:02d}"
def parse_tpex(x,day):
    # Require an explicitly returned date, not a query echo or current snapshot.
    dates=[x.get(k) for k in ("date","reportDate","dataDate") if x.get(k)]
    if day.replace("-","") not in [norm_date(d) for d in dates]:raise ValueError("unverified returned date "+str(dates))
    if "tables" in x:
        ts=[t for t in x["tables"] if isinstance(t,dict) and t.get("fields") and t.get("data")]
        found=[]
        for t in ts:
            f=t["fields"];ci=next((i for i,k in enumerate(f) if k in ("代號","證券代號","股票代號")),None)
            pi=next((i for i,k in enumerate(f) if k in ("收盤","收盤價")),None)
            if ci is not None and pi is not None:found.append((t["data"],ci,pi))
        if len(found)!=1:raise ValueError("ambiguous TPEx table")
        rows,ci,pi=found[0]
    elif "aaData" in x:
        # Legacy table layout: code, name, close, change, open, high, low...
        # Validate explicit header if supplied; only use code and close.
        rows=x["aaData"];ci,pi=0,2
        if len(rows)<300 or any(len(r)<7 for r in rows[:20]):raise ValueError("legacy table too short")
    else:raise ValueError("unknown response schema")
    result={}
    for r in rows:
        c=str(r[ci]).strip()
        if not re.fullmatch(r"[0-9A-Z]{4,6}",c):continue
        try:p=float(str(r[pi]).replace(",",""))
        except (ValueError,TypeError):continue
        if not math.isfinite(p) or p<=0:continue
        if c in result:raise ValueError("duplicate TPEx code")
        result[c]=p
    if len(result)<300:raise ValueError("incomplete TPEx market")
    return result
def tpex(day):
    y,m,d=map(int,day.split("-"));roc=f"{y-1911}/{m:02d}/{d:02d}"
    routes=[("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",{"date":day.replace("-","/"),"id":"","response":"json"}),("https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php",{"l":"zh-tw","d":roc,"s":"0,asc,0"})]
    errors=[]
    for url,params in routes:
        try:
            r=requests.get(url,params=params,headers={"User-Agent":"Mozilla/5.0"},timeout=35)
            r.raise_for_status()
            x=r.json();v=parse_tpex(x,day)
            (OUT/"raw"/f"{day}.json.gz").write_bytes(gzip.compress(r.content))
            return v,{"source":url,"sha256":hashlib.sha256(r.content).hexdigest(),"rows":len(v)}
        except Exception as exc:errors.append(str(exc))
        time.sleep(.7)
    raise ValueError("; ".join(errors))
def main():
    OUT.mkdir(exist_ok=True);(OUT/"raw").mkdir(exist_ok=True)
    events=json.loads((SPECIAL/"reconciliation"/"official_events.json").read_text())
    for c,e in SPLITS.items():
        if any(x["date"]==e["date"] for x in events.get(c,[])):raise ValueError("overlap "+c)
        events.setdefault(c,[]).append(e)
    # Recompute TWSE-only comparisons with only the confirmed additional factors changed.
    prior.OUT=OUT/"factor_recheck";prior.official_events=lambda _:events;prior.main()
    after=json.loads((prior.OUT/"audit.json").read_text())
    miss=json.loads((P/"endpoint_missing.json").read_text())
    raw=pd.read_csv(P/"raw_close.csv.gz",index_col=0)
    raw.index=raw.index.astype(str)
    pairs=defaultdict(list)
    for x in miss:
        if x["kind"]=="official_missing_vendor_exists":pairs[x["date"]].append(x["code"])
    evidence=[];errors={};sources={}
    for day,cs in sorted(pairs.items()):
        try:
            quotes,meta=tpex(day);sources[day]=meta
            for c in cs:
                v=float(raw.at[day,c])
                o=quotes.get(c)
                evidence.append({"date":day,"code":c,"vendor_close":v,"tpex_close":o,"status":"not_found_on_tpex" if o is None else "matched" if abs(v-o)<=max(.011,o*.0005) else "different_price_basis"})
        except Exception as exc:
            errors[day]=str(exc)
            for c in cs:evidence.append({"date":day,"code":c,"status":"source_unavailable"})
        time.sleep(1.2)
    # Do not incorporate OTC returns until OTC corporate-action source and listing timeline verified.
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","new_confirmed_splits":SPLITS,"return_comparison_counts":after["return_comparison_counts"],"top5_identical_months":after["top5_identical_months"],"top10_identical_months":after["top10_identical_months"],"largest_residuals":after["largest_residuals"],"rank_differences":[x for x in json.loads((prior.OUT/"rank_sensitivity.json").read_text()) if x["top5_overlap"]!=5 or x["top10_overlap"]!=10],"missing_twse_codes":dict(Counter(x["code"] for x in miss if x["kind"]=="official_missing_vendor_exists")),"tpex_dates_expected":len(pairs),"tpex_dates_downloaded":len(sources),"tpex_status_counts":dict(Counter(x["status"] for x in evidence)),"tpex_errors":errors,"tpex_sources":sources,"limitations":["OTC listing and corporate actions must be verified before merging series.","Reference factors are not certified total returns.","Still no independent official full index roster, no validated merger settlement or execution audit."]}
    (OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    (OUT/"tpex_endpoint_evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2,allow_nan=False))
    print("PIT_MARKET_SUMMARY",json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
