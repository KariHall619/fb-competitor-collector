# Architecture

## Goal

Automate Facebook competitor/internal-page collection from natural-language Codex requests through strict Feishu output.

```text
user request
-> scope interpretation
-> config/auth/runtime preflight
-> OpenCLI Browser Bridge capture in normal Chrome profile
-> homepage candidate discovery
-> detail enrichment
-> SQLite dedupe and enrichment queue
-> article material and Chinese story summary
-> strict final quality gate
-> Feishu output
-> Codex result summary
```

## Runtime Boundary

Live Facebook capture has exactly one supported route: OpenCLI Browser Bridge connected to the user's normal logged-in Chrome profile.

Do not reintroduce Playwright scraping, CDP-only collectors, Codex Chrome Extension collectors, old userscripts, or OpenCLI's generic `facebook feed` output as the business capture route. OpenCLI is the browser control/runtime dependency; project-owned extraction, normalization, enrichment, quality, SQLite, and Feishu code remain authoritative.

## Business Entrypoints

Use these entrypoints for business capture:

- All configured accounts: `scripts/run_accounts_job.py`
- One account: `scripts/run_account_job.py`
- Existing JSON/CSV import: `scripts/import_existing_result.py`
- Local filtering: `scripts/filter_posts.py`

`run_capture_pipeline.py` is a lower-level partial/import helper. Do not use it as the final business "capture and write Feishu" workflow.

## Account Flow

For each account:

1. Start at the account homepage top.
2. Discover all in-window candidates using relative labels only as coverage/window clues.
3. Keep valid Facebook content candidates even if fields are incomplete.
4. Open detail/comment surfaces for exact time, lead link, engagement, post type, and article material.
5. Store candidates and enrichment tasks in SQLite.
6. Generate/apply Chinese story summaries from article material.
7. Run strict quality gate.
8. Sync only complete rows to the formal Feishu table.
9. Report candidate count, final usable count, completion blockers, and special failures.

For multi-account jobs, repeat the full single-account loop account by account through `run_accounts_job.py`. Do not hand-stitch manual loops in chat.

## Low-Disturbance Browser Behavior

The intended operator experience is a separate automation surface that does not interrupt other Chrome usage:

- Account jobs open account pages via OpenCLI automation unless `--no-open-account-tabs` or explicit `--tab-page` is supplied.
- Child jobs receive `--tab-page` and `--opencli-session` instead of binding the user's active tab.
- Detail enrichment may reuse an automation-opened detail tab, with fallback only inside tracked automation tabs.
- Automation-opened homepage/detail tabs and session leases should be closed after the scoped account finishes.
- Never close the user's original manual Facebook tab.

## Human Blockers

Stop with `human_intervention_required` for:

- Facebook logged out.
- visitor preview or only one preview post.
- CAPTCHA/risk-control page.
- wrong Chrome profile.
- Browser Bridge extension not connected after bounded recovery.
- target page not visibly loading real posts.

Do not import or sync visitor-preview data.

## Source And Output Boundary

The Feishu account workbook is read-only. The output workbook is the only write target. See `docs/data-contract.md` for output columns and quality requirements.
