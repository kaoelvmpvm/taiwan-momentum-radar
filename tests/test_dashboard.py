import unittest
from dashboard import snapshot,update,markdown

def example():
    rows=[{"code":str(1000+i),"name":"股票"+str(i),"group":"0050" if i<50 else "0051","momentum":float(i)} for i in range(150)]
    rows.append({"code":"0050","name":"元大台灣50","group":"基準ETF","momentum":25.0})
    return {"complete":True,"asof":"2026-11-25","baseline":"2025-11-25","ranking":rows,"issues":[]}

class DashboardTests(unittest.TestCase):
    def test_complete(self):
        s=snapshot(example())
        self.assertEqual(len(s["ranking"]),151)
        self.assertEqual(s["ranking"][0]["code"],"1149")
        self.assertIn("全部151檔",markdown(s,"資料檢查通過","","https://github.com/example"))
    def test_invalid_does_not_replace(self):
        s=snapshot(example())
        p=example()
        p["ranking"].pop()
        self.assertEqual(update(p,s,"success")[0],s)
        p=example()
        p["ranking"][0]["code"]=p["ranking"][1]["code"]
        self.assertEqual(update(p,s,"success")[0],s)
        self.assertEqual(update(example(),s,"failure")[0],s)
        self.assertIn("不代表當前排名",markdown(s,"更新未通過","錯誤","https://github.com/example"))
    def test_old_and_missing(self):
        s=snapshot(example())
        p=example()
        p.update(asof="2026-11-24",baseline="2025-11-24")
        self.assertEqual(update(p,s,"success")[0],s)
        self.assertIsNone(update({},None,"failure")[0])
    def test_nonfinite(self):
        p=example()
        p["ranking"][0]["momentum"]=float("nan")
        with self.assertRaises(ValueError):
            snapshot(p)
