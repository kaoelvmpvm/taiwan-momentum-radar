"""V0.9 provisional historical-membership backtest.
Research only. Same frozen prices for dynamic and fixed-pool comparisons.
Never synthesize trade prices or liquidate a delisted position at its last quote.
A stopped path is explicitly incomplete and has no full-period performance metrics.
"""
import json,math,gzip,time,hashlib
from collections import defaultdict,Counter
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from regime_experiments import rebalance
ROOT=Path(__file__).resolve().parent
P=ROOT/"pit-price-input"
T=ROOT/"pit-timing-input"
OUT=ROOT/"pit-backtest-output"
START=pd.Timestamp("2020-07-01");END=pd.Timestamp("2026-11-25")
INITIAL=100000.
KEYS=("S0","M2","M3","M8","Q2")
BENCH="0050"
def extend_final_session(op,cl,raw):
    # Input archive ends 2026-11-24. Append only independently observed official quotes.
    # No mutation of the archived source, no assumption of a new trade.
    prior=pd.Timestamp("2026-11-24")
    if cl.index[-1]!=prior or prior not in raw.index or prior not in op.index:
        raise ValueError("unexpected input boundary; not permitted to shorten or interpolate")
    q0,h0=quote(str(prior.date()));q1,h1=quote(str(END.date()))
    # Ex-rights and reduction tables archived through target date; identify events explicitly.
    excluded={}
    for filename in (ROOT/"pit-reconcile-input"/"raw"/"ex_2026.json",ROOT/"pit-special-input"/"raw"/"reduction_2026_reducation_range.json"):
        if not filename.exists():
            # Locate the official reduction response by its validated title (not filename).
            if "reduction" in filename.name:
                matches=[]
                for f in (ROOT/"pit-special-input"/"raw").glob("*2026*.json"):
                    try:
                        x=json.loads(f.read_text())
                        if "股票減資恢復買賣參考價格" in str(x.get("title","")) and "115年11月25日" in str(x.get("title","")):matches.append(f)
                    except Exception:continue
                if len(matches)!=1:raise ValueError("cannot verify target day reduction source")
                filename=matches[0]
            else:raise ValueError("missing target day ex-right source")
        x=json.loads(filename.read_text())
        if x.get("stat")!="OK" or "115年11月25日" not in str(x.get("title","")):raise ValueError("action source not current through target")
        for row in x["data"]:
            f=dict(zip(x["fields"],row))
            ds=str(f.get("資料日期",f.get("恢復買賣日期","")))
            if ds in ("115年11月25日","115/11/25","2026/11/25","2026-11-25"):
                excluded[str(f.get("股票代號",f.get("證券代號","")))]="target_day_corporate_action_requires_separate_adjustment"
    # Daily price change must reconcile with prior raw close as an additional no-action guard.
    x=json.loads(gzip.decompress((OUT/"official_execution"/f"{END.date()}.json.gz").read_bytes()))
    t=next(t for t in x["tables"] if {"證券代號","收盤價","成交股數","漲跌價差"}.issubset(t.get("fields",[])))
    fields=t["fields"];ci=fields.index("證券代號");di=fields.index("漲跌價差");si=fields.index("漲跌(+/-)") if "漲跌(+/-)" in fields else None
    changes={}
    import re
    for row in t["data"]:
        try:
            v=float(str(row[di]).replace(",",""))
            sign=re.sub("<[^>]*>","",str(row[si])).strip() if si is not None else ""
            if sign=="-":v=-abs(v)
            elif sign=="+":v=abs(v)
            elif sign not in ("","X"," "):continue
            if sign=="X":continue
            changes[str(row[ci]).strip()]=v
        except (ValueError,TypeError):continue
    a={};z={};r={};issues={}
    for c in cl.columns:
        if c not in q1:
            issues[c]="no_target_day_official_trade";continue
        if c in excluded:
            issues[c]=excluded[c];continue
        if c not in q0 or pd.isna(raw.at[prior,c]) or pd.isna(cl.at[prior,c]):
            issues[c]="missing_prior_day_anchor";continue
        o0,c0,v0=q0[c];o1,c1,v1=q1[c]
        if abs(float(raw.at[prior,c])-c0)>max(.011,c0*.0005):
            issues[c]="prior_raw_price_basis_mismatch";continue
        if c not in changes or abs(c1-c0-changes[c])>max(.011,c0*.0005):
            issues[c]="official_change_not_consistent_with_previous_close";continue
        scale=float(cl.at[prior,c])/c0
        a[c]=o1*scale;z[c]=c1*scale;r[c]=c1
    report={"source":"TWSE MI_INDEX, dated official quotes plus ex-right and reduction guards","prior":str(prior.date()),"target":str(END.date()),"sha256":[h0,h1],"appended_codes":sorted(z),"unavailable_codes":issues}
    (OUT/"boundary_repair.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print("PIT_BOUNDARY_REPAIR",json.dumps({"appended":len(z),"unavailable":issues,"benchmark":{k:v for k,v in report.items() if k in ("prior","target")},"benchmark_close":z.get(BENCH)},ensure_ascii=False),flush=True)
    if BENCH not in z:raise ValueError("benchmark final-day repair failed")
    for f,values in [(op,a),(cl,z),(raw,r)]:
        f.loc[END]=pd.Series(values,dtype=float).reindex(f.columns)
    return op,cl,raw

def load_prices():
    def load(n):
        f=pd.read_csv(P/n,index_col=0,parse_dates=True)
        if f.index.has_duplicates or not f.index.is_monotonic_increasing:raise ValueError("invalid price index")
        return f
    op=load("adjusted_open.csv.gz");cl=load("adjusted_close.csv.gz");raw=load("raw_close.csv.gz")
    if END not in cl.index:
        op,cl,raw=extend_final_session(op,cl,raw)
    dates=pd.DatetimeIndex(cl[BENCH].dropna().index)
    if START not in dates or END not in dates:raise ValueError(f"missing benchmark boundary: {dates.min()} to {dates.max()}")
    cl=cl.reindex(dates);op=op.reindex(index=dates,columns=cl.columns);raw=raw.reindex(index=dates,columns=cl.columns)
    if (cl<=0).any().any() or (op<=0).any().any():raise ValueError("nonpositive observed price")
    return dates,op,cl,raw
def weights(names,frac=1.):
    return {c:frac/len(names) for c in names} if names else {}
def build(dates,cl,rosters,mode):
    anoms={c:cl[c].dropna().pct_change().abs().loc[lambda x:x>.35] for c in cl}
    first={c:cl[c].first_valid_index() for c in cl}
    fixed=sorted({r["code"] for r in json.loads((ROOT/"exploratory_pool_20261125.json").read_text())["ranking"]})
    if mode=="fixed" and not set(fixed)<=set(cl):raise ValueError("fixed universe incomplete")
    plans={k:{} for k in KEYS};records=[]
    for r in rosters:
        p=pd.Timestamp(r["signal_date"])
        i=dates.get_loc(p);d=dates[i+1]
        if d.month==p.month or not START<=d<=END:raise ValueError("invalid month boundary")
        b=dates[dates.searchsorted(p-pd.DateOffset(years=1),side="right")-1]
        b3=dates[dates.searchsorted(p-pd.DateOffset(months=3),side="right")-1]
        if (p-pd.DateOffset(years=1)-b).days>10:raise ValueError("stale baseline")
        pool=fixed if mode=="fixed" else sorted({c for v in r["groups"].values() for c in v}|{BENCH})
        excluded={};rank=[]
        for c in pool:
            if c not in cl or first[c] is None:reason="no_series"
            elif first[c]>b:reason="insufficient_listing_history"
            elif pd.isna(cl.at[b,c]) or pd.isna(cl.at[p,c]):reason="missing_endpoint"
            elif cl.loc[b:p,c].count()<200:reason="fewer_than_200_quotes"
            elif len(anoms[c].loc[b:p]):reason="unresolved_price_jump"
            else:rank.append(c);continue
            excluded[c]=reason
        if BENCH not in rank or len(rank)<10:raise ValueError("signal coverage failure "+str(p))
        score={c:float(cl.at[p,c]/cl.at[b,c]-1) for c in rank}
        rank.sort(key=lambda c:(-score[c],c));top=rank[:10]
        pos=[c for c in top if pd.notna(cl.at[b3,c]) and cl.at[p,c]>cl.at[b3,c]]
        pool20=rank[:math.ceil(len(rank)*.2)]
        high={c:float(cl.at[p,c]/cl.loc[b:p,c].max()) for c in pool20}
        q2=sorted(pool20,key=lambda c:(-high[c],rank.index(c)))[:10]
        target={"S0":{BENCH:1.},"M2":weights(rank[:5]),"M3":weights(top),"M8":{c:.1 for c in pos},"Q2":weights(q2)}
        for k,t in target.items():plans[k][d]=(p,t)
        records.append({"signal":str(p.date()),"execution":str(d.date()),"base12":str(b.date()),"base3":str(b3.date()),"universe":len(pool),"eligible":len(rank),"excluded":excluded,"rank":rank,"scores":score,"weights":target})
    return plans,records,anoms
def simulate(dates,op,cl,anoms,plans,start,end,fee_mult=1,buyhold=False):
    # Group-wide defer rule kept identical to the earlier research. No fictitious merger fills.
    hold={};cash=INITIAL;pending=None;delay=0;skips=0;cost=0.;trades=[];navs={};exps=[];stale=[];jumps=[];blocked=None
    marks=cl.ffill();last={c:cl[c].last_valid_index() for c in cl}
    for day in dates[(dates>=start)&(dates<=end)]:
        dead=[c for c in hold if last[c] is None or day>last[c]]
        if dead:
            blocked={"date":str(day.date()),"reason":"held_after_last_observed_price_requires_settlement_or_missing_price_repair","codes":dead};break
        if day in plans and (not buyhold or not hold):
            if pending is not None:skips+=1
            pending=plans[day]
        if pending is not None:
            sig,target=pending;names=sorted(set(hold)|set(target))
            row=op.loc[day,names]
            if row.notna().all() and (row>0).all():
                new,cashleft,fee,moves=rebalance(hold,cash,target,row.to_dict(),fee_mult)
                trades.append({"date":str(day.date()),"signal":str(sig.date()),"weights":target,"fee":fee,"moves":{c:float(v) for c,v in moves.items() if abs(v)>1e-6}})
                hold,cash=new,cashleft;cost+=fee;pending=None
            else:
                delay+=1
        value=0.
        for c,u in hold.items():
            if pd.isna(cl.at[day,c]):stale.append({"date":str(day.date()),"code":c})
            if day in anoms[c].index:jumps.append({"date":str(day.date()),"code":c,"abs_return":float(anoms[c].at[day])})
            value+=u*float(marks.at[day,c])
        nav=cash+value
        if not math.isfinite(nav) or nav<=0:raise ValueError("invalid NAV")
        navs[day]=nav;exps.append(value/nav)
    s=pd.Series(navs,dtype=float)
    result={"nav":s,"trades":trades,"audit":{"blocked":blocked,"delayed_sessions":delay,"skipped_signals":skips,"stale_marks":stale,"held_jumps":jumps,"pending_at_end":pending is not None,"cost":cost}}
    if blocked or s.empty or s.index[-1]!=end:
        result["summary"]={"status":"INCOMPLETE","last_observed":str(s.index[-1].date()) if len(s) else None}
        return result
    r=s.pct_change();r.iloc[0]=s.iloc[0]/INITIAL-1
    peak=pd.concat([pd.Series([INITIAL],index=[start-pd.Timedelta(days=1)]),s]).cummax().reindex(s.index)
    mdd=float((s/peak-1).min());vol=float(r.std(ddof=1)*math.sqrt(252));years=(end-start).days/365.2425
    cagr=float((s.iloc[-1]/INITIAL)**(1/years)-1)
    y=s.resample("YE").last();annual=y/y.shift(fill_value=INITIAL)-1
    result["summary"]={"status":"PROVISIONAL_COMPLETE","terminal":float(s.iloc[-1]),"cagr":cagr,"mdd":mdd,"sharpe":float(r.mean()*252/vol) if vol else None,"vol":vol,"calmar":cagr/abs(mdd) if mdd<0 else None,"exposure":float(np.mean(exps)),"cost":cost,"annual":{str(t.year):float(v) for t,v in annual.items()}}
    return result
def common_warm(dates,plans,full):
    # M9 uses only already observed M3 NAV; do not substitute future-member warmup.
    nav=full["M3"]["nav"]
    r=nav.pct_change().dropna();result={};audit=[]
    for d,(p,t) in plans["M3"].items():
        w=r.loc[:p].tail(126)
        if len(w)!=126:continue
        vol=float(w.std(ddof=1)*math.sqrt(252));frac=min(1.,.12/vol) if vol>0 else 1.
        result[d]=(p,{c:v*frac for c,v in t.items()});audit.append({"signal":str(p.date()),"vol126":vol,"fraction":frac})
    return result,audit
def quote(day):
    url="https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    for i in range(2):
        try:
            r=requests.get(url,params={"date":day.replace("-",""),"type":"ALLBUT0999","response":"json"},headers={"User-Agent":"Mozilla/5.0"},timeout=40)
            r.raise_for_status();x=r.json()
            if x.get("stat")!="OK" or str(x.get("date"))!=day.replace("-",""):raise ValueError("wrong returned day")
            fs={"證券代號","開盤價","收盤價","成交股數"}
            ts=[t for t in x["tables"] if fs<=set(t.get("fields",[]))]
            if len(ts)!=1:raise ValueError("ambiguous table")
            t=ts[0];idx=[t["fields"].index(k) for k in ("證券代號","開盤價","收盤價","成交股數")]
            rows={}
            for row in t["data"]:
                c=str(row[idx[0]]).strip()
                try:o,z,v=[float(str(row[j]).replace(",","")) for j in idx[1:]]
                except (ValueError,TypeError):continue
                if min(o,z,v)>0:rows[c]=(o,z,v)
            if BENCH not in rows or len(rows)<500:raise ValueError("incomplete market")
            (OUT/"official_execution"/f"{day}.json.gz").write_bytes(gzip.compress(r.content))
            return rows,hashlib.sha256(r.content).hexdigest()
        except Exception as exc:error=str(exc);time.sleep(2)
    raise ValueError(error)
def check_execution(paths,op,cl):
    required=defaultdict(dict)
    for label,r in paths.items():
        for t in r["trades"]:
            for c,v in t["moves"].items():
                required[t["date"]][c]=max(required[t["date"]].get(c,0.),abs(v))
    counts=Counter();issues=[];hashes={}
    for day,cs in sorted(required.items()):
        p=pd.Timestamp(day)
        try:q,sha=quote(day);hashes[day]=sha
        except Exception as exc:
            counts["source_unavailable"]+=len(cs);issues.append({"date":day,"error":str(exc),"codes":sorted(cs)});continue
        for c,v in cs.items():
            if c not in q:
                counts["official_trade_missing"]+=1;issues.append({"date":day,"code":c,"reason":"official_trade_missing"});continue
            o,z,vol=q[c]
            relative=float((op.at[p,c]/cl.at[p,c])/(o/z)-1)
            if abs(relative)>.001:
                counts["open_close_ratio_mismatch"]+=1;issues.append({"date":day,"code":c,"reason":"open_close_ratio_mismatch","relative":relative})
            else:counts["ratio_match"]+=1
            # Diagnostic only: daily volume not opening auction depth.
            if v/(o*vol)>.01:issues.append({"date":day,"code":c,"reason":"notional_exceeds_1pct_daily_volume_proxy","fraction":v/(o*vol)})
        time.sleep(1.1)
    return {"dates_expected":len(required),"dates_verified":len(hashes),"counts":dict(counts),"issues":issues,"sha256":hashes}
def main():
    OUT.mkdir(exist_ok=True);(OUT/"official_execution").mkdir(exist_ok=True)
    input_audit=json.loads((P/"audit.json").read_text());timing=json.loads((T/"audit.json").read_text())
    if input_audit["unique_codes_in_monthly_rosters_including_0050"]!=228 or input_audit["download_failures"] or timing["provisional_months"]!=77:raise ValueError("wrong source artifacts")
    dates,op,cl,raw=load_prices()
    rosters=json.loads((T/"provisional_monthly_rosters.json").read_text())
    if len(rosters)!=77:raise ValueError("wrong signals")
    report={"status":"PROVISIONAL_HISTORICAL_BACKTEST_NOT_CERTIFIED","start":str(START.date()),"end":str(END.date()),"initial":INITIAL,"pool_modes":{},"limitations":["Reverse-built monthly index proxy not an independently certified official roster; remaining ETF disclosure discrepancies.","Seven former stocks use reference-adjusted rather than exact total-return series; other series vendor-adjusted; missing OTC endpoint verification.","Fractional adjusted units; fixed costs, no lot/minimum fee, no limit queue or auction liquidity model.","Missing opening price defers the whole group; no fabricated settlement. If holding after last observed price, that path stops.","M9 warmup starts with observed historical M3 strategy returns, common later start; not compared to original full-period M9.","These are historical exploratory results, not an out-of-sample test or live order."]}
    all_paths={}
    for mode in ("historical","fixed"):
        plans,records,anoms=build(dates,cl,rosters,mode)
        def run(a,z,fee=1,targets=plans):
            return {k:simulate(dates,op,cl,anoms,t,a,z,fee,k=="S0") for k,t in targets.items()}
        full=run(START,END);double=run(START,END,2)
        for k,r in full.items():all_paths[mode+"_"+k]=r
        m9,warm=common_warm(dates,plans,full)
        later={}
        if m9:
            first=min(m9);target={**plans,"M9":m9}
            later=run(first,END,targets=target)
            for k,r in later.items():all_paths[mode+"_warm_"+k]=r
        periods={}
        for label,a,z in [("pre2026",START,pd.Timestamp("2025-12-31")),("2026_stress",pd.Timestamp("2026-01-01"),END)]:
            a=dates[dates.searchsorted(a)];z=dates[dates.searchsorted(z,side="right")-1]
            periods[label]={k:r["summary"] for k,r in run(a,z).items()}
        report["pool_modes"][mode]={"full":{k:r["summary"] for k,r in full.items()},"double_cost":{k:r["summary"] for k,r in double.items()},"execution_audits":{k:r["audit"] for k,r in full.items()},"periods":periods,"warm_start":str(first.date()) if m9 else None,"warm_comparison":{k:r["summary"] for k,r in later.items()},"warm_audits":{k:r["audit"] for k,r in later.items()},"eligibility_min":min(r["eligible"] for r in records),"eligibility_max":max(r["eligible"] for r in records),"signals":len(records)}
        (OUT/f"{mode}_signals.json").write_text(json.dumps(records,ensure_ascii=False,indent=2,allow_nan=False))
        (OUT/f"{mode}_m9_warmup.json").write_text(json.dumps(warm,indent=2))
    pd.DataFrame({k:r["nav"] for k,r in all_paths.items()}).to_csv(OUT/"daily_nav.csv")
    (OUT/"trades.json").write_text(json.dumps({k:r["trades"] for k,r in all_paths.items()},ensure_ascii=False,indent=2,allow_nan=False))
    # Persist preliminary results before official execution audit in case external source fails.
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    compact={m:{k:v for k,v in x.items() if k not in ("execution_audits","warm_audits")} for m,x in report["pool_modes"].items()}
    print("PIT_BACKTEST_RESULTS",json.dumps({"status":report["status"],"pool_modes":compact,"execution_audits":{m:{k:{"blocked":v["blocked"],"delayed_sessions":v["delayed_sessions"],"skipped_signals":v["skipped_signals"],"stale_marks":len(v["stale_marks"]),"held_jumps":v["held_jumps"]} for k,v in x["execution_audits"].items()} for m,x in report["pool_modes"].items()}},ensure_ascii=False,allow_nan=False),flush=True)
    checks=check_execution(all_paths,op,cl)
    report["official_execution_audit"]=checks
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print("PIT_BACKTEST_EXECUTION",json.dumps({k:v for k,v in checks.items() if k not in ("sha256","issues")}|{"issues":checks["issues"][:30],"total_issues":len(checks["issues"])},ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
