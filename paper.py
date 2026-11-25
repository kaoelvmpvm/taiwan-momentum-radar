"""Prospective paper experiment; no real trades, no historical replay."""
import copy
import json
import math
from datetime import datetime,time
from pathlib import Path
from zoneinfo import ZoneInfo
from rotation_engine import rebalance, cost

ROOT=Path(__file__).resolve().parent
TZ=ZoneInfo("Asia/Taipei")
INITIAL=100000.
RULES={"S0":1,"M2":5,"M3":10}
START="2026-11-25"

def initial():
    return {"version":1,"started":START,"last_day":None,"pending":None,"signals":[],"trades":[],"daily":[],
            "accounts":{s:{"cash":INITIAL,"positions":{},"nav":INITIAL,"peak":INITIAL,"mdd":0.,"cost":0.} for s in RULES}}

def calendar():
    data=json.loads((ROOT/"paper_calendar.json").read_text(encoding="utf-8"))
    days=data["sessions"]
    if days!=sorted(set(days)):raise ValueError("invalid calendar")
    return data

def next_session(day,cal):
    return next((d for d in cal["sessions"] if d>day),None)

def is_month_end(day,cal):
    n=next_session(day,cal)
    return day in cal["sessions"] and n is not None and n[:7]!=day[:7]

def positive(x):
    return isinstance(x,(float,int)) and math.isfinite(x) and x>0

def plan(snapshot,now,cal,state):
    d=snapshot["asof"]
    if not is_month_end(d,cal):return None
    if not snapshot.get("complete") or snapshot.get("issues") or len(snapshot.get("ranking",[]))!=151:
        raise ValueError("未完整驗證的月末排名")
    generated=datetime.fromisoformat(snapshot["generated"])
    if generated.astimezone(TZ).date().isoformat()!=d or now.astimezone(TZ).date().isoformat()!=d or now.astimezone(TZ).time()<time(13,30):
        raise ValueError("拒絕事後補造月末訊號")
    if any(x["signal_day"]==d for x in state["signals"]):return None
    rows=snapshot["ranking"]
    codes=[x["code"] for x in rows]
    if len(set(codes))!=151 or "0050" not in codes:raise ValueError("invalid ranking")
    if codes!=[x["code"] for x in sorted(rows,key=lambda x:(-x["momentum"],x["code"]))]:raise ValueError("ranking not sorted")
    nxt=next_session(d,cal)
    return {"signal_day":d,"created":now.isoformat(),"planned_open":nxt,
            "targets":{"S0":["0050"],"M2":codes[:5],"M3":codes[:10]},
            "scores":{x["code"]:x["momentum"] for x in rows[:10]}}

def evolve(old,snapshot,now,cal,quotes):
    """Quotes are daily return factors anchored to each position's last marked day.
    All three accounts advance together. A missed open is explicitly recorded as unfilled.
    """
    state=copy.deepcopy(old)
    day=snapshot["asof"]
    if day<START or day not in cal["sessions"] or now.astimezone(TZ).date().isoformat()!=day or now.astimezone(TZ).time()<time(13,30):
        raise ValueError("行情日期與當日交易日曆不符")
    last=state["last_day"]
    if last and day<last:raise ValueError("out-of-order update")
    if last==day:return state
    if not snapshot.get("complete") or snapshot.get("issues"):raise ValueError("資料驗證未通過")
    pending=state["pending"]
    fill=False
    if pending and day>=pending["planned_open"]:
        # No retrospective fills. Only a signal already persisted before today's open can fill.
        created=datetime.fromisoformat(pending["created"]).astimezone(TZ)
        open_time=datetime.fromisoformat(day+"T09:00:00").replace(tzinfo=TZ)
        if created>=open_time:raise ValueError("signal timestamp is after opening")
        if day!=pending["planned_open"]:
            state["trades"].append({"day":day,"signal_day":pending["signal_day"],"status":"missed_open_no_replay","planned_open":pending["planned_open"]})
            state["pending"]=None
        else:
            needed=set()
            for s,a in state["accounts"].items():
                if s=="S0" and a["positions"]:continue
                needed.update(a["positions"])
                needed.update(pending["targets"][s])
            if all(c in quotes and positive(quotes[c].get("open")) and positive(quotes[c].get("close")) for c in needed):
                fill=True
            else:
                state["trades"].append({"day":day,"signal_day":pending["signal_day"],"status":"unfilled_missing_quote","planned_open":pending["planned_open"]})
                state["pending"]=None
    for s,a in state["accounts"].items():
        positions=a["positions"]
        do_fill=fill and not (s=="S0" and positions)
        for c,value in positions.items():
            q=quotes.get(c,{})
            if not positive(q.get("prior")) or not positive(q.get("close")):raise ValueError("持有標的缺少可靠估值 "+c)
        if do_fill:
            # Existing positions are represented as value at last close, repriced with
            # same-vintage adjusted close/open ratios so future dividend revisions do not
            # silently change the number of historical adjusted shares.
            units={c:v/quotes[c]["prior"] for c,v in positions.items()}
            names=pending["targets"][s]
            opens={c:quotes[c]["open"] for c in set(positions)|set(names)}
            h,cash,fees,moves=rebalance(units,a["cash"],names,opens)
            a["positions"]={c:u*quotes[c]["close"] for c,u in h.items()}
            a["cash"]=cash
            a["cost"]+=fees
            state["trades"].append({"day":day,"signal_day":pending["signal_day"],"strategy":s,"status":"simulated","targets":names,
                                    "cost":fees,"orders":[{"code":c,"notional":abs(v),"side":"buy" if v>0 else "sell","cost":cost(c,v),"official_open":quotes[c]["raw_open"]} for c,v in sorted(moves.items()) if abs(v)>1e-6]})
        else:
            a["positions"]={c:v*quotes[c]["close"]/quotes[c]["prior"] for c,v in positions.items()}
        a["nav"]=a["cash"]+sum(a["positions"].values())
        a["peak"]=max(a["peak"],a["nav"])
        a["mdd"]=min(a["mdd"],a["nav"]/a["peak"]-1)
    if fill:state["pending"]=None
    p=plan(snapshot,now,cal,state)
    if p:
        if state["pending"]:raise ValueError("unexpected old pending signal")
        state["signals"].append(p)
        state["pending"]=p
    gap=[x for x in cal["sessions"] if last and last<x<day]
    state["daily"].append({"day":day,"nav":{s:a["nav"] for s,a in state["accounts"].items()},"unobserved_sessions":gap})
    state["last_day"]=day
    return state

def official_quotes(day):
    import requests
    r=requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",params={"date":day.replace("-",""),"type":"ALLBUT0999","response":"json"},headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"},timeout=45)
    r.raise_for_status()
    data=r.json()
    if data.get("stat")!="OK" or str(data.get("date"))!=day.replace("-",""):raise ValueError("官方行情日期不符")
    required={"證券代號","開盤價","收盤價","成交股數"}
    tables=[t for t in data.get("tables",[]) if required.issubset(t.get("fields",[]))]
    if len(tables)!=1:raise ValueError("官方行情欄位不符")
    t=tables[0]; f=t["fields"]; out={}
    for row in t["data"]:
        try:
            c=str(row[f.index("證券代號")]).strip()
            o,z,v=(float(str(row[f.index(k)]).replace(",","")) for k in ("開盤價","收盤價","成交股數"))
            if positive(o) and positive(z) and positive(v):out[c]=(o,z)
        except (ValueError,IndexError,TypeError):continue
    if len(out)<500 or "0050" not in out:raise ValueError("官方有效行情不足")
    return out

def fetch_quotes(state,day):
    import pandas as pd
    import yfinance as yf
    from radar import FACE_VALUE_EVENTS,verified_event_quotes,verify_suspended_rows,reconcile_face_value,dated_official_market
    held=set(c for a in state["accounts"].values() for c in a["positions"])
    needed=set(held)
    p=state["pending"]
    if p and p["planned_open"]==day:
        for s,names in p["targets"].items():
            if s!="S0" or not state["accounts"][s]["positions"]:needed.update(names)
    if not needed:return {}
    market=official_quotes(day)
    last=state["last_day"] or day
    start=(pd.Timestamp(last)-pd.Timedelta(days=8)).date().isoformat()
    end=(pd.Timestamp(day)+pd.Timedelta(days=1)).date().isoformat()
    raw=yf.download([c+".TW" for c in sorted(needed)],start=start,end=end,auto_adjust=False,actions=True,repair=False,group_by="ticker",threads=False,progress=False,timeout=25)
    result={}
    for c in needed:
        if c not in market:continue
        try:
            frame=raw[c+".TW"] if isinstance(raw.columns,pd.MultiIndex) else raw
            frame=frame.dropna(subset=["Close","Adj Close"]).copy()
            idx=pd.DatetimeIndex(frame.index)
            if idx.tz is not None:idx=idx.tz_convert("Asia/Taipei").tz_localize(None)
            frame.index=idx.normalize()
            if frame.index.has_duplicates:raise ValueError("duplicate history")
            t=pd.Timestamp(day); old=pd.Timestamp(last)
            if c in FACE_VALUE_EVENTS:
                event=FACE_VALUE_EVENTS[c]
                if old<=pd.Timestamp(event["last_old"]) and t>=pd.Timestamp(event["first_new"]):
                    evidence=json.loads((ROOT/"verified_corporate_actions.json").read_text())
                    a,b,lookup,_=verified_event_quotes(c,event,evidence,dated_official_market)
                    frame,_=verify_suspended_rows(frame,event,c,lookup)
                    frame,_=reconcile_face_value(frame,event,a,b)
            row=frame.loc[t]
            raw_o,raw_z=market[c]
            if not positive(float(row["Volume"])) or abs(float(row["Open"])-raw_o)>max(.011,raw_o*.0005) or abs(float(row["Close"])-raw_z)>max(.011,raw_z*.0005):
                raise ValueError("官方與資料商開收盤不符")
            close=float(row["Adj Close"])
            prior=float(frame.at[old,"Adj Close"]) if c in held else close
            ao=raw_o*close/raw_z
            seq=frame.loc[old:t,"Adj Close"].pct_change().abs()
            if (seq>.35).any() or not all(positive(v) for v in (close,prior,ao)):raise ValueError("還原價異常")
            result[c]={"open":ao,"close":close,"prior":prior,"raw_open":raw_o}
        except Exception as exc:
            print("PAPER_QUOTE_ISSUE",c,str(exc),flush=True)
    return result

def render(state,status):
    from datetime import date
    cal=calendar()
    p=state["pending"]
    lines=["# 前瞻性模擬交易 · V0.6","","**研究用虛擬帳本，與實際投資完全無關。**","",
           "更新狀態："+status,"","起始日："+state["started"]+"；最近估值："+str(state["last_day"] or "尚未估值"),"",
           "| 策略 | 資產淨值 | 累計報酬 | 最大回撤 | 累計交易成本 | 持股數 |","|---|---:|---:|---:|---:|---:|"]
    for s,a in state["accounts"].items():
        lines.append(f"| {s} | {a['nav']:,.2f} | {a['nav']/INITIAL-1:+.2%} | {a['mdd']:.2%} | {a['cost']:,.2f} | {len(a['positions'])} |")
    lines+=["","## 月底訊號",""]
    if p:
        lines += ["待模擬成交："+p["planned_open"]+"（訊號："+p["signal_day"]+"）",""]
        for s,cs in p["targets"].items():lines.append("- "+s+"："+", ".join(cs))
    else:
        lines.append("目前無待成交訊號。")
    lines += ["","## 各組持股（模擬市值）",""]
    for s,a in state["accounts"].items():
        lines += ["### "+s,"",f"現金：{a['cash']:,.2f} 元",""]
        for c,v in sorted(a["positions"].items(),key=lambda x:-x[1]):lines.append(f"- {c}：{v:,.2f} 元（{v/a['nav']:.2%}）")
        if not a["positions"]:lines.append("尚未建立部位。")
        lines.append("")
    lines += ["## 最近模擬交易與異常",""]
    for t in reversed(state["trades"][-15:]):
        lines.append("- "+t["day"]+" "+t.get("strategy","全部")+"："+t["status"]+"；訊號 "+t["signal_day"])
    if not state["trades"]:lines.append("尚無模擬成交。")
    lines += ["","[完整帳本 JSON](paper_state.json) · [每日排行榜](README.md)","",
              "S0為0050買入持有，M2為前5等權、M3為前10等權。每組初始10萬元，雙邊費率0.1425%及滑價0.05%，賣方個股稅0.3%、ETF稅0.1%。以調整後價格比例計算股息再投入，非真實持股或成交。假設全額、分數股；無最低手續費及漲跌停成交限制。",
              "只使用實際當日通過的排名；月末錯過或預定開盤缺價不補造成交。暫不回填漏掉的日淨值，因此發生資料中斷時最大回撤僅為已觀測日估計。",
              "交易日曆為官方公告的預定日期，有效至"+cal["valid_through"]+"；臨時休市需查核後修改。",""]
    return "\n".join(lines)

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument("--run-result",required=True);a=p.parse_args()
    dest=ROOT/"dashboard";path=dest/"paper_state.json"
    state=json.loads(path.read_text(encoding="utf-8")) if path.exists() else initial()
    try:
        if a.run_result!="success":raise ValueError("本次雷達更新失敗；保留舊帳本")
        snap=json.loads((ROOT/"report"/"ranking.json").read_text())
        now=datetime.now(TZ);cal=calendar()
        if snap.get("asof")!=now.date().isoformat():raise ValueError("非當日行情，保留舊帳本")
        if now.date().isoformat()>cal["valid_through"]:raise ValueError("交易日曆到期")
        if state["last_day"]==snap["asof"]:
            status="同日已處理，不重複交易"
        else:
            quotes=fetch_quotes(state,snap["asof"])
            state=evolve(state,snap,now,cal,quotes)
            status="當日資料處理完成"
        path.write_text(json.dumps(state,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
        (dest/"PAPER.md").write_text(render(state,status),encoding="utf-8")
        print("PAPER_SUCCESS",status,state["last_day"],flush=True)
        return 0
    except Exception as e:
        (dest/"PAPER.md").write_text(render(state,"未更新："+str(e)),encoding="utf-8")
        print("PAPER_BLOCKED",str(e),flush=True)
        return 1 if a.run_result=="success" else 0
if __name__=="__main__":
    raise SystemExit(main())
