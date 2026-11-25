"""Quarterly MOPS ETF disclosure checkpoint audit, not an official index certification."""
import gzip,hashlib,json,re,time,unicodedata
from datetime import date
from calendar import monthrange
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from historical_holdings_audit import URL,NAMES,parse as legacy_parse
from pit_review_audit import reverse
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-checkpoint-output"
PROBES=[(2020,2),(2021,4),(2025,3),(2026,3)]
GROUPS=("0050","0051")
def norm(s):return re.sub(r"\s+","",unicodedata.normalize("NFKC",str(s)))
def expected_at(day,anchor,events):
    s={g:set(anchor[g]) for g in GROUPS}
    for e in sorted(events,key=lambda x:x["effective"],reverse=True):
        if e["effective"]<=day:continue
        for g in GROUPS:
            a=set(e["groups"][g]["add"]);b=set(e["groups"][g]["remove"])
            if not a<=s[g] or b&s[g]:raise ValueError("inconsistent reverse: "+e["effective"])
            s[g]=(s[g]-a)|b
        if len(s["0050"])!=50 or len(s["0051"])!=100 or s["0050"]&s["0051"]:raise ValueError("bad membership")
    return s
def parse_details(raw):
    soup=BeautifulSoup(raw,"html.parser")
    result={g:{"records":{},"headers":[],"tables":[]} for g in GROUPS}
    active=None
    for table in soup.find_all("table"):
        if table.find("table"):continue
        txt=norm(table.get_text(" ",strip=True))
        if "公司代號" in txt and "證券投資信託基金" in txt:
            found=[k for k,v in NAMES.items() if v in txt]
            active=found[0] if len(found)==1 else None
            if active:
                result[active]["headers"].append(table.get_text(" ",strip=True)[:1500])
        if active is None or "股票代號" not in txt or "股票名稱" not in txt:continue
        d=result[active];records={};other=[];all_rows=[];code_idx=None;name_idx=None
        for tr in table.find_all("tr"):
            cells=[td.get_text(" ",strip=True) for td in tr.find_all(["td","th"],recursive=False)]
            if not cells:continue
            all_rows.append(cells)
            nc=[norm(x) for x in cells]
            if "股票代號" in nc and "股票名稱" in nc:
                code_idx=nc.index("股票代號");name_idx=nc.index("股票名稱")
                continue
            if code_idx is None or max(code_idx,name_idx)>=len(cells):
                other.append(cells);continue
            code=nc[code_idx]
            if re.fullmatch(r"[1-9]\d{3}",code):
                if code in records:raise ValueError("duplicate code "+code)
                records[code]=cells[name_idx]
            else:other.append(cells)
        d["tables"].append({"rows":all_rows,"nonstandard_rows":other,"header_code_idx":code_idx,"header_name_idx":name_idx})
        for c,n in records.items():
            if c in d["records"]:raise ValueError("duplicate fund code "+c)
            d["records"][c]=n
        # Keep active for further leaf tables until another fund heading. No implicit fill.
    return result
def fetch(q,anchor,events):
    y,k=q;month=k*3
    label=f"{y}Q{k}"
    end=date(y,month,monthrange(y,month)[1]).isoformat()
    expect=expected_at(end,anchor,events)
    payload={"encodeURIComponent":"1","step":"1","firstin":"1","off":"1","keyword4":"","code1":"","TYPEK2":"","checkbtn":"","queryName":"co_id","inpuType":"co_id","TYPEK":"all","isnew":"false","co_id":"","year":str(y-1911),"season":f"{k:02d}","type":"05"}
    r=requests.post(URL,data=payload,headers={"User-Agent":"Mozilla/5.0","Referer":"https://mopsov.twse.com.tw/mops/web/t78sb35_new"},timeout=45)
    r.raise_for_status()
    (OUT/f"{label}.html.gz").write_bytes(gzip.compress(r.content))
    legacy=legacy_parse(r.content);new=parse_details(r.content)
    report={"quarter":label,"comparison_asof":end,"source":URL,"request":payload,"sha256":hashlib.sha256(r.content).hexdigest(),"groups":{},"interpretation":"Disclosure is ETF holdings, not official index membership. Quarter-end comparison date is assumed until the disclosure's actual holding date is verified."}
    for g in GROUPS:
        actual=set(new[g]["records"])
        report["groups"][g]={"expected_count":len(expect[g]),"legacy_count":len(legacy.get(g,{})),"parsed_count":len(actual),"missing_from_disclosure":sorted(expect[g]-actual),"extra_in_disclosure":sorted(actual-expect[g]),"headers":new[g]["headers"],"nonstandard_rows":[row for t in new[g]["tables"] for row in t["nonstandard_rows"]],"table_count":len(new[g]["tables"])}
    (OUT/f"{label}_details.json").write_text(json.dumps({"report":report,"parsed":new,"expected":{g:sorted(expect[g]) for g in GROUPS}},ensure_ascii=False,indent=2))
    print("PIT_CHECKPOINT",json.dumps(report,ensure_ascii=False),flush=True)
    return report
def main():
    OUT.mkdir(exist_ok=True)
    reg=json.loads((ROOT/"backtest/pit_regular_events.json").read_text())
    other=json.loads((ROOT/"backtest/pit_nonregular_evidence.json").read_text())
    proof=json.loads((ROOT/"backtest/pit_reserve_marketcap_evidence.json").read_text())
    if proof["reserve"]["winner"]!="5434" or proof["reserve"]["reverse_passed"]!=30:raise ValueError("unverified reserve")
    events=reg+other["verified_nonregular"]+[{"effective":"2025-07-14","groups":{"0050":{"add":[],"remove":[]},"0051":{"add":["5434"],"remove":["2888"]}}}]
    pool=json.loads((ROOT/"exploratory_pool_20261125.json").read_text())
    anchor={g:[r["code"] for r in pool["ranking"] if r["group"]==g] for g in GROUPS}
    r,b=reverse(anchor,sorted(events,key=lambda x:x["effective"]))
    if b or len(r)!=30:raise ValueError("30 event reverse failed")
    reports=[]
    for q in PROBES:
        try:reports.append(fetch(q,anchor,events))
        except Exception as exc:
            report={"quarter":f"{q[0]}Q{q[1]}","error":str(exc)}
            reports.append(report);print("PIT_CHECKPOINT_BLOCKED",json.dumps(report,ensure_ascii=False),flush=True)
        time.sleep(2)
    summary={"status":"BLOCKED_FOR_FORMAL_BACKTEST","probes":len(PROBES),"downloaded":sum("groups" in x for x in reports),"exact_group_matches":sum(not g["missing_from_disclosure"] and not g["extra_in_disclosure"] for x in reports for g in x.get("groups",{}).values()),"total_group_comparisons":sum(len(x.get("groups",{})) for x in reports),"results":reports}
    (OUT/"audit.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print("PIT_CHECKPOINT_SUMMARY",json.dumps(summary,ensure_ascii=False),flush=True)
if __name__=="__main__":main()
