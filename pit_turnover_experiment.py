"""T1 monthly high-turnover experiment. Actual daily traded shares / dated issued shares.
No price momentum screen, no parameter optimization, no missing-data substitution."""
import gzip,hashlib,json,math,re,sys,time
from pathlib import Path
from collections import Counter
import pandas as pd
import numpy as np
import requests
import pit_historical_backtest as pit
import pit_persistence_experiments as prev
from regime_experiments import rebalance
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-turnover-output"
def load(n):return pd.read_csv(pit.P/n,index_col=0,parse_dates=True)
def sources():
    cl=load("adjusted_close.csv.gz")
    dates=pd.DatetimeIndex(cl["0050"].dropna().index)
    rows=json.loads((pit.T/"provisional_monthly_rosters.json").read_text())
    if len(rows)!=77:raise ValueError("incorrect roster input")
    return dates,rows
def date_ok(x,day):
    p=pd.Timestamp(day);roc=p.year-1911
    title=str(x.get("title",""))
    return str(x.get("date","")).replace("-","").replace("/","")==p.strftime("%Y%m%d") or re.search(rf"{roc}年0?{p.month}月0?{p.day}日",title) is not None
def table(x,fields):
    ts=[t for t in x.get("tables",[]) if fields<=set(t.get("fields",[]))]
    if fields<=set(x.get("fields",[])):ts.append(x)
    if len(ts)!=1:raise ValueError("wrong or ambiguous fields")
    return ts[0]
def num(x):
    s=str(x).replace(",","").strip()
    if s in ("","--","---","N/A"):return None
    v=float(s)
    if not math.isfinite(v) or v<0:raise ValueError("invalid number")
    return v
def get(day,kind,cache):
    p=cache/f"{day}_{kind}.json.gz"
    if p.exists():raw=gzip.decompress(p.read_bytes())
    else:
        paths=[ROOT/"pit-price-input"/"official"/f"{day}.json.gz",ROOT/"pit-prior-backtest"/"official_execution"/f"{day}.json.gz"] if kind=="volume" else []
        found=next((q for q in paths if q.exists()),None)
        if found:raw=gzip.decompress(found.read_bytes())
        else:
            url="https://www.twse.com.tw/rwd/zh/"+("afterTrading/MI_INDEX" if kind=="volume" else "fund/MI_QFIIS")
            params={"date":day.replace("-",""),"response":"json"}
            params.update({"type":"ALLBUT0999"} if kind=="volume" else {"selectType":"ALLBUT0999"})
            for attempt in range(3):
                try:
                    r=requests.get(url,params=params,headers={"User-Agent":"Mozilla/5.0"},timeout=35)
                    r.raise_for_status();raw=r.content
                    x=r.json()
                    if x.get("stat")!="OK" or not date_ok(x,day):raise ValueError("wrong dated response")
                    time.sleep(1.3)
                    break
                except Exception as e:
                    err=str(e);time.sleep(3*(attempt+1))
            else:raise ValueError(err)
    x=json.loads(raw)
    if x.get("stat")!="OK" or not date_ok(x,day):raise ValueError("wrong dated cached response")
    field="成交股數" if kind=="volume" else "發行股數"
    t=table(x,{"證券代號",field});ci=t["fields"].index("證券代號");vi=t["fields"].index(field)
    result={}
    for r in t["data"]:
        c=str(r[ci]).strip()
        try:v=num(r[vi])
        except (ValueError,TypeError):continue
        if v is not None:
            if c in result:raise ValueError("duplicate security")
            result[c]=v
    if len(result)<500 or result.get("2330",0)<=0:raise ValueError("partial market response")
    if kind=="shares" and not 10e9<result["2330"]<40e9:raise ValueError("unexpected TSMC share count unit")
    if not p.exists():p.write_bytes(gzip.compress(raw))
    return result,hashlib.sha256(raw).hexdigest()
def collect(year):
    dates,rows=sources();out=OUT/str(year);out.mkdir(parents=True,exist_ok=True)
    cache=out/"raw";cache.mkdir(exist_ok=True)
    signals=[x for x in rows if pd.Timestamp(x["signal_date"]).year==year]
    windows={x["signal_date"]:dates[dates.get_loc(pd.Timestamp(x["signal_date"]))-19:dates.get_loc(pd.Timestamp(x["signal_date"]))+1] for x in signals}
    days=sorted({str(p.date()) for w in windows.values() for p in w})
    data={};errors={};hashes={}
    for i,day in enumerate(days):
        try:
            v,hv=get(day,"volume",cache);s,hs=get(day,"shares",cache)
            data[day]=(v,s);hashes[day]={"volume":hv,"shares":hs}
        except Exception as e:errors[day]=str(e)
        if i%20==0:print("TURNOVER_FETCH",year,i+1,len(days),len(errors),flush=True)
    result=[]
    for x in signals:
        p=x["signal_date"];w=[str(d.date()) for d in windows[p]]
        if len(w)!=20:raise ValueError("window not 20 sessions")
        pool=sorted({c for g in x["groups"].values() for c in g}-{ "0050"})
        missing_days=[d for d in w if d not in data]
        rec={"signal":p,"window":w,"universe":len(pool),"source_missing":missing_days,"excluded":{},"scores":{}}
        if not missing_days:
            for c in pool:
                bad=[d for d in w if c not in data[d][0] or data[d][1].get(c,0)<=0]
                if bad:rec["excluded"][c]=bad;continue
                rec["scores"][c]=sum(data[d][0][c]/data[d][1][c] for d in w)
            rec["rank"]=sorted(rec["scores"],key=lambda c:(-rec["scores"][c],c))
        result.append(rec)
    (out/"signals.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (out/"audit.json").write_text(json.dumps({"year":year,"expected_days":len(days),"verified_days":len(data),"errors":errors,"hashes":hashes,"signals":len(result)},ensure_ascii=False,indent=2))
    print("TURNOVER_YEAR",json.dumps({"year":year,"days":len(days),"verified":len(data),"errors":errors,"eligible":[len(r["scores"]) for r in result]},ensure_ascii=False),flush=True)
def simulate(dates,op,cl,plans,start,end,mult=1):
    # Exact first-open purchase and last-close liquidation; no fabricated substitute prices.
    # Do not silently defer or replace a failed planned fill.
    cash=100000.;hold={};nav={};orders=[];blocked=None;cost=0.
    marks=cl.ffill();last={c:cl[c].last_valid_index() for c in cl}
    for day in dates[(dates>=start)&(dates<=end)]:
        if any(last[c] is None or day>last[c] for c in hold):
            blocked={"date":str(day.date()),"reason":"held_after_final_quote"};break
        if day in plans:
            if hold:raise ValueError("prior month position not liquidated")
            w=plans[day];row=op.loc[day,list(w)]
            if row.isna().any() or (row<=0).any():
                blocked={"date":str(day.date()),"reason":"missing_buy_open","codes":row[row.isna()|(row<=0)].index.tolist()};break
            hold,cash,fee,moves=rebalance({},cash,w,row.to_dict(),mult)
            cost+=fee;orders.append({"date":str(day.date()),"side":"open_buy","fee":fee,"moves":moves,"weights":w})
        if hold and day==dates[dates.to_period("M")==day.to_period("M")][-1]:
            row=cl.loc[day,list(hold)]
            if row.isna().any() or (row<=0).any():
                blocked={"date":str(day.date()),"reason":"missing_sell_close","codes":row[row.isna()|(row<=0)].index.tolist()};break
            hold,cash,fee,moves=rebalance(hold,cash,{},row.to_dict(),mult)
            cost+=fee;orders.append({"date":str(day.date()),"side":"close_sell","fee":fee,"moves":moves})
        nav[day]=cash+sum(u*marks.at[day,c] for c,u in hold.items())
    s=pd.Series(nav,dtype=float)
    if blocked or s.empty or s.index[-1]!=end:return {"status":"INCOMPLETE","blocked":blocked},s,orders
    r=s.pct_change();r.iloc[0]=s.iloc[0]/100000-1
    peak=pd.concat([pd.Series([100000.],index=[start-pd.Timedelta(days=1)]),s]).cummax().reindex(s.index)
    mdd=float((s/peak-1).min());vol=float(r.std()*math.sqrt(252));cagr=float((s.iloc[-1]/100000)**(365.2425/(end-start).days)-1)
    y=s.resample("YE").last();annual=y/y.shift(fill_value=100000.)-1
    return {"status":"PROVISIONAL_COMPLETE","terminal":float(s.iloc[-1]),"cagr":cagr,"mdd":mdd,"sharpe":float(r.mean()*252/vol),"vol":vol,"calmar":cagr/abs(mdd),"cost":cost,"annual":{str(d.year):float(v) for d,v in annual.items()}},s,orders
def aggregate():
    OUT.mkdir(exist_ok=True);pit.OUT=OUT/"boundary";(pit.OUT/"official_execution").mkdir(parents=True,exist_ok=True)
    pit.quote=prev.cached_quote
    dates,op,cl,raw=pit.load_prices()
    rows=[];audits=[]
    for year in range(2020,2027):
        folder=ROOT/"pit-turnover-input"/str(year)
        rows+=json.loads((folder/"signals.json").read_text());audits.append(json.loads((folder/"audit.json").read_text()))
    rows.sort(key=lambda x:x["signal"])
    if len(rows)!=77:raise ValueError("wrong source signals")
    # Last fully completed calendar month only. November 2026 is incomplete.
    start=pit.START;end=dates[dates.to_period("M")==pd.Period("2026-10","M")][-1]
    plans={"T1":{},"T0":{},"S0":{}};coverage=[];blocked=[]
    for x in rows:
        p=pd.Timestamp(x["signal"]);i=dates.get_loc(p);d=dates[i+1]
        if not start<=d<=end:continue
        if x["source_missing"] or len(x["scores"])<10:
            blocked.append({"signal":x["signal"],"source_missing":x["source_missing"],"eligible":len(x["scores"])});continue
        # Only official 20-day coverage; exclusions are reported, not filled.
        names=x["rank"][:10]
        plans["T1"][d]={c:.1 for c in names};plans["T0"][d]={"0050":1.};plans["S0"][d]=(p,{"0050":1.})
        coverage.append({"signal":x["signal"],"execution":str(d.date()),"eligible":len(x["scores"]),"universe":x["universe"],"excluded":x["excluded"],"selected":{c:x["scores"][c] for c in names}})
    report={"status":"BLOCKED_SOURCE_COVERAGE" if blocked else "PROVISIONAL_RESEARCH_NOT_CERTIFIED","period":[str(start.date()),str(end.date())],"blocked_signals":blocked,"coverage":coverage,"data_audits":[{k:v for k,v in a.items() if k!="hashes"} for a in audits],"limitations":["Historical membership provisional; excludes securities without all 20 dated official TWSE volume and share observations (may include newly listed or previously OTC issues).","Reported issued shares, not free float; no substitution by volume or current shares.","Fractional adjusted units, simulated opening/closing fills, no queue or minimum fee.","2026 November excluded because month not complete."]}
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if blocked:
        print("TURNOVER_BLOCKED",json.dumps({"blocked":blocked},ensure_ascii=False),flush=True);return
    if len(coverage)!=76:raise ValueError("unexpected completed month count")
    anoms={c:cl[c].dropna().pct_change().abs().loc[lambda x:x>.35] for c in cl}
    fullnav={};trades={}
    for label,a,z,m in [("full",start,end,1),("double_cost",start,end,2),("pre2026",start,dates[dates.to_period("M")==pd.Period("2025-12","M")][-1],1),("stress2026",dates[dates.searchsorted(pd.Timestamp("2026-01-01"))],end,1)]:
        report[label]={}
        for k in ("T1","T0"):
            stats,nav,order=simulate(dates,op,cl,plans[k],a,z,m)
            report[label][k]=stats
            if label=="full":fullnav[k]=nav;trades[k]=order
        s=pit.simulate(dates,op,cl,anoms,plans["S0"],a,z,m,True)
        report[label]["S0"]=s["summary"]
        if label=="full":fullnav["S0"]=s["nav"]
    pd.DataFrame(fullnav).to_csv(OUT/"daily_nav.csv")
    (OUT/"trades.json").write_text(json.dumps(trades,ensure_ascii=False,indent=2))
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print("TURNOVER_RESULTS",json.dumps({k:v for k,v in report.items() if k not in ("coverage","data_audits")}|{"eligible_min":min(x["eligible"] for x in coverage),"eligible_max":max(x["eligible"] for x in coverage)},ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":
    if sys.argv[1]=="collect":collect(int(sys.argv[2]))
    else:aggregate()
