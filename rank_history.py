"""Date-keyed successful research snapshots and static SVG charts."""
import html
import json
import math
from pathlib import Path

def save_snapshot(root, snap):
    if not snap:
        return
    from datetime import date
    day = date.fromisoformat(snap["asof"]).isoformat()
    folder = root / "history"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (day+".json")
    path.write_text(json.dumps(snap,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")

def load_history(root):
    from datetime import date
    folder = root/"history"
    result=[]
    for path in sorted(folder.glob("????-??-??.json")):
        item=json.loads(path.read_text(encoding="utf-8"))
        if date.fromisoformat(item["asof"]).isoformat()!=path.stem or len(item["ranking"])!=151:
            raise ValueError("歷史快照日期或筆數異常："+path.name)
        result.append(item)
    return result

def index(snapshot):
    return {x["code"]:(i,x) for i,x in enumerate(snapshot["ranking"],1)}

def changes(current, previous):
    old=index(previous) if previous else {}
    result={}
    for i,x in enumerate(current["ranking"],1):
        item=old.get(x["code"])
        result[x["code"]]={"rank":i,"rank_delta":item[0]-i if item else None,
                           "momentum_delta":x["momentum"]-item[1]["momentum"] if item else None}
    return result

def series(history, code):
    out=[]
    for item in history:
        row=index(item).get(code)
        out.append((item["asof"],row[0] if row else None,row[1]["momentum"] if row else None))
    return out

def svg_chart(points, code, name):
    """Two independent scales; rank axis always 1 at top, 151 at bottom."""
    e=lambda s:html.escape(str(s))
    w,h=760,460
    left,right=90,730
    n=len(points)
    x=lambda i: (left+right)/2 if n<=1 else left+(right-left)*i/(n-1)
    values=[p[2] for p in points if p[2] is not None]
    lo,hi=(min(values),max(values)) if values else (0,1)
    margin=max((hi-lo)*.1,1.0)
    lo,hi=lo-margin,hi+margin
    ym=lambda v:210-125*(v-lo)/(hi-lo)
    yr=lambda r:275+125*(r-1)/150
    a=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{e(code)} historical momentum and rank">',
       '<rect width="760" height="460" rx="14" fill="#101b2d"/>',
       f'<g font-family="sans-serif" fill="#e6eef9"><text x="24" y="34" font-size="22" font-weight="bold">{e(code)} {e(name)}</text>',
       f'<text x="24" y="60" font-size="13" fill="#a9bcd2">最近 {n} 個成功快照 · 觀測序列（非等距日曆）</text>',
       '<text x="24" y="80" font-size="13">12個月動能（%）</text><text x="24" y="260" font-size="13">全池排名（1為最高）</text>']
    for v in (lo,(lo+hi)/2,hi):
        y=ym(v)
        a.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#34485e"/><text x="82" y="{y+4:.1f}" font-size="12" text-anchor="end">{v:.1f}%</text>')
    for r in (1,50,100,151):
        y=yr(r)
        a.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#34485e"/><text x="82" y="{y+4:.1f}" font-size="12" text-anchor="end">{r}</text>')
    for col,scale,color in ((2,ym,"#f6b65d"),(1,yr,"#62d2e8")):
        prev=None
        for i,p in enumerate(points):
            if p[col] is None:
                prev=None
                continue
            xx,yy=x(i),scale(p[col])
            if prev is not None:
                a.append(f'<line x1="{prev[0]:.1f}" y1="{prev[1]:.1f}" x2="{xx:.1f}" y2="{yy:.1f}" stroke="{color}" stroke-width="2.5"/>')
            a.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="3" fill="{color}"><title>{e(p[0])}: {p[col]:.2f}</title></circle>')
            prev=(xx,yy)
    for i in sorted(set([0,n//2,n-1])) if n else []:
        a.append(f'<text x="{x(i):.1f}" y="425" font-size="12" text-anchor="middle">{e(points[i][0])}</text>')
    a.append('<text x="24" y="449" font-size="12" fill="#a9bcd2">缺席股票不連線；尚未收集的日期不補值。研究用，非交易訊號。</text></g></svg>')
    return "".join(a)

def render_history(root, history):
    if not history:
        return
    current=history[-1]
    charts=root/"charts"
    charts.mkdir(exist_ok=True)
    # Only current pool plus benchmark; historical members remain in dated JSON.
    active=set()
    for row in current["ranking"]:
        code=row["code"]
        active.add(code)
        (charts/(code+".svg")).write_text(svg_chart(series(history[-60:],code),code,row["name"]),encoding="utf-8")
    for f in charts.glob("*.svg"):
        if f.stem not in active:
            f.unlink()
    prev=history[-2] if len(history)>1 else None
    ch=changes(current,prev)
    leader=current["ranking"][0]
    lines=["# 歷史動能監測","",f"已累積 {len(history)} 個成功行情日：{history[0]['asof']} 至 {current['asof']}。","",
           "只記錄實際通過檢查的快照；同日重跑覆寫當日，不新增日期。未成功的行情日不補造排名。","",
           f"最近一筆第一名：{leader['code']} {leader['name']}（{leader['momentum']:+.2f}%）",""]
    if prev:
        old=prev["ranking"][0]
        lines += [f"比較基準：{prev['asof']}（上一個成功快照，不保證為前一交易日）。",
                  "第一名異動："+(f"{old['code']} {old['name']} → {leader['code']} {leader['name']}" if old["code"]!=leader["code"] else "未變動"),""]
        risers=sorted((x for x in current["ranking"] if ch[x["code"]]["rank_delta"] is not None),key=lambda x:(-ch[x["code"]]["rank_delta"],x["code"]))
        lines += ["## 名次變動","", "| 上升最多 | 名次變動 | 下降最多 | 名次變動 |","|---|---:|---|---:|"]
        up=[x for x in risers if ch[x["code"]]["rank_delta"]>0][:5]
        down=[x for x in reversed(risers) if ch[x["code"]]["rank_delta"]<0][:5]
        for i in range(max(len(up),len(down))):
            def fmt(arr):
                if i>=len(arr): return "— | —"
                x=arr[i]
                return f"{x['code']} {x['name']} | {ch[x['code']]['rank_delta']:+d}"
            lines.append("| "+fmt(up)+" | "+fmt(down)+" |")
        if not up and not down: lines.append("| 無 | — | 無 | — |")
    else:
        lines += ["目前僅有一個行情日，尚無跨日名次變動；圖表僅顯示單一觀測點。",""]
    lines += ["","## 個股走勢圖","","點選股票代碼查看最近60個成功快照的動能及排名圖。","",
              "| 排名 | 股票 | 動能 | 名次變動 | 動能變動（百分點） |","|---:|---|---:|---:|---:|"]
    for i,row in enumerate(current["ranking"],1):
        v=ch[row["code"]]
        r="—" if v["rank_delta"] is None else f"{v['rank_delta']:+d}"
        m="—" if v["momentum_delta"] is None else f"{v['momentum_delta']:+.2f}"
        name=row["name"].replace("|","\\|").replace("<","&lt;").replace(">","&gt;")
        lines.append(f"| {i} | [{row['code']} {name}](charts/{row['code']}.svg) | {row['momentum']:+.2f}% | {r} | {m} |")
    lines += ["","## 歷史快照（最近60筆）",""]
    for item in reversed(history[-60:]):
        top=item["ranking"][0]
        lines.append(f"- [{item['asof']}](history/{item['asof']}.json) — {top['code']} {top['name']} {top['momentum']:+.2f}%")
    lines += ["","[全部歷史快照](history/) · [返回排行榜](README.md)",""]
    (root/"HISTORY.md").write_text("\n".join(lines),encoding="utf-8")
