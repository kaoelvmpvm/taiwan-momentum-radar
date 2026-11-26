import unittest
import pandas as pd
from pit_historical_backtest import simulate
class TestBacktest(unittest.TestCase):
    def test_no_delisting_fill(self):
        dates=pd.date_range("2020-07-01",periods=3,freq="B")
        cl=pd.DataFrame({"1234":[10.,11.,float("nan")]},index=dates)
        r=simulate(dates,cl,cl,{"1234":pd.Series(dtype=float)},{dates[0]:(dates[0]-pd.Timedelta(days=1),{"1234":1.})},dates[0],dates[-1])
        self.assertEqual(r["summary"]["status"],"INCOMPLETE")
        self.assertEqual(r["audit"]["blocked"]["codes"],["1234"])
        self.assertEqual(len(r["trades"]),1)
    def test_wait_for_real_open(self):
        dates=pd.date_range("2020-07-01",periods=3,freq="B")
        cl=pd.DataFrame({"1234":[10.,11.,12.]},index=dates)
        op=cl.copy();op.iloc[0,0]=float("nan")
        r=simulate(dates,op,cl,{"1234":pd.Series(dtype=float)},{dates[0]:(dates[0]-pd.Timedelta(days=1),{"1234":1.})},dates[0],dates[-1])
        self.assertEqual(r["trades"][0]["date"],"2020-07-02")
        self.assertEqual(r["audit"]["delayed_sessions"],1)
        self.assertAlmostEqual(r["nav"].iloc[0],100000.)
