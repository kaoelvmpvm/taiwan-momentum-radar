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


def read_export_tables(raw):
    """Identify by actual bytes, not file extension."""
    import csv
    import io
    if raw.startswith(b"PK\x03\x04") or raw.startswith(b"\xd0\xcf\x11\xe0"):
        engine = "openpyxl" if raw.startswith(b"PK") else "xlrd"
        with pd.ExcelFile(io.BytesIO(raw), engine=engine) as workbook:
            for sheet in workbook.sheet_names:
                yield pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=str).fillna("").values.tolist()
        return
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "cp950", "big5")
    decoded = None
    for encoding in encodings:
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    if decoded is None:
        raise ValueError("Unknown export encoding")
    for delim in (",", "\t", ";"):
        yield list(csv.reader(io.StringIO(decoded), delimiter=delim))


def extract_export_members(tables, expected, preview):
    """Require an identified stock table and agreement with visible preview."""
    result = {}
    for table in tables:
        for i, line in enumerate(table):
            labels = [re.sub(r"\s+", "", str(x)).lower() for x in line]
            codes = [j for j,x in enumerate(labels) if x in ("商品代碼","股票代碼","證券代號","證券代碼","股票代號","代碼","stockcode","code")]
            names = [j for j,x in enumerate(labels) if x in ("商品名稱","股票名稱","證券名稱","名稱","stockname","name")]
            if not codes or not names:
                continue
            block = {}
            for row in table[i+1:]:
                if len(row) <= max(codes[0], names[0]):
                    if block:
                        break
                    continue
                code = str(row[codes[0]]).strip()
                # Excel-style CSV may encode identifiers as ="2330".
                code = re.sub(r'^="([0-9]+)"$', r'\1', code)
                name = str(row[names[0]]).strip()
                if CODE.fullmatch(code) and name:
                    if code in block:
                        raise ValueError("Duplicate stock code in exported table")
                    block[code] = name
                elif block:
                    break
            if len(block) == expected and preview and set(preview).issubset(block):
                if result and result != block:
                    raise ValueError("Export contains conflicting holding tables")
                result = block
    if len(result) != expected:
        raise ValueError(f"Export did not contain a matching {expected}-stock table")
    return result


def export_fund_rows(page, fund, expected, preview):
    button = page.get_by_text(re.compile(r"^(?:Export\s*excel|匯出\s*Excel|下載\s*Excel)$", re.I))
    with page.expect_download(timeout=25000) as pending:
        button.first.click(timeout=10000)
    download = pending.value
    path = OUT / ("issuer-" + fund + Path(download.suggested_filename).suffix.lower())
    download.save_as(str(path))
    raw = path.read_bytes()
    print(f"{fund} export: {download.suggested_filename}, {len(raw)} bytes, signature={raw[:12].hex()}")
    result = extract_export_members(read_export_tables(raw), expected, preview)
    path.unlink(missing_ok=True)
    return result


def fund_snapshot():
    from playwright.sync_api import sync_playwright
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for fund, expected in EXPECTED.items():
                page = browser.new_page(viewport={"width": 390, "height": 844}, locale="zh-TW", accept_downloads=True)
                try:
                    page.goto(FUND_URL.format(fund), wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_function("document.body.innerText.includes('基金權重-股票') || document.body.innerText.includes('基金權重－股票')", timeout=45000)
                    page.wait_for_timeout(2000)
                    def read_stock():
                        body = page.locator("body").inner_text()
                        segment = re.split(r"基金權重[-－]股票", body, maxsplit=1)[-1]
                        segment = re.split(r"基金權重[-－](?:期貨|債券|ETF)", segment, maxsplit=1)[0]
                        rows = fund_rows(segment)
                        # Desktop stock table: scope to a table with the stock header.
                        for table in page.locator("table:visible").all():
                            txt = table.inner_text()
                            if "商品代碼" in txt and "商品名稱" in txt and "商品年月" not in txt:
                                cells = table.locator("tr").evaluate_all("(rs) => rs.map(r => Array.from(r.querySelectorAll('td')).map(c => c.innerText))")
                                rows.update(fund_rows("", cells))
                        return segment, rows
                    segment, rows = read_stock()
                    dates = re.findall(r"(?:Trade Date|資料日期|交易日期|持股日期|日期)\s*[:：]?\s*(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", segment, re.I)
                    if not dates:
                        raise ValueError(f"{fund}：持股區塊找不到資料日期")
                    day = str(pd.Timestamp(dates[0]).date())
                    preview = dict(rows)
                    method = "page"
                    if len(rows) != expected:
                        try:
                            rows = export_fund_rows(page, fund, expected, preview)
                            method = "issuer Excel"
                        except Exception as exc:
                            print(f"{fund} Excel export unavailable: {exc}")
                            more = page.get_by_text(re.compile(r"^(More|更多|顯示更多|展開更多|查看更多|展開|看更多)$", re.I))
                            for attempt in range(30):
                                candidates = [x for x in more.all() if x.is_visible()]
                                if not candidates:
                                    break
                                try:
                                    candidates[0].scroll_into_view_if_needed(timeout=4000)
                                    candidates[0].click(timeout=6000)
                                except Exception as exc:
                                    print(f"{fund} More click failed: {exc}")
                                    break
                                page.wait_for_timeout(1200)
                                _, expanded = read_stock()
                                rows.update(expanded)
                                # Some layouts show all holdings in a separate modal.
                                for dialog in page.locator('[role="dialog"]:visible, .modal:visible').all():
                                    rows.update(fund_rows(dialog.inner_text()))
                                if len(rows) >= expected:
                                    break
                            method = "expanded page"
                    print(f"{fund}: {len(rows)}/{expected}, date={day}, method={method}")
                    if len(rows) != expected or not set(preview).issubset(rows):
                        raise ValueError(f"{fund}：預期 {expected} 檔，取得 {len(rows)} 檔")
                    result[fund] = {"date": day, "members": rows, "source": FUND_URL.format(fund), "method": method}
                except Exception:
                    try:
                        page.screenshot(path=str(OUT / f"debug-{fund}.png"), full_page=True)
                        (OUT / f"debug-{fund}.txt").write_text(page.locator("body").inner_text(), encoding="utf-8")
                    except Exception:
                        pass
                    raise
                finally:
                    page.close()
        finally:
            browser.close()
    a, b = result["0050"], result["0051"]
    if a["date"] != b["date"] or set(a["members"]) & set(b["members"]):
        raise ValueError("兩基金持股日期不一致或有重複股票")
    return result


def parse_dated_market(data, expected_date):
    """Verify exact response date and select the official stock quote table by fields."""
    if not isinstance(data, dict) or data.get("stat") != "OK":
        raise ValueError("證交所指定日行情尚未發布或查詢失敗")
    expected = pd.Timestamp(expected_date).strftime("%Y%m%d")
    if str(data.get("date", "")).strip() != expected:
        raise ValueError(f"指定日期行情回傳日期不符：{data.get('date')} / {expected}")
    candidates = [t for t in data.get("tables", []) if
                  {"證券代號", "收盤價", "成交股數"}.issubset(t.get("fields", []))]
    if len(candidates) != 1:
        raise ValueError("找不到唯一的證券收盤行情表")
    table = candidates[0]
    fields = table["fields"]
    i, j = fields.index("證券代號"), fields.index("收盤價")
    result = {}
    seen = set()
    for row in table.get("data", []):
        if len(row) <= max(i, j):
            continue
        stock = str(row[i]).strip()
        if stock in seen:
            raise ValueError(f"指定日行情含重複代碼：{stock}")
        seen.add(stock)
        try:
            price = float(str(row[j]).strip().replace(",", ""))
        except (ValueError, TypeError):
            continue
        if math.isfinite(price) and price > 0:
            result[stock] = (str(pd.Timestamp(expected_date).date()), price)
    if len(result) < 500 or "0050" not in result:
        raise ValueError("指定日行情筆數不足或缺少0050")
    return result


def dated_official_market(expected_date):
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    response = requests.get(url, params={"date":pd.Timestamp(expected_date).strftime("%Y%m%d"),
                                           "type":"ALLBUT0999","response":"json"},
                            timeout=45, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
    response.raise_for_status()
    return parse_dated_market(response.json(), expected_date)


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


# Verified corporate-action registry. Add events only with documented effective date,
# last old-share trading day, and official exchange ratio, never from price fitting.
FACE_VALUE_EVENTS = {
    "6949": {"last_old":"2026-08-26", "first_new":"2026-09-07", "ratio":20.0,
             "source":"https://www.twse.com.tw/exchangeReport/TWTB7U?date=undefined&response=html&selectType=undefined"}
}


def reconcile_face_value(frame, event, old_close, new_close):
    """Repair only a documented missing adjustment; never adjust twice."""
    old, new = pd.Timestamp(event["last_old"]), pd.Timestamp(event["first_new"])
    ratio = float(event["ratio"])
    if old not in frame.index or new not in frame.index:
        raise ValueError("面額變更前後交易日缺漏")
    if len(frame.loc[old:new]) != 2:
        raise ValueError("面額變更停牌區間出現非預期價格")
    before, after = float(frame.at[old,"Adj Close"]), float(frame.at[new,"Adj Close"])
    if not all(math.isfinite(x) and x > 0 for x in (before, after, old_close, new_close, ratio)):
        raise ValueError("面額變更價格或換股率無效")
    observed = after / before
    raw = new_close / old_close
    expected = raw * ratio
    matches = lambda a,b: abs(a/b-1) <= .005
    if matches(observed, expected):
        corrected = frame
        status = "already_adjusted"
    elif matches(observed, raw) and not matches(observed, expected):
        corrected = frame.copy()
        corrected.loc[corrected.index < new, "Adj Close"] /= ratio
        status = "missing_adjustment_repaired"
    else:
        raise ValueError(f"面額變更還原比率無法核對：observed={observed:.6f}, raw={raw:.6f}, expected={expected:.6f}")
    return corrected, {"status":status,"observed_ratio":observed,"expected_ratio":expected,
                       "official_old_close":old_close,"official_new_close":new_close, **event}


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
        expected_date = funds["0050"]["date"]
        payload["asof"] = expected_date
        try:
            market = official_market()
            print(f"TWSE OpenAPI date: {market['0050'][0]}, expected: {expected_date}")
            if market["0050"][0] != expected_date:
                raise ValueError("OpenAPI日期未同步")
            payload["market_source"] = MARKET
        except Exception as exc:
            print(f"OpenAPI unavailable for expected date: {exc}; trying official dated MI_INDEX")
            market = dated_official_market(expected_date)
            payload["market_source"] = "TWSE MI_INDEX"
            print(f"TWSE MI_INDEX date: {market['0050'][0]}, {len(market)} valid quotes")
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
                frame = frames[c]
                event = FACE_VALUE_EVENTS.get(c)
                if event and base <= pd.Timestamp(event["last_old"]) and pd.Timestamp(asof) >= pd.Timestamp(event["first_new"]):
                    old_quote = dated_official_market(event["last_old"])[c][1]
                    new_quote = dated_official_market(event["first_new"])[c][1]
                    frame, audit = reconcile_face_value(frame, event, old_quote, new_quote)
                    payload.setdefault("corporate_action_audit", {})[c] = audit
                    print(f"{c} corporate action audit: {json.dumps(audit, ensure_ascii=False)}")
                score = momentum(frame, asof, base, market[c][1])
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
