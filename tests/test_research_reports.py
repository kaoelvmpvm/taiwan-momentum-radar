import json
import tempfile
import unittest
from pathlib import Path
from paper import initial
from research_reports import events,completed_periods,generate

class ReportTests(unittest.TestCase):
    def fixture(self):
        s=initial()
        for d,n in [("2026-11-25",100000.),("2026-11-30",100000.),("2026-12-01",79000.),("2026-12-02",78000.),("2026-12-31",95000.)]:
            s["daily"].append({"day":d,"nav":{k:n for k in s["accounts"]},"unobserved_sessions":[]})
        s["last_day"]="2026-12-31"
        for a in s["accounts"].values():a.update(cash=95000.,nav=95000.,mdd=-.22)
        return s
    def test_threshold_dedup(self):
        s=self.fixture()
        ev=events(s)
        self.assertEqual(len(ev),6)
        self.assertEqual(len(set(x["id"] for x in ev)),6)
        self.assertEqual(ev,events(s))
    def test_period_end(self):
        s=self.fixture()
        cal={"sessions":["2026-11-25","2026-11-26","2026-11-30","2026-12-01","2026-12-02","2026-12-31","2027-01-04"]}
        out=completed_periods(s,cal)
        dec=next(x for x in out if x["period"]=="2026-12")
        self.assertAlmostEqual(dec["returns"]["S0"],-.05)
        nov=next(x for x in out if x["period"]=="2026-11")
        self.assertIn("部分期間",nov["status"])
        self.assertIn("2026-11-26",nov["missing_sessions"])
        s["daily"]=[x for x in s["daily"] if x["day"]!="2026-11-30"]
        dec=next(x for x in completed_periods(s,cal) if x["period"]=="2026-12")
        self.assertFalse(dec["returns"])
    def test_generate_no_stale_archive(self):
        s=initial();s["last_day"]="2026-11-25";s["daily"]=[{"day":"2026-11-25","nav":{k:100000. for k in s["accounts"]},"unobserved_sessions":[]}]
        snap={"asof":"2026-11-25","ranking":[{"code":str(1000+i),"name":"Test","momentum":150-i} for i in range(150)]+[{"code":"0050","name":"ETF","momentum":0}]}
        cal={"sessions":["2026-11-25","2026-11-30","2026-12-01"]}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/"history").mkdir()
            (p/"history"/"2026-11-25.json").write_text(json.dumps(snap))
            ledger=p/"paper_state.json";ledger.write_text(json.dumps(s));before=ledger.read_bytes()
            generate(p,"success","success","https://github.com","2026-11-25",cal)
            self.assertTrue((p/"reports"/"2026-11-25.md").exists())
            generate(p,"failure","skipped","https://github.com","2026-11-26",cal)
            self.assertFalse((p/"reports"/"2026-11-26.md").exists())
            self.assertIn("不是新行情",(p/"DAILY.md").read_text())
            self.assertEqual(ledger.read_bytes(),before)
