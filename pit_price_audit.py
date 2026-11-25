"""Freeze PIT candidate prices, audit official signal/baseline endpoints; no performance claims."""
import gzip,hashlib,json,time,math
from pathlib import Path
import pandas as pd
import numpy as np
import requests
import exploratory_rotation as ex
from radar import parse_dated_market
ROOT=Path(__file__).resolve().parent
INP=ROOT/"pit-timing-input"
OUT=ROOT/"pit-price-output"
RETIRED={"2448","2809","2823","2867","2888","3682","5264"}
def official(day):
    url="https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    for attempt in range(2):
        try:
            r=requests.get(url,params={"date":day.replace("-",""),"type":"ALLBUT0999","response":"json"},headers={"User-Agent":"Mozilla/5.0"},timeout=45)
            r.raise_for_status();x=r.json()
            result=parse_dated_market(x,day)
            (OUT/"official"/f"{day}.json.gz").write_bytes(gzip.compress(r.content))
            return result,hashlib.sha256(r.content).hexdigest()
        except Exception as err:
            error=str(err);time.sleep(2)
    raise ValueError(error)
def main():
    OUT.mkdir(exist_ok=True);(OUT/"official").mkdir(exist_ok=True)
    rows=json.loads((INP/"provisional_monthly_rosters.json").read_text())
    if len(rows)!=77 or any(r["status"]!="PROVISIONAL_NOT_CERTIFIED" for r in rows):raise ValueError("wrong roster artifact")
    codes=sorted({c for r in rows for g in r["groups"].values() for c in g}|{"0050"})
    frames,fail=ex.download(sorted(set(codes)-RETIRED))
    op,cl,anoms,action_audit=ex.prepare(frames)
    # Store actual raw vendor and adjusted data separately. Never normalize away discrepancies.
    raw={c:f["Close"].where((f["Volume"]>0)&(f["Close"]>0)) for c,f in frames.items()}
    for c in sorted(RETIRED&set(codes)):
        f=pd.read_csv(INP/f"{c}_reference_adjusted.csv",index_col="date",parse_dates=True)
        raw[c]=f["close"];op[c]=f["reference_adjusted_open"].reindex(op.index.union(f.index));cl[c]=f["reference_adjusted_close"].reindex(cl.index.union(f.index))
        anoms[c]=f.reference_adjusted_close.pct_change().abs().loc[lambda x:x>.35]
    raw=pd.DataFrame(raw)
    dates=pd.DatetimeIndex(cl["0050"].dropna().index).sort_values()
    for frame in (raw,op,cl):
        if frame.index.has_duplicates:raise ValueError("duplicate dates")
    raw=raw.reindex(dates);op=op.reindex(dates);cl=cl.reindex(dates)
    raw.to_csv(OUT/"raw_close.csv.gz",compression="gzip");op.to_csv(OUT/"adjusted_open.csv.gz",compression="gzip");cl.to_csv(OUT/"adjusted_close.csv.gz",compression="gzip")
    needed={}
    signals=[]
    for r in rows:
        p=pd.Timestamp(r["signal_date"])
        if p not in dates:raise ValueError("signal absent in calendar "+str(p))
        b=dates[dates.searchsorted(p-pd.DateOffset(years=1),side="right")-1]
        if b>p-pd.DateOffset(years=1) or (p-pd.DateOffset(years=1)-b).days>10:raise ValueError("baseline invalid")
        symbols=sorted({c for g in r["groups"].values() for c in g}|{"0050"})
        for d in [b,p]:needed.setdefault(d.date().isoformat(),set()).update(symbols)
        signals.append((p,b,symbols))
    quotes={};source_errors={};hashes={}
    for day in sorted(needed):
        try:quotes[day],hashes[day]=official(day)
        except Exception as err:source_errors[day]=str(err)
        time.sleep(1.2)
    diffs=[];missing=[];counts={"matched":0,"mismatch":0,"vendor_missing_official_exists":0,"official_missing_vendor_exists":0,"both_missing":0,"source_unavailable":0}
    for day,cs in sorted(needed.items()):
        p=pd.Timestamp(day)
        for c in sorted(cs):
            if day in source_errors:counts["source_unavailable"]+=1;continue
            v=raw.at[p,c] if c in raw else np.nan
            o=quotes[day].get(c,(day,None))[1]
            if pd.isna(v) and o is None:k="both_missing"
            elif pd.isna(v):k="vendor_missing_official_exists"
            elif o is None:k="official_missing_vendor_exists"
            elif abs(float(v)-o)<=max(.011,o*.0005):k="matched"
            else:k="mismatch"
            counts[k]+=1
            if k=="mismatch":diffs.append({"date":day,"code":c,"vendor_close":float(v),"official_close":o,"ratio":float(v)/o})
            elif k!="matched":missing.append({"date":day,"code":c,"kind":k})
    coverage=[]
    for p,b,cs in signals:
        reasons={};eligible=[]
        for c in cs:
            if c not in cl:k="no_series"
            elif pd.isna(cl.at[b,c]):k="baseline_missing"
            elif pd.isna(cl.at[p,c]):k="signal_close_missing"
            elif cl.loc[b:p,c].count()<200:k="fewer_than_200_quotes"
            elif len(anoms.get(c,pd.Series(dtype=float)).loc[b:p]):k="unresolved_jump"
            else:eligible.append(c);continue
            reasons[c]=k
        coverage.append({"signal":p.date().isoformat(),"baseline":b.date().isoformat(),"universe":len(cs),"eligible_by_price_only":len(eligible),"excluded":reasons})
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","signal_count":len(rows),"unique_codes_in_monthly_rosters_including_0050":len(codes),"vendor_downloaded":len(frames),"retired_reference_series":sorted(RETIRED&set(codes)),"download_failures":fail,"official_dates_expected":len(needed),"official_dates_downloaded":len(quotes),"source_errors":source_errors,"endpoint_counts":counts,"mismatch_by_code":pd.Series([x["code"] for x in diffs],dtype=str).value_counts().to_dict(),"eligibility_min":min(x["eligible_by_price_only"] for x in coverage),"eligibility_max":max(x["eligible_by_price_only"] for x in coverage),"corporate_action_audit":action_audit,"limitations":["Roster is provisional, not official certified index history.","Retired series reference-adjusted; vendor series dividend/split-adjusted, not yet verified to same return convention.","Official close only cross-checks signal and baseline; does not establish daily adjustment correctness or execution feasibility.","Both-missing endpoints not automatically classified as IPO or suspension.","Merger settlement dates, delivery and actual tradeability not certified."]}
    for name,obj in [("audit.json",report),("endpoint_mismatches.json",diffs),("endpoint_missing.json",missing),("coverage.json",coverage),("official_hashes.json",hashes)]:
        (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False,default=lambda v:v.item()),encoding="utf-8")
    print("PIT_PRICE_SUMMARY",json.dumps(report,ensure_ascii=False,allow_nan=False,default=lambda v:v.item()),flush=True)
if __name__=="__main__":main()
