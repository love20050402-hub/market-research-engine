# Format and operations

## Manual intake
One post per block, separated by a line containing `---`. Optional metadata keys, one per line: `url`, `author`, `title`, `published_at` (ISO 8601), `company`, `website`, `country`, `contact_page_or_public_contact`. Remaining lines are actual source text. Metadata must be verified by the operator.

UK leads require company, website, explicit UK location, small business scale and actual need evidence. Contact pages are never guessed; missing contact stays UNKNOWN. A .uk suffix alone does not establish location.

## Existing data
`python run_daily_radar.py --offline --import-file C:\Projects\top_candidates.csv`

Repeat `--import-file` for CSV or JSON arrays, including existing `output/raw_signals.json`. Supports title/text/author/created_at/url and source_title/source_text/source_url; hn_url takes precedence over external article URLs. UTF-8/BOM accepted. Malformed imports/rows are logged and skipped. Import original evidence, not generated report CSVs.

## Schema and ranking
`radar/core.py:FIELDS` is the CSV column contract. Money/Pain/LedgerDrop share it. Five score_* fields are integers 0–5; total_score is their mean (0–5).

- Authenticity: first-person ownership, concrete task, quantity or URL; heuristic only.
- Payment: explicit buyer payment/budget evidence scores 5; eligible request without explicit payment scores 2; negative/free signals score 0.
- Pain: concrete pain, recurrence/quantity, search for solution.
- Fit: scoped document, spreadsheet, automation, website, design or content work.
- SaaS: repeated concrete pain and stated alternative/feature gap; hypothesis only.

Report fields contain source excerpts or labelled hypotheses/UNKNOWN. Rules primarily support English; sarcasm, quotations and complex negation can be misclassified. Scores are not purchase probabilities. Verify identity, date and demand before contact. No messages are sent.

## Sources and limits
- Automatic HN Algolia: 4 searches, up to 30 results each, 1 second delay, 12 second timeout, last 30 days. Stories/comments; comment parent titles do not score. No pagination/retries.
- Manual HN item URLs: specified public item only.
- Manual HTML: maximum 10 URL entries; public HTTPS; retrievable robots.txt must allow access; max 1 MB, 12 second timeout, no redirects, JavaScript or login. Robots failures require pasted text. No linked-page traversal. Layout noise can require manual cleanup. No arbitrary HTML site is guaranteed compatible.
- X/Reddit: paste text; no API, login bypass or scraping in this CLI. Existing Reddit OAuth remains separate.
- UK: strict filter over evidence, not a company directory crawler. HN alone may produce zero UK leads.

### Cloud / mobile update
- WWR official RSS: one GET, at most first 100 items; only recent Contract listings, excluding full-time, recruiter and commission-only offers. Employer website comes from the feed's explicit URL field. Still passes ordinary evidence qualification; the job board itself is not a lead. [Official RSS](https://weworkremotely.com/remote-job-rss-feed), [usage guidance](https://weworkremotely.com/api-terms-and-guidelines).
- UK Contracts Finder official OCDS: one GET, previous two days, limit 50, no pagination. Only active tender notices, future deadline, explicit SME suitability, GBP 0–25,000, scoped digital task, UK buyer, buyer website and notice URL. SME suitability describes supplier eligibility, NOT buyer size. Public buyers are labelled separately, not presented as small private companies. Larger/unknown-value/closed contracts are excluded. [Official API](https://www.contractsfinder.service.gov.uk/apidocumentation/), [UK open data policy](https://www.gov.uk/government/publications/open-contracting).
- GitHub open Issues: up to 200, most recently updated first. Trusted repo members only; title starts Radar Intake, URL + TEXT required. Open Issues re-enter the ordinary deduper; edits update the same evidence URL. Close reviewed Issues if intake reaches the cap. Issue creation date is never substituted for the original publication date. No issue writes or external URL fetches from Issue content.
- JSONL retains the same logical records/identity model as SQLite. `--state-file` opts into JSONL; corrupt state fails closed rather than becoming an empty history. Atomic temp-file replacement and a writer lock protect JSONL. GitHub concurrency serializes workflow runs. A non-fast-forward push rebases once, never force pushes. Report and state are committed together only after tests pass.
- `--fresh-preview` uses an in-memory copy loaded read-only from SQLite or JSONL. It may show SEEN and older current-source results; dates remain visible. It writes only preview reports, not production reports/state. Tests use temporary roots, and scripts/run_tests.py rejects production access and checks persisted bytes.
- Public reports/state are intentionally readable in GitHub; local intake and local SQLite are excluded. No credential migration, no billing setting changes, no artifact uploads. Standard public runner use is free: [GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions). [Schedule caveats](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## History
`data/opportunity_history.sqlite3`: local SQLite, no server. Exact canonical URL, normalized text hash, or same author/company-domain plus similar title/text identify repeats. Company-domain also deduplicates UK exports. Different tasks by the same author can remain separate.

NEW = first observation; SEEN = same content; UPDATED = changed content/identity metadata. Important update = changed payment evidence/category or score changing by at least 1. first_seen_at persists; last_seen_at is this run's UTC time. Algorithm changes alone do not create fresh source evidence. CSVs show this run; history retains earlier observations. Keep the data folder. Concurrent runs fail on the SQLite lock. Back up the closed database file if needed.

## Tests / isolated demo
```powershell
python -m unittest discover -s tests -v
python run_daily_radar.py --offline --root output/demo --import-file tests/fixtures/radar_samples.json
```
Fixtures are fictional; normal runs never include them. Demo history stays under its root. CSV is UTF-8 BOM for Excel; formula-like text gets a leading apostrophe. Markdown is UTF-8.
