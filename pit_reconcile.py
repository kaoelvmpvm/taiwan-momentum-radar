"""Re-use original evidence, fix historical disclosures/ex-right dates, audit two newly discovered delistings."""
import json,gzip,re,time,hashlib,csv
from pathlib import Path
from datetime import date
import pandas as pd
from bs4 import BeautifulSoup
import pit_retired_audit as retired
import pit_source_audit as source
from pit_checkpoint_audit import expected_at
from pit_review_audit import reverse
from historical_holdings_audit import NAMES
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-reconcile-output"
OLD=ROOT/"pit-old-retired"
CHECK=ROOT/"pit-old-checkpoints"
GROUPS=("0050","0051")
A=json.loads((ROOT/"backtest/pit_retired_actions.json").read_text())
A.update(json.loads((ROOT/"backtest/pit_extra_actions.json").read_text()))
A["2888"]["shares"][1]["code"]="2887I"
CANDIDATES=["3596","9958","1717","6191","2498","2889","1560","3189","3030","6691"]
def extract(raw):
    soup=BeautifulSoup(raw,"html.parser");active=None;result={g:{} for g in GROUPS}
    for table in soup.find_all("table"):
        if table.find("table"):continue
        t=re.sub(r"\s+","",table.get_text(" ",strip=True))
        if "公司代號" in t and "證券投資信託基金" in t:
            f=[g for g,n in NAMES.items() if n in t];active=f[0] if len(f)==1 else None
        if active is None or "股票代號" not in t or "股票名稱" not in t:continue
        for tr in table.find_all("tr"):
            cells=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"],recursive=False)]
            # MOPS rowspans omit fund/industry columns in subsequent rows.
            # Identify the code/name/weight triple from the row itself.
            matches=[i for i in range(len(cells)-2) if re.fullmatch(r"[1-9]\d{3}",cells[i]) and re.fullmatch(r"\d+(?:\.\d+)?%",cells[i+2]) and cells[i+1]]
            if len(matches)>1:raise ValueError("ambiguous row "+str(cells))
            if not matches:continue
            i=matches[0];c=cells[i]
            if c in result[active]:raise ValueError("duplicate "+c)
            result[active][c]={"name":cells[i+1],"weight":cells[i+2]}
        active=None
    return result
def ev(d,a,b):
    return {"effective":d,"groups":{"0050":{"add":[],"remove":[]},"0051":{"add":[a],"remove":[b]}}}
def main():
    OUT.mkdir(exist_ok=True);raw=OUT/"raw";raw.mkdir(exist_ok=True)
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","reserve":{},"checkpoints":[],"ex_rights":{},"extra_raw":{}}
    source.OUT=raw
    try:
        q,qs=source.get(source.QURL,"2025-09-15",{"selectType":"ALLBUT0999"},"reserve_shares.json")
        p,ps=source.prices("2025-09-15","reserve_prices.json")
        n=source.shares(q,"2025-09-15")
        rows=[{"code":c,"cap":round(n[c]*p[c][1]),"shares":n[c],"close":p[c][1]} for c in CANDIDATES]
        rows.sort(key=lambda x:-x["cap"])
        report["reserve"]={"rows":rows,"winner":rows[0]["code"],"source_sha256":[qs,ps]}
    except Exception as exc:report["reserve"]={"error":str(exc)}
    regular=json.loads((ROOT/"backtest/pit_regular_events.json").read_text())
    other=json.loads((ROOT/"backtest/pit_nonregular_evidence.json").read_text())
    pool=json.loads((ROOT/"exploratory_pool_20261125.json").read_text())
    anchor={g:[r["code"] for r in pool["ranking"] if r["group"]==g] for g in GROUPS}
    events=regular+other["verified_nonregular"]+[ev("2025-07-14","5434","2888"),ev("2020-12-24","3714","2448")]
    if report["reserve"].get("winner")=="6191":
        events+=[ev("2025-09-18","6191","2809")]
        r,b=reverse(anchor,sorted(events,key=lambda e:e["effective"]))
        report["reverse"]={"passed":sum(x["status"]=="provisional_regular_only" for x in r),"blocked":b}
        if not b:
            for f in sorted(CHECK.glob("*.html.gz")):
                label=f.name.split(".")[0];y,k=map(int,label.split("Q"));d=(pd.Period(f"{y}Q{k}",freq="Q").end_time.date()).isoformat()
                exp=expected_at(d,anchor,events);actual=extract(gzip.decompress(f.read_bytes()))
                rows={g:{"count":len(actual[g]),"missing":sorted(exp[g]-set(actual[g])),"extra":sorted(set(actual[g])-exp[g]),"extra_details":{c:actual[g][c] for c in set(actual[g])-exp[g]}} for g in GROUPS}
                report["checkpoints"].append({"quarter":label,"groups":rows})
            (OUT/"provisional_events.json").write_text(json.dumps(sorted(events,key=lambda e:e["effective"]),ensure_ascii=False,indent=2))
    # Retrieve two newly uncovered former members. Existing five are retained in original artifact.
    for c in ("2448","2809"):
        a=A[c];months=list(pd.period_range(retired.START,pd.Timestamp(a["last"]),freq="M"));data=[];errors=[]
        for i,m in enumerate([months[-1]]+months[:-1]):
            label=str(m)
            try:
                x,sha=retired.fetch(retired.DAYURL,{"stockNo":c,"date":label.replace("-","")+"01"},raw/f"{c}_{label}.json")
                data+=retired.parse_day(x,c,label)
            except Exception as exc:
                errors.append({"month":label,"error":str(exc)})
                if i==0:break
            time.sleep(1.4)
        data.sort(key=lambda r:r["date"]);traded=[r for r in data if r["status"]=="traded"]
        if data:pd.DataFrame(data).to_csv(OUT/f"{c}_raw.csv",index=False)
        report["extra_raw"][c]={"expected_months":len(months),"errors":errors,"rows":len(traded),"last":traded[-1]["date"] if traded else None,"complete":not errors and len({r["date"][:7] for r in data})==len(months) and bool(traded) and traded[-1]["date"]==a["last"]}
        print("EXTRA_RAW",c,json.dumps(report["extra_raw"][c],ensure_ascii=False),flush=True)
    # The RWD endpoint takes startDate/endDate, not old strDate. Validate returned range.
    ex=[]
    for y in range(2019,2027):
        begin=f"{y}0101";end=f"{y}1231" if y<2026 else "20261125"
        try:
            x,sha=retired.fetch(retired.EXURL,{"startDate":begin,"endDate":end},raw/f"ex_{y}.json")
            if x.get("stat")!="OK":raise ValueError(str(x.get("stat")))
            title=x.get("title","")
            ds=re.findall(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日",title)
            actual=["%04d%02d%02d"%(int(a)+1911,int(b),int(c)) for a,b,c in ds]
            if actual!=[begin,end]:raise ValueError("returned range mismatch "+title)
            fields=x["fields"];ci=next(i for i,k in enumerate(fields) if k in ("股票代號","證券代號"))
            di=next(i for i,k in enumerate(fields) if k in ("資料日期","除權息日期","除權除息日期","日期"))
            for row in x["data"]:
                if str(row[ci]).strip() not in A:continue
                d=retired.roc(str(row[di]).replace("年","/").replace("月","/").replace("日",""))
                if not begin<=d.replace("-","")<=end:raise ValueError("bad date")
                ex.append({"code":str(row[ci]).strip(),"date":d,"fields":dict(zip(fields,row))})
            report["ex_rights"][str(y)]={"status":"parsed","all_rows":len(x["data"]),"sha256":sha}
        except Exception as exc:report["ex_rights"][str(y)]={"status":"blocked","error":str(exc)}
        time.sleep(1)
    (OUT/"ex_rights_events.json").write_text(json.dumps(ex,ensure_ascii=False,indent=2))
    (OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print("PIT_RECONCILE_SUMMARY",json.dumps({**report,"ex_event_counts":{c:sum(e["code"]==c for e in ex) for c in A}},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
