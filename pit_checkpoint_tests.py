import unittest
from pit_checkpoint_audit import parse_details,expected_at
class CheckpointTests(unittest.TestCase):
    def test_table_mapping(self):
        raw='<meta charset="utf-8"><table><tr><td>公司代號 元大台灣卓越50 證券投資信託基金</td></tr></table><table><tr><th>序號</th><th>股票代號</th><th>股票名稱</th></tr><tr><td>1</td><td>2330</td><td>台積電</td></tr><tr><td>2</td><td>現金</td><td>現金</td></tr></table>'.encode()
        r=parse_details(raw)
        self.assertEqual(r["0050"]["records"],{"2330":"台積電"})
        self.assertEqual(r["0050"]["tables"][0]["nonstandard_rows"],[["2","現金","現金"]])
    def test_effective_date(self):
        a={"0050":[str(x) for x in range(1000,1050)],"0051":[str(x) for x in range(2000,2100)]}
        e={"effective":"2025-07-14","groups":{"0050":{"add":[],"remove":[]},"0051":{"add":["2000"],"remove":["9999"]}}}
        self.assertIn("9999",expected_at("2025-07-13",a,[e])["0051"])
        self.assertNotIn("9999",expected_at("2025-07-14",a,[e])["0051"])
