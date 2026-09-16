# DONE — Cloud / Mobile Opportunity Radar
Date: 2026-09-15

## 2026-09-16 Remote-use quality patch

### Source Quality / False Negative Audit

- 基準：commit 7504697 的 119 筆 CSV（116 HN）；唯讀檢視 total score 最高 20 筆。108/119 primary 為 NO_CONCRETE_BUYER_OR_USER_NEED。
- 2 筆可人工追問的 extraction false negatives：HN 49722006（structured output／固定 workflow）、49722980（agent 改方向後殘留 code）；0 筆已具立即聯絡資格。hard gate 未改。
- Primary 原因互斥且總數等於 processed；near misses 只供診斷、不補滿 5 筆。HN 改用需求片語＋原文需求過濾；本次查詢 freelancer thread 僅回傳過期月份，未加入。
- Targeted tests → 完整 53 tests PASS（full suite 一次）；production data/output 雜湊不變。Fresh preview 過濾 237 個 HN 搜尋命中（含跨查詢重複），無合格 HN 留下；保留其他来源 3 筆，Action Queue 無垃圾。這證明雜訊未進入，不代表已找到新機會。actionlint / git diff 檢查 PASS。

- 手機 Feedback Issue、Cash／Market 分數、Solo-fit gate、30 天痛點群組與最多 3 件 Action Queue 已整合現有 JSONL／SQLite；CONTACTED 及後續成交狀態不重複推薦。
- 新增 action_queue.md、pain_clusters.md、filter_summary.md；daily_report 頂端顯示健康狀態，依行動／Cash／重複痛點／LedgerDrop／健康排列。文件驗證需要實際 workflow 證據。
- 281 筆既有 history 唯讀回測：0 actionable／0 Cash／0 合格群組；法院案例非 actionable，DN Colleges 為 TOO_LARGE。兩份 production history SHA-256 不變。
- Targeted tests 通過後，完整 50 tests PASS；production data/output SHA-256 不變。actionlint 與 git diff 檢查 PASS。
- 未新增來源、服務、費用或依賴；排程仍為台灣 07:17。Feedback 為保守規則與人工回報，不等於已驗證市場需求。

## 1. Windows 關機後，什麼仍能工作
GitHub 的每日排程、HN / RSS / UK 公開來源收集、Issue intake、同一套 classify/score/dedupe/report，以及 report + JSONL state commit。Windows local workflow 保留，不參與雲端執行。

## 2. GitHub Actions 能否獨立執行
已在 GitHub 標準 Ubuntu runner 獨立成功執行，包含真實取數、tests、report 和 state 自動 commit。無本機路徑或服務依賴，詳見本文末 Live verification。
Workflow 僅在公開 repo 的預設分支執行，有 contents:write / issues:read、10 分鐘 timeout、concurrency 和明確輸出 allowlist。測試失敗不 commit；來源失敗只記錄並繼續。不 force-push。

## 3. 每天何時
UTC 23:17，即台灣次日 07:17。避開整點但 GitHub 排程仍可能延遲；不是準點 SLA。workflow_dispatch 可手機手動觸發。公開 repo 60 天無活動可能被 GitHub 停用排程，手機 Actions 可查看啟用狀態。

## 4. 手機去哪裡看
[Daily report](https://github.com/love20050402-hub/market-research-engine/blob/main/output/daily_report.md)
看 Strongest Signal Today + Recommended Action，以及頂端時間。
[Actions](https://github.com/love20050402-hub/market-research-engine/actions/workflows/daily-radar.yml) 有 job summary 與錯誤 logs。

## 5. 真正全自動的來源
- HN Algolia：四個搜尋，每組最多 30 筆，近 30 天。
- We Work Remotely 官方 RSS：一個 feed，最多檢查 100 筆，只留近期 Contract；排除 full-time、recruiter、commission-only，再進原分類規則。
- UK Contracts Finder 官方 OCDS：一個請求，近兩日最多 50 個 notice，只留開放、未截止、SME-suitable、GBP <=25,000、有公司/買方網站與 evidence URL 的數位工作。
這些來源於 Windows 真實 preview 均回傳成功；當時 WWR 4 筆來源候選、CF 0 筆符合嚴格條件。來源可用不代表當天一定有合格 lead。

## 6. 仍需 manual input
X、Reddit 原文；缺少登入可見內容／動態內容的網頁；未提供公司、網站或地區的資料。手机用 Radar Intake Issue，URL: + TEXT:；僅 owner/member/collaborator 的公開 Issue 進入 pipeline。可選 metadata 必須自行核對。

## 7. X 為什麼需要 manual
不使用付費 API、cookies、登入、非官方端點或反爬繞過。URL 本身無法可靠提供完整正文，所以保留人貼原文。Issue 內容只當資料，不進 shell，也不由 Issue 觸發額外 URL 抓取。不自動發訊息或關 Issue。

## 8. UK Lead Radar 從哪裡來
WWR feed 提供雇主名稱、明示公司網站及國別；仍要求小公司與實際需求證據。另加入 Contracts Finder 有證據的小額數位採購買方，清楚標成公共採購，不把 SME eligibility 當成買方規模。
输出包含 company、website、evidence_url、detected_need、evidence_text、possible_offer、score。沒有證據 URL 不進 Top 5。不補假公司；可能零筆。

## 9. Production history 和 tests 隔離
- 本機 SQLite 不變；雲端 data/github_history.jsonl 從空開始，兩者使用同一 History identity 邏輯，不自動互相覆蓋。
- JSONL 原子寫入、writer lock，損壞 state 不會自動 reset。
- --fresh-preview 將原 history 以 read-only 載入記憶體，包含 SEEN，只寫 output/preview，不改 daily report 或 history。
- tests 全部用 TemporaryDirectory；scripts/run_tests.py 增加 production path guard 和前後 SHA-256 檢查。
- Fixture 匯入正式根目錄被阻擋，除非使用 fresh preview。
- Audit 確認原 unittest 已隔離，先前「全部 SEEN」源自直接正常模式試跑正式 history，而非 unittest fixtures。

## 10. 費用
沒有新增費用、信用卡、服務、VPS、付費 API 或依賴。現有 repo 為 public，使用標準 ubuntu-latest runner。不使用 artifact/cache 儲存；state 和報告直接進 Git。repo 若改 private，workflow 會跳過，不能默默轉成付費執行。沒有修改帳戶 billing 設定。

## Quality gate
- Windows Python 3.14.7：42 tests PASS。
- 真實 WSL Ubuntu Python 3.14.4：42 tests PASS。
- 兩平台 production data/output SHA-256 前後不變。
- actionlint 1.7.12 官方 release（下載 checksum 校驗）：workflow syntax / expressions 檢查 PASS。
- 真實 fresh preview 成功，HN、RSS、CF、GitHub Issues 都有 Source health。
- 測試涵蓋 JSONL/SQLite 相同 NEW/SEEN/UPDATED、duplicate、meaningful budget update、preview 不寫 history、空源、timeout、壞資料、malformed history、不信任 Issue、Unicode/Windows 路徑、Markdown、UK evidence/deadline/value gate。
- 原 18 項 engine 測試仍通過；原 run.py / scoring / collectors 未改。
- 沒有新增 UI、Radar 類別、雲端服務或付費 dependency。

## Live verification
- 已發布到既有公開 repository main。
- [首次實際執行成功](https://github.com/love20050402-hub/market-research-engine/actions/runs/34935938229)：Ubuntu 24.04.5 / Python 3.11.16。取數、41 tests、SHA-256 不污染檢查、job summary、commit/push 全部成功。
- [Bot commit](https://github.com/love20050402-hub/market-research-engine/commit/9c4a2126edc8b61dc667ead3d1973272b7feff45)：radar: daily update 2026-09-15。119 筆 NEW JSONL records；9 個 CSV/Markdown 報告與 state 同一 commit。
- 讀回 GitHub raw report/state，確認 HN 四查詢、RSS、Contracts Finder、Issue API 都成功；當時沒有開放 Intake Issue，UK 合格 lead 為 0，未用 fixture 填數。
- Review 真實 runner logs 發現舊 action 的 Node 20 deprecation。已改成官方 Node 24 checkout v5 / setup-python v6，固定各自核對過的 commit SHA。
- [第二次雲端執行成功](https://github.com/love20050402-hub/market-research-engine/actions/runs/34936209138)，新 Node 24 actions、tests、commit 全部通過；由最新 commit c6ce83d 的固定 SHA 讀回 state，確認 119 SEEN，沒有把舊資料重新標成 NEW。
- 手機每日報告另提供 output/preview/daily_report.md 連結，使用同次取數的記憶體結果產生，包含 SEEN，不額外抓來源或更動 history。
- [最後一次執行成功](https://github.com/love20050402-hub/market-research-engine/actions/runs/34936422457)：最終 42 tests、summary、commit/push 全部通過；[自動提交 e540ed5](https://github.com/love20050402-hub/market-research-engine/commit/e540ed56b05a1a8e4e3b8961f1fcd21b6e86feed) 已包含 [手機只讀預覽](https://github.com/love20050402-hub/market-research-engine/blob/main/output/preview/daily_report.md)。已直接讀回此 commit 的 report/preview，確認 SEEN 內容可見。
- 本機舊報告在同步時保留於 output/local-before-cloud-20260915/；原本 data/opportunity_history.sqlite3 不搬移、不覆寫。雲端 JSONL 為獨立檔案。

## 已知限制
規則主要支援英文，仍可能誤判語境；人工檢查來源與時效。Feed 有數量上限、沒有遍歷所有 UK 公司。私密資料不要貼入公開 Issues；report 與 cloud history 也會公開。雲端 state 和報告是同一 commit，不保證 GitHub 永不延遲或中斷。日誌留在 GitHub job logs／本機 logs，不購買外部儲存。
