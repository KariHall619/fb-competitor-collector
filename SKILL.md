---
name: fb-competitor-collector
description: Use when a business user wants to collect visible Facebook competitor/internal-page posts from their logged-in normal Chrome tab, import, deduplicate, filter, or sync post links to the Feishu sheet FB竞品帖子链接 through natural language in Codex. Uses OpenCLI Browser Bridge as the only live Facebook capture route, with local SQLite storage and Feishu lark-cli sync.
---

# FB Competitor Collector

This skill turns business-language requests into the first-stage FB competitor workflow:

```text
Chrome 已登录主页可见内容
-> 用 3h/12h/1d 等相对时间确定候选窗口
-> 打开候选帖子详情页确认精确发帖时间和评论引流链接
-> 标准化 -> SQLite 去重入库 -> 飞书同步 -> 条件筛选
```

Keep the user-facing interaction in natural language. Do not ask business users to run shell commands unless they explicitly want commands.

## Live Capture Rule

Live Facebook capture has exactly one supported route:

```text
OpenCLI Browser Bridge reads the user's normal Chrome tab where Facebook is already logged in and posts are visibly loaded.
```

If OpenCLI Browser Bridge is unavailable, stop and report the setup issue. Do not use another browser automation route for live Facebook capture.

If only the OpenCLI daemon is down, run the project environment check with `--fix-opencli` or execute bounded OpenCLI doctor/daemon recovery before asking the user. If the daemon is running but the Chrome extension is not connected to the business Chrome profile, that remains a human/profile setup blocker.

If the page shows a login prompt, visitor preview, or only one preview post, stop immediately with `human_intervention_required`. Tell the user to manually log in or confirm the Chrome profile, and do not keep scrolling, import, or sync.

## What Business Users Can Say

Map common requests as follows:

- “检查一下这个工具现在能不能用”
  - Run `python3 scripts/check_env.py --config config/settings.yaml`.
  - Report lark-cli status, Feishu source/output config, forced user identity, and OpenCLI Browser Bridge readiness.
  - If `recommended_capture_route.route` is not `opencli_browser_bridge`, stop before live capture.

- “试一下目标 Facebook 页面能不能抓到帖子正文”
  - Run `python3 scripts/check_env.py --config config/settings.yaml`.
  - If OpenCLI Browser Bridge is ready, use the OpenCLI Browser Bridge to list open tabs, claim the Facebook tab the user can visually see, then evaluate `scripts/fb_dom_extractors.js` in that tab.
  - Do not write Feishu during this test.

- “采集竞品调研账户第一个账号今天的帖子”
  - Run the resumable account job as the business entrypoint: `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --sync`.
  - Use `scripts/read_accounts.py` if the first account URL is needed from Feishu.
  - Ask the user to keep the target Facebook account page open in normal Chrome if no matching tab is available.
  - Treat relative labels such as `3h`, `12h`, and `1d` as homepage candidate-window clues only.
  - If the user says they can see a known checklist such as “13 条” or `38m,1h,2h...`, pass `--expected-post-count` and/or `--expected-labels` into `run_account_job.py`; do not call the run complete when the expected coverage is missing.
  - If extraction reports `capture_blocked`, `login_required`, or `visitor_preview`, stop immediately and ask for human intervention.
  - If the run is interrupted by token refresh, OpenCLI recovery, or Codex context changes, run the same `run_account_job.py` command again with `--resume-only` when appropriate. Do not manually write ledger rows and call the job finished while enrichment is still pending.

- “把这份抓取结果导入内容库”
  - Use `python3 scripts/import_existing_result.py --config config/settings.yaml --input <file> --no-sync`.
  - If no file is provided, ask for the JSON/CSV file or pasted rows.

- “把结果同步到飞书”
  - Confirm `feishu.output_spreadsheet_url` is configured.
  - Prefer `scripts/run_account_job.py --resume-only --sync` for account-scoped capture results so pending enrichment is reported.
  - Direct `import_existing_result.py --sync`, `filter_posts.py --sync`, and `sync_feishu.py` can still upsert ledger rows, but their `run_status` / `enrichment_completion` must be reported if incomplete.

- “筛选 5 月 21 日的竞品帖子”
  - Use `python3 scripts/filter_posts.py --config config/settings.yaml --date YYMMDD --account-type competitor`.
  - Add `--sync` only when the user asks to写入/同步/输出到飞书.

- “筛选浏览量大于 10 万” or “筛选点赞超过 100”
  - Use `--hot-views` or `--hot-likes`.
  - If current data has no views/likes, say the result is empty because current records do not include engagement values.

## Script Routing

Run all commands from the skill root.

| Intent | Script |
| --- | --- |
| Environment check | `python3 scripts/check_env.py --config config/settings.yaml` |
| Read Feishu accounts | `python3 scripts/read_accounts.py --config config/settings.yaml` |
| Current Chrome tab extraction | OpenCLI Browser Bridge `bind -> tab list/select -> eval(scripts/fb_dom_extractors.js)` |
| Verify OpenCLI Browser Bridge backend | `scripts/check_opencli_runtime_backend.mjs` from the OpenCLI Browser Bridge runtime |
| Verify FB exact timestamp capture | `scripts/opencli_verify_exact_time.mjs --run` from the OpenCLI Browser Bridge runtime |
| Import existing JSON/CSV | `python3 scripts/import_existing_result.py --config config/settings.yaml --input <file> --no-sync` |
| Import and sync new rows | `python3 scripts/import_existing_result.py --config config/settings.yaml --input <file> --sync` |
| Resumable account capture + sync | `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --last-hours 24 --sync` |
| Capture with visible checklist | `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --sync --expected-post-count 13 --expected-labels "38m,1h,2h"` |
| Resume interrupted account job | `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --resume-only --sync` |
| Status-only account check | `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --resume-only --status-only --sync --dry-run` |
| Fast partial capture/import | `python3 scripts/run_capture_pipeline.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --partial` as a lower-level helper only |
| Resume enrichment queue | `python3 scripts/enrichment_worker.py --config config/settings.yaml --stages detail_time,lead_link,engagement,post_type,article_material --account-url <url> --date YYMMDD --limit 50` |
| Audit missing fields and queue refetch | `python3 scripts/audit_fields.py --config config/settings.yaml --fix` |
| Filter local library | `python3 scripts/filter_posts.py --config config/settings.yaml ...` |
| Filter and sync | `python3 scripts/filter_posts.py --config config/settings.yaml ... --sync` |
| Prepare raw OpenCLI capture | `python3 scripts/prepare_capture_result.py --input <raw.json> --output <prepared.json> --target-date YYMMDD` |
| Detail enrichment | `node scripts/opencli_enrich_post_details.mjs --input <prepared.json> --output <detail_enriched.json> --target-date YYMMDD` |
| Fetch article material | `python3 scripts/enrich_article_summaries.py --input <detail_enriched.json> --output <with_article_material.json>` |
| Apply Codex Chinese summaries | `python3 scripts/apply_article_summaries.py --input <with_article_material.json> --summaries <summaries.json> --output <ready.json>` |
| Export SQLite summary requests | `python3 scripts/export_summary_requests.py --config config/settings.yaml --output exports/summary_requests.json` |
| Audit local summaries | `python3 scripts/audit_story_summaries.py --config config/settings.yaml` |
| Local acceptance test | `python3 tests/test_local_pipeline.py` |

## Data Contract

Minimum importable record:

```json
{
  "post_url": "https://www.facebook.com/...",
  "article_url": "https://...",
  "story_summary": "简述"
}
```

Preferred fields:

- `account_name`
- `account_url`
- `account_type`: `competitor` or `internal`
- `post_url`
- `raw_fb_url`
- `parent_post_url`
- `fb_link_kind`: `parent_post`, `reel`, `photo`, `video`, or `facebook`
- `post_type`
- `posted_date`: `YYMMDD`
- `posted_at`: hour-level or better post time, e.g. `2026年5月19日 17:00`; if only a Facebook relative label is available, estimate from crawl time and mark `time_source=relative_estimated`
- `relative_time_text`: visible FB label, e.g. `1h`
- `article_url`
- `lead_url_raw`
- `landing_url`
- `lead_link_status`: `qualified` only after a comment/reply lead link resolves to an external site
- `lead_link_source`: `comment` or `comment_reply`
- `story_summary`
- `summary_source`: `article` when the summary is based on article material
- `views`
- `likes`

Rules:

- `canonical_post_url` is the dedupe key.
- Logged-out Facebook pages must return `login_required`; do not import or sync visitor-preview rows.
- Visitor-preview pages must return `human_intervention_required` immediately; do not keep trying to scroll because Facebook commonly exposes only one preview post.
- Empty shells, comment-only blocks, or pages without visible real posts are blocking evidence.
- Missing `views`/`likes` is allowed; write note `互动数据未确认`.
- Never invent metrics.
- Never store passwords, cookies, API keys, or tokens.
- Capture must keep `photo.php`, `/photo/`, `/reel/`, `/watch/`, and `/videos/` candidates. These are valid FB content candidates and must not be dropped just because a parent `/posts/` link is missing.
- Parent post links are best-effort dedupe helpers. If a parent link is available, store it in `parent_post_url`; if not, keep the original `raw_fb_url` / `post_url` and leave later similarity review to a separate pass.
- Formal output requires a lead link posted by the account in the comment area or a comment reply. The link must resolve outside Facebook/Meta and be stored as `landing_url`; set `lead_link_status=qualified`.
- A comment/reply lead link already captured from the homepage or post comments is authoritative. Detail-page enrichment must not overwrite it with unrelated external links from right-column ads, suggested posts, feed ads, or other non-comment page surfaces.
- Missing share count, parent post URL, exact time, summary, or lead link must not drop the candidate at capture time. Keep the candidate as `needs_enrichment`; normal `--sync` may still upsert it to the formal Feishu table with a `待补抓：...` marker in `是否采用`.
- Normal `--sync` writes confirmed Facebook post candidates to the formal Feishu ledger even when fields are incomplete. Estimated time, missing article summary, missing lead link, missing engagement, missing post type, or incomplete coverage must be marked in `是否采用` as `待补抓：...`.
- Use `--strict-ready-only` only when the operator explicitly wants to sync complete `ready_for_output` rows. In strict mode, reject estimated relative-time sources such as `relative_estimated`, `relative_hour`, or `relative_label`, and reject non-article Chinese summaries.
- Relative labels such as `19m`, `2h`, `12h`, or `1d` are homepage windowing clues only. Use them to decide which visible posts should be opened for detail enrichment and where the scroll boundary probably is. Do not convert them into `posted_at` for formal output. Confirm `posted_at` from Facebook's timestamp tooltip or DOM attributes such as `aria-label`, `title`, `datetime`, or `data-tooltip-*`.
- Timestamp tooltip capture is automated by the skill. First try synthetic page hover through OpenCLI Browser Bridge; if Facebook does not show the tooltip, the skill may use OpenCLI Browser Bridge mouse movement as an automated fallback. Do not ask the business user to manually hover timestamps.
- Human intervention is only for blocking states such as login expiry, visitor preview, CAPTCHA/risk control, the wrong Chrome profile, or a page where posts are not visibly loaded.
- Before deleting any remaining relative-time fallback code, run the exact-time verifier against a real logged-in Facebook tab through the trusted OpenCLI Browser Bridge runtime and require `status=exact_time_confirmed`.
- Short posts must be kept if they have a valid FB content URL. If comment/reply lead link, landing URL, article summary, engagement, or exact time is missing, keep them as `needs_enrichment` instead of dropping them.
- For scale-out runs, first import visible candidates as `partial_review`, then resume queued enrichment stages in SQLite. Normal formal `--sync` upserts auditable candidates by post URL and fills missing-field reasons in `是否采用`; use `--strict-ready-only` only when the operator explicitly wants the legacy ready-only gate.
- For business capture-and-write requests, prefer `run_account_job.py` over manual stage stitching. The job result must be interpreted by `run_status`: `complete` means the scoped job finished; `synced_ledger_incomplete`, `incomplete_pending_tasks`, `coverage_incomplete`, or `needs_codex_summary` means Feishu may have ledger rows but the capture job is not done.
- `run_account_job.py` includes `next_commands`; when a run is incomplete or blocked, use those commands as the first recovery path instead of manually stitching later stages.
- `run_account_job.py --resume-only` recovers scoped stale `running` enrichment tasks before worker passes. The account job default stale window is 15 seconds so interrupted Codex/terminal runs can continue quickly; increase `--resume-stale-running-seconds` when a previous worker may still be active.
- Use `--sync-partial --dry-run` only for business preview output that must not affect the formal table.
- If current-tab extraction returns `coverage_incomplete=true`, the run hit the snapshot cap while still finding new candidates. Raise `--max-snapshots` or restart from the account homepage top before declaring the visible date window fully covered.
- If the user supplies a visible expected count or label checklist, missing expected posts/labels is also `coverage_incomplete` even when scrolling itself looked stable.
- Re-importing the same post must preserve higher-quality stored fields. Do not overwrite confirmed detail time, qualified comment/reply lead links, external landing URLs, valid Chinese article summaries, engagement values, manual `是否采用`, or final output status with weaker partial data.
- `enrichment_worker.py --stages summary` does not generate summaries. It only verifies whether a valid Codex-written Chinese summary has already been applied; otherwise it leaves `requires_codex_chinese_summary`. Generate Chinese summaries from `export_summary_requests.py` output and apply them with `apply_article_summaries.py --config ... --summaries ...`.

## Feishu Workflow

Target business sheet: `FB竞品帖子链接`.

Configured source/output documents:

- Source/read-only account workbook: `source_spreadsheet_url`
- Output/write workbook: `output_spreadsheet_url`
- Current output sheet id: `44013b`
- Current output columns are the Feishu A-K headers from `feishu.field_schema.output_headers`: `账号`, `账户类型`, `帖子链接`, `帖子类型`, `发帖时间`, `文章链接`, `故事概要`, `互动数据（点赞量）`, `浏览量`, `是否采用`, `对应站内链接`.
- `scripts/field_schema.py` owns output header aliases, account-sheet header roles, and output row ordering. Do not create another Feishu row mapping in a separate script.
- Never write to the source account workbook.

Before real sync:

1. Run the built-in Feishu auth preflight before capture/import work if the command will do a real Feishu write.
2. The preflight auto-sets `lark-cli config default-as user` and `lark-cli config strict-mode user`.
3. If `auth status` reports `identity=user` but `tokenStatus=needs_refresh`, try CLI recovery first and re-check status.
4. If silent recovery is impossible, auto-start `lark-cli auth login --json --no-wait` and report its verification payload; stop only after this automated attempt.
5. If status falls back to `bot`, stop before writing and require user identity.
6. Use dry-run first when possible.

If Feishu sync fails, report the exact `lark-cli` error and keep local SQLite results intact.

## Current MVP Boundary

First stage only:

- collect visible FB post links and text from the same-profile capture window
- import links
- normalize fields
- SQLite storage and dedupe
- Feishu sync
- filtering

Do not implement article generation, site publishing, FB lead-post generation, subagent chaining, or hot-theme similarity matching in this stage.

## Project-Owned Extractors

- `scripts/fb_dom_extractors.js`: visible DOM post-link, timestamp, external-link, and engagement extraction.
- `scripts/opencli_extract_current_tab.mjs`: syntax-checkable reference for the current-tab route. Actual live execution should use the OpenCLI Browser Bridge runtime.
- `scripts/opencli_enrich_post_details.mjs`: post detail exact-time, comment/reply expansion, lead-link resolution, target-date filtering, and ready/needs-enrichment status updates.
- `scripts/field_schema.py`: Feishu A-K output format and account/source sheet header aliases.
- OpenCLI's built-in Facebook adapter is a browser/connectivity dependency and research reference; do not use its generic `facebook feed` columns as this project's final business contract.

## Cross-platform Runtime Detection

The project configuration is cross-platform by default:

```text
lark_cli_path: auto
opencli_path: auto
opencli_session: fb-competitor
```

`scripts/config_loader.py` resolves the current platform before scripts run:

- Mac keeps the validated `/Users/a1/.npm-global/bin/lark-cli` override.
- Windows uses `lark-cli.cmd` from PATH unless `platform_overrides.windows.lark_cli_path` provides a full path.
- OpenCLI Browser Bridge is resolved from PATH or npx and checked through the OpenCLI daemon status.

When helping a Windows business user, run `python3 scripts/check_env.py --config config/settings.yaml` first and read the `runtime`, `lark_cli`, and `opencli_browser_bridge` sections. Only ask them to edit `platform_overrides.windows.*` if auto detection cannot find the installed command.

The handoff checks still require:

- OpenCLI Browser Bridge availability/profile setup
- Facebook login in the same normal Chrome profile
- Feishu write preflight with `identity=user` and `tokenStatus=valid`; write paths auto-recover `needs_refresh` before asking for manual login
- OpenCLI daemon recovery via `check_env.py --fix-opencli`; extension/profile connection is still a human setup blocker if recovery cannot connect it
- scheduler setup, if daily automation is enabled later
