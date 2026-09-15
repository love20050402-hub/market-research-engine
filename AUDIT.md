# Opportunity Radar Audit — 2026-09-15

## 現況
- 主專案：`C:/Projects/market-research-engine`，Python CLI、標準函式庫、HN Algolia、Reddit OAuth、Ollama 分析。
- `run.py` → collectors → prefilter → pain extraction → clustering → validation/scoring → Markdown。
- `collectors/hackernews.py` 已有 HTML 清理及 HN 正規化；`pipeline/prefilter.py` 有 payment/pain/workaround/seeking 詞組。
- 原 scoring 是市場群集 100 分制，非單篇 0–5；不直接更改它。
- `tests/test_engine.py` 有 fake HTTP、schema、scoring、pipeline 測試。
- `README.md` 描述原流程。已有 output JSON；上層 `top_candidates.csv` 為早期 HN 匯出。
- 上層 `product-opportunity-research` 有 n8n HN workflow 與 JS 測試；不修改。
- 在目前 workspace 檔案清單未找到 X Radar、benchmark_review.csv 或每日持久 history。
- Git 原有未追蹤的研究政策及候選文件，保留。

## 沿用
HN collector / clean_text、原始 JSON/CSV 相容匯入；參考既有 pain 詞組但採取更保守的逐句需求規則。保留舊 CLI、測試和評分。

## 缺少與今日新增
統一 0–5 schema、保守 evidence rules、Money/Pain/UK/LedgerDrop、手動文字與 URL intake、免費有界 HN 收集、CSV/JSON 匯入、SQLite history、NEW/SEEN/UPDATED、每日 Markdown、失敗隔離與 logs、fixtures 和回歸測試。

## 實作邊界
使用者本次明確授權內部 research engine 開發與公開來源收集；不涉及產品/UI/部署。無新 dependency、無 LLM 呼叫、無 Reddit OAuth 或 X 登入需求。UK 公司須具明示地區、規模與需求證據；不足時空表。

## Cross-platform / mobile audit — 2026-09-15
- Current remote: public `love20050402-hub/market-research-engine`, default branch main, write access available. Local Radar files were not yet published; remote only contained the original engine.
- Runtime uses pathlib and standard library; absolute Windows path occurs in the local README example only. Add repository-relative Linux command while keeping that example.
- Existing unittest fixtures already used temporary directories. Production SEEN pollution came from direct normal-mode development runs, not those isolated tests. Add preview and an explicit production guard so this distinction cannot be missed again.
- SQLite stays local; cloud uses a separate tracked JSONL state with the same History identity implementation. Do not seed cloud with earlier production dry-run data.
- output/ was entirely ignored; allowlist only the ten requested report CSV/Markdown files, leaving preview/raw output ignored. Local manual intake remains ignored and Issues supply cloud intake.
- Tests now run on Windows and real Ubuntu; both verify production bytes unchanged. Workflow is validated with official actionlint before publishing and then verified on GitHub itself.
