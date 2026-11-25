"""Point-in-time monthly rotation research engine. No synthetic data reported as real."""
import argparse
import csv
import json
import math
from bisect import bisect_right
from calendar import monthrange
from datetime import date
from pathlib import Path

BENCH="0050"
STRATEGIES=("S0","M1","M2","M3","M4","M5")
COMMISSION=.001425
SLIPPAGE=.0005
STOCK_TAX=.003
ETF_TAX=.001
INITIAL=100000.

def year_before(d):
    return d.replace(year=d.year-1,day=min(d.day,monthrange(d.year-1,d.month)[1]))

def read_csv(path):
    with path.open(encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))

def load(root):
    root=Path(root)
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    required=("price_source","calendar_source","membership_source","adjustment_method","corporate_action_policy","membership_verified_through")
    if any(not isinstance(manifest.get(k),str) or not manifest[k].strip() for k in required):
        raise ValueError("缺少資料來源與查核說明")
    if manifest.get("data_kind") not in ("research","synthetic"):
        raise ValueError("必須標記資料種類 research 或 synthetic")
    dates=[date.fromisoformat(x["date"]) for x in read_csv(root/"calendar.csv")]
    if dates!=sorted(set(dates)) or not dates:
        raise ValueError("交易日曆重複、缺漏或未排序")
    valid=set(dates)
    prices={}
    for x in read_csv(root/"prices.csv"):
        key=(date.fromisoformat(x["date"]),x["code"])
        o,c=float(x["adj_open"]),float(x["adj_close"])
        if key in prices or key[0] not in valid or not all(math.isfinite(v) and v>0 for v in (o,c)):
            raise ValueError("價格重複或無效："+str(key))
        prices[key]=(o,c)
    membership={}
    for x in read_csv(root/"universe.csv"):
        day=date.fromisoformat(x["signal_date"])
        known=date.fromisoformat(x["known_date"])
        effective=date.fromisoformat(x["effective_date"])
        code=x["code"]
        if day not in valid or known>day or effective>day or not x["source"].strip() or code==BENCH:
            raise ValueError("股票池資料不符合當時可知原則")
        if code in membership.setdefault(day,{}):
            raise ValueError("重複成分股")
        membership[day][code]=x
    for day, members in membership.items():
        if len(members)!=150:
            raise ValueError(f"{day} 股票池非150檔")
    return dates,prices,membership,manifest

def quote(prices,day,code,field):
    try:
        return prices[(day,code)][field]
    except KeyError:
        raise ValueError(f"缺少 {day} {code} {'開盤' if field==0 else '收盤'}，不允許假設可成交或以舊價代替")

def rank_at(day,dates,prices,membership):
    if day not in membership or len(membership[day])!=150:
        raise ValueError(f"{day} 缺少完整當時股票池")
    i=bisect_right(dates,year_before(day))-1
    if i<0:
        raise ValueError(f"{day} 缺少12個月暖機資料")
    base=dates[i]
    if (year_before(day)-base).days>10:
        raise ValueError("共同基準交易日過舊")
    codes=set(membership[day])|{BENCH}
    scores={c:quote(prices,day,c,1)/quote(prices,base,c,1)-1 for c in codes}
    return sorted(codes,key=lambda c:(-scores[c],c))

def target(strategy,rank):
    if strategy=="S0": return [BENCH]
    n={"M1":1,"M2":5,"M3":10,"M4":math.ceil(len(rank)*.2),"M5":len(rank)}[strategy]
    return rank[:n]

def cost(code,delta):
    return abs(delta)*(COMMISSION+SLIPPAGE+(0 if delta>=0 else ETF_TAX if code==BENCH else STOCK_TAX))

def rebalance(holdings,cash,targets,opens):
    """Fractional adjusted units; solve post-cost equal weights without leverage."""
    held={c:units*opens[c] for c,units in holdings.items()}
    nav=cash+sum(held.values())
    if nav<=0 or not targets or len(set(targets))!=len(targets):
        raise ValueError("無效淨值或目標組合")
    universe=set(held)|set(targets)
    def deltas(after):
        return {c:(after/len(targets) if c in targets else 0)-held.get(c,0.) for c in universe}
    lo,hi=0.,nav
    for _ in range(70):
        mid=(lo+hi)/2
        fee=sum(cost(c,v) for c,v in deltas(mid).items())
        if mid+fee>nav: hi=mid
        else: lo=mid
    after=(lo+hi)/2
    moves=deltas(after)
    fees=sum(cost(c,v) for c,v in moves.items())
    cash_left=cash-sum(moves.values())-fees
    if abs(cash_left)>max(1e-6,nav*1e-10):
        raise ValueError("交易後現金不平衡")
    return {c:after/len(targets)/opens[c] for c in targets},max(0.,cash_left),fees,moves

def run(dates,prices,membership,manifest,start,end):
    if start not in dates or end not in dates or start>end:
        raise ValueError("起訖須為交易日")
    if manifest["data_kind"]=="research" and date.fromisoformat(manifest["membership_verified_through"])<end:
        raise ValueError("歷史成分股查核尚未涵蓋回測結束日")
    i0,i1=dates.index(start),dates.index(end)
    if i0==0 or dates[i0-1].month==start.month:
        raise ValueError("起點須為月初首個交易日，且前一月底資料已存在")
    # Only actual month boundaries established by the supplied calendar are signals.
    # Partial final month never generates a hindsight month-end signal.
    signals={}
    for i in range(i0,i1+1):
        day=dates[i]
        prev=dates[i-1]
        if (day.year,day.month)!=(prev.year,prev.month):
            signals[day]=rank_at(prev,dates,prices,membership)
    result={}
    for strategy in STRATEGIES:
        holdings,cash={},INITIAL
        cumulative_cost=0.
        daily,orders=[],[]
        peak=INITIAL
        mdd=0.
        for i in range(i0,i1+1):
            day=dates[i]
            if day in signals and (strategy!="S0" or not holdings):
                names=target(strategy,signals[day])
                opens={c:quote(prices,day,c,0) for c in set(holdings)|set(names)}
                holdings,cash,fee,moves=rebalance(holdings,cash,names,opens)
                cumulative_cost+=fee
                for c,amount in sorted(moves.items()):
                    if abs(amount)>1e-7:
                        orders.append({"date":str(day),"signal_date":str(dates[i-1]),"code":c,
                                       "side":"buy" if amount>0 else "sell","notional":abs(amount),"cost":cost(c,amount)})
            nav=cash+sum(units*quote(prices,day,c,1) for c,units in holdings.items())
            peak=max(peak,nav)
            drawdown=nav/peak-1
            mdd=min(mdd,drawdown)
            daily.append({"date":str(day),"nav":nav,"drawdown":drawdown})
        final=daily[-1]["nav"]
        years=(end-start).days/365.2425
        result[strategy]={"summary":{"terminal":final,"return":final/INITIAL-1,"cagr":(final/INITIAL)**(1/years)-1 if years>=1 else None,"mdd":mdd,"cost":cumulative_cost,"orders":len(orders)},"daily":daily,"orders":orders}
    return {"data_kind":manifest["data_kind"],"source_manifest":manifest,"start":str(start),"end":str(end),"initial":INITIAL,"results":result}

def write_outputs(result,out):
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    (out/"results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    with (out/"summary.csv").open("w",newline="",encoding="utf-8-sig") as f:
        fields=["strategy","terminal","return","cagr","mdd","cost","orders"]
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        for name,x in result["results"].items():
            w.writerow({"strategy":name,**x["summary"]})
    for key in ("daily","orders"):
        fields=["strategy"]+(["date","nav","drawdown"] if key=="daily" else ["date","signal_date","code","side","notional","cost"])
        with (out/(key+".csv")).open("w",newline="",encoding="utf-8-sig") as f:
            w=csv.DictWriter(f,fieldnames=fields)
            w.writeheader()
            for name,x in result["results"].items():
                for row in x[key]: w.writerow({"strategy":name,**row})

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data",required=True)
    p.add_argument("--start",required=True)
    p.add_argument("--end",required=True)
    p.add_argument("--out",default="backtest-output")
    a=p.parse_args()
    result=run(*load(a.data),date.fromisoformat(a.start),date.fromisoformat(a.end))
    write_outputs(result,a.out)
    print("Data kind:",result["data_kind"],"; strategies:",len(result["results"]))
if __name__=="__main__":
    main()
