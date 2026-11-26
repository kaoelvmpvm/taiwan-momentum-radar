"""Audit actual 12-month return ratios against official ex-right reference factors.
Diagnostic only: reference adjustment != exact total return; unknown actions remain blocked.
Do not modify vendor prices, trade ledger, or certify provisional membership."""
import gzip,json,math,re
from collections import Counter,defaultdict
from pathlib import Path
import pandas as pd
from radar import parse_dated_market
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-factor-output"
P=ROOT/"pit-price-input"
X=ROOT/"pit-reconcile-input"
KNOWN={
 "0050":[{"date":"2025-06-18","factor":0.25,"source":"https://www.twse.com.tw/zh/ETFortune/announcement?company=A00005&date=20250617&fund=0050&seq=1&type=all"}],
 "6949":[{"date":"2026-09-07","factor":0.05,"source":"verified_corporate_actions.json"}],
 "3682":[{"date":"2020-01-20","factor":1/0.65543142,"source":"backtest/pit_timing_evidence.json"}]
}
def number(x):return float(str(x).replace(",",""))
def official_events(codes):
    result=defaultdict(list)
    seen=set()
    for y in range(2019,2027):
        x=json.loads((X/"raw"/f"ex_{y}.json").read_bytes())
        if x.get("stat")!="OK":raise ValueError("invalid ex source")
        fields=x["fields"]
        for r in x["data"]:
            f=dict(zip(fields,r));c=str(f["股票代號"]).strip()
            if c not in codes:continue
            m=re.fullmatch(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日",str(f["資料日期"]))
            if not m:raise ValueError("unexpected date")
            a,b,d=map(int,m.groups());day=f"{a+1911:04d}-{b:02d}-{d:02d}"
            if (c,day) in seen:raise ValueError("duplicate corporate action "+c+" "+day)
            seen.add((c,day))
            try:
                before=number(f["除權息前收盤價"]);ref=number(f["除權息參考價"])
                if min(before,ref)<=0:raise ValueError("nonpositive")
                fact=ref/before
            except (TypeError,ValueError):
                fact=None
            result[c].append({"date":day,"factor":fact,"type":f.get("權/息"),"before":f.get("除權息前收盤價"),"reference":f.get("除權息參考價"),"source":f"TWT49U {y}"})
    for c,events in KNOWN.items():
        for e in events:
            if (c,e["date"]) in seen:raise ValueError("overlapping special event "+c)
            result[c].append(e)
    return result
def ratio_audit(mismatches):
    by=defaultdict(list)
    for x in mismatches:by[x["code"]].append(x)
    result={}
    for c,rows in by.items():
        # Report observed ratios only, no speculative split-factor correction.
        cnt=Counter(round(r["ratio"],4) for r in rows)
        result[c]={"count":len(rows),"common_ratios":[{"ratio":r,"count":n} for r,n in cnt.most_common(8)],"first":min(r["date"] for r in rows),"last":max(r["date"] for r in rows)}
    return result
def main():
    OUT.mkdir(exist_ok=True)
    rosters=json.loads((ROOT/"pit-timing-input"/"provisional_monthly_rosters.json").read_text())
    coverage=json.loads((P/"coverage.json").read_text())
    if len(rosters)!=77 or len(coverage)!=77:raise ValueError("incomplete 77-month inputs")
    cl=pd.read_csv(P/"adjusted_close.csv.gz",index_col=0,dtype=None)
    raw=pd.read_csv(P/"raw_close.csv.gz",index_col=0,dtype=None)
    cl.index=cl.index.astype(str);raw.index=raw.index.astype(str)
    official={}
    for path in sorted((P/"official").glob("*.json.gz")):
        day=path.name[:10]
        official[day]={c:p for c,(d,p) in parse_dated_market(json.loads(gzip.decompress(path.read_bytes())),day).items()}
    if len(official)!=111:raise ValueError("official date coverage incomplete")
    events=official_events(set(cl.columns))
    mismatch=json.loads((P/"endpoint_mismatches.json").read_text())
    miss=json.loads((P/"endpoint_missing.json").read_text())
    ratios=ratio_audit(mismatch)
    comparisons=[];stats=Counter();flagcodes=Counter();flagmonths=Counter();rankdiff=[]
    for r,cov in zip(rosters,coverage):
        p=r["signal_date"];b=cov["baseline"]
        if p!=cov["signal"]:raise ValueError("unaligned signals")
        symbols=sorted({c for v in r["groups"].values() for c in v}|{"0050"})
        v_scores={};ref_scores={}
        for c in symbols:
            if c in cov["excluded"]:stats["excluded_by_original_price_eligibility"]+=1;continue
            if c not in official[b] or c not in official[p]:stats["official_endpoint_missing"]+=1;continue
            es=[e for e in events.get(c,[]) if b<e["date"]<=p]
            if any(e["factor"] is None for e in es):stats["invalid_official_factor"]+=1;continue
            fact=math.prod(e["factor"] for e in es)
            reference=official[p][c]/official[b][c]/fact
            vendor=float(cl.at[p,c]/cl.at[b,c])
            if not math.isfinite(reference) or not math.isfinite(vendor) or min(reference,vendor)<=0:raise ValueError("invalid return")
            residual=vendor/reference-1
            # Fixed diagnostic tolerance, not optimized for returns.
            status="within_0_5pct" if abs(residual)<=.005 else "over_0_5pct"
            stats[status]+=1
            if status=="over_0_5pct":flagcodes[c]+=1;flagmonths[p]+=1
            comparisons.append({"signal":p,"baseline":b,"code":c,"vendor_growth":vendor,"official_reference_growth":reference,"relative_residual":residual,"status":status,"events":es})
            v_scores[c]=vendor;ref_scores[c]=reference
        v=sorted(v_scores,key=lambda c:(-v_scores[c],c));w=sorted(ref_scores,key=lambda c:(-ref_scores[c],c))
        rankdiff.append({"signal":p,"common_comparable_candidates":len(v),"top5_overlap":len(set(v[:5])&set(w[:5])),"top10_overlap":len(set(v[:10])&set(w[:10])),"top5_differences":{"vendor_only":sorted(set(v[:5])-set(w[:5])),"reference_only":sorted(set(w[:5])-set(v[:5]))},"top10_differences":{"vendor_only":sorted(set(v[:10])-set(w[:10])),"reference_only":sorted(set(w[:10])-set(v[:10]))}})
    counts=Counter((r["kind"],r["date"]) for r in miss)
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","official_ex_events_for_pool":sum(len(v) for v in events.values()),"return_comparison_counts":dict(stats),"flagged_codes":dict(flagcodes.most_common()),"flagged_months":dict(flagmonths.most_common()),"top5_identical_months":sum(x["top5_overlap"]==5 for x in rankdiff),"top10_identical_months":sum(x["top10_overlap"]==10 for x in rankdiff),"total_months":len(rankdiff),"largest_residuals":[{k:v for k,v in x.items() if k!="events"} for x in sorted(comparisons,key=lambda x:-abs(x["relative_residual"]))[:20]],"missing_endpoint_by_date":[{"kind":k,"date":d,"count":n} for (k,d),n in counts.most_common(20)],"0050_observed_ratios":ratios.get("0050"),"limitations":["Official reference factor is not exact dividend/rights total return. Residual is a diagnostic, not proof of vendor error.","TWT49U excludes certain capital reductions and splits. Only three known special actions are included; additional action evidence required.","Rank comparisons use common comparable candidates, not full investable universe or strategy performance.","No automatic repair or formal backtest."]}
    for name,data in [("audit.json",report),("return_comparisons.json",comparisons),("rank_sensitivity.json",rankdiff),("observed_raw_ratios.json",ratios),("official_events.json",dict(events))]:
        (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    print("PIT_FACTOR_SUMMARY",json.dumps(report,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
