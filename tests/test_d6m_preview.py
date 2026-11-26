import copy, unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from d6m_preview import preview_snapshot
class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.cal={'sessions':[f'2026-11-{i:02d}' for i in range(1,27)],'valid_from':'2026-11-01','valid_through':'2026-11-30'}
        self.now=datetime(2026,11,26,21,tzinfo=ZoneInfo('Asia/Taipei'))
        self.snapshot={'asof':'2026-11-26','status':'partial_turnover','coverage':150,'current_core':'2330','formal':{'signal_date':'2026-10-30'},'signal_history':[{'signal_date':'2026-10-30'}],'preview':{'i2_available':False},'rows':[{'code':str(1000+i),'r6':150-i,'t20':None} for i in range(150)]}
        self.state={'month_ends':{'2026-10':{'top30':[str(1000+i) for i in range(45)]}}}
    def test_lagged_preview_not_formal(self):
        original=copy.deepcopy(self.snapshot)
        def fetch(days,codes):
            self.assertEqual(len(days),20)
            self.assertEqual(days[-1],'2026-11-25')
            return {code:i+1 for i,code in enumerate(codes)}
        result=preview_snapshot(self.snapshot,self.state,self.cal,self.now,fetch)
        self.assertEqual(result['status'],'partial_turnover')
        self.assertEqual(result['turnover_asof'],'2026-11-25')
        self.assertTrue(result['preview']['i2_provisional'])
        self.assertEqual(len(result['preview']['i2']),10)
        self.assertEqual(result['formal'],original['formal'])
        self.assertEqual(result['signal_history'],original['signal_history'])
        self.assertEqual(result['current_core'],original['current_core'])
    def test_already_complete_not_overwritten(self):
        self.snapshot['status']='complete'
        self.assertIsNone(preview_snapshot(self.snapshot,self.state,self.cal,self.now,lambda *_:self.fail('unexpected fetch')))
    def test_stale_not_overwritten(self):
        self.snapshot['asof']='2026-11-25'
        self.assertIsNone(preview_snapshot(self.snapshot,self.state,self.cal,self.now,lambda *_:self.fail('unexpected fetch')))
if __name__=='__main__':unittest.main()
