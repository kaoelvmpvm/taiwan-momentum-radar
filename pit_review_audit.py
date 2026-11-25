"""Stage 1: source-backed regular-review event audit, NOT verified PIT membership."""
import datetime as dt
import hashlib,json,re,time,unicodedata
from pathlib import Path
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"pit-audit-output"
REVIEW_IDS=[149,169,171,178,181,191,198,203,211,221,227,234,247,263,276,294,303,320,337,350,362,382,391,405,424,450]
QUARTERS=[(y,m) for y in range(2020,2027) for m in (3,6,9,12) if (2020,6)<=(y,m)<=(2026,9)]
GROUPS={"0050":"臺灣50指數","0051":"臺灣中型100指數"}
COUNTS={"0050":50,"0051":100}
BASE="https://taiwanindex.com.tw/news/"
def compact(t):return re.sub(r"\s+","",unicodedata.normalize("NFKC",t)).replace("台灣","臺灣")
def codes(t):
    if t.strip("。.;；") in ("無","無變動","無調整"):return []
    c=re.findall(r"\(([0-9]{4,6})\)",t)
    if not c or len(c)!=len(set(c)):raise ValueError("unrecognized or duplicate codes: "+t[:160])
    return c
def parse(raw,url,quarter):
    soup=BeautifulSoup(raw,"html.parser")
    text=compact(soup.get_text(" ",strip=True))
    if "成分股定期審核結果" not in text:raise ValueError("not regular review notice")
    # Publication date in article heading, not data observation or effective date.
    dates=re.findall(r"(20\d{2})/(\d{1,2})/(\d{1,2})\d{1,2}:\d{2}",text)
    pub=[dt.date(*map(int,x)) for x in dates if tuple(map(int,x[:2]))==quarter]
    effective=re.search(r"亦即自(20\d{2})年(\d{1,2})月(\d{1,2})日",text)
    if not effective:
        effective=re.search(r"交易結束後生效.{0,35}?自(20\d{2})年(\d{1,2})月(\d{1,2})日",text)
    if not effective:raise ValueError("no explicit effective date")
    day=dt.date(*map(int,effective.groups()))
    if (day.year,day.month)!=quarter or not pub or not min(pub)<day:raise ValueError("date validation failed")
    groups={}
    for k,title in GROUPS.items():
        pattern=title+r"(?:[：:]|\([一二]\))*成分股納入[:：](.*?)(?:\([一二]\))?成分股刪除[:：](.*?)(?=(?:\([三]\))?候補名單|(?:[二三四]、)?臺灣(?:中型100|資訊科技|發達)指數)"
        m=re.search(pattern,text)
        if not m:raise ValueError("missing section "+title)
        a,b=map(codes,m.groups())
        if set(a)&set(b):raise ValueError("overlap "+title)
        groups[k]={"add":a,"remove":b}
    return {"url":url,"published":str(min(pub)),"effective":str(day),"quarter":f"{quarter[0]}Q{quarter[1]//3}","groups":groups,"sha256":hashlib.sha256(raw).hexdigest()}
def reverse(anchor,events):
    s={k:set(v) for k,v in anchor.items()}
    if any(len(s[k])!=COUNTS[k] for k in GROUPS) or s["0050"]&s["0051"]:raise ValueError("invalid anchor")
    records=[];blocked=False
    for event in reversed(events):
        issues=[]
        for k in GROUPS:
            a=set(event["groups"][k]["add"]);b=set(event["groups"][k]["remove"])
            absent=sorted(a-s[k]);present=sorted(b&s[k])
            if absent or present:issues.append({"group":k,"missing_additions":absent,"unexpected_removals":present})
        if issues:
            records.append({"effective":event["effective"],"status":"blocked","issues":issues})
            blocked=True;break
        before={k:(s[k]-set(event["groups"][k]["add"]))|set(event["groups"][k]["remove"]) for k in GROUPS}
        if any(len(before[k])!=COUNTS[k] for k in GROUPS) or before["0050"]&before["0051"]:
            records.append({"effective":event["effective"],"status":"blocked","issues":[{"counts":{k:len(v) for k,v in before.items()},"overlap":sorted(before["0050"]&before["0051"])}]})
            blocked=True;break
        records.append({"effective":event["effective"],"status":"provisional_regular_only","before":{k:sorted(v) for k,v in before.items()}})
        s=before
    return records,blocked
def main():
    OUT.mkdir(exist_ok=True)
    source=OUT/"raw";source.mkdir(exist_ok=True)
    events=[];errors=[]
    assert len(REVIEW_IDS)==len(QUARTERS)==26
    for q,i in zip(QUARTERS,REVIEW_IDS):
        url=BASE+str(i)
        try:
            r=requests.get(url,headers={"User-Agent":"Mozilla/5.0"},timeout=35)
            r.raise_for_status()
            (source/f"{i}.html").write_bytes(r.content)
            e=parse(r.content,url,q);events.append(e)
            print("REVIEW",json.dumps(e,ensure_ascii=False),flush=True)
        except Exception as exc:
            err={"quarter":f"{q[0]}Q{q[1]//3}","url":url,"error":str(exc)}
            errors.append(err);print("REVIEW_BLOCKED",json.dumps(err,ensure_ascii=False),flush=True)
        time.sleep(.7)
    snapshot=json.loads((ROOT/"exploratory_pool_20261125.json").read_text())
    anchor={k:[x["code"] for x in snapshot["ranking"] if x["group"]==k] for k in GROUPS}
    records,blocked=reverse(anchor,events) if not errors else ([],True)
    current=set().union(*map(set,anchor.values()))
    mentioned={c for e in events for v in e["groups"].values() for side in ("add","remove") for c in v[side]}
    report={"status":"BLOCKED_FOR_FORMAL_BACKTEST","quarterly_expected":len(QUARTERS),"quarterly_parsed":len(events),"errors":errors,"anchor":{"asof":snapshot["asof"],"kind":"ETF holdings proxy, NOT independent official index anchor","counts":{k:len(v) for k,v in anchor.items()}},"reverse_steps":records,"reverse_conflict":blocked,"historical_event_codes_outside_current_pool":sorted(mentioned-current),"historical_event_codes_outside_current_pool_count":len(mentioned-current),"missing_requirements":["Independent dated full official 50/100 membership anchor","Complete nonregular changes and effective dates, including replacements, mergers and deletions","Independent historical checkpoints","Validated price, corporate actions and delisting proceeds for former members"],"events":events}
    (OUT/"audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    brief={k:v for k,v in report.items() if k not in ("events","reverse_steps")}
    brief["reverse_passed"]=sum(x["status"]=="provisional_regular_only" for x in records)
    brief["first_conflict"]=next((x for x in records if x["status"]=="blocked"),None)
    print("PIT_AUDIT_SUMMARY",json.dumps(brief,ensure_ascii=False),flush=True)
    # Audit can succeed as an audit even if formal data is blocked; never emits formal membership file.
if __name__=="__main__":main()
