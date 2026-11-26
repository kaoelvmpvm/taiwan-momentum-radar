import unittest
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
if __name__=='__main__':unittest.main()
