"""Intersection experiment with fixed prior rules: P1 relative momentum persistence and T4 turnover.
Requires the prior turnover_variants_cashgap.py and its archived inputs in working directory.
No threshold search. P1C/T4C match I1's monthly total target stock exposure across all ten parent stocks. Common start 2020-08-03; all active strategies flat Aug–Dec 2025.
I1: final P1 top ten AND T4 top ten, 10% per matching stock, unused capital in cash.
I2: full P1 persistent candidate list AND positive six-month return AND verified 20-session
turnover, select top 10 by turnover. 10% per name. Retain/rebalance across months.
"""
from pathlib import Path
# Reuse verified input loader, snapshot/held quote repair, and identical simulation math.
base=Path(__file__).with_name('turnover_variants_cashgap.py')
src=base.read_text()
exec(compile(src[:src.index("keys=['T1','T2','T3'")],str(base),'exec'))
START=pd.Timestamp('2020-08-03')
OUT=Path('intersection-output');OUT.mkdir(exist_ok=True)
anoms={c:cl[c].dropna().pct_change().abs().loc[lambda x:x>.35] for c in cl}
first={c:cl[c].first_valid_index() for c in cl}
def momentum_plans():
 result={k:{} for k in ('P1','I1','I2')}
 records=[];prev=None
 for x in sorted(rows,key=lambda x:x['signal']):
  p=pd.Timestamp(x['signal']);d=dates[dates.get_loc(p)+1]
  b12=dates[dates.searchsorted(p-pd.DateOffset(years=1),side='right')-1]
  b6=dates[dates.searchsorted(p-pd.DateOffset(months=6),side='right')-1]
  # Original P1 begins with the original twelve-month eligibility screen.
  pool=sorted(set(x['rank'])|set(x['excluded']))
  assert len(pool)==x['universe']
  rank6={}
  for c in pool:
   if c not in cl or first[c] is None or first[c]>b12:continue
   if pd.isna(cl.at[b12,c]) or pd.isna(cl.at[p,c]) or cl.loc[b12:p,c].count()<200 or len(anoms[c].loc[b12:p]):continue
   if pd.isna(cl.at[b6,c]) or cl.loc[b6:p,c].count()<100 or len(anoms[c].loc[b6:p]):continue
   rank6[c]=float(cl.at[p,c]/cl.at[b6,c]-1)
  rank=sorted(rank6,key=lambda c:(-rank6[c],c))
  top=rank[:math.ceil(.3*len(rank))]
  if prev is not None and START<=d<=END and not PAUSE<d<RESUME:
   persistent=[c for c in top if c in prev]
   p1=persistent[:10]
   t4=list(plans['T4'][d])
   a=[c for c in t4 if c in p1]
   b=[c for c in x['rank'] if c in persistent and rank6[c]>0][:10]
   for k,names in [('P1',p1),('I1',a),('I2',b)]:result[k][d]={c:.1 for c in names}
   records.append({'signal':str(p.date()),'buy':str(d.date()),'pool':len(pool),'rank6_count':len(rank),'persistent_pool':len(persistent),'P1':p1,'T4':t4,'I1':a,'I2':b})
  prev=set(top)
 assert len(records)==70
 return result,records
newplans,records=momentum_plans();plans.update(newplans)
# Exposure-matched controls: distribute I1's aggregate allocation across the
# entire parent portfolio. Target exposure is identical at each rebalance.
for key,parent in [('P1C','P1'),('T4C','T4')]:
 plans[key]={}
 for d,t in plans['I1'].items():
  src=plans[parent][d];total=sum(t.values())
  plans[key][d]={c:total/len(src) for c in src} if total and src else {}

# Check all new execution/holding gaps, use quote cache. Do not invent trade price or silently defer.
gaps=defaultdict(set);boundaries=set()
for k,pl in newplans.items():
 prior_target={}
 for d,target in pl.items():
  e=dates[dates.to_period('M')==d.to_period('M')][-1]
  for c in set(target)|set(prior_target):
   boundaries.add((d,c))
   if pd.isna(op.at[d,c]):gaps[c].add(d)
  if e in (PAUSE,END):
   for c in target:boundaries.add((e,c))
  for c in target:gaps[c].update(cl.loc[d:e,c].index[cl.loc[d:e,c].isna()])
  prior_target=target if e!=PAUSE else {}
print('INTERSECTION_GAPS',{c:[str(d.date()) for d in sorted(ds)] for c,ds in gaps.items() if ds},flush=True)
print('COUNT', {k:{'min':min(len(r[k]) for r in records),'max':max(len(r[k]) for r in records),'mean':np.mean([len(r[k]) for r in records])} for k in ['P1','T4','I1','I2']},flush=True)
new_audit={'repaired':[],'no_quote_marks':[],'unresolved':[]}
for c,ds in sorted(gaps.items()):
 for d in sorted(ds):
  q=quote(d)
  if q is None:new_audit['unresolved'].append([str(d.date()),c,'missing_official_day']);continue
  v=q.get(c)
  if not tradeable(v):
   if (d,c) in boundaries:new_audit['unresolved'].append([str(d.date()),c,'execution_no_trade'])
   else:new_audit['no_quote_marks'].append([str(d.date()),c,'no_row' if v is None else 'zero_or_missing_quote'])
   continue
  valid=cl[c].notna() & raw[c].notna() & ~cl.index.isin(ds)
  prior=cl.index[valid & (cl.index<d)];after=cl.index[valid & (cl.index>d)]
  if len(prior)==0 or len(after)==0:new_audit['unresolved'].append([str(d.date()),c,'missing_anchor']);continue
  a,b=prior[-1],after[0];ss=[];rs=[]
  for p in (a,b):
   pq=quote(p);pv=pq.get(c) if pq else None
   if not tradeable(pv):break
   ss.append(float(cl.at[p,c]/pv[1]));rs.append(float(raw.at[p,c]/pv[1]))
  if len(ss)!=2 or abs(ss[0]/ss[1]-1)>.0001 or abs(rs[0]/rs[1]-1)>.0001:
   new_audit['unresolved'].append([str(d.date()),c,'anchor_basis_mismatch',ss,rs]);continue
  if pd.isna(op.at[d,c]):op.at[d,c]=v[0]*ss[0]
  if pd.isna(cl.at[d,c]):cl.at[d,c]=v[1]*ss[0]
  if pd.isna(raw.at[d,c]):raw.at[d,c]=v[1]*rs[0]
  new_audit['repaired'].append([str(d.date()),c,v[0],v[1],ss[0]])
print('INTERSECTION_PRICE_AUDIT',new_audit,flush=True)
assert not new_audit['unresolved']
# Rebuild candidate signal plans after mark repairs and verify no selected stock changes.
anoms={c:cl[c].dropna().pct_change().abs().loc[lambda x:x>.35] for c in cl}
assert momentum_plans()[0]==newplans
# run() uses the T4 retain/rebalance rule for all four stock portfolios.
# Alias the current strategy's plan as T4 temporarily, keeping exact engine and fee model.
def simulate(k,a,b,m):
 if k in ('P1','I1','I2','P1C','T4C'):
  old=plans['T4']
  try:
   plans['T4']=plans[k]
   return run('T4',a,b,m)
  finally:plans['T4']=old
 return run(k,a,b,m)
keys=['P1','T4','I1','I2','P1C','T4C','S0cash','S0']
report={'period':[str(START.date()),str(END.date())],'cash_gap':['2025-08-01','2025-12-31'],'active_months':70,'new_price_audit':new_audit,'common_snapshot_repair_count':len(snapshot_audit['repaired']),'counts':{k:{'min':min(len(r[k]) for r in records),'max':max(len(r[k]) for r in records),'mean':float(np.mean([len(r[k]) for r in records])),'zero_months':sum(len(r[k])==0 for r in records),'ten_months':sum(len(r[k])==10 for r in records)} for k in ('P1','T4','I1','I2')}}
daily={};orders={}
for label,a,b,m in [('full',START,END,1),('pre',START,PAUSE,1),('2026',dates[dates.searchsorted(RESUME)],END,1),('gross',START,END,0),('double_cost',START,END,2)]:
 report[label]={}
 for k in keys:
  v,n,t=simulate(k,a,b,m);report[label][k]=v
  if label=='full':daily[k]=n;orders[k]=t
# All simulated transactions have a dated official quote; ratio audit of observed open/close.
for_audit={}
for k,ts in orders.items():
 count=Counter();issues=[]
 for t in ts:
  d=t['date'];p=pd.Timestamp(d);q=quote(d)
  for c,v in t['moves'].items():
   if abs(v)<=.000001:continue
   if not q:count['no_cached_day']+=1;continue
   if not tradeable(q.get(c)):count['no_official_quote']+=1;issues.append([d,c,'no_quote']);continue
   if pd.isna(op.at[p,c]) or pd.isna(cl.at[p,c]):count['vendor_missing_ratio']+=1;continue
   o,z,_=q[c];res=float((op.at[p,c]/cl.at[p,c])/(o/z)-1)
   if abs(res)>.001:count['ratio_mismatch']+=1;issues.append([d,c,res])
   else:count['ratio_match']+=1
 for_audit[k]={'counts':dict(count),'issues':issues}
report['execution_audit']=for_audit
(OUT/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
(OUT/'signals.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
(OUT/'orders.json').write_text(json.dumps(orders,ensure_ascii=False,indent=2,allow_nan=False))
pd.DataFrame(daily).to_csv(OUT/'daily_nav.csv')
for label in ['full','pre','2026','gross','double_cost']:
 print(label)
 for k,v in report[label].items():print(k,round(v['terminal']),round(v['cagr']*100,2),round(v['mdd']*100,2),round(v['sharpe'],3),round(v['cost']),round(v['exposure']*100,1),v['orders'])
print('EXECUTION_AUDIT',for_audit)
