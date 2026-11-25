import unittest
import pandas as pd
from radar import fund_rows, momentum, baseline_date, clean_history, render

class RadarTests(unittest.TestCase):
    def setUp(self):
        idx = pd.bdate_range("2025-01-02","2026-01-05")
        self.f = pd.DataFrame({"Close":100., "Adj Close":100.}, index=idx)
        self.f.iloc[0] = [80.,80.]
        self.f.iloc[-1] = [110.,110.]

    def test_momentum(self):
        base = self.f.index[0]
        self.assertAlmostEqual(momentum(self.f,self.f.index[-1],base,110.),37.5)
        with self.assertRaises(ValueError):
            momentum(self.f,self.f.index[-1],base,90.)
        with self.assertRaises(ValueError):
            momentum(self.f,self.f.index[-1],pd.Timestamp("2024-01-01"),110.)

    def test_calendar(self):
        self.assertEqual(baseline_date(self.f,"2026-01-04"),pd.Timestamp("2025-01-03"))

    def test_fund_rows(self):
        self.assertEqual(fund_rows("商品代碼 2330\n商品名稱 台積電\n商品數量 123"),{"2330":"台積電"})

    def test_duplicate(self):
        with self.assertRaises(ValueError):
            clean_history(pd.concat([self.f,self.f.iloc[:1]]))

    def test_incomplete(self):
        result=render({"generated":"now","complete":False,"ranking":[],"issues":["missing"]})
        self.assertIn("不可判定全池第一名",result)
        self.assertIn("missing",result)

if __name__ == "__main__":
    unittest.main()
