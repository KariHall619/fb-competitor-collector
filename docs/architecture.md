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
3. Treat a normal homepage post block as the primary extraction unit: collect exact time from the time anchor, visible engagement metrics, and account-owned homepage lead links before opening detail pages.
4. Keep valid Facebook content candidates even if fields are incomplete.
5. Open detail/comment surfaces only for missing or special-case exact time, lead link, engagement, post type, and article material.
6. Store candidates and enrichment tasks in SQLite.
7. Generate/apply Chinese story summaries from article material.
8. Run strict quality gate.
9. Sync only complete rows to the formal Feishu table.
10. Report candidate count, final usable count, completion blockers, and special failures.

For multi-account jobs, repeat the full single-account loop account by account through `run_accounts_job.py`. Do not hand-stitch manual loops in chat.

## Low-Disturbance Browser Behavior

The intended operator experience is a separate automation surface that does not interrupt other Chrome usage:

- Project scripts do not pre-open, select, bind, or close Chrome tabs directly.
- Account discovery and detail enrichment both call `opencli facebook fb-competitor-posts ...` directly through the configured OpenCLI command.
- The adapter is initialized with `opencli browser <session> init facebook/fb-competitor-posts` and installed into the real OpenCLI home at `~/.opencli/clis/facebook/fb-competitor-posts.js` by `scripts/install_opencli_adapter.py`.
- The committed adapter implementation source is `scripts/opencli_fb_competitor_posts.js`; runtime code must not copy adapters into project-local `data/opencli-home` or override `HOME`.
- The adapter opens and navigates its own background browser session through OpenCLI's official `cli({ browser: true, strategy: Strategy.COOKIE })` mechanism.
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
