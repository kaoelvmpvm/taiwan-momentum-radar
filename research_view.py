"""Read-only visual research dashboard. Never mutates the paper ledger."""
import argparse
import html
import json
import math
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parent
INITIAL=100000.
STRATEGIES=("S0","M2","M3")
LABELS={"S0":"0050 買入持有","M2":"動能前5名","M3":"動能前10名"}
COLORS={"S0":"#9cb3cd","M2":"#f3b55f","M3":"#4fd1bd"}

def esc(value):
    return html.escape(str(value),quote=True)

def cell(value):
    return esc(value).replace("|","&#124;").replace("\n"," ").replace("\r"," ")

def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)

def validate(state):
    if state.get("version")!=1 or set(state["accounts"])!=set(STRATEGIES):
        raise ValueError("不支援的帳本版本或策略")
    daily=state["daily"]
    days=[date.fromisoformat(x["day"]) for x in daily]
    if days!=sorted(set(days)) or (daily and state["last_day"]!=daily[-1]["day"]):
        raise ValueError("淨值日期重複或未排序")
    if not daily and state["last_day"] is not None:
        raise ValueError("最近估值日期與紀錄不符")
    for row in daily:
        if set(row["nav"])!=set(STRATEGIES) or any(not finite(v) or v<=0 for v in row["nav"].values()):
            raise ValueError("每日淨值資料異常")
    for s,a in state["accounts"].items():
        values=[a["cash"],a["nav"],a["cost"],a["mdd"],a["peak"],*a["positions"].values()]
        if not all(finite(v) for v in values) or a["nav"]<=0 or min(a["cash"],a["cost"],*a["positions"].values())<0:
            raise ValueError("帳本數值異常 "+s)
        if abs(a["cash"]+sum(a["positions"].values())-a["nav"])>max(.0001,a["nav"]*1e-9):
            raise ValueError("持股市值與資產淨值不符 "+s)
        if daily and abs(daily[-1]["nav"][s]-a["nav"])>.0001:
            raise ValueError("最新每日淨值與帳本不一致 "+s)
        peak=INITIAL
        mdd=0.
        for row in daily:
            nav=row["nav"][s];peak=max(peak,nav);mdd=min(mdd,nav/peak-1)
        if abs(a["peak"]-peak)>.0001 or abs(a["mdd"]-mdd)>1e-9:
            raise ValueError("回撤紀錄不一致 "+s)

def points(state,kind):
    peaks={s:INITIAL for s in STRATEGIES}
    data={s:[] for s in STRATEGIES}
    for row in state["daily"]:
        for s in STRATEGIES:
            nav=row["nav"][s]
            peaks[s]=max(peaks[s],nav)
            value={"nav":nav,"return":100*(nav/INITIAL-1),"drawdown":100*(nav/peaks[s]-1)}[kind]
            data[s].append((row["day"],value))
    return data

def chart(state,kind):
    title={"nav":"資產淨值（新臺幣）","return":"累積報酬率（%）","drawdown":"回撤（%）"}[kind]
    data=points(state,kind)
    baseline=INITIAL if kind=="nav" else 0.
    all_values=[baseline]+[v for series in data.values() for _,v in series]
    lo,hi=min(all_values),max(all_values)
    span=max(hi-lo,1000. if kind=="nav" else 1.)
    lo,hi=lo-span*.12,hi+span*.12
    if kind=="drawdown":hi=0.;lo=min(lo,-1.)
    if hi<=lo:hi=lo+1.
    dates=[date.fromisoformat(row["day"]) for row in state["daily"]]
    left,right,top,bottom=92,734,95,295
    def x(d):
        if len(dates)<2:return (left+right)/2
        return left+(right-left)*(date.fromisoformat(d)-dates[0]).days/(dates[-1]-dates[0]).days
    def y(v):return bottom-(v-lo)*(bottom-top)/(hi-lo)
    a=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 365" role="img" aria-label="'+esc(title)+'">',
       '<rect width="760" height="365" rx="14" fill="#101b2d"/><g font-family="sans-serif" fill="#e8eef7">',
       '<text x="24" y="32" font-size="21" font-weight="bold">'+esc(title)+'</text>']
    a.append('<text x="24" y="54" font-size="12" fill="#a9bcd2">'+esc(f"實際觀測 {len(dates)} 日；缺失交易日不補值")+'</text>')
    for i,s in enumerate(STRATEGIES):
        xx=24+i*240
        a.append(f'<circle cx="{xx+5}" cy="75" r="4" fill="{COLORS[s]}"/><text x="{xx+15}" y="79" font-size="13">{s} {esc(LABELS[s])}</text>')
    for i in range(5):
        v=lo+(hi-lo)*i/4;yy=y(v)
        label=f"{v:,.0f}" if kind=="nav" else f"{v:.1f}%"
        a.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{right}" y2="{yy:.2f}" stroke="#35465e"/><text x="82" y="{yy+4:.2f}" font-size="12" text-anchor="end">{label}</text>')
    if lo<=baseline<=hi:
        yy=y(baseline)
        a.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{right}" y2="{yy:.2f}" stroke="#879bb3" stroke-dasharray="4 5"/>')
    gap_days={row["day"] for row in state["daily"] if row.get("unobserved_sessions")}
    # Distinct markers are drawn concentrically when strategies have identical values.
    for i,s in enumerate(STRATEGIES):
        prev=None
        for d,v in data[s]:
            xx,yy=x(d),y(v)
            if prev and d not in gap_days:
                a.append(f'<line x1="{prev[0]:.2f}" y1="{prev[1]:.2f}" x2="{xx:.2f}" y2="{yy:.2f}" stroke="{COLORS[s]}" stroke-width="2.4"/>')
            a.append(f'<circle cx="{xx:.2f}" cy="{yy:.2f}" r="{5-i*1.3}" fill="{COLORS[s]}"><title>{s} {esc(d)}: {v:.2f}</title></circle>')
            prev=(xx,yy)
    if dates:
        idx=sorted(set([0,len(dates)//2,len(dates)-1])) if len(dates)>4 else sorted(set([0,len(dates)-1]))
        for i in idx:
            d=dates[i].isoformat()
            a.append(f'<text x="{x(d):.2f}" y="320" font-size="12" text-anchor="middle">{d}</text>')
    else:a.append('<text x="415" y="195" font-size="16" text-anchor="middle">尚未有估值紀錄</text>')
    if len(dates)<2:
        a.append('<text x="415" y="347" font-size="13" text-anchor="middle" fill="#a9bcd2">目前尚未形成歷史曲線</text>')
    else:
        a.append('<text x="415" y="347" font-size="12" text-anchor="middle" fill="#a9bcd2">研究模擬資料；非實際資產或未來報酬預測</text>')
    return "".join(a)+"</g></svg>"

def allocations(state,names):
    a=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 256" role="img" aria-label="三組持股配置"><rect width="760" height="256" rx="14" fill="#101b2d"/><g fill="#e8eef7" font-family="sans-serif"><text x="24" y="33" font-size="21" font-weight="bold">當前持股配置</text><text x="24" y="54" font-size="12" fill="#a9bcd2">依模擬市值計算；灰色表示現金</text>']
    palette=["#5c87c3","#53b6a9","#e2b161","#bd91d1","#da806c","#6db7d4","#b7bd6a","#8e9bd6","#de92ac","#83a98a"]
    for i,s in enumerate(STRATEGIES):
        account=state["accounts"][s];nav=account["nav"];y=80+i*55
        rows=sorted(account["positions"].items(),key=lambda z:(-z[1],z[0]))
        rows.append(("現金",account["cash"]))
        a.append(f'<text x="24" y="{y+17}" font-size="14" font-weight="bold">{s}</text>')
        xx=82
        for j,(c,v) in enumerate(rows):
            if v<=0:continue
            width=650*v/nav;color="#64748b" if c=="現金" else palette[j%len(palette)]
            a.append(f'<rect x="{xx:.3f}" y="{y}" width="{width:.3f}" height="23" fill="{color}"><title>{esc(c)} {esc(names.get(c,""))} {v/nav:.2%}</title></rect>')
            if width>=55:a.append(f'<text x="{xx+width/2:.2f}" y="{y+16}" font-size="12" text-anchor="middle" fill="#101b2d">{esc(c)}</text>')
            xx+=width
        a.append(f'<text x="82" y="{y+40}" font-size="11" fill="#a9bcd2">現金 {account["cash"]/nav:.1%} · {len(account["positions"])} 檔持股</text>')
    return "".join(a)+"</g></svg>"

def render(state,ranking,radar_result,paper_result,run_url):
    names={x["code"]:x["name"] for x in ranking.get("ranking",[])}
    lines=["# 台股動能研究平台 · V0.7","","**前瞻性研究｜虛擬本金｜不涉及實際交易**","",
           "[每日排行榜](README.md) · [歷史監測](HISTORY.md) · [詳細模擬帳本](PAPER.md) · [回測結果](../backtest/RESEARCH_RESULT.md) · [本次執行紀錄]("+run_url+")","",
           f"排名行情日期：{cell(ranking.get('asof','尚無'))}　｜　模擬估值日期：{cell(state['last_day'] or '尚無')}",""]
    if radar_result!="success" or paper_result!="success":
        lines += ["> 本次資料或模擬帳本未完整更新。下方保留最近已保存資料，不能視為當日結果。",""]
    else:
        lines += ["本次流程通過。仍須以各資料日期為準；尚未建立部位不代表策略已完成實際驗證。",""]
    lines += ["## 三組策略即時比較","","| 策略 | 淨值（元） | 累積報酬 | 相對0050（百分點） | 最大回撤 | 累計成本 |","|---|---:|---:|---:|---:|---:|"]
    bench=state["accounts"]["S0"]["nav"]/INITIAL-1
    for s in STRATEGIES:
        a=state["accounts"][s];ret=a["nav"]/INITIAL-1
        lines.append(f"| {s} {LABELS[s]} | {a['nav']:,.2f} | {ret:+.2%} | {(ret-bench)*100:+.2f} | {a['mdd']:.2%} | {a['cost']:,.2f} |")
    lines += ["","## 真實觀測曲線","","![累積報酬](research/returns.svg)","","![資產淨值](research/nav.svg)","","![回撤](research/drawdown.svg)",""]
    if len(state["daily"])<2:lines += ["目前尚未形成跨日曲線；顯示實際初始觀測，不補造資料。",""]
    gaps=sum(len(x.get("unobserved_sessions",[])) for x in state["daily"])
    if gaps:lines += [f"有 {gaps} 個預定交易日缺少觀測；曲線斷開，最大回撤只反映已觀測日。",""]
    lines += ["## 持股配置","","![各策略配置](research/allocation.svg)",""]
    for s in STRATEGIES:
        a=state["accounts"][s]
        lines += ["### "+s+" "+LABELS[s],"","| 股票 | 模擬市值（元） | 權重 |","|---|---:|---:|"]
        for c,v in sorted(a["positions"].items(),key=lambda z:(-z[1],z[0])):
            lines.append(f"| {cell(c)} {cell(names.get(c,''))} | {v:,.2f} | {v/a['nav']:.2%} |")
        lines.append(f"| 現金 | {a['cash']:,.2f} | {a['cash']/a['nav']:.2%} |")
        lines.append("")
    lines += ["## 下一次換股與最近訊號",""]
    p=state["pending"]
    if p:
        lines += [f"待模擬成交：{cell(p['planned_open'])}；訊號日：{cell(p['signal_day'])}",""]
        for s in STRATEGIES:lines.append("- "+s+"："+", ".join(cell(c+" "+names.get(c,"")) for c in p["targets"][s]))
    else:lines.append("目前無待成交訊號。")
    if state["signals"]:
        recent=state["signals"][-1]
        lines += ["",f"最近已保存訊號：{cell(recent['signal_day'])}；預定開盤 {cell(recent['planned_open'])}。"]
    lines += ["","## 當前強勢股（排名觀察，非換股指令）","","| 排名 | 代碼 | 名稱 | 12個月動能 |","|---:|---|---|---:|"]
    for i,x in enumerate(ranking.get("ranking",[])[:10],1):
        lines.append(f"| {i} | {cell(x['code'])} | {cell(x['name'])} | {x['momentum']:+.2f}% |")
    lines += ["","## 交易成本與最近交易","",
              "| 策略 | 模擬買進次數 | 模擬賣出次數 | 累計成交參考金額（元） | 帳本累計成本（元） |",
              "|---|---:|---:|---:|---:|"]
    for s in STRATEGIES:
        orders=[o for t in state["trades"] if t.get("strategy")==s and t.get("status")=="simulated" for o in t.get("orders",[])]
        lines.append(f"| {s} | {sum(o['side']=='buy' for o in orders)} | {sum(o['side']=='sell' for o in orders)} | {sum(o['notional'] for o in orders):,.2f} | {state['accounts'][s]['cost']:,.2f} |")
    lines += ["","| 日期 | 策略 | 結果 | 買進 | 賣出 | 成本（元） |","|---|---|---|---|---|---:|"]
    for t in reversed(state["trades"][-12:]):
        orders=t.get("orders",[])
        buy=", ".join(cell(o["code"]) for o in orders if o["side"]=="buy") or "—"
        sell=", ".join(cell(o["code"]) for o in orders if o["side"]=="sell") or "—"
        fee=f"{t['cost']:,.2f}" if "cost" in t else "—"
        lines.append(f"| {cell(t['day'])} | {cell(t.get('strategy','全部'))} | {cell(t['status'])} | {buy} | {sell} | {fee} |")
    if not state["trades"]:lines.append("| — | — | 尚無模擬成交 | — | — | — |")
    lines += ["","[完整訊號、交易與淨值資料 JSON](paper_state.json)","","## 研究限制","",
              "此頁只呈現現有帳本，不改動訊號、資金與成交規則。相對0050是累積報酬差，非風險調整後 Alpha。個股市值與配置權重不是個股損益貢獻；帳本未保存足夠逐日歸因資料，因此不估算個股貢獻。",
              "每日排名使用當日ETF持股代理池。先前150檔探索性回測有倖存者偏誤，尚未完成無偏誤歷史驗證。模擬使用分數股、參考開盤價與預設費率，並非真實成交。私人 GitHub 文件，非公開網站。",""]
    return "\n".join(lines)

def generate(dest,radar_result,paper_result,run_url):
    state=json.loads((dest/"paper_state.json").read_text(encoding="utf-8"))
    ranking=json.loads((dest/"last_success.json").read_text(encoding="utf-8"))
    validate(state)
    names={x["code"]:x["name"] for x in ranking.get("ranking",[])}
    root=dest/"research";root.mkdir(exist_ok=True)
    # Compute before writing any page; the ledger itself is never rewritten.
    charts={"nav.svg":chart(state,"nav"),"returns.svg":chart(state,"return"),"drawdown.svg":chart(state,"drawdown"),"allocation.svg":allocations(state,names)}
    page=render(state,ranking,radar_result,paper_result,run_url)
    for name,svg in charts.items():(root/name).write_text(svg,encoding="utf-8")
    (dest/"RESEARCH.md").write_text(page,encoding="utf-8")
    print("RESEARCH_VIEW_SUCCESS",len(state["daily"]),state["last_day"],flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--radar-result",required=True)
    p.add_argument("--paper-result",required=True)
    p.add_argument("--run-url",required=True)
    a=p.parse_args()
    dest=ROOT/"dashboard"
    try:
        generate(dest,a.radar_result,a.paper_result,a.run_url)
    except Exception as exc:
        # Never present a stale page as a new successful update.
        (dest/"RESEARCH.md").write_text("# 台股動能研究平台 · V0.7\n\n> 研究首頁更新未通過："+cell(exc)+"\n\n[查看原始帳本](PAPER.md) · [執行紀錄]("+a.run_url+")\n",encoding="utf-8")
        print("RESEARCH_VIEW_BLOCKED",str(exc),flush=True)
        return 1
    return 0

if __name__=="__main__":
    raise SystemExit(main())
