import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from datetime import datetime
from zoneinfo import ZoneInfo
from fund_cache import collect, load, save, latest_common, FUND_URL, EXPECTED
from momentum_alert import is_extreme, marker
from radar import today_session
from dashboard import snapshot, markdown
from test_dashboard import example

DAY = "2026-11-27"
OLD = "2026-11-26"

def report(fund, day, offset=None):
    start = offset if offset is not None else (1000 if fund == "0050" else 2000)
    return {"date": day, "members": {str(start+i): "測試" for i in range(EXPECTED[fund])},
            "source": FUND_URL.format(fund), "method": "issuer Excel"}

class FundCacheTests(unittest.TestCase):
    def test_retry_only_missing_then_exact(self):
        with TemporaryDirectory() as tmp:
            five = iter([report("0050", OLD), report("0050", DAY)])
            fetch = Mock(side_effect=lambda fund: next(five) if fund=="0050" else report(fund, DAY))
            wait = Mock()
            result = collect(DAY, tmp, fetch, observed_today=DAY, sleep=wait)
            self.assertEqual({r["date"] for r in result.values()}, {DAY})
            self.assertEqual([x.args[0] for x in fetch.call_args_list], ["0050","0051","0050"])
            self.assertEqual(load(tmp,"0050",OLD)["date"], OLD)
            self.assertEqual(load(tmp,"0051",DAY)["date"], DAY)
            wait.assert_called_once_with(15)

    def test_different_dates_preserved_but_never_used_as_current(self):
        with TemporaryDirectory() as tmp:
            save(tmp, "0051", report("0051",OLD))
            def fetch(fund):
                return report(fund, OLD if fund=="0050" else DAY)
            with self.assertRaisesRegex(ValueError, "最近已驗證共同日：2026-11-26"):
                collect(DAY, tmp, fetch, observed_today=DAY, attempts=2, sleep=lambda _:None)
            self.assertEqual(latest_common(tmp,DAY), OLD)
            self.assertEqual(load(tmp,"0051",DAY)["date"], DAY)
            next_fetch=Mock(return_value=report("0050",DAY))
            result=collect(DAY,tmp,next_fetch,observed_today=DAY,sleep=lambda _:None)
            next_fetch.assert_called_once_with("0050")
            self.assertEqual({x["date"] for x in result.values()}, {DAY})

    def test_missing_fund_does_not_lose_other(self):
        with TemporaryDirectory() as tmp:
            def fetch(fund):
                if fund=="0050":raise OSError("temporary issue")
                return report(fund,DAY)
            with self.assertRaisesRegex(ValueError,"ETF持股未同步"):
                collect(DAY,tmp,fetch,observed_today=DAY,attempts=1)
            self.assertIsNotNone(load(tmp,"0051",DAY))

    def test_overlap_rejected_and_refetched(self):
        with TemporaryDirectory() as tmp:
            save(tmp,"0050",report("0050",DAY))
            save(tmp,"0051",report("0051",DAY,1000))
            self.assertIsNone(latest_common(tmp,DAY))
            fetch=Mock(side_effect=lambda fund:report(fund,DAY))
            result=collect(DAY,tmp,fetch,observed_today=DAY,sleep=lambda _:None)
            self.assertEqual(fetch.call_count,2)
            self.assertFalse(set(result["0050"]["members"]) & set(result["0051"]["members"]))

    def test_invalid_cache_and_future_data_rejected(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/f"{DAY}_fund_0050.json"
            path.write_text('{"date":"2026-11-26"}')
            self.assertIsNone(load(tmp,"0050",DAY))
            def fetch(fund):return report(fund,"2026-11-30")
            with self.assertRaisesRegex(ValueError,"報表日期晚於查核日期"):
                collect(DAY,tmp,fetch,observed_today=DAY,attempts=1)
            self.assertIsNone(load(tmp,"0050","2026-11-30"))
            with self.assertRaises(ValueError):
                collect("2026-11-30",tmp,fetch,observed_today=DAY)

    def test_today_calendar(self):
        cal={"valid_from":OLD,"valid_through":"2026-11-30","sessions":[OLD,DAY,"2026-11-30"]}
        def dt(day,hour):return datetime.fromisoformat(f"{day}T{hour}:00").replace(tzinfo=ZoneInfo("Asia/Taipei"))
        self.assertEqual(today_session(dt(DAY,"19:00"),cal),DAY)
        with self.assertRaisesRegex(ValueError,"非預定交易日"):
            today_session(dt("2026-11-28","19:00"),cal)
        with self.assertRaisesRegex(ValueError,"尚未收盤"):
            today_session(dt(DAY,"12:00"),cal)

class ExtremeMomentumTests(unittest.TestCase):
    def test_threshold_and_nonfinite(self):
        for n in (500.,841.45,-80.,-90.):self.assertTrue(is_extreme(n))
        for n in (499.99,-79.99,0,None,float("inf"),float("nan")):self.assertFalse(is_extreme(n))
        self.assertIn("極端動能",marker(841.45))
    def test_annotation_does_not_change_scores_or_order(self):
        p=example()
        p["ranking"][0]["momentum"]=841.448329705253
        p["ranking"][1]["momentum"]=-80.
        expected=sorted(p["ranking"],key=lambda x:(-x["momentum"],x["code"]))
        s=snapshot(p)
        self.assertEqual([(r["code"],r["momentum"]) for r in s["ranking"]],
                         [(r["code"],r["momentum"]) for r in expected])
        self.assertEqual({r["code"] for r in s["ranking"] if r["extreme_momentum"]},{"1000","1001"})
        self.assertIn("⚠ 極端動能",markdown(s,"資料檢查通過","","https://github.com/example"))
        self.assertEqual(len(s["ranking"]),151)
