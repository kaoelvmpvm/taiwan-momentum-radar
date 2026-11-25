import unittest
from datetime import date,timedelta
from rotation_engine import run,rank_at,rebalance,target,cost,INITIAL,BENCH

class RotationTests(unittest.TestCase):
    def fixture(self):
        start=date(2024,12,1)
        dates=[start+timedelta(days=i) for i in range(440) if (start+timedelta(days=i)).weekday()<5]
        codes=[str(1000+i) for i in range(150)]+[BENCH]
        prices={}
        for i,d in enumerate(dates):
            for j,c in enumerate(codes):
                v=100*(1+i*(j+1)*.00001)
                prices[d,c]=(v,v)
        membership={dates[i-1]:{c:{} for c in codes[:-1]} for i in range(1,len(dates)) if dates[i].month!=dates[i-1].month}
        manifest={"data_kind":"synthetic"}
        return dates,prices,membership,manifest

    def test_compounding_and_costs(self):
        dates,prices,membership,manifest=self.fixture()
        start=date(2026,1,1); end=date(2026,2,6)
        r=run(dates,prices,membership,manifest,start,end)
        self.assertEqual(len(r["results"]),6)
        self.assertEqual(len(r["results"]["S0"]["orders"]),1)
        self.assertEqual(r["results"]["M1"]["orders"][0]["signal_date"],"2025-12-31")
        self.assertEqual(r["results"]["M1"]["orders"][0]["date"],"2026-01-01")
        self.assertEqual(len(target("M4",rank_at(date(2025,12,31),dates,prices,membership))),31)
        self.assertGreater(r["results"]["S0"]["summary"]["cost"],0)
        self.assertEqual(r["data_kind"],"synthetic")
    def test_fail_closed(self):
        dates,prices,membership,manifest=self.fixture()
        start=date(2026,1,1); end=date(2026,2,6)
        del membership[date(2025,12,31)]
        with self.assertRaisesRegex(ValueError,"股票池"):
            run(dates,prices,membership,manifest,start,end)
        dates,prices,membership,manifest=self.fixture()
        del prices[(date(2024,12,31),"1000")]
        with self.assertRaisesRegex(ValueError,"缺少"):
            run(dates,prices,membership,manifest,start,end)
    def test_rebalance_cost_accounting(self):
        h,c,fee,moves=rebalance({},INITIAL,["1000"],{"1000":100.})
        self.assertAlmostEqual(h["1000"]*100+c+fee,INITIAL,places=6)
        h2,c2,fee2,moves2=rebalance(h,c,[BENCH],{"1000":100.,BENCH:50.})
        self.assertAlmostEqual(h2[BENCH]*50+c2+fee2,h["1000"]*100+c,places=6)
        self.assertAlmostEqual(fee2,sum(cost(k,v) for k,v in moves2.items()))
        self.assertGreater(cost("1000",-1000),cost(BENCH,-1000))
    def test_same_asset_no_churn(self):
        h,c,fee,moves=rebalance({"1000":1000.},0.,["1000"],{"1000":100.})
        self.assertAlmostEqual(fee,0.,places=6)
        self.assertAlmostEqual(h["1000"],1000.,places=6)
