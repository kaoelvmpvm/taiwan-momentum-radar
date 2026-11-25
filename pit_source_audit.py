"""Independent dated reserve market cap and former-member price feasibility audit.
Never certifies official PIT membership or fills missing returns."""
import json,re,time,hashlib
from pathlib import Path
from decimal import Decimal
import requests
import pandas as pd
from radar import parse_dated_market
from pit_review_audit import reverse
import exploratory_rotation as original
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-source-output"
DAY="2025-07-09"
QURL="https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
PURL="https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
LAST={"5264":{"last":"2021-01-06","delisted":"2021-01-15","source":"https://taiwanindex.com.tw/news/176"},"2823":{"last":"2021-12-17","delisted":"2021-12-30","source":"https://taiwanindex.com.tw/news/199"},"2888":{"last":"2025-07-11","delisted":"2025-07-24","source":"https://research.ftserussell.com/products/index-notices/home/getnotice/?id=2616449","swap_ratio":"0.672","successor":"2887"}}
def get(url,day,params,filename):
    r=requests.get(url,params={"date":day.replace("-",""),"response":"json",**params},headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"},timeout=45)
    r.raise_for_status()
    (OUT/filename).write_bytes(r.content)
    x=r.json()
    if x.get("stat")!="OK":raise ValueError("source status "+str(x.get("stat")))
    return x,hashlib.sha256(r.content).hexdigest()
def date_matches(x,day):
    d=str(x.get("date","")).replace("/","").replace("-","")
    if d==day.replace("-",""):return True
    if d==str(int(day[:4])-1911)+day[5:7]+day[8:10]:return True
    title=str(x.get("title",""))
    return bool(re.search(rf"{int(day[:4])-1911}年0?{int(day[5:7])}月0?{int(day[8:10])}日",title))
def shares(x,day):
    if not date_matches(x,day):raise ValueError("share count date mismatch")
    fields=x.get("fields",[])
    code=next((i for i,v in enumerate(fields) if str(v).strip()=="證券代號"),None)
    issued=next((i for i,v in enumerate(fields) if str(v).strip()=="發行股數"),None)
    if code is None or issued is None:raise ValueError("unknown shares fields "+str(fields))
    result={}
    for row in x.get("data",[]):
        try:
            c=str(row[code]).strip();n=int(str(row[issued]).replace(",",""))
        except (ValueError,IndexError):continue
        if n<=0:continue
        if c in result:raise ValueError("duplicate shares code")
        result[c]=n
    if len(result)<500:raise ValueError("incomplete shares")
    return result
def prices(day,filename):
    x,sha=get(PURL,day,{"type":"ALLBUT0999"},filename)
    p=parse_dated_market(x,day)
    return p,sha
def main():
    OUT.mkdir(exist_ok=True)
    e=json.loads((ROOT/"backtest/pit_nonregular_evidence.json").read_text())
    regular=json.loads((ROOT/"backtest/pit_regular_events.json").read_text())
    pool=json.loads(original.SNAPSHOT.read_text())
    anchor={g:[r["code"] for r in pool["ranking"] if r["group"]==g] for g in ("0050","0051")}
    candidates=e["unresolved"]["candidates"]
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","reserve_date":DAY,"reserve":{},"last_trade":{}}
    try:
        q,qs=get(QURL,DAY,{"selectType":"ALLBUT0999"},"shares_20250709.json")
        p,ps=prices(DAY,"prices_20250709.json")
        n=shares(q,DAY)
        rows=[]
        for c in candidates:
            if c not in n or c not in p:raise ValueError("missing candidate "+c)
            pd_day,close=p[c]
            if str(pd_day)!=DAY:raise ValueError("wrong close date")
            cap=Decimal(str(close))*n[c]
            rows.append({"code":c,"shares":n[c],"close":float(close),"market_cap":int(cap)})
        rows.sort(key=lambda v:-v["market_cap"])
        if rows[0]["market_cap"]==rows[1]["market_cap"]:raise ValueError("tied top candidate")
        winner=rows[0]["code"]
        nonregular={"effective":"2025-07-14","groups":{"0050":{"add":[],"remove":[]},"0051":{"add":[winner],"remove":["2888"]}}}
        ev=sorted(regular+e["verified_nonregular"]+[nonregular],key=lambda x:x["effective"])
        rec,blocked=reverse(anchor,ev)
        report["reserve"]={"status":"computed_from_official_dated_shares_and_close","rows":rows,"winner":winner,"matches_prior_inference":winner=="5434","source_sha256":[qs,ps],"reverse_passed":sum(x["status"]=="provisional_regular_only" for x in rec),"reverse_blocked":blocked,"first_conflict":next((x for x in rec if x["status"]=="blocked"),None),"caveat":"Rule reconstruction, not an independent full index roster; eligibility/reserve changes still need confirmation."}
        (OUT/"provisional_reverse.json").write_text(json.dumps(rec,ensure_ascii=False,indent=2))
    except Exception as exc:report["reserve"]={"status":"blocked","error":str(exc)}
    for c,x in LAST.items():
        try:
            p,sha=prices(x["last"],f"last_{c}.json")
            if c not in p:raise ValueError("last trade code missing")
            report["last_trade"][c]={**x,"official_close":p[c][1],"source_sha256":sha}
        except Exception as exc:report["last_trade"][c]={**x,"error":str(exc)}
        time.sleep(1)
    old={c for event in regular+e["verified_nonregular"] for g in event["groups"].values() for side in ("add","remove") for c in g[side]}
    current={r["code"] for r in pool["ranking"]}
    former=sorted(old-current)
    report["former_count"]=len(former)
    # Historical vendor feasibility only: no present-day membership requirement, no filling missing series.
    frames,fail=original.download(former)
    data={}
    for c,f in frames.items():
        valid=f.loc[(f["Volume"]>0)&(f["Adj Close"]>0)]
        if valid.empty:data[c]={"status":"empty"}
        else:data[c]={"status":"downloaded_unverified","first":str(valid.index[0].date()),"last":str(valid.index[-1].date()),"rows":len(valid),"unresolved_jumps":int((valid["Adj Close"].pct_change().abs()>.35).sum())}
    for c,error in fail.items():data[c]={"status":"missing","error":str(error)}
    report["former_price_coverage"]=data
    report["former_downloaded"]=sum(x["status"]=="downloaded_unverified" for x in data.values())
    report["missing_price_codes"]=[c for c,x in data.items() if x["status"]!="downloaded_unverified"]
    report["remaining_requirements"]=["Independent dated complete index roster and historical checkpoints","Verify nonregular event completeness","Validate adjusted series and merger cash/share settlement, suspensions and delisting proceeds"]
    (OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    brief={k:v for k,v in report.items() if k!="former_price_coverage"}
    print("PIT_SOURCE_SUMMARY",json.dumps(brief,ensure_ascii=False,allow_nan=False),flush=True)
if __name__=="__main__":main()
