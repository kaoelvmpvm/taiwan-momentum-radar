import unittest
import pandas as pd
from pit_expanded_audit import adjusted
class AdjustmentTests(unittest.TestCase):
    def fixture(self):
        return pd.DataFrame([{"date":"2020-07-01","open":100,"high":100,"low":100,"close":100,"volume":1000,"status":"traded"},{"date":"2020-07-02","open":90,"high":90,"low":90,"close":90,"volume":1000,"status":"traded"},{"date":"2020-07-03","open":99,"high":99,"low":99,"close":99,"volume":1000,"status":"traded"}])
    def test_ex_adjustment(self):
        d,r=adjusted(self.fixture(),[{"date":"2020-07-02","fields":{"除權息前收盤價":"100","除權息參考價":"90"}}],["2020-07-01","2020-07-02","2020-07-03"])
        self.assertEqual(list(d.reference_adjusted_close),[90.,90.,99.])
        self.assertFalse(r["event_errors"])
    def test_bad_prior(self):
        d,r=adjusted(self.fixture(),[{"date":"2020-07-02","fields":{"除權息前收盤價":"80","除權息參考價":"72"}}],[])
        self.assertTrue(r["event_errors"])
        self.assertEqual(list(d.reference_adjusted_close),[100.,90.,99.])
