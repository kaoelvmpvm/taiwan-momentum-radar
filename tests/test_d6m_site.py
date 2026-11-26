"""Browser smoke tests with the committed snapshot; no market requests."""
import json
import unittest
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
class TestMobileDashboard(unittest.TestCase):
    def test_mobile_search_watchlist_and_signals(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(ROOT/'site')))
        Thread(target=server.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as p:
                if not Path(p.chromium.executable_path).exists():
                    self.skipTest('Chromium not installed; run playwright install chromium')
                browser=p.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
                    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.wait_for_selector('#overview .hero')
                    self.assertIn('150/150',page.locator('#status').inner_text())
                    page.locator('[data-tab="list"]').click()
                    self.assertEqual(page.locator('.stock').count(),150)
                    code=json.loads((ROOT/'site/data.json').read_text())['rows'][0]['code']
                    page.fill('#search',code)
                    self.assertEqual(page.locator('.stock').count(),1)
                    page.locator('.star').click()
                    page.locator('[data-tab="watch"]').click()
                    self.assertEqual(page.locator('.stock').count(),1)
                    page.reload();page.wait_for_selector('#overview .hero')
                    page.locator('[data-tab="watch"]').click()
                    self.assertEqual(page.locator('.stock').count(),1)
                    page.locator('[data-tab="signals"]').click()
                    self.assertIn('最近月末正式訊號',page.locator('#signals').inner_text())
                    page.locator('[data-tab="list"]').click()
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
                    self.assertFalse(errors,errors)
                finally:browser.close()
        finally:
            server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
