import unittest
import pandas as pd
from regime_experiments import rebalance,weights,simulate

class RegimeTests(unittest.TestCase):
    def test_partial_and_cash(self):
        h,c,fee,m=rebalance({},100000.,{"1000":.1},{"1000":100.},1)
        nav=c+h["1000"]*100
        self.assertAlmostEqual(h["1000"]*100/nav,.1,places=9)
        self.assertAlmostEqual(nav+fee,100000.,places=6)
        h2,c2,fee2,_=rebalance(h,c,{},{"1000":100.},1)
        self.assertEqual(h2,{})
        self.assertAlmostEqual(c2+fee2,nav,places=6)
    def test_no_lookahead_execution(self):
        dates=pd.to_datetime(["2026-01-30","2026-02-02","2026-02-03"])
        op=pd.DataFrame({"1000":[100.,None,110.]},index=dates)
        cl=pd.DataFrame({"1000":[100.,105.,120.]},index=dates)
        p={dates[1]:(dates[0],weights(["1000"],.5))}
        r=simulate(dates,op,cl,{"1000":pd.Series(dtype=float)},p,dates[1],dates[2])
        self.assertEqual(r["summary"]["delays"],1)
        self.assertEqual(r["nav"].iloc[0],100000.)
        self.assertEqual(r["trades"][0]["date"],"2026-02-03")
    def test_cash_only(self):
        dates=pd.to_datetime(["2026-01-30","2026-02-02","2026-02-03"])
        op=pd.DataFrame({"1000":[100.,100.,110.]},index=dates)
        p={dates[1]:(dates[0],{})}
        r=simulate(dates,op,op,{"1000":pd.Series(dtype=float)},p,dates[1],dates[2])
        self.assertEqual(r["summary"]["terminal"],100000.)
        self.assertEqual(r["summary"]["exposure"],0.)
        self.assertIsNone(r["summary"]["sharpe"])
