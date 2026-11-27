"""Exact-date issuer holdings cache. Never mix dates or backfill a formal signal."""
import json
import re
import time
from datetime import date
from pathlib import Path

EXPECTED = {"0050": 50, "0051": 100}
FUND_URL = "https://www.yuantaetfs.com/product/detail/{}/ratio"


def validate(fund, report, day=None):
    observed = report["date"]
    if date.fromisoformat(observed).isoformat() != observed or (day and observed != day):
        raise ValueError(f"{fund} 報表日期不符")
    members = report["members"]
    if (not isinstance(members, dict) or len(members) != EXPECTED[fund]
            or any(not isinstance(c, str) or not re.fullmatch(r"[1-9][0-9]{3}", c)
                   or not isinstance(n, str) or not n.strip() for c, n in members.items())):
        raise ValueError(f"{fund} 成分股筆數或欄位不符")
    if report.get("source") != FUND_URL.format(fund) or report.get("method") not in (
            "page", "issuer Excel", "expanded page"):
        raise ValueError(f"{fund} 來源未通過稽核")
    return report


def cache_path(cache, fund, day):
    return Path(cache) / f"{day}_fund_{fund}.json"


def load(cache, fund, day):
    try:
        return validate(fund, json.loads(cache_path(cache, fund, day).read_text(encoding="utf-8")), day)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(cache, fund, report):
    validate(fund, report)
    p = cache_path(cache, fund, report["date"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def latest_common(cache, expected):
    """Find a previously verified common date, for diagnostics only."""
    days = sorted({p.name[:10] for p in Path(cache).glob("*_fund_0050.json")
                   if p.name[:10] <= expected}, reverse=True)
    for day in days:
        a, b = (load(cache, fund, day) for fund in EXPECTED)
        if a and b and not set(a["members"]) & set(b["members"]):
            return day
    return None


def collect(expected, cache, fetch, *, observed_today, attempts=3, sleep=time.sleep):
    """Fetch only missing issuer reports; archive valid reports independently.
    Only a complete, disjoint pair for the requested session may be returned.
    """
    if attempts < 1:
        raise ValueError("至少需要一輪查核")
    if date.fromisoformat(expected) > date.fromisoformat(observed_today):
        raise ValueError("不可查核未來行情日")
    observed = {}
    errors = {}
    force_refresh = False
    for i in range(attempts):
        exact = {}
        for fund in EXPECTED:
            saved = None if force_refresh else load(cache, fund, expected)
            if saved:
                exact[fund] = saved
                observed[fund] = f"{expected}（已驗證快取）"
                errors.pop(fund, None)
                continue
            try:
                item = validate(fund, fetch(fund))
                day = item["date"]
                observed[fund] = day
                if day > observed_today:
                    raise ValueError("報表日期晚於查核日期")
                save(cache, fund, item)
                if day == expected:
                    exact[fund] = item
                    errors.pop(fund, None)
                else:
                    errors[fund] = f"預期 {expected}，取得 {day}"
            except Exception as exc:
                errors[fund] = f"{type(exc).__name__}: {str(exc)[:180]}"
        overlap = len(exact) == len(EXPECTED) and bool(set(exact["0050"]["members"]) & set(exact["0051"]["members"]))
        if len(exact) == len(EXPECTED) and not overlap:
            return exact
        force_refresh = overlap
        detail = "；".join(f"{f}：{observed.get(f, '未取得')} {errors.get(f, '')}".strip() for f in EXPECTED)
        if overlap:
            detail += "；同日候選池代碼重複"
        print(f"ETF 持股稽核 {i+1}/{attempts}：{detail}", flush=True)
        if i + 1 < attempts:
            sleep(15 * (i + 1))
    common = latest_common(cache, expected)
    raise ValueError(f"ETF持股未同步（預期 {expected}）：{detail}；最近已驗證共同日：{common or '無'}（舊資料不產生新訊號）")
