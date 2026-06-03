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
  - Expected labels can use common variants such as `1h`, `1 hour ago`, `1 小时`, or `1小时`; the coverage check normalizes these for matching.
  - If extraction reports `capture_blocked`, `login_required`, or `visitor_preview`, stop immediately and ask for human intervention.
  - If the run is interrupted by token refresh, OpenCLI recovery, or Codex context changes, use the emitted `next_commands` first, or run the same `run_account_job.py` command again with `--resume-only --force-recover-running` when appropriate. Do not manually write ledger rows and call the job finished while enrichment is still pending.
  - Non-`complete` account jobs return nonzero by default so automation cannot mistake partial ledger sync for a finished job. Use `--allow-incomplete-success` only for explicit preview/backward-compatibility checks where JSON will be inspected.

- “采集全部目标账号/所有账号今天的帖子并写入飞书”
  - Run the batch business entrypoint: `python3 scripts/run_accounts_job.py --config config/settings.yaml --target-date YYMMDD --sync`.
  - It reads the Feishu account source sheet, calls the full `run_account_job.py` flow for each enabled account, automatically follows same-account machine-runnable recovery commands for the base `--auto-follow-attempts` budget (default 8), and keeps following same-account machine-runnable `next_commands` up to the hard safety limit while work remains. Do not stop just because the previous attempt did not improve aggregate metrics; stop only on completion, hard blocker, no followable command, or the hard limit.
  - The batch entrypoint may run the same scoped resume command repeatedly. This is expected when one account needs several passes to clear detail fields, post type, article material, generated summaries, and final sync; do not stop merely because the command text repeats.
  - If an account reaches `captured_not_synced` or `resumed_not_synced` during a `--sync` batch, continue the emitted same-account sync recovery command so Feishu receives the completed local rows before reporting done.
  - If an account reports both coverage gaps and pending detail/enrichment work, the batch should auto-follow the scoped `pending_enrichment` command first so already imported rows get complete fields and Feishu upserts before another homepage coverage rerun.
  - Enrichment queue passes must keep both detail fields and article-material/summary work moving. A long `post_type` or detail backlog should not leave Feishu rows with empty `故事概要` when article material can already be fetched and summarized.
  - If an account emits same-account `prepare_failed` or `import_failed` recovery commands, the batch should auto-follow the full-capture rerun up to the hard limit. Keep `worker_failed` manual/fix-first because it means the补抓执行器 or environment returned an unstructured failure, but top-level `next_commands` must still include the original batch rerun command before the child resume command so the full account range can continue after the worker issue is fixed.
  - If the Feishu account source sheet cannot be read, `run_accounts_job.py` should return `accounts_load_failed` with an executable batch rerun command preserving the original date/filter/sync/budget flags.
  - If the account source is readable but the current filters match zero accounts, `run_accounts_job.py` should return `no_accounts` with a `read_accounts.py` inspection command and an executable batch rerun command; do not treat zero target accounts as complete.
  - If a child account job crashes, prints non-JSON, or reports completion without a quality summary, treat it as a script/output-contract failure; top-level `next_commands` should preserve the original batch rerun command before any single-account retry.
  - If the operator provided `--expected-post-count` or `--expected-labels`, the batch must pass those flags into every child account job and preserve them in auth/OpenCLI batch retry commands.
  - By default it opens each target account homepage in Chrome through OpenCLI and closes those automation-opened homepage tabs at the end of the batch; use `--no-open-account-tabs` only when matching account tabs are already intentionally open.
  - If an account homepage cannot be opened through OpenCLI, the batch must still continue later accounts. Report both emitted top-level recovery commands: first the OpenCLI environment fix, then the original scoped batch rerun command with date/filter/sync/budget flags preserved.
  - If a child account job reaches `blocked_opencli` during scoped detail补抓, still report the top-level batch recovery path before the child-only resume: first the OpenCLI environment fix, then the original scoped batch rerun command with date/filter/sync/budget/threshold flags preserved, then any single-account `resume_after_opencli` command.
  - If real Feishu write auth blocks before account reading or Facebook capture, report the emitted `blocked_auth` batch rerun command after the user restores authorization; it must preserve the original date/filter/sync/budget flags.
  - If a child account job reaches `blocked_auth` during sync or scoped resume, still report the top-level batch recovery path before the child-only resume: first the Feishu auth fix, then the original scoped batch rerun command with date/filter/sync/budget/threshold flags preserved, then any single-account resume command.
  - If a child account job reaches `human_intervention_required`, report the original scoped batch rerun command for after the operator restores Facebook login/Profile/page visibility, then any single-account resume command; do not leave the business user with only one-account recovery.
  - Non-`complete` batch runs return nonzero by default. Use `--allow-incomplete-success` only for explicit preview/backward-compatibility checks where JSON will be inspected.
  - Do not manually loop account commands in chat; that is how later accounts, detail enrichment, or summary export steps get skipped.

- “把这份抓取结果导入内容库”
  - Use `python3 scripts/import_existing_result.py --config config/settings.yaml --input <file> --no-sync`.
  - If no file is provided, ask for the JSON/CSV file or pasted rows.

- “把结果同步到飞书”
  - Confirm `feishu.output_spreadsheet_url` is configured.
  - Prefer the emitted `next_commands` or `scripts/run_account_job.py --resume-only --force-recover-running --sync` for account-scoped capture results so pending enrichment is reported.
  - Direct `import_existing_result.py --sync`, `filter_posts.py --sync`, and `sync_feishu.py` can still upsert ledger rows, but their `run_status`, `completion_blockers`, and `enrichment_completion` must be reported if incomplete.

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
| Batch all configured accounts + sync | `python3 scripts/run_accounts_job.py --config config/settings.yaml --last-hours 24 --sync` |
| Capture with visible checklist | `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --sync --expected-post-count 13 --expected-labels "38m,1h,2h"` |
| Resume interrupted account job | `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --resume-only --force-recover-running --sync` |
| Status-only account check | `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --resume-only --status-only --sync --dry-run` |
| Automation hard completion gate | `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --resume-only --status-only --sync --dry-run --fail-on-incomplete` |
| Fast partial capture/import | `python3 scripts/run_capture_pipeline.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --partial` as a lower-level helper only; add `--fail-on-incomplete` or quality threshold flags for automation gates |
| Resume enrichment queue | `python3 scripts/enrichment_worker.py --config config/settings.yaml --stages detail_time,lead_link,engagement,post_type,article_material --account-url <url> --date YYMMDD --limit 50` |
| Audit missing fields and queue refetch | `python3 scripts/audit_fields.py --config config/settings.yaml --fix` |
| Filter local library | `python3 scripts/filter_posts.py --config config/settings.yaml ...` |
| Filter and sync | `python3 scripts/filter_posts.py --config config/settings.yaml ... --sync` |
| Prepare raw OpenCLI capture | `python3 scripts/prepare_capture_result.py --input <raw.json> --output <prepared.json> --target-date YYMMDD` |
| Detail enrichment | `node scripts/opencli_enrich_post_details.mjs --input <prepared.json> --output <detail_enriched.json> --target-date YYMMDD` |
| Fetch article material | `python3 scripts/enrich_article_summaries.py --input <detail_enriched.json> --output <with_article_material.json>` |
| Generate Chinese summaries | `python3 scripts/generate_article_summaries.py --input exports/summary_requests.json --output exports/article_summaries.json` |
| Apply Codex Chinese summaries | `python3 scripts/apply_article_summaries.py --input <with_article_material.json> --summaries <summaries.json> --output <ready.json>` |
| Export SQLite summary requests | `python3 scripts/export_summary_requests.py --config config/settings.yaml --output exports/summary_requests.json --date YYMMDD --account-url <url> --account-type competitor` |
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
- Facebook content identity is centralized in `scripts/models.py::facebook_content_key`. Feishu `post_url` upsert and SQLite canonical URLs must use the same identity rule so photo, video/watch, reel, share, and group-post URL variants update one ledger row instead of creating duplicates.
- OpenCLI snapshot dedupe uses `scripts/opencli_extract_current_tab.mjs::postKey`; keep it aligned with `facebook_content_key` when adding new accepted Facebook URL forms.
- Logged-out Facebook pages must return `login_required`; do not import or sync visitor-preview rows.
- Visitor-preview pages must return `human_intervention_required` immediately; do not keep trying to scroll because Facebook commonly exposes only one preview post.
- Empty shells, comment-only blocks, or pages without visible real posts are blocking evidence.
- Missing `views`/`likes` is allowed; write note `互动数据未确认`.
- Never invent metrics.
- Never store passwords, cookies, API keys, or tokens.
- Capture must keep `photo.php`, `/photo/`, `/photos/`, `/reel/`, `/watch/`, `/video/`, `/videos/`, `/share/`, and group-post candidates. These are valid FB content candidates and must not be dropped just because a parent `/posts/` link is missing.
- Parent post links are best-effort dedupe helpers. If a parent link is available, store it in `parent_post_url`; if not, keep the original `raw_fb_url` / `post_url` and leave later similarity review to a separate pass.
- Formal output requires a lead link posted by the account in the comment area or a comment reply. The link must resolve outside Facebook/Meta and be stored as `landing_url`; set `lead_link_status=qualified`.
- A comment/reply lead link already captured from the homepage or post comments is authoritative. Detail-page enrichment must not overwrite it with unrelated external links from right-column ads, suggested posts, feed ads, or other non-comment page surfaces.
- Missing share count, parent post URL, exact time, summary, or lead link must not drop the candidate at capture time. Keep the candidate as `needs_enrichment`; normal `--sync` may still upsert it to the formal Feishu table with a `待补抓：...` marker in `是否采用`.
- Normal `--sync` writes confirmed Facebook post candidates to the formal Feishu ledger even when fields are incomplete. Estimated time, missing article summary, missing lead link, missing engagement, missing post type, or incomplete coverage must be marked in `是否采用` as `待补抓：...`.
- Feishu upsert must not downgrade previously filled `帖子类型` or article-sourced `故事概要` to blank when a later partial/audit row for the same post is still incomplete. Manual `是否采用` values stay protected; system `待补抓：...` markers may update or clear as gaps change.
- Use `--strict-ready-only` only when the operator explicitly wants to sync complete `ready_for_output` rows. In strict mode, reject estimated relative-time sources such as `relative_estimated`, `relative_hour`, or `relative_label`, and reject non-article Chinese summaries.
- Relative labels such as `19m`, `2h`, `12h`, or `1d` are homepage windowing clues only. Use them to decide which visible posts should be opened for detail enrichment and where the scroll boundary probably is. Do not convert them into `posted_at` for formal output. Confirm `posted_at` from Facebook's timestamp tooltip or DOM attributes such as `aria-label`, `title`, `datetime`, or `data-tooltip-*`.
- Timestamp tooltip capture is automated by the skill. First try synthetic page hover through OpenCLI Browser Bridge; if Facebook does not show the tooltip, the skill may use OpenCLI Browser Bridge mouse movement as an automated fallback. Do not ask the business user to manually hover timestamps.
- Human intervention is only for blocking states such as login expiry, visitor preview, CAPTCHA/risk control, the wrong Chrome profile, or a page where posts are not visibly loaded.
- Before deleting any remaining relative-time fallback code, run the exact-time verifier against a real logged-in Facebook tab through the trusted OpenCLI Browser Bridge runtime and require `status=exact_time_confirmed`.
- Short posts must be kept if they have a valid FB content URL. If comment/reply lead link, landing URL, article summary, engagement, or exact time is missing, keep them as `needs_enrichment` instead of dropping them.
- For scale-out runs, first import visible candidates as `partial_review`, then resume queued enrichment stages in SQLite. Normal formal `--sync` upserts auditable candidates by post URL and fills missing-field reasons in `是否采用`; use `--strict-ready-only` only when the operator explicitly wants the legacy ready-only gate.
- For business capture-and-write requests, prefer `run_account_job.py` over manual stage stitching. The job result must be interpreted by `run_status`: `complete` means the scoped job finished; `synced_ledger_incomplete`, `incomplete_pending_tasks`, `coverage_incomplete`, or `needs_codex_summary` means Feishu may have ledger rows but the capture job is not done.
- For “all target accounts” capture-and-write requests, prefer `run_accounts_job.py` over manual loops. The batch result is complete only when every account-level `run_status` is `complete`; otherwise report the affected accounts and the emitted per-account `next_commands`. The batch entrypoint automatically follows same-account machine-runnable recovery commands before reporting incomplete.
- Report the account-job `quality_summary` in user-facing updates: `coverage_health`, `ledger_candidate_count` / `ledger_usable_rate`, `final_usable_count` / `final_usable_rate`, `top_field_gaps`, `stage_pressure_notes`, and `feishu_sync.run_status`. Treat a high ledger rate with a low final rate as an explicit补抓 state, not as bad write quality or full completion. Use `open_task_stage_counts`, `missing_stage_counts`, and `stage_pressure` to say which stage should resume next.
- `run_account_job.py` also exposes top-level `completion_blockers` mirrored from `quality_summary.completion_blockers`. Use this ordered list as the first human/automation explanation of why the scoped job is not complete: coverage, OpenCLI/auth/login blockers, queued enrichment, Codex summary, field gaps, Feishu sync, or explicit quality threshold failures.
- For acceptance/automation runs, add explicit quality thresholds such as `--require-coverage-complete --min-ledger-usable-rate 1 --min-final-usable-rate 0.9 --min-completion-rate 0.9`. Threshold failures return `quality_threshold_failed` / `exit_status_reason=quality_threshold_failed` while preserving the underlying补抓 `run_status` and `next_commands`.
- `next_commands` preserves explicit quality threshold flags, so retries after `quality_threshold_failed` keep the same acceptance bar.
- `run_account_job.py` includes `next_commands`; when a run is incomplete or blocked, use those commands as the first recovery path instead of manually stitching later stages.
- If a single-account result includes both coverage and field-completion commands, use the first scoped `pending_enrichment` resume command before another homepage coverage rerun. This prevents already imported posts from staying in Feishu without detail fields while coverage is still being improved. If no scoped posts or non-coverage stages exist, rerun homepage discovery instead of resuming an empty queue.
- `run_account_job.py` exits nonzero for any non-`complete` `run_status` by default; use `--allow-incomplete-success` only when a caller intentionally accepts incomplete status for preview and will inspect the JSON.
- Automatic summary generation may partially succeed. When some summaries are generated and others are rejected, the account job should apply the usable summaries and continue recomputing/syncing those rows, while rejected rows remain visible as completion blockers.
- Coverage gaps should remain visible as blockers, but they should not stop known imported rows from getting generated/applied story summaries.
- If a real-write Feishu auth check, OpenCLI readiness check, or homepage login/profile blocker stops the job before homepage discovery/import, the recovery command must rerun the full account job from the homepage top, not `--resume-only`; otherwise no new posts will be discovered after the operator fixes the blocker.
- `run_account_job.py --resume-only` still checks OpenCLI Browser Bridge before running scoped `detail_time`, `lead_link`, `engagement`, or `post_type` tasks. If the bridge is unavailable, it returns `blocked_opencli` before calling `enrichment_worker.py`, so predictable environment issues do not become failed补抓 tasks.
- `opencli_session_busy` / `action_required=retry_later` during detail enrichment is recoverable contention. The worker requeues those detail tasks as `pending` without counting them as failed; use the emitted `next_commands` or rerun the same scoped worker/account job.
- Detail enrichment lock files self-recover when the recorded process is gone or older than `performance.detail_session_lock_stale_seconds`, so interrupted detail runs should not permanently block later补抓.
- Account-job output exposes `worker_retry_later*` fields when detail tasks were requeued because of lock contention. Report this as a resumable state and continue with the emitted `next_commands`.
- Account-job `run_status=worker_failed` means the補抓执行器 returned non-structured or unknown output. Check `worker_failure_reasons`, fix the script/environment issue, then use `next_commands`; do not present it as ordinary待补抓 or completed ledger sync.
- For `blocked_opencli`, use the account-job `next_commands` exactly: pre-discovery blockers rerun full capture from the homepage top after OpenCLI is fixed, while `--resume-only` detail blockers resume the scoped SQLite queue with `--force-recover-running`.
- `run_account_job.py --resume-only` recovers scoped stale `running` enrichment tasks before worker passes. The default stale window is conservative at 30 minutes to avoid duplicate detail navigation; use the generated `--force-recover-running` resume command after a known Codex/terminal interruption when scoped candidates or running tasks already exist.
- Use `--sync-partial --dry-run` only for business preview output that must not affect the formal table.
- `run_capture_pipeline.py` also reports `quality_summary`, top-level `completion_blockers`, `feishu_sync`, and `enrichment_tasks`; if it returns `complete=false`, treat the result as imported/queued work, not as final collection completion. Use `--fail-on-incomplete` when a caller must not accept incomplete partial imports.
- Stable no-new-post extraction stop is normal completion. Business account capture defaults to `--max-snapshots 32 --min-snapshots 6` so Facebook virtualized feeds get multiple scroll samples before stopping. If current-tab extraction returns raw snapshot-cap `coverage_incomplete=true`, `run_account_job.py` and `run_capture_pipeline.py` automatically retry once from the page top with a higher snapshot budget before reporting the run. If it remains incomplete after the automatic retry, keep rows visible in the ledger and mark coverage as `待补抓：覆盖不足`.
- `coverage_note` should clear after a later same-post homepage rerun proves coverage complete. Do not preserve an old coverage marker forever, or the batch can keep reporting `coverage_incomplete` after the user-visible coverage has already been fixed.
- If the user supplies a visible expected count or label checklist, missing expected posts/labels is also `coverage_incomplete` even when scrolling itself looked stable.
- Re-importing the same post must preserve higher-quality stored fields. Do not overwrite confirmed detail time, qualified comment/reply lead links, external landing URLs, valid Chinese article summaries, engagement values, manual `是否采用`, or final output status with weaker partial data.
- Import and prepare paths must recognize business header aliases for the two fields operators reported missing: `内容类型` is `post_type`, and `内容摘要` / `文章摘要` / `摘要` / `故事概要` are article-sourced `story_summary` fields. If a post has these explicit fields, do not leave the Feishu `帖子类型` / `故事概要` cells empty.
- Real project status recomputation must use the loaded config. `ready_for_output` is only valid after current `quality_audit` passes, so rows missing `post_type` or a valid article-sourced `story_summary` stay incomplete and keep the corresponding补抓/summary work visible.
- A historical `enrichment_tasks.status=done` only means that stage was satisfied at that time. If the current row later lacks `post_type`, engagement, article material, or a valid article-based Chinese summary, the normal account job must reopen the non-running task as `pending` and continue the pipeline.
- `enrichment_worker.py --stages summary` does not generate summaries. It only verifies whether a valid Codex-written Chinese summary has already been applied; otherwise it reports `run_status=needs_codex_summary` with exit code `2` instead of a generic worker failure.
- `run_account_job.py` automatically handles the normal summary-only path: export scoped requests, run `generate_article_summaries.py`, apply the generated Chinese summaries, recompute completion, then sync. If it still reports `needs_codex_summary` or `summary_auto_apply_failed`, use its scoped `next_commands` first. Do not run an unscoped all-database summary export for an account-specific job.
- Once article material exists, account jobs should generate/apply `story_summary` even if independent OpenCLI補抓 stages such as `post_type`, engagement, exact time, or lead link still have blockers. Missing `post_type` remains a `待补抓：帖子类型` gap and must not block story-summary generation for rows that already have article material.
- When `has_summary_only_work=true` and no automatic detail/article stages remain, account jobs should skip the worker summary verifier and go directly to automatic summary export/generate/apply.
- If article-material or summary-application input is malformed, treat `run_status=article_material_failed` or `summary_apply_failed` as recoverable operational state; fix the file/config and rerun, not as completed capture.
- If Feishu sync returns `ok=false`, report its `run_status` and `next_actions`; local SQLite results remain authoritative for retry.

## Feishu Workflow

Target business sheet: `FB竞品帖子链接`.

Configured source/output documents:

- Source/read-only account workbook: `source_spreadsheet_url`
- Account source range: `feishu.account_source_range`, default `A1:Z200`; keep it wide enough so account name, competitor account, internal account, generic account, and future target columns are all read.
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
