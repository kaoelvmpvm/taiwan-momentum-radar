"""Private browser dashboard. Failed updates never overwrite a valid snapshot."""
import argparse
import json
import math
from momentum_alert import is_extreme, marker, EXPLANATION
from rank_history import save_snapshot, load_history, render_history, changes
from datetime import date
from pathlib import Path

GROUP_COUNTS = {"0050":50,"0051":100,"基準ETF":1}

def snapshot(payload):
    if not isinstance(payload,dict) or payload.get("complete") is not True or payload.get("issues"):
        raise ValueError("本次資料未完整通過檢查")
    asof=date.fromisoformat(payload["asof"])
    baseline=date.fromisoformat(payload["baseline"])
    if not 355 <= (asof-baseline).days <= 376:
        raise ValueError("基準日期異常")
    rows=payload["ranking"]
    if len(rows)!=151:
        raise ValueError("排名未達151檔")
    codes,counts,clean=set(),{},[]
    for row in rows:
        code,name,group,value=row["code"],row["name"],row["group"],row["momentum"]
        if not isinstance(code,str) or len(code)!=4 or not code.isdigit() or code in codes:
            raise ValueError("代碼缺漏或重複")
        if not isinstance(name,str) or not name.strip() or group not in GROUP_COUNTS:
            raise ValueError("股票名稱或群組異常")
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=-100:
            raise ValueError("動能數值異常")
        if (code=="0050")!=(group=="基準ETF"):
            raise ValueError("基準ETF身分不符")
        codes.add(code)
        counts[group]=counts.get(group,0)+1
        clean.append({"code":code,"name":name,"group":group,"momentum":value,"extreme_momentum":is_extreme(value)})
    if counts!=GROUP_COUNTS:
        raise ValueError("股票池組成不符")
    clean.sort(key=lambda x:(-x["momentum"],x["code"]))
    return {"asof":str(asof),"baseline":str(baseline),"ranking":clean,"generated":str(payload.get("generated","—"))}

def cell(x):
    return str(x).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("|","\\|").replace("\n"," ").replace("\r"," ")

def update(payload,previous,run_result):
    if run_result!="success":
        return previous,"更新未通過","行情擷取或測試未成功"
    try:
        current=snapshot(payload)
        if previous and current["asof"]<previous["asof"]:
            raise ValueError("回傳行情早於最近一次成功日期")
        return current,"資料檢查通過",""
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        return previous,"更新未通過",str(exc)

def markdown(current,status,error,run_url,issues=(),history=()):
    lines=["# 台股動能雷達 · V0.7","","[整合研究首頁](RESEARCH.md) · [歷史監測與個股走勢](HISTORY.md) · [前瞻性模擬帳本](PAPER.md) · [本次執行紀錄]("+run_url+")","","**更新狀態："+cell(status)+"**",""]
    if error:
        lines += ["> 本次更新未通過。下方如有資料，僅為最近一次成功的歷史快照，不代表當前排名。","",cell(error),""]
        lines += ["- "+cell(x) for x in issues[:20]]
        if issues: lines.append("")
    if not current:
        return "\n".join(lines+["尚未有完整通過驗收的排行榜。",""])
    rows=current["ranking"]
    prev=next((s for s in reversed(history) if s["asof"]<current["asof"]),None)
    delta=changes(current,prev)
    lines += [f"已累積 {len(history)} 個成功行情日。"+(f" 名次比較基準：{prev['asof']}（上一個成功快照）。" if prev else " 目前尚無跨日名次變動。"),""]
    if prev:
        a,b=prev["ranking"][0],rows[0]
        lines += ["第一名異動："+(f"{a['code']} {cell(a['name'])} → {b['code']} {cell(b['name'])}" if a["code"]!=b["code"] else "未變動"),""]
    bench=next(x for x in rows if x["code"]=="0050")
    rank=next(i for i,x in enumerate(rows,1) if x["code"]=="0050")
    lines += ["## 最近成功快照","","| 項目 | 數值 |","|---|---|",
              "| 行情日期 | "+cell(current["asof"])+" |",
              "| 動能基準日期 | "+cell(current["baseline"])+" |",
              "| 資料覆蓋率 | 151／151 |",
              "| 0050 全池排名 | "+str(rank)+" |",
              "| 0050 12個月報酬 | "+f"{bench['momentum']:+.2f}% |",
              "| 產製時間 | "+cell(current["generated"])+" |","",
              "### 前五名（快照）","","| 排名 | 代碼 | 名稱 | 12月動能 | 相對0050（百分點） | 名次變動 |","|---:|---|---|---:|---:|---:|"]
    def row_text(i,x,group=False):
        group_text=(" | "+cell(x["group"])) if group else ""
        change=delta[x["code"]]["rank_delta"]
        movement="—" if change is None else f"{change:+d}"
        return f"| {i} | {cell(x['code'])} | [{cell(x['name']+marker(x['momentum']))}](charts/{x['code']}.svg){group_text} | {x['momentum']:+.2f}% | {x['momentum']-bench['momentum']:+.2f} | {movement} |"
    lines += [row_text(i,x) for i,x in enumerate(rows[:5],1)]
    lines += ["","### 全部151檔","","| 排名 | 代碼 | 名稱 | 群組 | 12月動能 | 相對0050（百分點） | 名次變動 |","|---:|---|---|---|---:|---:|---:|"]
    lines += [row_text(i,x,True) for i,x in enumerate(rows,1)]
    lines += ["","## 方法與限制","","完整12個月還原價格報酬；元大0050及0051持股代理池＋0050 ETF。相對0050為報酬差（百分點），不是風險調整後超額報酬。",
              EXPLANATION,
              "每日快照僅供研究；月底規則僅用於前瞻性模擬帳本。歷史公司行動尚未全面獨立查核。不得據此直接下單。",
              "資料若未更新，請以行情日期和本次執行狀態為準。此為私人研究頁面，非公開 GitHub Pages。",""]
    return "\n".join(lines)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--report",default="report/ranking.json")
    p.add_argument("--dashboard",default="dashboard")
    p.add_argument("--run-result",required=True)
    p.add_argument("--run-url",required=True)
    args=p.parse_args()
    dest=Path(args.dashboard)
    dest.mkdir(exist_ok=True,parents=True)
    last=dest/"last_success.json"
    previous=json.loads(last.read_text(encoding="utf-8")) if last.exists() else None
    try:
        payload=json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError,ValueError):
        payload={}
    current,status,error=update(payload,previous,args.run_result)
    if previous and not (dest/"history"/(previous["asof"]+".json")).exists():
        save_snapshot(dest,previous)
    if not error and current is not None:
        save_snapshot(dest,current)
        last.write_text(json.dumps(current,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    issues=payload.get("issues",[]) if isinstance(payload,dict) else []
    if not isinstance(issues,list): issues=[]
    history=load_history(dest)
    render_history(dest,history)
    (dest/"README.md").write_text(markdown(current,status,error,args.run_url,issues,history),encoding="utf-8")
    print(status,error,"latest snapshot:",current["asof"] if current else "none")
    return 1 if args.run_result=="success" and error else 0

if __name__=="__main__":
    raise SystemExit(main())
