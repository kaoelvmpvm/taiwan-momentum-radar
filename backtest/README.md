# V0.4 歷史回測引擎

狀態：引擎及測試程式建置；正式市場回測尚未執行。現有2026-11-25快照不是歷史成分股資料。

## 預先固定的規格

- 候選池：每個訊號日實際有效的台灣50及中型100共150檔股票，另加入0050（151檔）；這與目前監測的持股代理池定義有資料來源差異，正式研究需說明。
- S0 0050買入持有；M1 前1、M2 前5、M3 前10、M4 前20%（向上取整31檔）、M5 全151檔。M1–M5 每月等權再平衡，單一持股若不變不產生實質交易。M5 包含0050，保持與動能組相同候選範圍。
- 月末交易日收盤判斷，使用12個月前相對日期（不晚於該日的共同交易日）還原收盤價，下一交易日開盤交易。沒有略過近期一個月。月末尚未結束不產生訊號。
- 初始10萬元；分數股數、無槓桿、無現金利息；雙邊手續費0.1425%、滑價0.05%，賣方證交稅個股0.3%、0050 0.1%。按成交額估計，不含最低手續費及整數股限制。以還原價單位模擬股息再投入；不模擬個別現金股息入帳時間、漲跌停排隊及成交量限制。期末收盤估值、不強制賣出。
- 每日收盤資產、最大回撤、總報酬、滿一年才顯示年化報酬、交易成本及交易明細。缺少持股交易或估值價格，立即停止，不用舊價補算。
- 首版要求每個訊號日151檔都有共同基準日價格，若遇到新上市或下市等特殊情況即停止，之後需預先制定適格及下市處置規則，不擅自刪掉股票。
- 歷史資料來源與驗證資訊是必要欄位，但程式無法單憑聲明證明資料完整可靠，正式研究需人工查核。勿將當前成分股套用過去。

## 輸入檔案（自行放在 data/，不提交授權受限行情）

calendar.csv: date（完整證交所交易日曆，YYYY-MM-DD，包含至少12個月暖機）

prices.csv: date,code,adj_open,adj_close（還原開收盤價，包含已下市成員；代碼保留前導零）

universe.csv: signal_date,code,known_date,effective_date,source（每個月末150檔；資訊公開日與生效日均不可晚於訊號日。source為可追溯來源。不得含0050，程式會加入。）

manifest.json: data_kind（research 或 synthetic）, price_source, calendar_source, membership_source, adjustment_method, corporate_action_policy, membership_verified_through（YYYY-MM-DD）

執行：python rotation_engine.py --data data --start YYYY-MM-DD --end YYYY-MM-DD --out backtest-output

起始日期須為該月首個交易日，結束日期須為交易日。輸出 results.json、summary.csv、daily.csv、orders.csv。測試資料永遠標記 synthetic。

## 目前資料缺口

- [臺灣指數公司定期審核公告範例](https://taiwanindex.com.tw/news/450)
- [FTSE Russell 指數系列](https://www.lseg.com/en/ftse-russell/indices/twse-taiwan)
- [歷史定期審核範例（2014）](https://research.ftserussell.com/products/index-notices/home/getnotice/?id=704147)

需要完整歷史錨點名單、歷年定期及非定期調整、實際生效日、下市公司行情與公司行動資料。公告存在不代表已收集完整。正式回測前不輸出真實市場績效。

[返回排行榜](../dashboard/README.md)

## 2026-11-25 實證檢驗結論

[完整研究結果及反證檢查](RESEARCH_RESULT.md)。四檔股票池的穩健性檢驗已完成；150檔正式回測仍受歷史資料完整性限制，不啟用實盤策略。
