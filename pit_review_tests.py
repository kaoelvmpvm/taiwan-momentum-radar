import unittest
from pit_review_audit import parse,reverse
class PITTests(unittest.TestCase):
    def test_parse(self):
        raw='<h1>成分股定期審核結果</h1><p>2020/06/05 17:00</p><p>亦即自2020年6月22日</p><p>一、臺灣50指數</p><p>(一)成分股納入：甲(1234)。</p><p>(二)成分股刪除：乙(2345)。</p><p>(三)候補名單：丙(3456)。</p><p>二、臺灣中型100指數</p><p>(一)成分股納入：無。</p><p>(二)成分股刪除：無。</p><p>(三)候補名單：無。</p>'.encode()
        # Explicit UTF-8 metadata mirrors original source.
        raw=b'<meta charset="utf-8">'+raw
        x=parse(raw,"fixture",(2020,6))
        self.assertEqual(x["groups"]["0050"],{"add":["1234"],"remove":["2345"]})
        self.assertEqual(x["groups"]["0051"],{"add":[],"remove":[]})
    def test_reverse_blocks(self):
        a={"0050":[str(x) for x in range(1000,1050)],"0051":[str(x) for x in range(2000,2100)]}
        e={"effective":"2020-06-22","groups":{"0050":{"add":["9999"],"remove":["9998"]},"0051":{"add":[],"remove":[]}}}
        r,b=reverse(a,[e]);self.assertTrue(b);self.assertEqual(r[0]["issues"][0]["missing_additions"],["9999"])
