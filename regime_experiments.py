"""Predeclared independent risk-management experiments on a frozen current-member pool.
Exploration only: not a point-in-time membership test or live ledger."""
import json,math
from pathlib import Path
import pandas as pd
import numpy as np
import exploratory_rotation as original
from rotation_engine import INITIAL

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"regime-output"
START=pd.Timestamp("2020-07-01")
END=pd.Timestamp("2026-11-25")
WARM=pd.Timestamp("2019-07-01")
RULES=("S0","M3","M6","M7","M8","M9","M10")
REGIME=("M6","M7","M8","M9")
VOL_TARGET=.12
VOL_WINDOW=126

def weights(names,fraction=1.):
    return {c:fraction/len(names) for c in names} if names else {}

def rebalance(hold,cash,target,opens,fee_mult):
    held={c:u*opens[c] for c,u in hold.items()}
    nav=cash+sum(held.values()); universe=set(held)|set(target)
    if any(v<0 for v in target.values()) or sum(target.values())>1+1e-12:raise ValueError("invalid weights")
    def fee(c,v):
        return abs(v)*fee_mult*(.001425+.0005+(0 if v>=0 else .001 if c=="0050" else .003))
    def moves(a):return {c:a*target.get(c,0.)-held.get(c,0.) for c in universe}
    lo,hi=0.,nav
    for _ in range(48):
        mid=(lo+hi)/2
        if mid+sum(fee(c,v) for c,v in moves(mid).items())>nav:hi=mid
        else:lo=mid
    a=(lo+hi)/2;m=moves(a);fees=sum(fee(c,v) for c,v in m.items())
    cashleft=cash-sum(m.values())-fees
    if cashleft < -max(.00001,nav*1e-10):raise ValueError("negative cash")
    return {c:a*w/opens[c] for c,w in target.items() if w>0},max(0.,cashleft),fees,m

def simulate(dates,op,cl,anoms,plans,start,end,fee_mult=1,buyhold=False):
    marks=cl.ffill();daily_dates=dates[(dates>=start)&(dates<=end)]
    h={};cash=INITIAL;pending=None;total_fee=0.;orders=0;delays=0;skips=0;stale=0;held_jumps=[];trades=[];navs=[];exps=[]
    for day in daily_dates:
        if day in plans and (not buyhold or not h):
            if pending is not None:skips+=1
            pending=plans[day]
        if pending is not None:
            sig,target=pending
            names=sorted(set(h)|set(target))
            row=op.loc[day,names]
            if row.notna().all():
                h,cash,fee,m=rebalance(h,cash,target,row.to_dict(),fee_mult)
                total_fee+=fee;orders+=sum(abs(v)>1e-6 for v in m.values())
                trades.append({"date":str(day.date()),"signal":str(sig.date()),"weights":target,"fee":fee})
                pending=None
            else:delays+=1
        value=0.
        for c,u in h.items():
            if day in anoms[c].index:held_jumps.append([str(day.date()),c,float(anoms[c].at[day])])
            if pd.isna(cl.at[day,c]):stale+=1
            value+=u*float(marks.at[day,c])
        nav=cash+value
        if not math.isfinite(nav) or nav<=0:raise ValueError("invalid NAV")
        navs.append(nav);exps.append(value/nav)
    s=pd.Series(navs,index=daily_dates)
    exp=pd.Series(exps,index=daily_dates)
    returns=s.pct_change();returns.iloc[0]=s.iloc[0]/INITIAL-1
    draw=s/pd.concat([pd.Series([INITIAL],index=[start-pd.Timedelta(days=1)]),s]).cummax().reindex(s.index)-1
    years=(daily_dates[-1]-daily_dates[0]).days/365.2425
    vol=float(returns.std(ddof=1)*math.sqrt(252))
    cagr=float((s.iloc[-1]/INITIAL)**(1/years)-1)
    mdd=float(draw.min())
    summary={"terminal":float(s.iloc[-1]),"cagr":cagr,"mdd":mdd,"sharpe":float(returns.mean()*252/vol) if vol>0 else None,"vol":vol,"calmar":cagr/abs(mdd) if mdd<0 else None,"exposure":float(exp.mean()),"cost":total_fee,"orders":orders,"delays":delays,"skips":skips,"stale_marks":stale,"held_jumps":held_jumps,"pending_at_end":pending is not None}
    return {"nav":s,"exposure":exp,"summary":summary,"trades":trades}

def make_plans(dates,cl,anoms,sigs,warm_returns):
    plans={k:{} for k in RULES}
    market={};audit=[]
    for d,(p,rank) in sigs.items():
        if d<START:continue
        top=rank[:10]
        b=dates[dates.searchsorted(p-pd.DateOffset(years=1),side="right")-1]
        b3=dates[dates.searchsorted(p-pd.DateOffset(months=3),side="right")-1]
        # p is actual prior month-end. Include its close and preceding nine month-end closes.
        month_closes=cl.loc[:p,"0050"].resample("ME").last().dropna()
        if len(month_closes)<10:raise ValueError("MA warmup missing")
        ma=float(month_closes.tail(10).mean());price=float(cl.at[p,"0050"])
        above=price>ma
        market[d]=above
        positive12=[c for c in top if float(cl.at[p,c]/cl.at[b,c]-1)>0]
        positive3=[c for c in top if pd.notna(cl.at[b3,c]) and float(cl.at[p,c]/cl.at[b3,c]-1)>0]
        window=warm_returns.loc[:p].tail(VOL_WINDOW)
        if len(window)!=VOL_WINDOW or window.isna().any():raise ValueError("M9 insufficient warmup")
        vol=float(window.std(ddof=1)*math.sqrt(252))
        fraction=min(1.,VOL_TARGET/vol) if vol>0 else 1.
        # 12–2 monthly factor: prior month-end divided by the month-end 12 months before p.
        # Retain the original eligibility filter, and additionally require valid endpoints.
        skip_end=dates[dates.searchsorted(p.replace(day=1),side="left")-1]
        year_month=(p-pd.DateOffset(months=12)).to_period("M")
        cutoff=year_month.end_time.normalize()
        skip_base=dates[dates.searchsorted(cutoff,side="right")-1]
        eligible=[c for c in rank if pd.notna(cl.at[skip_base,c]) and pd.notna(cl.at[skip_end,c]) and not len(anoms[c].loc[skip_base:skip_end])]
        rankskip=sorted(eligible,key=lambda c:(-float(cl.at[skip_end,c]/cl.at[skip_base,c]-1),c))
        if len(rankskip)<10:raise ValueError("M10 coverage")
        targets={"S0":{"0050":1.},"M3":weights(top),"M6":weights(top) if above else {},
                 "M7":{c:.1 for c in positive12},"M8":{c:.1 for c in positive3},"M9":weights(top,fraction),"M10":weights(rankskip[:10])}
        for k,t in targets.items():plans[k][d]=(p,t)
        audit.append({"date":str(d.date()),"signal":str(p.date()),"base12":str(b.date()),"base3":str(b3.date()),"M10_base":str(skip_base.date()),"M10_end":str(skip_end.date()),"market_close":price,"ma10":ma,"market_above":above,"vol126":vol,"M9_fraction":fraction,"weights":targets})
    return plans,market,audit

def compare(results,market):
    benchmark=results["S0"]["nav"]
    control=results["M3"]["nav"]
    for k,v in results.items():
        s=v["nav"];y=s.resample("YE").last();yr=y/y.shift(fill_value=INITIAL)-1
        by=benchmark.resample("YE").last();byr=by/by.shift(fill_value=INITIAL)-1
        cy=control.resample("YE").last();cyr=cy/cy.shift(fill_value=INITIAL)-1
        full=[d for d in yr.index if d.year>s.index[0].year and d.year<s.index[-1].year]
        m=s.resample("ME").last();mr=m/m.shift(fill_value=INITIAL)-1
        cm=control.resample("ME").last();cmr=cm/cm.shift(fill_value=INITIAL)-1
        bm=benchmark.resample("ME").last()
        rr=m/m.shift(36)-1;cr=cm/cm.shift(36)-1;br=bm/bm.shift(36)-1
        valid=rr.notna()&cr.notna()&br.notna()
        condition={d.to_period("M"):val for d,val in market.items()}
        regimes={}
        for label,flag in [("above_ma10",True),("below_ma10",False)]:
            idx=[d for d in mr.index if condition.get(d.to_period("M")) is flag]
            regimes[label]={"months":len(idx),"mean_monthly_return":float(mr.loc[idx].mean()) if idx else None,"mean_monthly_excess_vs_M3":float((mr.loc[idx]-cmr.loc[idx]).mean()) if idx else None}
        v["summary"].update({"annual":{str(t.year):float(x) for t,x in yr.items()},"annual_wins_0050":int((yr.loc[full]>byr.loc[full]).sum()),"annual_wins_M3":int((yr.loc[full]>cyr.loc[full]).sum()),"annual_count":len(full),"rolling36_wins_0050":int((rr[valid]>br[valid]).sum()),"rolling36_wins_M3":int((rr[valid]>cr[valid]).sum()),"rolling36_count":int(valid.sum()),"market_regimes":regimes})
    return {k:v["summary"] for k,v in results.items()}

def suite(dates,op,cl,anoms,plans,market,start,end,fee_mult=1,match=True):
    results={k:simulate(dates,op,cl,anoms,plans[k],start,end,fee_mult,k=="S0") for k in RULES}
    if match:
        # Ex-post diagnostic only, not a tradable strategy: fixed fraction equal to
        # dynamic strategy's average observed daily exposure; disclose realized difference.
        for k in REGIME:
            frac=results[k]["summary"]["exposure"]
            fixed={d:(p,{c:w*frac for c,w in t.items()}) for d,(p,t) in plans["M3"].items()}
            z=simulate(dates,op,cl,anoms,fixed,start,end,fee_mult)
            z["summary"]["fixed_target_fraction"]=frac
            z["summary"]["exposure_difference"]=z["summary"]["exposure"]-frac
            results["C"+k[1:]]=z
    return results,compare(results,market)

def main():
    OUT.mkdir(exist_ok=True)
    snapshot=json.loads(original.SNAPSHOT.read_text())
    codes=sorted(x["code"] for x in snapshot["ranking"])
    if len(codes)!=151 or len(set(codes))!=151:raise ValueError("invalid fixed pool")
    original.BEGIN="2018-01-01"
    frames,fail=original.download(codes)
    print("DOWNLOAD_FAILURES",json.dumps(fail,ensure_ascii=False),flush=True)
    if fail:raise ValueError("incomplete download")
    op,cl,anoms,audit=original.prepare(frames)
    dates=pd.DatetimeIndex(cl["0050"].dropna().index).sort_values()
    op=op.reindex(dates);cl=cl.reindex(dates)
    warm=dates[dates.searchsorted(WARM)]
    sigs,coverage=original.signals(dates,cl,anoms,codes,warm,END)
    warmplan={d:(p,weights(rank[:10])) for d,(p,rank) in sigs.items()}
    wr=simulate(dates,op,cl,anoms,warmplan,warm,END)["nav"].pct_change().dropna()
    plans,market,signals=make_plans(dates,cl,anoms,sigs,wr)
    if len(signals)!=77:raise ValueError("unexpected signal count")
    results,summary=suite(dates,op,cl,anoms,plans,market,START,END)
    _,double=suite(dates,op,cl,anoms,plans,market,START,END,2,False)
    periods={}
    for label,lo,hi in [("2020H2-2023","2020-07-01","2023-12-31"),("2024-2026","2024-01-01","2026-11-25")]:
        a=dates[dates.searchsorted(pd.Timestamp(lo))];z=dates[dates.searchsorted(pd.Timestamp(hi),side="right")-1]
        _,periods[label]=suite(dates,op,cl,anoms,plans,market,a,z,1,False)
    # Compare baseline against prior run; small vendor revisions are flagged, not concealed.
    end_diff={}
    b=dates[dates.searchsorted(END-pd.DateOffset(years=1),side="right")-1]
    for row in snapshot["ranking"]:
        c=row["code"]
        if pd.notna(cl.at[b,c]) and pd.notna(cl.at[END,c]):
            diff=float((cl.at[END,c]/cl.at[b,c]-1)*100-row["momentum"])
            if abs(diff)>.5:end_diff[c]=diff
        else:end_diff[c]="missing endpoint"
    report={"audit":{"kind":"survivor_biased_exploration","pool_asof":snapshot["asof"],"downloaded":len(frames),"start":str(START.date()),"end":str(END.date()),"warmup":str(warm.date()),"signals":len(signals),"endpoint_differences_pp":end_diff,"unresolved_jumps":{c:{str(d.date()):float(x) for d,x in v.items()} for c,v in anoms.items() if len(v)},"prior_M3_terminal":2434591.4553167005,"prior_S0_terminal":478126.8238255605,"market_below_months":sum(not v for v in market.values()),"M7_filtered_slots":sum(10-len(s["weights"]["M7"]) for s in signals),"M8_filtered_slots":sum(10-len(s["weights"]["M8"]) for s in signals),"M9_mean_target":float(np.mean([s["M9_fraction"] for s in signals]))},"summary":summary,"double_cost":double,"subperiods":periods}
    (OUT/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    (OUT/"signals.json").write_text(json.dumps(signals,ensure_ascii=False,indent=2,allow_nan=False))
    pd.DataFrame({k:v["nav"] for k,v in results.items()}).to_csv(OUT/"daily_nav.csv")
    pd.DataFrame({k:v["exposure"] for k,v in results.items()}).to_csv(OUT/"daily_exposure.csv")
    (OUT/"trades.json").write_text(json.dumps({k:v["trades"] for k,v in results.items()},ensure_ascii=False))
    print("REGIME_SUMMARY",json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)

if __name__=="__main__":main()
