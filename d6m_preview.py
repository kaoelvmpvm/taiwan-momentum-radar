"""Explicit one-day-lagged turnover preview only; never changes model state."""
from datetime import datetime
import d6m_live as live

def preview_snapshot(snapshot, state, cal, now, turnover_fetch=live.collect_turnover):
    # Never overwrite a complete/current signal, stale snapshot or another session.
    if snapshot['status'] != 'partial_turnover' or snapshot['asof'] != live.expected_session(now, cal):
        return None
    asof=snapshot['asof']
    days=[d for d in cal['sessions'] if d<asof][-20:]
    if len(days)!=20:raise ValueError('昨日以前20個交易日不足')
    assert days[-1]<asof
    prior=state['month_ends'].get(str(live.pd.Period(asof,'M')-1))
    if prior is None:raise ValueError('缺少上月底動能快照，不能產生I2預覽')
    rows=snapshot['rows']
    if len(rows)!=150 or len({r['code'] for r in rows})!=150 or any(r['r6'] is None for r in rows):
        raise ValueError('動能候選池未完整稽核')
    turnover=turnover_fetch(days,[r['code'] for r in rows])
    count=0
    for row in rows:
        value=turnover[row['code']]
        row['t20']=value
        row['reason']=f'暫時預覽：20日換手率截至 {days[-1]}；今日換手率待更新' if value is not None else '昨日20日換手率缺漏；不列入預覽'
        if value is not None:count+=1
    if count==0:raise ValueError('無可查核換手率')
    # Ranking uses today's verified momentum and the exact prior-day turnover window.
    _, chosen=live.rank_i2(rows,prior)
    for row in rows:
        if row['t20'] is not None:row['t20']=round(row['t20'],4)
    snapshot['preview'].update(i2=chosen,i2_available=True,i2_cash_pct=50-5*len(chosen),
                               turnover_asof=days[-1],i2_provisional=True)
    snapshot['turnover_asof']=days[-1]
    snapshot['turnover_coverage']=count
    snapshot['message']=f'【暫時預覽】動能日期 {asof}；20日換手率截至 {days[-1]}（{count}/150檔）。I2為混合日期示範，非當日正式結果；今日資料備妥後自動替換。'
    snapshot['last_attempt']=now.isoformat(timespec='seconds')
    # Keep status partial_turnover and unchanged formal/current_core/signal_history.
    return snapshot

def main():
    snapshot=live.read(live.DATA)
    before=(snapshot['asof'],snapshot['last_attempt'],snapshot['status'])
    result=preview_snapshot(snapshot,live.read(live.STATE),live.calendar(),datetime.now(live.radar.TZ))
    if result is None:
        print('正式資料已備妥，或非當日部分快照；不寫入暫時預覽。')
        return
    latest=live.read(live.DATA)
    if (latest['asof'],latest['last_attempt'],latest['status'])!=before:
        raise ValueError('來源已更新，拒絕覆寫')
    live.write(live.DATA,result)
    print(result['message'])
    print('I2示範：'+', '.join(result['preview']['i2']))
if __name__=='__main__':main()
