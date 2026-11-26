import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock
import d6m_live as live
from datetime import datetime
from zoneinfo import ZoneInfo
from d6m_live import core_decision, rank_i2, evaluate, expected_session, date_and_fields

TZ=ZoneInfo('Asia/Taipei')
BACK={'1216':10,'2881':15,'2412':5,'0050':8}
RNEG={'2330':-1,'0050':5}
RPOS={'2330':15,'0050':5}
CAL={'valid_from':'2026-11-25','valid_through':'2026-12-31','sessions':['2026-11-25','2026-11-26','2026-11-27','2026-11-30','2026-12-01','2026-12-31']}
def rows(n=150):
    return [{'code':str(1000+i),'r6':n-i,'t20':i+1} for i in range(n)]
def state(prior=True):
    return {'model_core':'2330','month_ends':{'2026-10':{'date':'2026-10-30','top30':[str(1000+i) for i in range(45)],'provenance':'observed'}} if prior else {},'signals':[]}
def dt(day,h): return datetime.fromisoformat(day+'T'+h+':00').replace(tzinfo=TZ)
class TestD6M(unittest.TestCase):
    def test_core_switch_to_strongest(self):
        self.assertEqual(core_decision('2330',RNEG,BACK)['target'],'2881')
    def test_sticky_backup(self):
        self.assertEqual(core_decision('2412',RNEG,BACK)['target'],'2412')
    def test_recovery_needs_positive_absolute_and_relative(self):
        self.assertEqual(core_decision('2881',{'2330':-1,'0050':-10},BACK)['target'],'2881')
        self.assertEqual(core_decision('2881',RPOS,BACK)['target'],'2330')
    def test_relative_is_ratio_not_difference(self):
        self.assertAlmostEqual(core_decision('2330',{'2330':10,'0050':20},BACK)['relative_12m'],-100/12)
    def test_rank_persistence_and_turnover(self):
        r=rows(); top, chosen=rank_i2(r,state()['month_ends']['2026-10'])
        self.assertEqual(len(top),45)
        self.assertEqual(chosen,[str(i) for i in range(1044,1034,-1)])
        self.assertEqual(sum(x['selected'] for x in r),10)
        self.assertFalse(r[45]['persistent'])
    def test_missing_turnover_is_excluded(self):
        r=rows();r[44]['t20']=None;top,chosen=rank_i2(r,state()['month_ends']['2026-10'])
        self.assertNotIn('1044',chosen);self.assertIn('1034',chosen)
    def test_missing_prior_no_signal(self):
        s,p,_,isend=evaluate(state(False),'2026-11-30',rows(),RNEG,BACK,dt('2026-11-30','18:15'),CAL)
        self.assertTrue(isend);self.assertEqual(s['signals'],[]);self.assertIsNone(p['i2_cash_pct'])
    def test_intra_month_never_switch(self):
        s,p,_,isend=evaluate(state(),'2026-11-27',rows(),RNEG,BACK,dt('2026-11-27','18:15'),CAL)
        self.assertFalse(isend);self.assertEqual(s['model_core'],'2330');self.assertEqual(p['target'],'2881');self.assertFalse(s['signals'])
    def test_month_end_idempotent(self):
        s,p,_,_=evaluate(state(),'2026-11-30',rows(),RNEG,BACK,dt('2026-11-30','18:15'),CAL)
        self.assertEqual(s['model_core'],'2881');self.assertEqual(len(s['signals']),1)
        self.assertEqual(s['signals'][0]['next_session'],'2026-12-01')
        self.assertEqual(s['signals'][0]['i2_cash_pct'],0)
        old=s['signals'][0].copy()
        s,_,_,_=evaluate(s,'2026-11-30',rows(),RPOS,BACK,dt('2026-11-30','20:00'),CAL)
        self.assertEqual(s['signals'],[old])
    def test_partial_turnover_no_formal_signal(self):
        r=rows();
        for row in r:row['t20']=None
        s,p,reason,_=evaluate(state(),'2026-11-30',r,RNEG,BACK,dt('2026-11-30','18:15'),CAL,False)
        self.assertEqual(s['model_core'],'2330');self.assertFalse(s['signals']);self.assertIn('2026-11',s['month_ends']);self.assertIsNone(p['i2_cash_pct']);self.assertFalse(p['i2_available'])
        s,p,_,_=evaluate(s,'2026-11-30',rows(),RNEG,BACK,dt('2026-11-30','21:15'),CAL,True)
        self.assertEqual(len(s['signals']),1);self.assertTrue(p['i2_available'])
    def test_no_late_signal(self):
        s,_,_,_=evaluate(state(),'2026-11-30',rows(),RNEG,BACK,dt('2026-12-01','09:00'),CAL)
        self.assertFalse(s['signals']);self.assertEqual(s['model_core'],'2330')
    def test_calendar_after_close(self):
        self.assertEqual(expected_session(dt('2026-11-27','18:15'),CAL),'2026-11-27')
        self.assertEqual(expected_session(dt('2026-11-27','11:00'),CAL),'2026-11-26')
    def test_reject_wrong_date(self):
        with self.assertRaises(ValueError):date_and_fields({'stat':'OK','date':'20261126'},'2026-11-27','成交股數')
    def test_missing_candidates_cash(self):
        r=rows(); prior={'date':'2026-10-30','top30':['1000','1001'],'provenance':'observed'}
        s=state();s['month_ends']['2026-10']=prior
        s,p,_,_=evaluate(s,'2026-11-30',r,RNEG,BACK,dt('2026-11-30','18:15'),CAL)
        self.assertEqual(p['i2_cash_pct'],40);self.assertEqual(len(p['i2']),2)
class TestOfficialTurnover(unittest.TestCase):
    def report(self,day='115年11月26日'):
        return ('"'+day+' 報表"\n"證券代號","發行股數"\n'+'\n'.join(f'"{c}","100000000"' for c in range(1000,1500))+'\n"2330","25,932,000,000"\n').encode('utf-8-sig')
    def test_csv_exact_day(self):
        x=live.parse_report_csv(self.report(),'2026-11-26','發行股數')
        self.assertEqual(live.date_and_fields(x,'2026-11-26','發行股數')['2330'],25932000000)
    def test_csv_wrong_day(self):
        with self.assertRaises(ValueError):live.parse_report_csv(self.report('115年11月25日'),'2026-11-26','發行股數')
    def test_contradictory_report_dates(self):
        x=live.parse_report_csv(self.report(),'2026-11-26','發行股數')
        x['date']='20261125'
        with self.assertRaises(ValueError):live.date_and_fields(x,'2026-11-26','發行股數')
    def test_json_fallback_csv(self):
        response_json=Mock();response_json.json.return_value={'stat':'OK','date':'20261125'}
        response_csv=Mock();response_csv.content=self.report()
        with TemporaryDirectory() as tmp, patch.object(live,'CACHE',Path(tmp)), patch.object(live.requests,'get',side_effect=[response_json,response_csv],create=True) as get, patch.object(live.time,'sleep'):
            out=live.daily_table('2026-11-26','shares')
            self.assertEqual(out['2330'],25932000000)
            self.assertEqual(get.call_args_list[1].kwargs['params']['response'],'csv')
            self.assertEqual(live.daily_table('2026-11-26','shares'),out)
            self.assertEqual(get.call_count,2)
    def test_latest_day_missing_prevents_turnover_calculation(self):
        days=[str(i) for i in range(20)]
        def report(day,kind):
            if day=='19' and kind=='shares':raise ValueError('unpublished')
            return {'2330':100}
        with patch.object(live,'daily_table',side_effect=report) as get:
            with self.assertRaisesRegex(ValueError,'19/20'):live.collect_turnover(days,['2330'])
            self.assertEqual(get.call_count,40)
            self.assertEqual(get.call_args_list[1].args,('19','shares'))
            self.assertEqual(get.call_args_list[-1].args,('0','shares'))
    def test_duplicate_sessions_rejected(self):
        with self.assertRaises(ValueError):live.collect_turnover(['2026-11-26']*20,['2330'])
    def test_public_response_diagnostic(self):
        r=Mock(status_code=200,headers={'Content-Type':'application/json'},content=b'{"stat":"not published"}')
        self.assertIn('not published',live.response_detail(r))
        self.assertIn('HTTP 200',live.response_detail(r))
    def test_no_cross_date_turnover_fill(self):
        days=[str(i) for i in range(20)]
        def report(day,kind):
            return {'2330':100,'1216':100} if kind=='volume' else ({'2330':10000} if day=='19' else {'2330':10000,'1216':10000})
        with patch.object(live,'daily_table',side_effect=report):
            result=live.collect_turnover(days,['2330','1216'])
            self.assertEqual(result['2330'],20)
            self.assertIsNone(result['1216'])
class TestVerifiedPriceRecovery(unittest.TestCase):
    def setUp(self):
        from datetime import timedelta
        self.day='2026-11-26';self.now=dt('2026-11-27','06:30')
        dates=[(datetime(2026,11,7)+timedelta(days=i)).date().isoformat() for i in range(20)]
        self.cal={'valid_from':dates[0],'valid_through':'2026-12-01','sessions':dates+['2026-11-27','2026-11-30','2026-12-01']}
        self.state=state()
        r=rows()
        for a in r:a['r12']=10.0
        _,chosen=rank_i2(r,self.state['month_ends']['2026-10'])
        self.saved={'version':live.VERSION,'asof':self.day,'status':'partial_turnover','coverage':150,'current_core':'2330','signal_history':[],'is_month_end':False,'preview':{'prior_month':'2026-10-30','prior_provenance':'observed','i2':chosen},'rows':r}
    def test_checkpoint_reuse_no_price_download(self):
        vals={k:10.0 for k in live.CORE}
        c={'version':live.VERSION,'asof':self.day,'rows':self.saved['rows'],'r12':vals,'r6':vals,'days':live.dated_sessions(self.cal,self.day)}
        with TemporaryDirectory() as tmp,patch.object(live,'CACHE',Path(tmp)),patch.object(live,'fetch_prices') as prices,patch.object(live,'collect_turnover',side_effect=lambda days,codes:{k:1.5 for k in codes}):
            live.write(Path(tmp)/f'{self.day}_observation.json',c)
            a,r,_,_,ok=live.fetch_observation(self.now,self.cal)
            self.assertTrue(ok);self.assertEqual(a,self.day);self.assertEqual(r[0]['t20'],1.5);prices.assert_not_called()
            c['asof']='2026-11-25'
            with self.assertRaises(ValueError):live.validate_price_checkpoint(c,self.day,self.cal)
    def test_complete_only_exact_day_and_keep_model_state(self):
        def turnover(days,codes):
            self.assertEqual(days[-1],self.day)
            return {c:float(i+1) for i,c in enumerate(codes)}
        data=live.recover_verified_preview(self.saved,self.state,self.day,self.cal,self.now,turnover,clock=lambda:self.now)
        self.assertEqual(data['status'],'complete');self.assertEqual(data['rows'][0]['rank6'],1);self.assertEqual(data['rows'][0]['t20'],1.0)
        self.assertEqual(data['preview']['i2'],[str(i) for i in range(1044,1034,-1)])
        self.assertFalse(self.state['signals']);self.assertEqual(self.state['model_core'],'2330')
    def test_recovery_reject_month_end_and_wrong_date(self):
        with self.assertRaises(ValueError):live.recover_verified_preview(self.saved,self.state,'2026-11-27',self.cal,self.now)
        self.saved['is_month_end']=True
        with self.assertRaises(ValueError):live.recover_verified_preview(self.saved,self.state,self.day,self.cal,self.now)
    def test_unavailable_report_keeps_explicit_preview(self):
        def fail(days,codes):raise ValueError('not published')
        data=live.recover_verified_preview(self.saved,self.state,self.day,self.cal,self.now,fail,clock=lambda:self.now)
        self.assertEqual(data['status'],'partial_turnover')
        self.assertFalse(self.state['signals'])
if __name__=='__main__':unittest.main()
