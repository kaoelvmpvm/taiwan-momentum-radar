import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from paper import initial
from research_view import validate,points,chart,allocations,generate,render

class ResearchViewTests(unittest.TestCase):
    def state(self):
        s=initial()
        s["last_day"]="2026-11-25"
        s["daily"]=[{"day":"2026-11-25","nav":{k:100000. for k in s["accounts"]},"unobserved_sessions":[]}]
        return s
    def test_initial_no_fake_curve(self):
        s=self.state();validate(s)
        for kind in ("nav","return","drawdown"):
            svg=chart(s,kind)
            ET.fromstring(svg)
            self.assertIn("尚未形成歷史曲線",svg)
            self.assertEqual(len(points(s,kind)["S0"]),1)
        ET.fromstring(allocations(s,{}))
        self.assertIn("尚無模擬成交",render(s,{},"success","success","https://github.com"))
    def test_drawdown_and_gap(self):
        s=self.state()
        for day,nav,gaps in [("2026-11-26",110000.,[]),("2026-11-30",88000.,["2026-11-27"])]:
            s["daily"].append({"day":day,"nav":{k:nav for k in s["accounts"]},"unobserved_sessions":gaps})
        s["last_day"]="2026-11-30"
        for a in s["accounts"].values():a.update(cash=88000.,nav=88000.,peak=110000.,mdd=-.2)
        validate(s)
        self.assertAlmostEqual(points(s,"drawdown")["S0"][-1][1],-20.)
        self.assertIn("缺少觀測",render(s,{},"success","success","https://github.com"))
    def test_fail_closed(self):
        s=self.state();s["accounts"]["M2"]["cash"]=99.
        with self.assertRaises(ValueError):validate(s)
    def test_generate_read_only(self):
        s=self.state()
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);path=p/"paper_state.json"
            path.write_text(json.dumps(s));before=path.read_bytes()
            (p/"last_success.json").write_text(json.dumps({"asof":"2026-11-25","ranking":[]}))
            generate(p,"failure","skipped","https://github.com")
            self.assertEqual(path.read_bytes(),before)
            self.assertIn("未完整更新",(p/"RESEARCH.md").read_text())
            for f in (p/"research").glob("*.svg"):ET.parse(f)
            self.assertEqual(len(list((p/"research").glob("*.svg"))),4)
