"""Research-only daily momentum monitor. Fails closed on incomplete coverage."""
import html
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "report"
TZ = ZoneInfo("Asia/Taipei")
MARKET = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
FUND_URL = "https://www.yuantaetfs.com/product/detail/{}/ratio"
CODE = re.compile(r"^[1-9][0-9]{3}$")
EXPECTED = {"0050": 50, "0051": 100}


def fund_rows(text, tables=()):
    """Parse only stock rows. Do not guess missing members."""
    rows = {}
    for code, name in re.findall(r"商品代碼\s*([1-9][0-9]{3})\s*商品名稱\s*([^\n]+)", text):
        rows[code] = name.strip()
    for cells in tables:
        cells = [str(x).strip() for x in cells]
        if len(cells) >= 4 and CODE.fullmatch(cells[0]) and cells[1]:
            rows[cells[0]] = cells[1]
    return rows


def fund_snapshot():
    from playwright.sync_api import sync_playwright
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for fund, expected in EXPECTED.items():
                page = browser.new_page(viewport={"width": 390, "height": 844}, locale="zh-TW")
                try:
                    page.goto(FUND_URL.format(fund), wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_function("document.body.innerText.includes('基金權重-股票') || document.body.innerText.includes('基金權重－股票')", timeout=45000)
                    page.wait_for_timeout(2000)
                    rows = {}
                    for _ in range(30):
                        body = page.locator("body").inner_text()
                        # Scope to stock section, excluding derivatives.
                        segment = re.split(r"基金權重[-－]股票", body, maxsplit=1)[-1]
                        segment = re.split(r"基金權重[-－](?:期貨|債券|ETF)", segment, maxsplit=1)[0]
                        # Find visible table cells for desktop layout, and labelled rows for mobile.
                        cells = page.locator("tr:visible").evaluate_all("(rs) => rs.map(r => Array.from(r.querySelectorAll('td')).map(c => c.innerText))")
                        rows = fund_rows(segment, cells)
                        if len(rows) >= expected:
                            break
                        more = page.get_by_text(re.compile(r"^(More|更多|顯示更多|展開更多|查看更多|展開|看更多)$", re.I)).filter(visible=True)
                        if not more.count():
                            break
                        clicked = False
                        for idx in range(more.count()):
                            try:
                                more.nth(idx).click(timeout=3000)
                                clicked = True
                                break
                            except Exception:
                                pass
                        if not clicked:
                            break
                        page.wait_for_timeout(700)
                    dates = re.findall(r"(?:Trade Date|資料日期|交易日期|持股日期|日期)\s*[:：]?\s*(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", segment, re.I)
                    if not dates:
                        raise ValueError(f"{fund}：持股區塊找不到資料日期")
                    day = str(pd.Timestamp(dates[0]).date())
                    if len(rows) != expected:
                        raise ValueError(f"{fund}：預期 {expected} 檔，取得 {len(rows)} 檔")
                    result[fund] = {"date": day, "members": rows, "source": FUND_URL.format(fund)}
                finally:
                    page.close()
        finally:
            browser.close()
    a, b = result["0050"], result["0051"]
    if a["date"] != b["date"] or set(a["members"]) & set(b["members"]):
        raise ValueError("兩基金持股日期不一致或有重複股票")
    return result


def official_market():
    response = requests.get(MARKET, timeout=45, headers={"User-Agent": "MomentumRadarResearch/0.1"})
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list) or len(data) < 500:
        raise ValueError("證交所資料格式或筆數不符預期")
    def parse_date(s):
        s = str(s).strip()
        if re.fullmatch(r"\d{7}", s):
            s = str(int(s[:3])+1911) + s[3:]
        return str(pd.to_datetime(s, format="%Y%m%d").date())
    result = {}
    for row in data:
        code = str(row.get("Code", "")).strip()
        try:
            price = float(str(row["ClosingPrice"]).replace(",", ""))
            day = parse_date(row["Date"])
            if math.isfinite(price) and price > 0:
                result[code] = (day, price)
        except (KeyError, ValueError, TypeError):
            continue
    if "0050" not in result:
        raise ValueError("證交所資料缺少0050")
    return result


def clean_history(frame):
    frame = frame.copy()
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is not None:
        idx = idx.tz_convert("Asia/Taipei").tz_localize(None)
    frame.index = idx.normalize()
    if frame.index.has_duplicates:
        raise ValueError("duplicate history dates")
    for col in ("Close", "Adj Close"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.sort_index()


def get_history(codes, day):
    import yfinance as yf
    start = (pd.Timestamp(day) - pd.DateOffset(months=14)).date().isoformat()
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).date().isoformat()
    frames, errors = {}, {}
    for k in range(0, len(codes), 12):
        batch = codes[k:k+12]
        try:
            raw = yf.download([c+".TW" for c in batch], start=start, end=end,
                              auto_adjust=False, actions=True, repair=False,
                              group_by="ticker", threads=False, progress=False, timeout=20)
            for code in batch:
                try:
                    frame = raw[code+".TW"] if isinstance(raw.columns, pd.MultiIndex) else raw
                    frames[code] = clean_history(frame.dropna(subset=["Close","Adj Close"]))
                except Exception as exc:
                    errors[code] = str(exc)
        except Exception as exc:
            errors.update({code: str(exc) for code in batch})
        time.sleep(2)
    return frames, errors


def baseline_date(benchmark, asof):
    cutoff = pd.Timestamp(asof) - pd.DateOffset(years=1)
    candidates = benchmark.loc[:cutoff]
    if candidates.empty or (cutoff - candidates.index[-1]).days > 10:
        raise ValueError("缺少一年前共同交易日")
    return candidates.index[-1]


def momentum(frame, asof, base, official):
    t = pd.Timestamp(asof)
    if t not in frame.index or base not in frame.index:
        raise ValueError("缺少共同基準日或最新交易日")
    now, old = float(frame.at[t,"Adj Close"]), float(frame.at[base,"Adj Close"])
    last = float(frame.at[t,"Close"])
    if not all(math.isfinite(x) and x > 0 for x in (now, old, last)):
        raise ValueError("價格無效")
    if abs(last-official) > max(0.011, official*0.0005):
        raise ValueError(f"最新收盤不符官方：{last} / {official}")
    series = frame.loc[base:t,"Adj Close"]
    if len(series) < 200 or (series <= 0).any():
        raise ValueError("歷史價格筆數或數值異常")
    if (series.pct_change().abs() > .35).any():
        raise ValueError("還原價單日變動超過35%，需核對公司行動")
    return 100*(now/old-1)


def render(payload):
    e = lambda x: html.escape(str(x))
    rows = "".join(
        f"<tr><td>{i}</td><td>{e(x['code'])}</td><td>{e(x['name'])}</td><td>{e(x['group'])}</td><td class={'up' if x['momentum']>=0 else 'down'}>{x['momentum']:+.2f}%</td></tr>"
        for i, x in enumerate(payload.get("ranking", []), 1))
    issues = "".join(f"<li>{e(x)}</li>" for x in payload.get("issues", []))
    state = "資料檢查通過（仍為研究排名）" if payload["complete"] else "資料不完整／未核對，不可判定全池第一名"
    return f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>台股動能雷達 V0.1</title>
<style>body{{font:16px system-ui,sans-serif;background:#0d1421;color:#edf2fa;margin:0}}main{{max-width:920px;margin:auto;padding:28px 16px}}h1{{font-size:32px;margin:8px 0}}p,small{{color:#aebed1;line-height:1.6}}section{{border:1px solid #324258;background:#152235;padding:20px;border-radius:12px;margin:16px 0}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px 8px;text-align:left;border-bottom:1px solid #324258}}td:last-child,th:last-child{{text-align:right}}.up{{color:#ff9b96}}.down{{color:#7de0bc}}.warn{{color:#f2c879}}input{{width:95%;padding:10px;background:#0d1421;color:white;border:1px solid #52647a;border-radius:6px}}a{{color:#9cceff}}</style>
<main><small>TAIWAN MOMENTUM RADAR / V0.1</small><h1>台股動能雷達</h1><p>完整12個月還原價格報酬｜每日觀察｜月底交易規則尚未啟用</p>
<section><strong class="warn">{e(state)}</strong><p>行情日期：{e(payload.get('asof','—'))}　基準日：{e(payload.get('baseline','—'))}<br>有效筆數：{len(payload.get('ranking',[]))} / 151　產製時間：{e(payload['generated'])}</p><p>0050、0051持股代理池 + 0050 ETF；不代表官方指數名單。即使資料檢查通過，還原算法仍待核對，不構成交易指令。</p></section>
<section><h2>動能排名</h2><input id="q" placeholder="搜尋股票代碼或名稱" aria-label="搜尋股票"><table><thead><tr><th>排名</th><th>代碼</th><th>名稱</th><th>分組</th><th>12月報酬</th></tr></thead><tbody>{rows}</tbody></table></section>
<section><h2>資料異常與限制</h2><ul>{issues or '<li>本次自動檢查未發現異常。</li>'}</ul><p>歷史成分股與公司行動尚未獨立驗證。不得據此直接下單。</p><p><a href="https://www.yuantaetfs.com/product/detail/0050/ratio">元大0050持股</a>｜<a href="https://www.yuantaetfs.com/product/detail/0051/ratio">元大0051持股</a>｜<a href="https://openapi.twse.com.tw/">TWSE</a></p></section>
<script>document.getElementById('q').addEventListener('input',function(){{for(const r of document.querySelectorAll('tbody tr'))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())}})</script></main></html>"""


def main():
    OUT.mkdir(exist_ok=True)
    payload = {"generated":datetime.now(TZ).isoformat(timespec="seconds"),"complete":False,"ranking":[],"issues":[]}
    try:
        funds = fund_snapshot()
        payload["funds"] = funds
        market = official_market()
        asof = market["0050"][0]
        payload["asof"] = asof
        if any(f["date"] != asof for f in funds.values()):
            raise ValueError("投信持股與證交所行情日期不一致（可能盤後資料尚未更新）")
        if (datetime.now(TZ).date() - pd.Timestamp(asof).date()).days > 7:
            raise ValueError("行情過舊")
        pool = {c: (n, f) for f, data in funds.items() for c,n in data["members"].items()}
        pool["0050"] = ("元大台灣50", "基準ETF")
        frames, errors = get_history(sorted(pool), asof)
        base = baseline_date(frames["0050"], asof)
        payload["baseline"] = str(base.date())
        for c, (name, group) in pool.items():
            try:
                if c in errors:
                    raise ValueError(errors[c])
                if c not in market or market[c][0] != asof:
                    raise ValueError("官方當日行情缺漏")
                score = momentum(frames[c], asof, base, market[c][1])
                payload["ranking"].append({"code":c,"name":name,"group":group,"momentum":score})
            except Exception as exc:
                payload["issues"].append(f"{c} {name}: {exc}")
        payload["ranking"].sort(key=lambda x:(-x["momentum"],x["code"]))
        payload["complete"] = len(payload["ranking"]) == 151 and not payload["issues"]
    except Exception as exc:
        payload["issues"].append(str(exc))
    (OUT/"ranking.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    (OUT/"index.html").write_text(render(payload),encoding="utf-8")
    print(f"Coverage: {len(payload['ranking'])}/151; asof: {payload.get('asof')}; complete: {payload['complete']}")
    for issue in payload["issues"]:
        print(issue)
    return 0 if payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
