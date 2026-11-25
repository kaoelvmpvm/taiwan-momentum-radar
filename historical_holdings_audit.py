"""Check whether published historical ETF quarterly holdings can supply a point-in-time proxy.
This is a data feasibility audit, not a performance backtest or current-constituent backfill.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from calendar import monthrange
from pathlib import Path
import json,re,time
import requests
from bs4 import BeautifulSoup

URL="https://mopsov.twse.com.tw/mops/web/ajax_t78sb04"
NAMES={"0050":"元大台灣卓越50","0051":"元大台灣中型100"}
EXPECTED={"0050":50,"0051":100}
def parse(raw):
    soup=BeautifulSoup(raw,"html.parser")
    result={}
    active=None
    for table in soup.find_all("table"):
        # Only leaf tables, avoiding outer layout tables that mix funds.
        if table.find("table"):
            continue
        txt=re.sub(r"\s+","",table.get_text(" ",strip=True))
        if "公司代號" in txt and "證券投資信託基金" in txt:
            found=[k for k,v in NAMES.items() if v in txt]
            active=found[0] if len(found)==1 else None
        if active is None or "股票代號" not in txt or "股票名稱" not in txt:
            continue
        records={}
        for tr in table.find_all("tr"):
            cells=[td.get_text(" ",strip=True) for td in tr.find_all(["td","th"])]
            if len(cells)<2:continue
            code=cells[0].strip()
            if re.fullmatch(r"[1-9][0-9]{3}",code):
                records[code]=cells[1]
        if records:
            result[active]=records
            active=None
    return result

def fetch(q):
    year,quarter=q
    payload={"encodeURIComponent":"1","step":"1","firstin":"1","off":"1","keyword4":"","code1":"","TYPEK2":"","checkbtn":"","queryName":"co_id","inpuType":"co_id","TYPEK":"all","isnew":"false","co_id":"","year":str(year-1911),"season":f"{quarter:02d}","type":"05"}
    label=f"{year}Q{quarter}"
    try:
        response=requests.post(URL,data=payload,headers={"User-Agent":"Mozilla/5.0","Referer":"https://mopsov.twse.com.tw/mops/web/t78sb35_new"},timeout=35)
        response.raise_for_status()
        data=parse(response.content)
        counts={k:len(data.get(k,{})) for k in NAMES}
        valid=all(counts[k]==EXPECTED[k] for k in NAMES) and not (set(data.get("0050",{}))&set(data.get("0051",{})))
        month=quarter*3
        quarter_end=date(year,month,monthrange(year,month)[1])
        available=quarter_end+timedelta(days=45)
        return {"quarter":label,"quarter_end":str(quarter_end),"assumed_available_after":str(available),"counts":counts,"valid":valid,"members":data if valid else {},"http_status":response.status_code,"bytes":len(response.content)}
    except Exception as e:
        return {"quarter":label,"valid":False,"error":str(e)}
def main():
    # Test widely separated quarters first: avoid hammering the source if format unavailable.
    probes=[(2020,1),(2023,4),(2026,3)]
    results=[]
    for q in probes:
        r=fetch(q);results.append(r)
        print(json.dumps({k:v for k,v in r.items() if k!="members"},ensure_ascii=False),flush=True)
        time.sleep(2)
    if all(x["valid"] for x in results):
        remaining=[(y,q) for y in range(2020,2027) for q in range(1,5) if (y,q)<=(2026,3) and (y,q) not in probes]
        for q in remaining:
            r=fetch(q);results.append(r)
            print(json.dumps({k:v for k,v in r.items() if k!="members"},ensure_ascii=False),flush=True)
            time.sleep(2)
    out=Path("historical-audit");out.mkdir(exist_ok=True)
    (out/"quarterly_holdings.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
    valid=sum(x["valid"] for x in results)
    print(f"HISTORICAL_PROBE: {valid}/{len(results)} complete quarters; required 27. Quarterly proxy only, not official index membership.",flush=True)
    return 0 if valid==27 else 2
if __name__=="__main__":
    raise SystemExit(main())
