"""T1–T4 preregistered variants. Skip Aug–Dec 2025 uniformly; no imputation of trade prices.
Inputs: original GitHub workflow zip archives, price.zip, repaired.zip, prior.zip,
reconcile.zip and turn{2020,2021,2022,2024,2025,2026}.zip. Research only.
GitHub artifact IDs: price 15074456981, repaired 15089733025, prior 15074729327,
reconcile 15070393151, 2020 15084351328, 2021 15084683845, 2022 15085343916,
2024 15087028548, 2025 15088330970, 2026 15088342772.
Historical roster is provisional, vendor-adjusted fractional units and simulated
opening/closing fills; no minimum fees, board-lot constraints, or limit-queue simulation.
"""
import gzip,hashlib,json,math,zipfile
from pathlib import Path
from collections import defaultdict,Counter
import numpy as np
import pandas as pd
ROOT=Path('.')
OUT=ROOT/'turnover-variants-output'
OUT.mkdir(exist_ok=True)
START=pd.Timestamp('2020-07-01');END=pd.Timestamp('2026-10-30')
PAUSE=pd.Timestamp('2025-07-31');RESUME=pd.Timestamp('2026-01-01')
INITIAL=100000.
price=zipfile.ZipFile(ROOT/'price.zip');prior=zipfile.ZipFile(ROOT/'prior.zip')
archives={y:zipfile.ZipFile(ROOT/('repaired.zip' if y==2023 else f'turn{y}.zip')) for y in range(2020,2027)}
def matrix(n):return pd.read_csv(price.open(n),compression='gzip',index_col=0,parse_dates=True)
op=matrix('adjusted_open.csv.gz');cl=matrix('adjusted_close.csv.gz');raw=matrix('raw_close.csv.gz')
dates=cl['0050'].dropna().index
assert START in dates and END in dates and PAUSE in dates
rows=[];quote_index={};qcache={}
for y,z in archives.items():
 prefix='2023/' if y==2023 else ''
 r=json.loads(z.read(prefix+'signals.json'));a=json.loads(z.read(prefix+'audit.json'))
 assert a['verified_days']==a['expected_days'] and not a['errors']
 rows+=r
 for d,h in a['hashes'].items():
  p=prefix+f'raw/{d}_volume.json.gz'
  assert p in z.namelist()
  quote_index[d]=(z,p,h['volume'])
assert len(rows)==77 and len({r['signal'] for r in rows})==77
# Additional cached official execution/price quotes, used only when no daily turnover cache.
for z,prefix in [(price,'official/'),(prior,'official_execution/')]:
 for p in z.namelist():
  if p.startswith(prefix) and p.endswith('.json.gz'):
   d=p.split('/')[-1][:-8]
   quote_index.setdefault(d,(z,p,None))
def num(x):
 try:return float(str(x).replace(',',''))
 except (ValueError,TypeError):return None
def quote(d):
 d=str(pd.Timestamp(d).date())
 if d in qcache:return qcache[d]
 if d not in quote_index:return None
 z,p,h=quote_index[d];data=gzip.decompress(z.read(p));x=json.loads(data)
 assert x['stat']=='OK' and str(x['date']).replace('-','')==d.replace('-','')
 if h:assert hashlib.sha256(data).hexdigest()==h
 f={'證券代號','開盤價','收盤價','成交股數'}
 t=[t for t in x['tables'] if f<=set(t.get('fields',[]))]
 assert len(t)==1
 t=t[0];rs={}
 for row in t['data']:
  f=dict(zip(t['fields'],row));rs[str(f['證券代號']).strip()]=(num(f['開盤價']),num(f['收盤價']),num(f['成交股數']))
 assert len(rs)>500
 qcache[d]=rs
 return rs
def tradeable(v):return v is not None and all(x is not None and x>0 for x in v)
# Independently confirmed 2024-03-29 vendor close snapshot anomaly.
# Repair prior to building price-based signals; validate official open scale and two
# adjacent-day close/raw factors. This also corrects the daily NAV marks for T4.
snapshot_audit={'repaired':[],'unresolved':[]}
day=pd.Timestamp('2024-03-29');a=pd.Timestamp('2024-03-28');b=pd.Timestamp('2024-04-01')
qa,qd,qb=quote(a),quote(day),quote(b)
for c in cl.columns:
 v=qd.get(c);va=qa.get(c);vb=qb.get(c)
 if not tradeable(v) or pd.isna(op.at[day,c]) or pd.isna(cl.at[day,c]):continue
 ratio=float((op.at[day,c]/cl.at[day,c])/(v[0]/v[1])-1)
 if abs(ratio)<=.0001:continue
 if not tradeable(va) or not tradeable(vb) or any(pd.isna(x) for x in (cl.at[a,c],cl.at[b,c],raw.at[a,c],raw.at[b,c])):
  snapshot_audit['unresolved'].append([c,ratio,'anchor_not_available']);continue
 sa=cl.at[a,c]/va[1];sb=cl.at[b,c]/vb[1];ra=raw.at[a,c]/va[1];rb=raw.at[b,c]/vb[1]
 if abs(sa/sb-1)>.0001 or abs(ra/rb-1)>.0001 or abs((op.at[day,c]/v[0])/sa-1)>.0001:
  snapshot_audit['unresolved'].append([c,ratio,'anchor_not_consistent']);continue
 old=float(cl.at[day,c]);cl.at[day,c]=v[1]*sa;raw.at[day,c]=v[1]*ra
 snapshot_audit['repaired'].append([c,old,float(cl.at[day,c]),ratio])
print('SNAPSHOT_AUDIT',len(snapshot_audit['repaired']),snapshot_audit['unresolved'],flush=True)
def make_plans():
 plans={k:{} for k in ['T1','T2','T3','T4','T0']}
 signals=[]
 for x in sorted(rows,key=lambda x:x['signal']):
  p=pd.Timestamp(x['signal']);i=dates.get_loc(p);d=dates[i+1]
  if not START<=d<=END or PAUSE<d<RESUME:continue
  assert not x['source_missing'] and len(x['window'])==20 and len(x['rank'])>=10
  b20=dates[i-20];b6=dates[dates.searchsorted(p-pd.DateOffset(months=6),side='right')-1]
  # Filter the entire eligible candidate pool, then rank by turnover (not momentum magnitude).
  def qualified(b):
   return [c for c in x['rank'] if c in cl and pd.notna(cl.at[b,c]) and pd.notna(cl.at[p,c]) and cl.at[p,c]>cl.at[b,c]][:10]
  n={'T1':x['rank'][:10],'T2':qualified(b20),'T3':qualified(b6),'T4':qualified(b6),'T0':['0050']}
  for k,names in n.items():
   plans[k][d]={c:(1. if k=='T0' else .1) for c in names}
  signals.append({'signal':str(p.date()),'buy':str(d.date()),'base20':str(b20.date()),'base6m':str(b6.date()),'targets':n})
 assert len(signals)==71
 return plans,signals
plans,signals=make_plans()
# The one unresolved 2324 snapshot is below the selected rank in every affected
# signal, and not held during the snapshot date. It cannot alter selected names.
assert len(snapshot_audit['unresolved'])==1 and snapshot_audit['unresolved'][0][0]=='2324'
row_by_date={r['signal']:r for r in rows}
for rec in signals:
 if '2024-03-29' in (rec['signal'],rec['base20'],rec['base6m']):
  rank=row_by_date[rec['signal']]['rank']
  if '2324' in rank:
   for k in ('T1','T2','T3','T4'):
    assert rank.index('2324')>max(rank.index(c) for c in rec['targets'][k])
 if rec['buy']=='2024-03-01':
  assert all('2324' not in rec['targets'][k] for k in ('T1','T2','T3','T4'))

# Detect missing held daily marks and execution quotes before simulation.
gaps=defaultdict(set);boundaries=set()
for k,pl in plans.items():
 previous={}
 for d,target in pl.items():
  e=dates[dates.to_period('M')==d.to_period('M')][-1]
  for c in set(target)|(set(previous) if k=='T4' else set()):
   boundaries.add((d,c))
   if pd.isna(op.at[d,c]):gaps[c].add(d)
  if k!='T4' or e in (PAUSE,END):
   for c in target:
    boundaries.add((e,c))
  for c in target:
   gaps[c].update(cl.loc[d:e,c].index[cl.loc[d:e,c].isna()])
  previous=target if e!=PAUSE else {}
audit={'repaired':[],'no_official_trade_marked_at_last_price':[],'unresolved':[]}
for c,ds in sorted(gaps.items()):
 for d in sorted(ds):
  q=quote(d)
  if q is None:
   audit['unresolved'].append([str(d.date()),c,'missing_official_day']);continue
  v=q.get(c)
  if not tradeable(v):
   if (d,c) in boundaries:audit['unresolved'].append([str(d.date()),c,'execution_no_trade'])
   else:audit['no_official_trade_marked_at_last_price'].append([str(d.date()),c,'no_row' if v is None else 'zero_or_missing_quote'])
   continue
  valid=cl[c].notna() & raw[c].notna() & ~cl.index.isin(ds)
  prev=cl.index[valid & (cl.index<d)];nxt=cl.index[valid & (cl.index>d)]
  if not len(prev) or not len(nxt):audit['unresolved'].append([str(d.date()),c,'no_anchor']);continue
  a,b=prev[-1],nxt[0];ss=[];rs=[]
  for p in (a,b):
   qq=quote(p);pp=qq.get(c) if qq else None
   if not tradeable(pp):break
   ss.append(float(cl.at[p,c]/pp[1]));rs.append(float(raw.at[p,c]/pp[1]))
  matching=len(ss)==2 and abs(ss[0]/ss[1]-1)<=.0001 and abs(rs[0]/rs[1]-1)<=.0001
  method='two_anchor'
  if not matching and c=='2603' and str(d.date())=='2021-08-17' and str(a.date())=='2021-08-16' and str(b.date())=='2021-08-18' and len(ss)==2 and abs(rs[0]/rs[1]-1)<=.0001:
   x=json.loads(zipfile.ZipFile(ROOT/'reconcile.zip').read('raw/ex_2021.json'))
   es=[dict(zip(x['fields'],r)) for r in x['data']]
   event=[e for e in es if e['股票代號']==c and e['資料日期']=='110年08月18日']
   assert x['stat']=='OK' and len(event)==1 and not any(e['股票代號']==c and e['資料日期']=='110年08月17日' for e in es)
   before,ref=[num(event[0][k]) for k in ('除權息前收盤價','除權息參考價')]
   matching=abs(before-v[1])<.011 and abs((ss[0]/ss[1])/(ref/before)-1)<.001
   method='verified_pre_ex_dividend'
  if not matching:audit['unresolved'].append([str(d.date()),c,'anchor_basis_mismatch',ss,rs]);continue
  if pd.isna(op.at[d,c]):op.at[d,c]=v[0]*ss[0]
  if pd.isna(cl.at[d,c]):cl.at[d,c]=v[1]*ss[0]
  if pd.isna(raw.at[d,c]):raw.at[d,c]=v[1]*rs[0]
  audit['repaired'].append([str(d.date()),c,v[0],v[1],ss[0],method])
assert not audit['unresolved'],audit
# Price repairs are only in-month marks; verify original rankings unchanged.
assert make_plans()[1]==signals
def rebalance(hold,cash,target,prices,mult):
 assert all(np.isfinite(prices[c]) and prices[c]>0 for c in set(hold)|set(target))
 held={c:u*prices[c] for c,u in hold.items()};nav=cash+sum(held.values());univ=set(held)|set(target)
 def fee(c,v):return abs(v)*mult*(.001425+.0005+(0 if v>=0 else .001 if c=='0050' else .003))
 def moves(a):return {c:a*target.get(c,0.)-held.get(c,0.) for c in sorted(univ)}
 lo,hi=0.,nav
 for _ in range(48):
  mid=(lo+hi)/2
  if mid+sum(fee(c,v) for c,v in moves(mid).items())>nav:hi=mid
  else:lo=mid
 a=(lo+hi)/2;m=moves(a);fees=sum(fee(c,v) for c,v in m.items());cashleft=cash-sum(m.values())-fees
 assert cashleft>=-max(.00001,nav*1e-10)
 return {c:a*w/prices[c] for c,w in target.items() if w>0},max(0.,cashleft),fees,m,nav
def summary(s,fees,order_count,notional,stale,exposure):
 r=s.pct_change();r.iloc[0]=s.iloc[0]/INITIAL-1
 peak=pd.concat([pd.Series([INITIAL],index=[s.index[0]-pd.Timedelta(days=1)]),s]).cummax().reindex(s.index)
 mdd=float((s/peak-1).min());vol=float(r.std()*math.sqrt(252));cagr=float((s.iloc[-1]/INITIAL)**(365.2425/(s.index[-1]-s.index[0]).days)-1)
 y=s.resample('YE').last();annual=y/y.shift(fill_value=INITIAL)-1
 return {'terminal':float(s.iloc[-1]),'cagr':cagr,'mdd':mdd,'sharpe':float(r.mean()*252/vol),'vol':vol,'calmar':cagr/abs(mdd),'cost':fees,'orders':order_count,'notional':notional,'stale_marks':stale,'exposure':exposure,'annual':{str(d.year):float(v) for d,v in annual.items()}}
def run(k,start,end,mult=1):
 cash=INITIAL;hold={};nav={};fees=0.;orders=[];stale=0;exp=[];notional=0.
 marks=cl.ffill();last={c:cl[c].last_valid_index() for c in cl}
 active=dates[(dates>=start)&(dates<=end)]
 for d in active:
  for c in hold:assert d<=last[c]
  paused=PAUSE<d<RESUME
  if k=='S0':
   target={'0050':1.} if d==active[0] else None
  elif k=='S0cash':
   resume_day=active[active.searchsorted(RESUME)] if RESUME<=active[-1] else None
   target={'0050':1.} if (d==active[0] or d==resume_day) and not paused and not hold else None
  else:target=plans[k].get(d)
  def transact(target,when):
   nonlocal hold,cash,fees,notional
   prs=(op if when=='open' else cl).loc[d,list(set(hold)|set(target))].to_dict()
   hold,cash,f,m,n=rebalance(hold,cash,target,prs,mult);fees+=f
   notional+=sum(abs(v) for v in m.values())/n
   orders.append({'date':str(d.date()),'when':when,'cost':f,'moves':m,'target':target})
  if target is not None:transact(target,'open')
  if k not in ('S0','S0cash','T4') and hold and d==dates[dates.to_period('M')==d.to_period('M')][-1]:transact({},'close')
  if k in ('T4','S0cash') and hold and d in (PAUSE,end):transact({},'close')
  if k!='S0' and paused:assert not hold
  stale+=sum(pd.isna(cl.at[d,c]) for c in hold)
  value=sum(u*marks.at[d,c] for c,u in hold.items());nav[d]=cash+value;exp.append(value/nav[d])
 s=pd.Series(nav)
 return summary(s,fees,sum(sum(abs(v)>.000001 for v in t['moves'].values()) for t in orders),notional,stale,float(np.mean(exp))),s,orders
keys=['T1','T2','T3','T4','T0','S0cash','S0']
report={'period':[str(START.date()),str(END.date())],'cash_gap':['2025-08-01','2025-12-31'],'active_months':71,'price_audit':audit,'snapshot_audit':snapshot_audit,'screens':{'T1':'20-session cumulative turnover ranking top 10','T2':'same turnover rank after positive 20-session adjusted return filter','T3':'same turnover rank after positive six-calendar-month adjusted return filter','T4':'same targets as T3, monthly rebalance net positions at first open; liquidate at cash gap and final sample close'}}
full_nav={};all_orders={}
for label,a,b,m in [('full',START,END,1),('gross',START,END,0),('double_cost',START,END,2),('pre',START,PAUSE,1),('2026',dates[dates.searchsorted(RESUME)],END,1)]:
 report[label]={}
 for k in keys:
  stats,nav,orders=run(k,a,b,m);report[label][k]=stats
  if label=='full':full_nav[k]=nav;all_orders[k]=orders
# Test T1 against previously verified pre-gap and post-gap outputs to catch implementation drift.
initial=json.loads(archives[2023].read('summary.json'))
assert abs(report['pre']['T1']['terminal']/218871.7449763164-1)<.03  # one verified snapshot correction only
assert abs(report['2026']['T1']['terminal']-initial['stress2026']['T1']['terminal'])<.01
# On available official snapshots, check vendor opening/closing price ratio for executions.
execution_audit={}
for k,orders in all_orders.items():
 ct=Counter();issues=[]
 for t in orders:
  d=t['date'];p=pd.Timestamp(d);q=quote(d)
  for c,v in t['moves'].items():
   if abs(v)<=.000001:continue
   if not q:ct['date_not_in_cached_official']+=1;continue
   if not tradeable(q.get(c)):ct['official_no_quote']+=1;issues.append([d,c,'no_quote']);continue
   if pd.isna(op.at[p,c]) or pd.isna(cl.at[p,c]):ct['vendor_missing_ratio']+=1;continue
   o,z,_=q[c];res=float((op.at[p,c]/cl.at[p,c])/(o/z)-1)
   if abs(res)>.001:ct['ratio_mismatch']+=1;issues.append([d,c,res])
   else:ct['ratio_match']+=1
 execution_audit[k]={'counts':dict(ct),'issues':issues}
report['execution_audit']=execution_audit
pairs=[(a,b) for a,b in zip(signals[:-1],signals[1:]) if pd.Period(b['buy'],freq='M')-pd.Period(a['buy'],freq='M')==pd.offsets.MonthEnd(1)]
report['target_retention']={k:sum(len(set(a['targets'][k])&set(b['targets'][k])) for a,b in pairs)/len(pairs) for k in ('T1','T2','T3','T4')}
(OUT/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
(OUT/'signals.json').write_text(json.dumps(signals,ensure_ascii=False,indent=2))
(OUT/'orders.json').write_text(json.dumps(all_orders,ensure_ascii=False,indent=2,allow_nan=False))
pd.DataFrame(full_nav).to_csv(OUT/'daily_nav.csv')
print('PRICE_AUDIT',len(audit['repaired']),len(audit['no_official_trade_marked_at_last_price']))
for label in ('full','gross','double_cost','pre','2026'):
 print(label)
 for k,v in report[label].items():print(k,round(v['terminal']),round(v['cagr']*100,2),round(v['mdd']*100,2),round(v['sharpe'],3),round(v['cost']),v['orders'],v['stale_marks'])
print('EXECUTION_AUDIT',{k:v['counts'] for k,v in execution_audit.items()})
