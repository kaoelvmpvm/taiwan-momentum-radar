# 台股動能雷達 Taiwan Momentum Radar

## V0.1｜資料驗證與每日研究排行榜

本專案只作研究及模擬交易，不連接券商、不下單，也不使用生成式 AI API。

- 候選池：元大 0050 與 0051 官網當日股票持股（作為兩指數成分股的代理，並非官方歷史指數成分股名單）；預期 50 + 100 檔、無重疊，另加入 0050 作為比較標的。
- 價格：Yahoo Finance 經 yfinance 取得 Adj Close（包含資料供應商的股息與分割調整），不等同 E點贏還原公式。與 TWSE 官方最新收盤價核對，且要求同日、同一基準日、價格有效；異常跳動列入待查。
- 動能：100 × (當日還原收盤價 / 一年前對應交易日還原收盤價 − 1)。完整12個月，包含最近一個月，無參數搜尋。
- 每日排行榜只供研究，並非換股指令；正式月底訊號須另接交易日曆與資料審核。
- 缺少資料或股票池筆數不足時，禁止宣稱找到全池第一名。可查看有資料的暫定排名。
- 目前成分股不得反向套用至過去做正式回測。歷史成分股、下市與公司行動仍待處理。
- Yahoo 及投信資料有使用條款限制；先保留私有研究，不對外發布原始價量，不保證免費服務可長期維持。

## 使用方法

GitHub Actions → Daily momentum radar → Run workflow。完成後在該次 run 的 Artifacts 下載 radar-report，解壓並開啟 index.html。報告為單一 HTML 檔，不需要伺服器。每次 run 的報告會保留 30 天。尚未上線 GitHub Pages。

本機測試：Python 3.11，pip install -r requirements.txt，python -m playwright install chromium，python -m unittest discover -s tests，python radar.py。

## 來源

- https://www.yuantaetfs.com/product/detail/0050/ratio
- https://www.yuantaetfs.com/product/detail/0051/ratio
- https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
- https://ranaroussi.github.io/yfinance/reference/yfinance.functions.html

V0.1 僅是資料可行性測試；公司行動還原歷史資料尚未獨立驗證，任何排名都不能作為實盤操作依據。

## 公司行動核對

已登錄6949於2026-09-07恢復交易之20:1面額變更（最後舊股交易日2026-08-26）。若12個月視窗跨越事件，向TWSE指定日期行情查詢前後收盤，判定Yahoo還原價是否已正確調整；僅在確認遺漏時按官方比例修正事件前還原價格。無法核對則維持不完整狀態，不排除股票。紀錄保存在ranking.json的corporate_action_audit。這是已知事件的查核，並非完整市場公司行動資料庫。
