"""Deterministic research reports and private alert center, read-only on ledger."""
import argparse
import json
import os
from momentum_alert import is_extreme, marker, EXPLANATION
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from research_view import validate,cell,STRATEGIES,LABELS,INITIAL
from rank_history import load_history,changes
from paper import calendar,is_month_end

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo("Asia/Taipei")
THRESHOLDS=(.10,.20,.30)

def events(state):
    """Stable event IDs, reconstructed solely from recorded observations."""
    out=[]
    for p in state["signals"]:
        day=p["signal_day"]
        targets="；".join(s+"："+", ".join(p["targets"][s]) for s in STRATEGIES)
        out.append({"id":"signal-"+day,"day":day,"type":"月底訊號","message":"預定 "+p["planned_open"]+" 模擬開盤；"+targets})
    for i,t in enumerate(state["trades"]):
        s=t.get("strategy","全部")
        status=t["status"]
        msg=s+" "+status+"；訊號日 "+t["signal_day"]
        if status=="simulated":msg+=f"；成本 {t['cost']:,.2f} 元"
        out.append({"id":f"trade-{t['day']}-{i}","day":t["day"],"type":"模擬成交" if status=="simulated" else "成交異常","message":msg})
    peaks={s:INITIAL for s in STRATEGIES}
    previous={s:0. for s in STRATEGIES}
    for row in state["daily"]:
        for s in STRATEGIES:
            nav=row["nav"][s];peaks[s]=max(peaks[s],nav);dd=1-nav/peaks[s]
            for t in THRESHOLDS:
                if previous[s]+1e-12<t<=dd+1e-12:
                    out.append({"id":f"dd-{row['day']}-{s}-{round(t*100)}","day":row["day"],"type":"回撤警示","message":f"{s} 已觀測回撤 {dd:.2%}，跨越 {t:.0%} 研究門檻；不觸發交易。"})
            previous[s]=dd
        if row.get("unobserved_sessions"):
            out.append({"id":"gap-"+row["day"],"day":row["day"],"type":"資料缺口","message":"未觀測預定交易日："+", ".join(row["unobserved_sessions"])})
    return sorted(out,key=lambda x:(x["day"],x["id"]),reverse=True)

def completed_periods(state,cal):
    """Only exact observed period-end valuations; no forward filling."""
    byday={r["day"]:r for r in state["daily"]}
    ends=[d for d in cal["sessions"] if is_month_end(d,cal)]
    out=[]
    for kind in ("月報","季報"):
        prev=None
        for day in ends:
            if kind=="季報" and day[5:7] not in ("03","06","09","12"):continue
            if day>str(state["last_day"] or ""):break
            period=day[:7] if kind=="月報" else day[:4]+" Q"+str((int(day[5:7])-1)//3+1)
            if day not in byday:
                prev=day
                continue
            if prev and prev in byday:
                base=byday[prev]["nav"];start=prev;label="完整期初與期末估值"
            elif prev is None or prev<state["started"]:
                base={s:INITIAL for s in STRATEGIES};start=state["started"];label="研究啟動後的部分期間"
            else:
                base=None;start=prev;label="缺少上期期末估值，不計報酬"
            expected=[d for d in cal["sessions"] if start<d<=day]
            if label.startswith("研究") and start in cal["sessions"]:expected.insert(0,start)
            gaps=[d for d in expected if d not in byday]
            returns={s:byday[day]["nav"][s]/base[s]-1 for s in STRATEGIES} if base else {}
            out.append({"kind":kind,"period":period,"start":start,"end":day,"status":label,"missing_sessions":gaps,"returns":returns})
            prev=day
    return sorted(out,key=lambda x:(x["end"],x["kind"]),reverse=True)

def daily_text(state,history,radar_result,paper_result,run_url,today,ev,periods):
    current=history[-1]
    prev=history[-2] if len(history)>1 else None
    fresh=radar_result=="success" and paper_result=="success" and current["asof"]==today and state["last_day"]==today
    lines=["# 每日動能研究報告 · V0.8","",
           "報告執行日期："+today+"；排名日期："+current["asof"]+"；帳本日期："+str(state["last_day"]),"",
           "[研究首頁](RESEARCH.md) · [通知中心](ALERTS.md) · [月報與季報](PERIODS.md) · [執行紀錄]("+run_url+")",""]
    if not fresh:
        lines += ["> 本次未取得完整當日結果，以下是最近已保存的資料，不是新行情。",""]
    lines += ["## 一、本次觀察","",
              "| 指標 | 結果 |","|---|---|"]
    leader=current["ranking"][0]
    bench=next(x for x in current["ranking"] if x["code"]=="0050")
    lines.append(f"| 動能第一名 | {cell(leader['code'])} {cell(leader['name']+marker(leader['momentum']))} {leader['momentum']:+.2f}% |")
    lines.append(f"| 0050 十二個月動能 | {bench['momentum']:+.2f}% |")
    lines.append(f"| 正動能標的 | {sum(x['momentum']>0 for x in current['ranking'])}/151（含0050） |")
    if prev:
        prior=prev["ranking"][0]
        lines.append(f"| 第一名變動 | {cell(prior['code'])} → {cell(leader['code'])}（比較 {prev['asof']}） |")
        prior5={x["code"] for x in prev["ranking"][:5]};curr5={x["code"] for x in current["ranking"][:5]}
        lines.append("| 前5名新進／退出 | "+(", ".join(sorted(curr5-prior5)) or "無")+"／"+(", ".join(sorted(prior5-curr5)) or "無")+" |")
        ch=changes(current,prev)
        rising=sorted((x for x in current["ranking"] if (ch[x["code"]]["rank_delta"] or 0)>0),key=lambda x:(-ch[x["code"]]["rank_delta"],x["code"]))[:3]
        falling=sorted((x for x in current["ranking"] if (ch[x["code"]]["rank_delta"] or 0)<0),key=lambda x:(ch[x["code"]]["rank_delta"],x["code"]))[:3]
        for title,arr in [("名次上升前三",rising),("名次下降前三",falling)]:
            lines.append("| "+title+" | "+("；".join(f"{cell(x['code'])} {cell(x['name'])} {ch[x['code']]['rank_delta']:+d}" for x in arr) or "無")+" |")
    else:lines.append("| 排名變動 | 僅一個成功行情日，尚無比較 |")
    lines += ["","### 12個月動能前10名（"+current["asof"]+" 快照）","","| 排名 | 代碼 | 名稱 | 12個月動能 |","|---:|---|---|---:|"]
    for i,x in enumerate(current["ranking"][:10],1):
        lines.append(f"| {i} | {cell(x['code'])} | {cell(x['name']+marker(x['momentum']))} | {x['momentum']:+.2f}% |")
    flagged=[x for x in current["ranking"] if is_extreme(x["momentum"])]
    lines += ["","全池極端動能標記："+("；".join(f"{cell(x['code'])} {cell(x['name'])} {x['momentum']:+.2f}%" for x in flagged) or "無"),EXPLANATION]
    lines += ["","## 二、模擬績效","","| 策略 | 累計報酬 | 相對0050（百分點） | 當前回撤 |","|---|---:|---:|---:|"]
    b=state["accounts"]["S0"]["nav"]/INITIAL-1
    for s in STRATEGIES:
        a=state["accounts"][s];ret=a["nav"]/INITIAL-1
        lines.append(f"| {s} {LABELS[s]} | {ret:+.2%} | {(ret-b)*100:+.2f} | {a['nav']/a['peak']-1:.2%} |")
    lines += ["","## 三、訊號與風險提醒",""]
    if state["pending"]:
        p=state["pending"];lines.append("已保存月底訊號："+p["signal_day"]+"；預定模擬開盤："+p["planned_open"]+"。")
    else:lines.append("目前無待成交訊號。")
    today_events=[x for x in ev if x["day"]==today]
    if fresh:
        lines += [""]+["- "+cell(x["type"])+"："+cell(x["message"]) for x in today_events]
        if not today_events:lines.append("當日沒有新增已記錄訊號、成交或回撤門檻事件。")
    else:lines.append("本次資料更新未通過或日期未同步，不能判斷當日是否新增事件。")
    lines += ["","## 四、研究判讀與後續追蹤",""]
    if not any(t.get("status")=="simulated" for t in state["trades"]):
        lines.append("尚未建立模擬部位，目前不能評估前瞻性策略績效。")
    else:lines.append("比較三組實際累積報酬、回撤與交易成本；單日及單月勝負不構成策略有效證明。")
    lines += ["維持既定12個月動能與月底再平衡，不因日排名或警示任意改參數。","回撤10%、20%、30%為觀察門檻，非停損規則。缺失日與公司行動仍需查證。","此報告為規則式整理，非AI預測、實盤指令或外部推播。",""]
    return "\n".join(lines),fresh

def generate(dest,radar_result,paper_result,run_url,today,cal):
    state=json.loads((dest/"paper_state.json").read_text(encoding="utf-8"))
    validate(state)
    history=load_history(dest)
    if not history:raise ValueError("尚無有效排行榜")
    ev=events(state);periods=completed_periods(state,cal)
    page,fresh=daily_text(state,history,radar_result,paper_result,run_url,today,ev,periods)
    (dest/"DAILY.md").write_text(page,encoding="utf-8")
    root=dest/"reports";root.mkdir(exist_ok=True)
    if fresh:(root/(today+".md")).write_text(page.replace("](RESEARCH.md)","](../RESEARCH.md)").replace("](ALERTS.md)","](../ALERTS.md)").replace("](PERIODS.md)","](../PERIODS.md)"),encoding="utf-8")
    (root/"events.json").write_text(json.dumps(ev,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# 私人研究通知中心","","[每日摘要](DAILY.md) · [研究首頁](RESEARCH.md)","",
           "本頁記錄帳本事件。同日重跑不新增重複事件；不代表已發送手機或Email推播。","",
           "| 日期 | 類型 | 內容 |","|---|---|---|"]
    for x in ev[:100]:lines.append(f"| {cell(x['day'])} | {cell(x['type'])} | {cell(x['message'])} |")
    if not ev:lines.append("| — | — | 尚無已記錄事件 |")
    lines+=["","[完整事件資料](reports/events.json)",""]
    (dest/"ALERTS.md").write_text("\n".join(lines),encoding="utf-8")
    lines=["# 月度與季度研究報告","","[每日摘要](DAILY.md) · [研究首頁](RESEARCH.md)","",
           "只計算已有期末估值的期間；沒有前期期末估值不補算。缺少中間觀測日另標示。","",
           "| 類型 | 期間 | S0 | M2 | M3 | 缺失日數 | 說明 |","|---|---|---:|---:|---:|---:|---|"]
    for p in periods:
        vals=[f"{p['returns'][s]:+.2%}" if s in p["returns"] else "—" for s in STRATEGIES]
        lines.append(f"| {p['kind']} | {p['period']} | "+" | ".join(vals)+f" | {len(p['missing_sessions'])} | {p['status']} |")
    if not periods:lines.append("| — | 尚無完整期末觀測 | — | — | — | — | 尚待月底 |")
    lines+=["","[期間資料 JSON](reports/periods.json)",""]
    (dest/"PERIODS.md").write_text("\n".join(lines),encoding="utf-8")
    (root/"periods.json").write_text(json.dumps(periods,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    archived=sorted((p for p in root.glob("????-??-??.md")),reverse=True)
    (root/"README.md").write_text("# 每日研究報告存檔\n\n"+"\n".join(f"- [{p.stem}]({p.name})" for p in archived)+"\n",encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf-8") as f:f.write(page+"\n")
    print("REPORT_SUCCESS",today,"fresh",fresh,"events",len(ev),"periods",len(periods),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument("--radar-result",required=True);p.add_argument("--paper-result",required=True);p.add_argument("--run-url",required=True)
    a=p.parse_args()
    dest=ROOT/"dashboard";today=datetime.now(TZ).date().isoformat()
    try:generate(dest,a.radar_result,a.paper_result,a.run_url,today,calendar())
    except Exception as e:
        (dest/"DAILY.md").write_text("# 每日研究報告\n\n> 更新未通過："+cell(e)+"\n",encoding="utf-8")
        print("REPORT_BLOCKED",str(e),flush=True);return 1
    return 0

if __name__=="__main__":raise SystemExit(main())
