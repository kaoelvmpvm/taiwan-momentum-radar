"""Frozen D6-M + I2 research signals. No broker, no orders, no personal holdings."""
import json, math, re, time
from datetime import datetime, time as dt_time
from pathlib import Path
import pandas as pd
import requests
import radar

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'d6m_state.json'
DATA=ROOT/'site/data.json'
CACHE=ROOT/'.d6m-raw'
BACKUP=('1216','2881','2412','0050')
CORE=('2330',)+BACKUP
NAMES={'2330':'台積電','1216':'統一','2881':'富邦金','2412':'中華電','0050':'元大台灣50'}
VERSION='D6-M v1.0'
def read(path): return json.loads(path.read_text(encoding='utf-8'))
def write(path,data):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(path)
def core_decision(current, r12, r6):
    if current not in CORE or not all(k in r6 for k in BACKUP):
        raise ValueError('missing core input')
    relative=(1+r12['2330']/100)/(1+r12['0050']/100)-1
    ranked=sorted(BACKUP,key=lambda k:(-r6[k],BACKUP.index(k)))
    target=current
    if current=='2330' and relative<0:target=ranked[0]
    elif current!='2330' and relative>0 and r12['2330']>0:target='2330'
    return {'from':current,'target':target,'relative_12m':relative*100,'tsmc_12m':r12['2330'],'0050_12m':r12['0050'],
            'backups':[{'code':k,'name':NAMES[k],'r6':r6[k]} for k in ranked],'action':'switch' if current!=target else 'hold'}
def rank_i2(rows, prior):
    for row in rows:row.pop('i2_rank',None)
    eligible=[r for r in rows if r['r6'] is not None and r.get('price_eligible',True)]
    ordered=sorted(eligible,key=lambda r:(-r['r6'],r['code']))
    top={r['code'] for r in ordered[:math.ceil(.3*len(ordered))]}
    ranks={r['code']:i+1 for i,r in enumerate(ordered)}
    previous=set(prior['top30']) if prior is not None else None
    for r in rows:
        c=r['code'];r['rank6']=ranks.get(c);r['top30']=c in top
        r['persistent']=None if previous is None else c in top and c in previous
        r['selected']=False
    candidates=[r for r in rows if r['persistent'] and r['r6']>0 and r['t20'] is not None]
    candidates.sort(key=lambda r:(-r['t20'],r['code']))
    selected=[r['code'] for r in candidates[:10]]
    for i,r in enumerate(candidates):r['i2_rank']=i+1;r['selected']=r['code'] in selected
    return sorted(top), selected
def calendar():
    c=read(ROOT/'paper_calendar.json')
    if c['sessions']!=sorted(set(c['sessions'])):raise ValueError('invalid official calendar')
    return c
def expected_session(now,c):
    day=now.date().isoformat()
    if not c['valid_from']<=day<=c['valid_through']:raise ValueError('交易日曆需更新')
    candidates=[d for d in c['sessions'] if d<day or (d==day and now.time()>=dt_time(16,0))]
    if not candidates:raise ValueError('缺少可驗證交易日')
    return candidates[-1]
def date_and_fields(x,day,field):
    stamp=pd.Timestamp(day);roc=stamp.year-1911
    ok=str(x.get('date','')).replace('-','').replace('/','')==stamp.strftime('%Y%m%d') or re.search(rf'{roc}年0?{stamp.month}月0?{stamp.day}日',str(x.get('title',''))) is not None
    if x.get('stat')!='OK' or not ok:raise ValueError('日期不符或未發布 '+day)
    fields={'證券代號',field};ts=[t for t in x.get('tables',[]) if fields<=set(t.get('fields',[]))]
    if fields<=set(x.get('fields',[])):ts.append(x)
    if len(ts)!=1:raise ValueError('資料欄位不符')
    t=ts[0];ci=t['fields'].index('證券代號');vi=t['fields'].index(field);out={}
    for r in t['data']:
        try:
            c=str(r[ci]).strip();v=float(str(r[vi]).replace(',','').strip())
            if not math.isfinite(v) or v<0:continue
        except (ValueError,TypeError,IndexError):continue
        if c in out:raise ValueError('重複代碼')
        out[c]=v
    if len(out)<500 or out.get('2330',0)<=0:raise ValueError('市場資料不完整')
    if field=='發行股數' and not 10e9<out['2330']<40e9:raise ValueError('股數單位異常')
    return out
def daily_table(day,kind):
    field='成交股數' if kind=='volume' else '發行股數'
    p=CACHE/f'{day}_{kind}.json'
    if p.exists():
        try:return date_and_fields(read(p),day,field)
        except Exception:p.unlink()
    url='https://www.twse.com.tw/rwd/zh/'+('afterTrading/MI_INDEX' if kind=='volume' else 'fund/MI_QFIIS')
    params={'date':day.replace('-',''),'response':'json',('type' if kind=='volume' else 'selectType'):'ALLBUT0999'}
    for i in range(3):
        try:
            response=requests.get(url,params=params,headers={'User-Agent':'Mozilla/5.0'},timeout=35);response.raise_for_status()
            x=response.json();out=date_and_fields(x,day,field);write(p,x);time.sleep(1.3);return out
        except Exception as exc:err=str(exc);time.sleep(2*(i+1))
    raise ValueError(f'{day} {kind}: {err}')
def collect_turnover(days,codes):
    if len(days)!=20:raise ValueError('20個交易日不足')
    totals={c:0. for c in codes};missing=set()
    for day in days:
        v=daily_table(day,'volume');s=daily_table(day,'shares')
        for c in codes:
            if c not in v or s.get(c,0)<=0:missing.add(c)
            else:totals[c]+=100*v[c]/s[c]
    return {c:None if c in missing else totals[c] for c in codes}
def fetch_observation(now,cal):
    radar.OUT.mkdir(exist_ok=True);CACHE.mkdir(exist_ok=True)
    funds=radar.fund_snapshot();asof=funds['0050']['date']
    if asof!=expected_session(now,cal) or any(f['date']!=asof for f in funds.values()):raise ValueError(f'投信/預期行情日未同步：{asof} / {expected_session(now,cal)}')
    pool={c:(n,f) for f,info in funds.items() for c,n in info['members'].items()}
    if len(pool)!=150:raise ValueError('候選池非150檔')
    market=radar.dated_official_market(asof)
    frames,errors=radar.get_history(sorted(set(pool)|set(CORE)),asof)
    t=pd.Timestamp(asof);bench=frames['0050']
    base12=radar.baseline_date(bench,asof)
    cutoff6=t-pd.DateOffset(months=6);base6=bench.loc[:cutoff6].index[-1]
    if (cutoff6-base6).days>10:raise ValueError('缺少六個月共同交易日')
    # Core uses exact prior month-end, same as the historical D6-M backtest.
    def corebase(n):
        m=t.to_period('M')-n
        dates=bench.index[bench.index.to_period('M')==m]
        if len(dates)==0:raise ValueError('缺少核心月末基準')
        return dates[-1]
    cb12,cb6=corebase(12),corebase(6)
    corrected={};r12={};r6core={};rows=[];issues=[]
    for c in sorted(set(pool)|set(CORE)):
        try:
            if c in errors or c not in frames or c not in market:raise ValueError(errors.get(c,'缺少行情'))
            f=frames[c];event=radar.FACE_VALUE_EVENTS.get(c)
            if event and base12<=pd.Timestamp(event['last_old']) and t>=pd.Timestamp(event['first_new']):
                evidence=read(ROOT/'verified_corporate_actions.json')
                old,new,lookup,_=radar.verified_event_quotes(c,event,evidence,radar.dated_official_market)
                f,_=radar.verify_suspended_rows(f,event,c,lookup)
                f,_=radar.reconcile_face_value(f,event,old,new)
            score=radar.momentum(f,asof,base12,market[c][1])
            corrected[c]=f;r12[c]=score
            if c in CORE:
                for b in (cb6,cb12):
                    if b not in f.index:raise ValueError('缺少核心月末價')
                    if (f.loc[b:t,'Adj Close'].pct_change().abs()>.35).any():raise ValueError('核心還原價異常')
                r6core[c]=100*(f.at[t,'Adj Close']/f.at[cb6,'Adj Close']-1)
            if c in pool:
                if base6 not in f.index or len(f.loc[base6:t])<100:raise ValueError('六個月價格資料不足')
                rows.append({'code':c,'name':pool[c][0],'group':pool[c][1],'r12':score,'r6':100*(f.at[t,'Adj Close']/f.at[base6,'Adj Close']-1),'reason':None})
        except Exception as exc:
            issues.append(f'{c}: {exc}')
            if c in pool:rows.append({'code':c,'name':pool[c][0],'group':pool[c][1],'r12':None,'r6':None,'reason':'價格待核對'})
    # Do not call partial universe a complete rank or emit trade signals.
    if issues or len(corrected)<len(set(pool)|set(CORE)):
        raise ValueError(f'價格稽核未通過 {len(issues)} 檔：'+ '; '.join(issues[:8]))
    days=[str(d.date()) for d in bench.loc[:t].index[-20:]]
    turnover=collect_turnover(days,pool)
    for r in rows:
        r['t20']=turnover[r['code']]
        if r['t20'] is None:r['reason']='20日成交股數或發行股數缺漏；不列入I2'
    r12core={c:100*(corrected[c].at[t,'Adj Close']/corrected[c].at[cb12,'Adj Close']-1) for c in CORE}
    return asof,rows,r12core,r6core
def evaluate(state,asof,rows,r12,r6,now,cal):
    p=pd.Period(asof,'M');prior=state['month_ends'].get(str(p-1))
    top,chosen=rank_i2(rows,prior)
    current=state['model_core'];preview=core_decision(current,r12,r6)
    preview.update({'i2':chosen,'i2_cash_pct':50-5*len(chosen) if prior is not None else None,'prior_month':prior['date'] if prior else None,'prior_provenance':prior['provenance'] if prior else None})
    upcoming=[d for d in cal['sessions'] if d>asof]
    nextday=upcoming[0] if upcoming else None
    is_end=nextday is not None and nextday[:7]!=asof[:7]
    existing=next((s for s in state['signals'] if s['signal_date']==asof),None)
    reason='非月底，僅觀察，不換倉'
    if is_end:
        state['month_ends'].setdefault(str(p),{'date':asof,'top30':top,'eligible_count':sum(r['r6'] is not None for r in rows),'provenance':'observed'})
        before_open=now.date().isoformat()<nextday or (now.date().isoformat()==nextday and now.time()<dt_time(9,0))
        if existing:reason='月底訊號已存檔，不覆寫'
        elif prior is None:reason='缺少上月底快照；本月正式訊號暫停'
        elif not before_open:reason='開盤前未完成；不回填正式訊號'
        else:
            sig={'signal_date':asof,'next_session':nextday,'generated':now.isoformat(timespec='seconds'),**preview,
                 'status':'provisional_seed' if prior['provenance']=='historical_reconstruction' else 'observed_signal'}
            state['signals'].append(sig);state['model_core']=sig['target'];reason='月底模型目標已存檔；非實際成交'
    return state,preview,reason,is_end
def main():
    now=datetime.now(radar.TZ);state=read(STATE);old=read(DATA)
    try:
        cal=calendar();asof,rows,r12,r6=fetch_observation(now,cal)
        state,preview,reason,is_end=evaluate(state,asof,rows,r12,r6,now,cal)
        past={r['code']:r for r in old.get('rows',[])}
        for r in rows:
            prev=past.get(r['code'],{})
            r['rank_delta']=(prev['rank6']-r['rank6']) if old.get('asof')!=asof and prev.get('rank6') is not None and r['rank6'] is not None else prev.get('rank_delta') if old.get('asof')==asof else None
            for key in ('r6','r12','t20'):
                if r[key] is not None:r[key]=round(r[key],4)
        formal=state['signals'][-1] if state['signals'] else None
        data={'version':VERSION,'asof':asof,'generated':now.isoformat(timespec='seconds'),'status':'complete','message':reason,'coverage':len(rows),'expected':150,
          'current_core':state['model_core'],'preview':preview,'formal':formal,'signal_history':state['signals'],'rows':sorted(rows,key=lambda r:(r['rank6'] or 9999,r['code'])),
          'last_attempt':now.isoformat(timespec='seconds'),'is_month_end':is_end,'sources':['元大0050及0051持股代理池（非官方歷史成分名單）','Yahoo Finance 含息還原價格衍生指標','TWSE 日期查核成交股數及發行股數']}
        write(STATE,state);write(DATA,data)
        print(f'D6-M {asof}: {reason}; I2候選 {len(preview["i2"])}; 模型核心 {state["model_core"]}')
        return 0
    except Exception as exc:
        old.update(status='stale',message='本次資料未通過稽核，保留前次快照，不產生新訊號。',last_attempt=now.isoformat(timespec='seconds'))
        write(DATA,old);print(f"資料稽核失敗：{exc}");return 1
if __name__=='__main__':raise SystemExit(main())
