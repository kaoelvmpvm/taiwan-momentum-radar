import unittest
from datetime import datetime
from paper import initial,evolve,TZ

class PaperTests(unittest.TestCase):
    def snap(self,day):
        rows=[{"code":str(1000+i),"momentum":float(150-i)} for i in range(150)]+[{"code":"0050","momentum":0.}]
        return {"asof":day,"complete":True,"issues":[],"generated":day+"T18:15:00+08:00","ranking":rows}
    def now(self,d):return datetime.fromisoformat(d+"T18:30:00+08:00")
    def calendar(self):return {"sessions":["2026-11-25","2026-11-26","2026-11-27","2026-11-30","2026-12-01","2026-12-02","2026-12-31","2027-01-04"]}
    def test_signal_and_fill(self):
        cal=self.calendar();s=evolve(initial(),self.snap("2026-11-30"),self.now("2026-11-30"),cal,{})
        self.assertEqual(s["pending"]["planned_open"],"2026-12-01")
        self.assertEqual(s["accounts"]["M2"]["nav"],100000.)
        qs={c:{"open":100.,"close":110.,"prior":100.,"raw_open":100.} for c in set(s["pending"]["targets"]["M3"]+["0050"])}
        t=evolve(s,self.snap("2026-12-01"),self.now("2026-12-01"),cal,qs)
        self.assertEqual(len(t["accounts"]["M2"]["positions"]),5)
        self.assertEqual(len(t["accounts"]["M3"]["positions"]),10)
        self.assertGreater(t["accounts"]["S0"]["nav"],100000.)
        self.assertIsNone(t["pending"])
        self.assertEqual(t,evolve(t,self.snap("2026-12-01"),self.now("2026-12-01"),cal,{}))
        z=evolve(t,self.snap("2026-12-02"),self.now("2026-12-02"),cal,{c:{"prior":110.,"close":121.} for c in qs})
        self.assertAlmostEqual(z["accounts"]["M3"]["nav"],t["accounts"]["M3"]["nav"]*1.1,places=6)
    def test_missed_open_no_replay(self):
        cal=self.calendar();s=evolve(initial(),self.snap("2026-11-30"),self.now("2026-11-30"),cal,{})
        t=evolve(s,self.snap("2026-12-02"),self.now("2026-12-02"),cal,{})
        self.assertEqual(t["trades"][0]["status"],"missed_open_no_replay")
        self.assertEqual(t["accounts"]["M2"]["nav"],100000.)
    def test_missing_quote_no_fill(self):
        cal=self.calendar();s=evolve(initial(),self.snap("2026-11-30"),self.now("2026-11-30"),cal,{})
        t=evolve(s,self.snap("2026-12-01"),self.now("2026-12-01"),cal,{})
        self.assertEqual(t["trades"][0]["status"],"unfilled_missing_quote")
        self.assertFalse(t["accounts"]["M2"]["positions"])
    def test_no_stale_signal(self):
        with self.assertRaises(ValueError):
            evolve(initial(),self.snap("2026-11-30"),self.now("2026-12-01"),self.calendar(),{})
