import unittest
import pandas as pd
from pit_timing_audit import corrected,members,reduction_adjust
class TimingTests(unittest.TestCase):
    def test_temporary_size(self):
        a={"0050":[str(x) for x in range(1000,1050)],"0051":[str(x) for x in range(2000,2100)]}
        e=[{"effective":"2026-03-23","groups":{"0050":{"add":["1000"],"remove":["9999"]},"0051":{"add":[],"remove":[]}}}]
        x=[{"scheduled":"2026-03-23","effective":"2026-04-14","code":"1000","changes":[["0050","add"]],"source":"fixture"}]
        ev=corrected(e,x)
        self.assertEqual(len(members("2026-03-31",a,ev,x)["0050"]),49)
        self.assertEqual(len(members("2026-04-14",a,ev,x)["0050"]),50)
    def test_reduction(self):
        d=pd.DataFrame({"date":["2020-01-08","2020-01-20"],"close":[10.,20.],"reference_factor":[1.,1.],**{"reference_adjusted_"+k:[10.,20.] for k in ("open","high","low","close")}})
        r=reduction_adjust(d,{"effective":"2020-01-20","share_ratio":.5,"cash_return":0,"halt_start":"2020-01-09"})
        self.assertEqual(list(d.reference_adjusted_close),[20.,20.])
        self.assertEqual(r["share_adjusted_return"],0.)
