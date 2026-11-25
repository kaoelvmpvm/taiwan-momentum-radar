"""Survivor-biased exploration: fixed 2026-11-25 pool, point-in-time price eligibility.
Not a historical-membership backtest. No broker connection."""
import json, math, time, statistics, csv
from pathlib import Path
from bisect import bisect_right
import pandas as pd
import numpy as np
import yfinance as yf
from radar import ROOT, FACE_VALUE_EVENTS, verified_event_quotes, verify_suspended_rows, reconcile_face_value, dated_official_market
from rotation_engine import STRATEGIES, target, INITIAL

OUT=ROOT/"exploratory-output"
START="2020-07-01"
END="2026-11-25"
BEGIN="2019-01-01"
BENCH="0050"
SNAPSHOT=ROOT/"exploratory_pool_20261125.json"

def download(codes):
    frames={}
    failures={}
    for k in range(0,len(codes),12):
        batch=codes[k:k+12]
        raw=None
        for attempt in range(3):
            try:
                raw=yf.download([c+".TW" for c in batch],start=BEGIN,end="2026-11-26",auto_adjust=False,actions=True,repair=False,group_by="ticker",threads=False,progress=False,timeout=25)
                if raw is not None and not raw.empty: break
            except Exception as e:
                print("batch retry",batch,str(e),flush=True)
            time.sleep(3)
        for code in batch:
            try:
                f=raw[code+".TW"] if isinstance(raw.columns,pd.MultiIndex) else raw
                f=f[["Open","Close","Adj Close","Volume"]].apply(pd.to_numeric,errors="coerce").dropna(subset=["Close","Adj Close"])
                idx=pd.DatetimeIndex(f.index)
                if idx.tz is not None: idx=idx.tz_convert("Asia/Taipei").tz_localize(None)
                f.index=idx.normalize()
                if f.index.has_duplicates or f.empty: raise ValueError("empty or duplicate dates")
                frames[code]=f.sort_index()
            except Exception as e:
                failures[code]=str(e)
        print("DOWNLOAD",min(k+12,len(codes)),len(codes),"valid",len(frames),flush=True)
        time.sleep(1)
    # Retry only missing tickers independently, without changing universe.
    for c in list(failures):
        try:
            f=yf.download(c+".TW",start=BEGIN,end="2026-11-26",auto_adjust=False,actions=True,repair=False,group_by="ticker",threads=False,progress=False,timeout=25)
            if isinstance(f.columns,pd.MultiIndex):f=f[c+".TW"]
            f=f[["Open","Close","Adj Close","Volume"]].apply(pd.to_numeric,errors="coerce").dropna(subset=["Close","Adj Close"])
            idx=pd.DatetimeIndex(f.index)
            if idx.tz is not None: idx=idx.tz_convert("Asia/Taipei").tz_localize(None)
            f.index=idx.normalize()
            if f.empty or f.index.has_duplicates:raise ValueError("empty or duplicate")
            frames[c]=f.sort_index()
            del failures[c]
        except Exception as e: failures[c]=str(e)
    return frames,failures

def prepare(frames):
    audit={}
    evidence=json.loads((ROOT/"verified_corporate_actions.json").read_text())
    for c,event in FACE_VALUE_EVENTS.items():
        if c not in frames:continue
        f=frames[c]
        old,new,lookup,source=verified_event_quotes(c,event,evidence,dated_official_market)
        f,removed=verify_suspended_rows(f,event,c,lookup)
        f,record=reconcile_face_value(f,event,old,new)
        record["removed"]=removed
        record["historical_evidence"]=source
        audit[c]=record
        frames[c]=f
    opens={}; closes={}; anomalies={}
    for c,f in frames.items():
        # Only real positive-volume sessions. Mark suspended positions at last close, never trade there.
        valid=(f["Volume"]>0)&(f["Close"]>0)&(f["Adj Close"]>0)&np.isfinite(f["Close"])&np.isfinite(f["Adj Close"])
        f=f.loc[valid].copy()
        f["AO"]=f["Open"]*f["Adj Close"]/f["Close"]
        closes[c]=f["Adj Close"]
        opens[c]=f["AO"].where((f["AO"]>0)&np.isfinite(f["AO"])).dropna()
        anomalies[c]=f["Adj Close"].pct_change().abs().loc[lambda x:x>.35]
    return pd.DataFrame(opens),pd.DataFrame(closes),anomalies,audit

def signals(dates,close,anoms,codes,start,end):
    out={}; coverage=[]
    first={c:close[c].first_valid_index() for c in codes}
    for i in range(1,len(dates)):
        d,p=dates[i],dates[i-1]
        if d<start or d>end or d.month==p.month:continue
        cutoff=p-pd.DateOffset(years=1)
        bi=dates.searchsorted(cutoff,side="right")-1
        if bi<0 or (cutoff-dates[bi]).days>10:raise ValueError("No shared baseline")
        b=dates[bi]
        window=close.loc[b:p]
        valid=window.count()
        eligible=[]; excluded={}
        for c in codes:
            if first[c] is None or first[c]>b:reason="insufficient_listing_history"
            elif pd.isna(close.at[b,c]) or pd.isna(close.at[p,c]):reason="missing_endpoint"
            elif valid[c]<200:reason="fewer_than_200_quotes"
            elif len(anoms[c].loc[b:p]):reason="unresolved_price_jump"
            else:
                eligible.append(c);continue
            excluded[c]=reason
        if BENCH not in eligible:raise ValueError("benchmark not eligible "+str(p))
        scores=(close.loc[p,eligible]/close.loc[b,eligible]-1).to_dict()
        order=sorted(eligible,key=lambda c:(-scores[c],c))
        out[d]=(p,order)
        counts={r:list(excluded.values()).count(r) for r in sorted(set(excluded.values()))}
        coverage.append({"signal":str(p.date()),"execution":str(d.date()),"base":str(b.date()),"eligible":len(order),"excluded":counts,"excluded_codes":excluded,"top":order[0]})
    return out,coverage

def rebalance(hold,cash,names,opens,fee_mult):
    held={c:u*opens[c] for c,u in hold.items()}
    nav=cash+sum(held.values()); universe=set(held)|set(names)
    def fee(c,v):
        return abs(v)*fee_mult*(.001425+.0005+(0 if v>=0 else .001 if c==BENCH else .003))
    def moves(a):return {c:(a/len(names) if c in names else 0.)-held.get(c,0.) for c in universe}
    lo,hi=0.,nav
    for _ in range(55):
        mid=(lo+hi)/2
        if mid+sum(fee(c,v) for c,v in moves(mid).items())>nav:hi=mid
        else:lo=mid
    a=(lo+hi)/2; m=moves(a); fees=sum(fee(c,v) for c,v in m.items())
    return {c:a/len(names)/opens[c] for c in names},max(0.,cash-sum(m.values())-fees),fees,m

def simulate(dates,op,cl,anoms,sigs,start,end,fee_mult=1):
    result={}; daily_dates=dates[(dates>=start)&(dates<=end)]
    marks=cl.ffill()
    for strategy in STRATEGIES:
        h={};cash=INITIAL;pending=None;peak=INITIAL;total_fee=0.;orders=0;turnover=0.;delays=0;skips=0; max_delay=0; pending_days=0; held_jumps=[];stale_marks=0
        navs=[]; trades=[]
        for day in daily_dates:
            if day in sigs and (strategy!="S0" or not h):
                if pending is not None:skips+=1
                signal,rank=sigs[day]
                pending=(signal,target(strategy,rank)); pending_days=0
            if pending:
                sig,names=pending
                # Never replace missing execution with estimated opening price.
                symbols=sorted(set(h)|set(names))
                row=op.loc[day,symbols]
                if row.notna().all():
                    h,cash,fee,m=rebalance(h,cash,names,row.to_dict(),fee_mult)
                    total_fee+=fee
                    orders+=sum(abs(v)>1e-6 for v in m.values())
                    turnover+=sum(abs(v) for v in m.values())
                    trades.append({"date":str(day.date()),"signal":str(sig.date()),"targets":names,"fee":fee,"delay_sessions":pending_days})
                    max_delay=max(max_delay,pending_days);pending=None
                else:delays+=1;pending_days+=1
            for c in h:
                if day in anoms[c].index:
                    held_jumps.append({"date":str(day.date()),"code":c,"abs_change":float(anoms[c].at[day])})
                if pd.isna(cl.at[day,c]):stale_marks+=1
            nav=cash+sum(u*float(marks.at[day,c]) for c,u in h.items())
            if not math.isfinite(nav) or nav<=0:raise ValueError("invalid NAV")
            peak=max(peak,nav);navs.append(nav)
        s=pd.Series(navs,index=daily_dates)
        returns=s.pct_change();returns.iloc[0]=s.iloc[0]/INITIAL-1
        draw=s/pd.concat([pd.Series([INITIAL],index=[start-pd.Timedelta(days=1)]),s]).cummax().reindex(s.index)-1
        years=(end-start).days/365.2425
        summary={"terminal":float(s.iloc[-1]),"cagr":float((s.iloc[-1]/INITIAL)**(1/years)-1),"mdd":float(draw.min()),"sharpe":float(returns.mean()/returns.std(ddof=1)*math.sqrt(252)),"cost":total_fee,"orders":orders,"turnover_notional":turnover,"delayed_sessions":delays,"skipped_signals":skips,"max_delay":max_delay,"stale_marks":stale_marks,"held_jumps":held_jumps,"pending_at_end":pending is not None}
        result[strategy]={"summary":summary,"nav":s,"trades":trades}
    return result

def compare(result):
    bench=result["S0"]["nav"]
    bmonthly=bench.resample("ME").last()
    # First partial return measured from initial capital.
    byr=bench.resample("YE").last()
    byret=byr/byr.shift(fill_value=INITIAL)-1
    # Incomplete last year excluded from annual win rate, but separately reported.
    full_years=[y for y in byret.index if y.year>bench.index[0].year and y.year<bench.index[-1].year]
    for k,v in result.items():
        s=v["nav"];m=s.resample("ME").last();y=s.resample("YE").last();yr=y/y.shift(fill_value=INITIAL)-1
        rr=m/m.shift(36)-1;br=bmonthly/bmonthly.shift(36)-1
        valid=rr.notna()&br.notna()
        v["summary"].update({"annual_returns":{str(t.year):float(x) for t,x in yr.items()},"annual_wins_full":int((yr.loc[full_years]>byret.loc[full_years]).sum()),"annual_full_count":len(full_years),"rolling_36m_wins":int((rr[valid]>br[valid]).sum()),"rolling_36m_count":int(valid.sum())})
    return {k:v["summary"] for k,v in result.items()}

def main():
    OUT.mkdir(exist_ok=True)
    snapshot=json.loads(SNAPSHOT.read_text())
    codes=sorted(x["code"] for x in snapshot["ranking"])
    if len(codes)!=151 or len(set(codes))!=151:raise ValueError("invalid frozen universe")
    frames,fail=download(codes)
    print("DOWNLOAD_FAILURES",json.dumps(fail,ensure_ascii=False),flush=True)
    if fail:raise ValueError("Cannot claim 151-stock exploration with failed downloads")
    op,cl,anoms,audit=prepare(frames)
    dates=pd.DatetimeIndex(cl[BENCH].dropna().index).sort_values()
    op=op.reindex(dates);cl=cl.reindex(dates)
    start=pd.Timestamp(START);end=pd.Timestamp(END)
    if start not in dates or end not in dates:raise ValueError("benchmark start/end missing")
    sigs,coverage=signals(dates,cl,anoms,codes,start,end)
    # Validate final benchmark and vendor endpoint momentum against stored snapshot.
    latest={}
    b=dates[dates.searchsorted(end-pd.DateOffset(years=1),side="right")-1]
    for row in snapshot["ranking"]:
        c=row["code"]
        if pd.notna(cl.at[b,c]) and pd.notna(cl.at[end,c]):
            diff=float((cl.at[end,c]/cl.at[b,c]-1)*100-row["momentum"])
            if abs(diff)>.5:latest[c]=diff
        else:latest[c]="missing_endpoint"
    print("ENDPOINT_DIFFERENCES",json.dumps(latest,ensure_ascii=False),flush=True)
    result=simulate(dates,op,cl,anoms,sigs,start,end)
    summary=compare(result)
    doubled=compare(simulate(dates,op,cl,anoms,sigs,start,end,2))
    # Fixed chronological subperiods, no optimization.
    periods={}
    for label,lo,hi in [("2020H2-2023","2020-07-01","2023-12-31"),("2024-2026","2024-01-01",END)]:
        a=dates[dates.searchsorted(pd.Timestamp(lo))]
        z=dates[dates.searchsorted(pd.Timestamp(hi),side="right")-1]
        periods[label]=compare(simulate(dates,op,cl,anoms,sigs,a,z))
    audit_summary={"data_kind":"survivor_biased_exploration","start":START,"end":END,"pool_asof":snapshot["asof"],"downloaded":len(frames),"signal_count":len(coverage),"eligibility_min":min(x["eligible"] for x in coverage),"eligibility_max":max(x["eligible"] for x in coverage),"eligibility_avg":statistics.mean(x["eligible"] for x in coverage),"first_signal":coverage[0],"last_signal":coverage[-1],"unresolved_jumps":{c:{str(d.date()):float(x) for d,x in v.items()} for c,v in anoms.items() if len(v)},"endpoint_differences_pp":latest,"corporate_action_audit":audit}
    report={"audit":audit_summary,"summary":summary,"double_cost":doubled,"subperiods":periods}
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    (OUT/"coverage.json").write_text(json.dumps(coverage,ensure_ascii=False,indent=2))
    pd.DataFrame({k:v["nav"] for k,v in result.items()}).to_csv(OUT/"daily_nav.csv")
    (OUT/"trades.json").write_text(json.dumps({k:v["trades"] for k,v in result.items()},ensure_ascii=False))
    # Compact verified outputs in job logs for analysis without downloading artifacts.
    print("EXPLORATORY_SUMMARY",json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
