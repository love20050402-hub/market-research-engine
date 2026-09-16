# Opportunity Radar

免費 GitHub Actions + 本機 Python 3.11+；不需套件、付費 API、Ollama 或信用卡。

## 人在外地，只用手機怎麼看

1. 打開 [GitHub repository](https://github.com/love20050402-hub/market-research-engine)。
2. 打開 [output/daily_report.md](https://github.com/love20050402-hub/market-research-engine/blob/main/output/daily_report.md)。
3. 看 **Today's Action Queue**；先確認頂端日期與 HEALTHY／DEGRADED／FAILED。沒有合格機會會顯示 `NO ACTION REQUIRED TODAY`。

## 手機回饋

建立 [Radar Feedback Issue](https://github.com/love20050402-hub/market-research-engine/issues/new?template=radar-feedback.md)，填原始證據 `URL:` 與 `STATUS:`：`GOOD / BAD / MAYBE / CONTACTED / REPLIED / TESTER / PAID / IGNORED`。僅採用 owner／member／collaborator；可編輯同一 Issue，最新更新為準，關閉仍保留結果。每次讀最近更新 200 個 Issues；未見於 history 的 URL 要等該訊號匯入才採用。

BAD／IGNORED 壓低同類「對象＋操作」分數；CONTACTED 與後續 REPLIED／TESTER／PAID 不再推薦聯絡；後三者提高群組驗證價值，不繞過證據門檻。GOOD 提高驗證排序，MAYBE 保持中立。

`output/pain_clusters.md` 看最近 30 天群組（未知發布時間用首次看到時間）；至少 3 位不同作者，或 2 個不同來源且有付款／現行解法，才值得驗證。`output/filter_summary.md` 顯示淘汰原因。Cash 與 Market 分開評分；Cash Top 只收有明確商業需求的 SOLO_FIT，複雜招標保留在 CSV。LedgerDrop 只計入具體文件處理痛點。

今日無新增時，可點報告頂端「查看目前最佳候選」，在手機直接看包含 SEEN 的只讀預覽，不需 terminal。

每天 **台灣時間 07:17（UTC 23:17）** 自動執行，Windows 可以關機。GitHub 排程可能延遲；[Actions](https://github.com/love20050402-hub/market-research-engine/actions/workflows/daily-radar.yml) 可看成功／失敗或手動 Run workflow。只在預設分支執行；公開 repository 長期無活動時 GitHub 可能停用排程。

## 手機丟 X／Reddit 貼文

1. 到 [New Issue](https://github.com/love20050402-hub/market-research-engine/issues/new?template=radar-intake.md) 選 Radar Intake。
2. 填 `URL:`，在 `TEXT:` 下貼實際原文；可加 COMPANY／WEBSITE／COUNTRY。
3. Submit；下次排程會讀取。修改同一 Issue 可更新內容，確認不再需要後可關閉。

這是公開 repo，Issue、雲端 history 與報告都是公開的，僅貼可公開分享的來源。只接收 owner／member／collaborator 的 Issue；不需要 personal token，不會發訊息或自動關閉 Issue。

## 每天怎麼跑
Windows PowerShell 原用法保留：
```powershell
python C:\Projects\market-research-engine\run_daily_radar.py
```

Windows／Linux 在 repository 目錄皆可：`python run_daily_radar.py`。

### 預覽與測試（開發時使用）

`python run_daily_radar.py --fresh-preview`：包含 SEEN，寫到 `output/preview/daily_report.md`；不改正式 history／daily report。

`python scripts/run_tests.py`：暫存資料庫、production path guard、測試前後 SHA-256 檢查。Fixture 試跑須用 `--fresh-preview` 或 `--root output/demo`。

## 我要手動丟 X 貼文怎麼做
1. 開啟 `input/manual_text.txt`。
2. 貼 `url: https://x.com/...`，下一行貼實際原文；多篇以獨立一行 `---` 分隔。
3. 儲存為 UTF-8，再執行上述 command。

只有網址可放 `input/manual_urls.txt`（每行一個）；X/Reddit 仍需貼文字，待補項目見 `output/pending_intake.csv`。

## 結果去哪裡看
`output/daily_report.md`。完整 CSV 同在 `output/`；NEW 與重要 UPDATED 優先，SEEN 留在 CSV。已知超過 30 天的貼文不進每日 Top。

## 出錯怎麼辦
手機看 Actions 的 job summary／失敗步驟與報告 Source health；本機看 `logs/radar.log`。來源失敗會跳過；离線加 `--offline`。history locked 時關閉另一個執行中的 Radar，不要刪 history。

雲端用 `data/github_history.jsonl`（自動 commit）；本機仍用 SQLite，兩者共用同一去重規則，但預設獨立累積、不自動互相覆蓋。雲端從空 state 開始，不上傳本機試跑 history。若要本機查看已下載的雲端 state，用 `--fresh-preview --state-file data/github_history.jsonl`。

僅使用公開 repository 的標準免費 Linux runner；不使用 artifact/cache 儲存、付費 API 或付費 runner。若 repository 改為 private，workflow 會跳過執行以避免費用。詳細啟用與已驗證狀態見 [DONE.md](DONE.md)。

進階格式與限制見 [RADAR_FORMAT.md](RADAR_FORMAT.md)。原 CLI 說明見 [README_LEGACY.md](README_LEGACY.md)。
