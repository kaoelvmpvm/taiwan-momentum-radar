import copy
import tempfile
import unittest
from pathlib import Path
from rank_history import save_snapshot,load_history,changes,series,svg_chart,render_history
from test_dashboard import example
from dashboard import snapshot

class HistoryTests(unittest.TestCase):
    def test_date_key_and_movement(self):
        first=snapshot(example())
        second=copy.deepcopy(first)
        second["asof"]="2026-11-26"
        second["baseline"]="2025-11-26"
        second["ranking"][0],second["ranking"][1]=second["ranking"][1],second["ranking"][0]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            save_snapshot(root,first)
            save_snapshot(root,first)
            self.assertEqual(len(load_history(root)),1)
            save_snapshot(root,second)
            h=load_history(root)
            self.assertEqual(len(h),2)
            code=second["ranking"][0]["code"]
            self.assertEqual(changes(second,first)[code]["rank_delta"],1)
            self.assertEqual([x[1] for x in series(h,code)],[2,1])
            render_history(root,h)
            self.assertTrue((root/"charts"/(code+".svg")).exists())
            self.assertIn("第一名異動",(root/"HISTORY.md").read_text())
    def test_missing_and_first(self):
        s=snapshot(example())
        self.assertIsNone(changes(s,None)["0050"]["rank_delta"])
        self.assertIsNone(series([s],"9999")[0][1])
        self.assertIn("<svg",svg_chart(series([s],"0050"),"0050","台灣50"))
