# 第三輪：動能品質與風險分散（結果產生前固定規格）

研究問題：M8 在暴漲後及反彈後恢復滿倉，2026-06-22至11-20回撤55.59%。本次不搜尋最佳週期、門檻或權重；全部規則在運行前固定。2026已被觀察，僅壓力測試，不能稱樣本外。原固定2026-11-25 151檔池有倖存者及前視選樣偏誤。沿用月末排名、次開盤、原資料資格、成本與缺價延期。禁止槓桿、放空、組合疊加。

控制：S0、M3、M8，重算並核對M3/M8全期淨值。Q1、Q2固定在原動能排名前20%（向上取整，文獻分組啟發），再選10檔、等權、全額曝險，不與M8過濾疊加。Q3、Q4保留M3的原10檔，僅改權重、全額股票曝險。

- Q1 資訊連續性：Da, Gurun & Warachka (2014), Frog in the Pan, RFS 27(7), 2171–2218, https://doi.org/10.1093/rfs/hhu003 。ID=sign(12m return)*(negative daily return fraction - positive daily return fraction)。取ID最小10檔，同分按原動能排名。用原完整12月視窗、相鄰共同交易日有效收盤報酬，零報酬列入分母，至少180筆有效日報酬（資料品質門檻非績效參數）；不足者不參與Q1。非原論文多空與形成期的精確複製。
- Q2 高點接近度：George & Hwang (2004), The 52-Week High and Momentum Investing, JF, https://doi.org/10.1111/j.1540-6261.2004.00695.x 。取當前還原收盤／過去12個月最高還原收盤最大的10檔，同分按原動能排名。不自行指定80%或90%門檻。調整後收盤代理而非原研究價格資料完全複製。Hao et al. (2016) 台灣研究顯示定錨與近期偏誤並存，證據混合：https://www.sciencedirect.com/science/article/pii/S1059056015001859 。
- Q3 反波動度：Asness, Frazzini & Pedersen (2012), Leverage Aversion and Risk Parity, FAJ, https://doi.org/10.2469/faj.v68.n1.1 。w_i ∝ 1/sigma_i，歸一化，非精確風險平價。選126交易日視窗與前輪M9一致，非原論文3年資產類別波動度；共同有效日報酬至少100筆，否則Q3/Q4同時退回M3等權且記錄。無槓桿。
- Q4 最大分散化：Choueifaty & Coignard (2008), Toward Maximum Diversification, JPM 35(1), 40–51, https://www.tobam.fr/wp-content/uploads/2014/12/TOBAM-JoPM-Maximum-Div-2008.pdf 。最大化 (w dot sigma)/sqrt(w'Cov w)，w>=0、sum(w)=1；與Q3共用126日樣本協方差，等價相關矩陣凸最佳化，不搜尋其他參數。可能配置零權重或少數股票，需報最大權重與有效持股數1/sum(w^2)。若求解失敗中止，不暗中替代。
- 反證：DeMiguel, Garlappi & Uppal (2009), RFS, https://doi.org/10.1093/rfs/hhm075 ：樣本外最佳化未穩定擊敗等權。不能將估計的分散比率當成未來風險保證。
- 判定：同時看報酬、MDD、Sharpe、Calmar、成本、有效持股數；全期、2020H2–2023、2024–2025、2026壓力、截至2025、費用加倍。分期均重新以10萬元起算。優先候選須全期與截至2025均改善M3的Sharpe及MDD，並2026回撤不劣於M3；兩倍成本結果須報告，不靠單一年份獲利下結論。此為探索篩選不是統計顯著性或樣本外驗證，無候選亦接受。不得改前瞻帳本，任何通過者仍須預先固定新前瞻組實際驗證。
