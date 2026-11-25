import unittest
import pandas as pd
from radar import fund_rows, momentum, baseline_date, clean_history, render, read_export_tables, extract_export_members, parse_dated_market, reconcile_face_value

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

    def test_csv_export(self):
        csv = '基金名稱,測試\n商品代碼,商品名稱,商品權重\n2330,台積電,60\n2303,聯電,1\n\n商品代碼,商品名稱\n9999,其他\n'
        raw = csv.encode("utf-8-sig")
        self.assertEqual(extract_export_members(read_export_tables(raw),2,{"2330":"台積電"}),{"2330":"台積電","2303":"聯電"})
        with self.assertRaises(ValueError):
            extract_export_members(read_export_tables(raw),3,{"2330":"台積電"})
        with self.assertRaises(ValueError):
            extract_export_members(read_export_tables(raw),2,{})
        with self.assertRaises(ValueError):
            extract_export_members(read_export_tables(raw),2,{"9999":"其他"})

    def test_dated_market(self):
        rows = [["0050","1,000","90.30"]] + [[str(1000+i),"500","100.00"] for i in range(500)]
        payload = {"stat":"OK","date":"20261125","tables":[{"fields":["證券代號","成交股數","收盤價"],"data":rows}]}
        parsed = parse_dated_market(payload,"2026-11-25")
        self.assertEqual(parsed["0050"],("2026-11-25",90.30))
        with self.assertRaises(ValueError):
            parse_dated_market(payload,"2026-11-24")
        with self.assertRaises(ValueError):
            parse_dated_market({"stat":"OK","date":"20261125","tables":[]},"2026-11-25")

    def test_face_value_reconciliation(self):
        event = {"last_old":"2026-08-26","first_new":"2026-09-07","ratio":20}
        idx = pd.to_datetime(["2026-08-25","2026-08-26","2026-09-07","2026-09-08"])
        f = pd.DataFrame({"Close":[980.,1000.,52.,53.],"Adj Close":[980.,1000.,52.,53.]},index=idx)
        fixed,audit = reconcile_face_value(f,event,1000.,52.)
        self.assertEqual(audit["status"],"missing_adjustment_repaired")
        self.assertEqual(fixed.iloc[1]["Adj Close"],50.)
        self.assertEqual(f.iloc[1]["Adj Close"],1000.)
        same,audit = reconcile_face_value(fixed,event,1000.,52.)
        self.assertEqual(audit["status"],"already_adjusted")
        pd.testing.assert_frame_equal(same,fixed)
        bad=f.copy()
        bad.loc[idx[2],"Adj Close"]=100.
        with self.assertRaises(ValueError):
            reconcile_face_value(bad,event,1000.,52.)
        with self.assertRaises(ValueError):
            reconcile_face_value(f.drop(idx[1]),event,1000.,52.)

    def test_duplicate(self):
        with self.assertRaises(ValueError):
            clean_history(pd.concat([self.f,self.f.iloc[:1]]))

    def test_incomplete(self):
        result=render({"generated":"now","complete":False,"ranking":[],"issues":["missing"]})
        self.assertIn("不可判定全池第一名",result)
        self.assertIn("missing",result)

if __name__ == "__main__":
    unittest.main()
