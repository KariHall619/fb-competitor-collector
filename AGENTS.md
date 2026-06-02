# Agent Project Memory

This file is the first-stop project memory for future agents working in this repo. Read it before changing capture, Feishu sync, or documentation.

## Current Project State

- Branch context: `codex/fix-fb-capture-coverage-engagement`.
- The live Facebook capture runtime is now **OpenCLI Browser Bridge**. Do not reintroduce Codex Chrome Extension, CDP-only collectors, old userscripts, or downloaded skill bundles as live capture routes.
- The user-validated workflow is:
  1. Business user opens the target Facebook account homepage in their normal logged-in Chrome profile.
  2. OpenCLI Browser Bridge binds that tab, prefers direct `--tab` reads without selecting it, and falls back to tab select only when direct access fails.
  3. Homepage relative labels such as `3h`, `12h`, `1d` only define the candidate window and scroll boundary.
  4. Candidate detail enrichment reuses one detail tab when possible, with the original fresh-tab flow as fallback, to confirm exact `posted_at`, expand comments/replies, and capture the account-owned lead link.
  5. The lead link resolves to an external `landing_url`.
  6. Article material is fetched from the landing page, and the Chinese `story_summary` must be based on that material.
  7. Normal `--sync` upserts all auditable candidates to the formal Feishu table by post URL and marks missing/suspicious fields in `是否采用` as `待补抓：...`.
  8. Use `--strict-ready-only` only when the operator explicitly wants the legacy ready-only gate.
  9. Business “抓取并写入飞书” runs should use `scripts/run_account_job.py`, which persists progress in SQLite, resumes scoped enrichment after interruptions, and reports `run_status`. A ledger write with pending enrichment is not a completed job.
  10. If homepage discovery or detail enrichment sees `login_required`, `visitor_preview`, `facebook_tab_missing`, or `human_intervention_required`, the account job must stop with `run_status=human_intervention_required`; do not import/sync visitor-preview rows or hide this as a generic failed enrichment task.

## Important User Feedback Already Incorporated

- The old problem was not only extraction logic. Codex Chrome Extension made browser operations hard to stabilize. OpenCLI is valuable because it exposes browser bind/tab/eval/scroll/hover operations as a more testable API.
- The OpenCLI built-in `facebook feed` adapter is not the business data contract. It is only a connectivity/reference layer. The project-owned extractor, enrichment, quality gate, SQLite dedupe, and Feishu sync remain authoritative.
- For "today's posts", always start from the top of the account homepage. Facebook virtualizes the feed DOM, so continuing from a low scroll position can miss newer posts above it.
- If the user reports visible labels like `38m, 1h, 2h ... 17h`, treat that list as a coverage checklist, then open each candidate detail/comment area.
- When the operator knows the visible checklist, pass it into `scripts/run_account_job.py` with `--expected-post-count <n>` and/or `--expected-labels "38m,1h,2h,..."`. A mismatch is a hard coverage signal: the job must report `coverage_incomplete`, write a coverage note, and keep rows as `待补抓：覆盖不足` instead of calling the run complete.
- Expected-label matching is tolerant of common relative-time variants: `1h`, `1 hour ago`, `1 小时`, and `1小时` are treated as the same coverage label, while reports still show the original operator-provided labels.
- Some homepage labels that look like "today" can resolve to the previous calendar date after detail-page exact-time confirmation. Formal output is gated on detail-page exact time, not homepage relative labels.
- Short posts, photo/reel/watch/video links, missing parent post links, missing share counts, missing engagement, or missing summary must not cause capture-time deletion. Keep them as `needs_enrichment`.
- Comment/reply lead links posted by the account are authoritative. Do not let detail-page right-column ads, suggested posts, feed ads, or unrelated external links overwrite a captured comment/reply lead link.
- Quality gate is an output/sync gate, not an import gate. Valid candidates should enter SQLite first as `needs_enrichment`; later enrichment can promote them to `ready_for_output`.
- Detail-page engagement must be anchored to the current main post DOM. Do not parse `document.body.innerText` or broad page text for likes/comments/shares, because comment blocks, recommendations, and ads can bind the wrong number to labels such as `Like`.
- When a detail page exposes clustered metrics like `811 / 350 / 31`, treat them positionally as reactions/likes, comments, and shares after confirming the cluster belongs to the main post.
- Homepage capture should avoid stopping on a few stable DOM snapshots while Facebook can still scroll. `opencli_extract_current_tab.mjs` and the business entrypoint `run_account_job.py` default to `--max-snapshots 32 --min-snapshots 6` plus scroll-movement guards; stable no-new-post termination is normal completion, while `coverage_incomplete=true` means the last allowed snapshot still found new candidates and the operator should raise `--max-snapshots` or retry from the page top.
- `fb_dom_extractors.js` must split oversized DOM containers by time anchors/post links. When a multi-post container is split successfully, do not also emit the whole container as a candidate, because that pollutes one Feishu row with several posts' text and links.
- SQLite upsert must be merge-oriented, not overwrite-oriented. Re-imported partial rows must not downgrade confirmed time, qualified comment/comment-reply lead links, external landing/article URLs, valid Chinese article summaries, engagement values, manual adoption decisions, or final statuses.
- `output_synced` is not a permanent exclusion from ledger writes. If later enrichment improves a previously synced row, normal audit/ledger sync must include it again and upsert the same Feishu row by post URL.
- Missing or suspicious fields such as lead link, engagement, low likes, or post type can be marked with `待补抓：...` and queued for `lead_link`, `engagement`, or `post_type` refetch. These markers are operational audit hints, not permission to bypass the final quality gate.
- Import and sync summaries should expose enrichment queue pressure by stage. `enrichment_tasks.stage_counts` shows which stages newly needed work, and `open_stage_counts` shows currently open補抓 work for the scoped candidates; report these when explaining why final usable rate is low.
- The formal Feishu table is also the business capture ledger. If a Facebook post candidate is confirmed by URL and account context, normal sync should upsert it even when incomplete. Missing exact time, lead link, article summary, engagement, post type, or capture coverage is expressed in `是否采用` as `待补抓：...`; later enrichment updates the same row by post URL.
- Avoid manual stage stitching for business runs. If token refresh, OpenCLI recovery, Codex interruption, or user handoff interrupts the flow, follow the emitted `next_commands`. If the interruption happened before homepage discovery/import, the next command must rerun the full account job from the homepage top; if scoped candidates already exist or the operator explicitly used `--resume-only`, the next command may resume SQLite pending tasks with `--force-recover-running`.
- Login/profile interruptions are not normal补抓 failures. `run_account_job.py` promotes homepage and detail-page login/visitor-preview blockers to `run_status=human_intervention_required`; pre-import homepage blockers rerun full capture after the operator restores Chrome/Facebook state, while detail-stage blockers resume scoped local tasks.
- Malformed raw capture/import files are operational failures, not Python tracebacks. `prepare_capture_result.py` returns `run_status=prepare_failed` and `import_existing_result.py` returns `run_status=import_failed` with `stage=input_load`, paths, and `next_actions` before writing SQLite or Feishu.
- Malformed article-material or Codex-summary inputs are also structured recovery states. `enrich_article_summaries.py` returns `run_status=article_material_failed`; `apply_article_summaries.py` returns `run_status=summary_apply_failed`; both include the failing paths and do not update SQLite/output files before input validation passes.
- Feishu sync failures, empty output gates, and strict quality-gate stops must include `complete=false`, a `run_status`, and `next_actions`. A failed write keeps SQLite results intact and should be retried after fixing auth/table/config/network issues, not treated as a completed capture.
- `run_account_job.py` must promote Feishu write failures and output-gate stops into the account-level `run_status` after local completion is checked. A job with complete local enrichment but failed Feishu write is `sync_failed`/`quality_gate`, not `complete`.
- `run_account_job.py --resume-only` still checks OpenCLI Browser Bridge before running scoped `detail_time`, `lead_link`, `engagement`, or `post_type` tasks. If the bridge is unavailable, it returns `run_status=blocked_opencli` before calling `enrichment_worker.py`, so predictable environment issues do not increment task failure counts.
- If `opencli_enrich_post_details.mjs` reports `opencli_session_busy` / `action_required=retry_later`, `enrichment_worker.py` must requeue the scoped detail tasks as `pending` without incrementing attempts or marking them failed. This is a self-recoverable contention state, not a data-quality failure.
- `opencli_enrich_post_details.mjs` owns a per-session lock to prevent overlapping detail navigation in the same OpenCLI session. Stale lock files are auto-recovered when the recorded PID is gone or the lock age exceeds `performance.detail_session_lock_stale_seconds`, so a crashed previous detail run should not permanently block补抓.
- Account jobs surface worker lock contention as top-level `worker_retry_later`, `worker_retry_later_count`, `worker_retry_later_reasons`, and matching `quality_summary` fields. Treat this as “已重排、继续续跑” rather than a completed run or a hard data failure.
- For `blocked_opencli`, account-job `next_commands` must preserve context: pre-discovery blockers rerun full capture from the homepage top after OpenCLI is fixed, while `--resume-only` detail blockers resume the scoped SQLite queue with `--force-recover-running`.
- `run_account_job.py --resume-only` recovers scoped stale `running` enrichment tasks before worker passes. The default is intentionally conservative at 30 minutes to avoid duplicate detail navigation; account-job `next_commands` use `--force-recover-running` after known interruptions so the operator can continue immediately without waiting.
- `run_account_job.py` emits a top-level `quality_summary` so business status is readable without digging through nested JSON. Report `coverage_health`, `ledger_candidate_count` / `ledger_usable_rate`, `final_usable_count` / `final_usable_rate`, `top_field_gaps`, `stage_pressure_notes`, and `feishu_sync.run_status` together. `ledger_usable_rate` means candidates are visible in the Feishu ledger; `final_usable_rate` means rows passed the strict completeness bar. A high ledger rate with a low final rate is a normal补抓 state, not a completed run. Use `open_task_stage_counts`, `missing_stage_counts`, and `stage_pressure` to identify whether the remaining work is exact time, lead link, engagement, post type, article material, summary, or coverage.
- `run_account_job.py` also mirrors `quality_summary.completion_blockers` to top-level `completion_blockers`. This ordered list is the preferred explanation of why the scoped job is not done and what to do next; it covers hard blockers, coverage gaps, OpenCLI retry-later contention, automatic enrichment stages, Codex summary requirements, final field gaps, Feishu sync failure, and explicit quality-threshold failures.
- `run_account_job.py` supports explicit quality threshold gates for acceptance/automation: `--require-coverage-complete`, `--min-ledger-usable-rate`, `--min-final-usable-rate`, `--min-completion-rate`, `--min-expected-post-coverage-rate`, and `--min-expected-label-coverage-rate`. Defaults are zero/off so normal ledger sync still writes auditable candidates; when thresholds are supplied, failures emit `quality_threshold_failed`, `exit_status_reason=quality_threshold_failed`, and recovery hints without hiding the underlying `run_status`.
- Account-job `next_commands` must preserve explicit quality threshold flags. Otherwise a retry after `quality_threshold_failed` may silently lower the acceptance bar and make a still-incomplete run look acceptable.

## Do Not Reintroduce

- `agents/openai.yaml`: deleted as unused packaging metadata. Root `SKILL.md` is the actual skill entrypoint.
- `research/userscripts` references: removed because that directory is not part of the repo and should not be treated as a maintained dependency.
- `chrome_extension_*` scripts and `check_chrome_runtime_backend.mjs`: replaced by OpenCLI runtime scripts.
- Mac-only Codex extension path assumptions in config. Runtime detection is centralized.
- Any code path that silently falls back to bot identity or writes to the source Feishu workbook.

## Feishu Boundaries

- Account source workbook is read-only:
  - Wiki: `https://pic6ktmsyi.feishu.cn/wiki/QzfUwyYyTi3zt7kl7TDcSzZKn3f?sheet=oZg2HR`
  - Spreadsheet token: `QkRSshqQDh2dfWtfLtLcikWKnIb`
  - Sheet id: `oZg2HR`
- Output workbook is write-only for results:
  - Wiki: `https://pic6ktmsyi.feishu.cn/wiki/BqkSw67zgiYlbikZWx3cqwZ5nAf`
  - Spreadsheet token: `Md8As2SJzhyuBHtMuOmcLqy3nyf`
  - Current output sheet id: `44013b`
- Real Feishu write paths must run auth preflight before capture/import/sync work. The preflight enforces `lark-cli config default-as user` and `lark-cli config strict-mode user`, then requires `lark-cli auth status` with `identity=user` and `tokenStatus=valid`.
- If `tokenStatus=needs_refresh`, the code must attempt CLI recovery first, currently via `lark-cli doctor` followed by another `auth status` check. Only if CLI recovery cannot restore a valid user token should the run stop.
- If silent recovery is impossible, the code may start `lark-cli auth login --json --no-wait` and report the verification payload, but it must do this before Facebook capture or local import side effects when the command intends to write Feishu.

## Feishu Output Format

The output table `FB竞品帖子链接` uses A-K columns. This is a project contract:

```text
账号
账户类型
帖子链接
帖子类型
发帖时间
文章链接
故事概要
互动数据（点赞量）
浏览量
是否采用
对应站内链接
```

- `config/settings.yaml` and `.example` store this under `feishu.field_schema.output_headers`.
- `scripts/field_schema.py` is the single code source for output column order, header aliases, account-source sheet header roles, account type display, and engagement text formatting.
- Do not add another row-mapping implementation in import/filter/sync scripts.

## Quality Gate For Final Feishu Sync

Final output requires all of the following:

- Valid Facebook content URL.
- `posted_at` confirmed from detail-page tooltip or DOM exact time attributes, hour-level or better, e.g. `2026年5月29日 12:32`.
- `time_confirmed=true`.
- `time_source` is not `relative_estimated`, `relative_hour`, or `relative_label`.
- `lead_link_status=qualified`.
- `lead_link_source` is `comment` or `comment_reply`.
- `landing_url` or `article_url` resolves outside Facebook/Meta.
- `story_summary` is a valid Chinese article summary and `summary_source=article`; copied article title, meta description, text excerpt, or English source text does not qualify.

Rows that fail the gate remain local `needs_enrichment`. Do not force-sync them.

## Script Map

- `scripts/check_env.py`: first command before testing, capture, or sync. Reports platform, `lark-cli`, Feishu config, OpenCLI command, daemon, Browser Bridge state, and recommended capture route. Add `--fix-auth` to actively run the same Feishu auth/config recovery used by real write paths; add `--fix-opencli` to try bounded OpenCLI daemon/doctor recovery before declaring browser bridge blocked.
- `scripts/config_loader.py`: owns platform/runtime resolution. Keep `lark_cli_path`, `opencli_path`, and `opencli_session` on `auto` unless a machine-specific override is necessary.
- `scripts/read_accounts.py`: reads source Feishu accounts. It supports competitor/internal columns and generic account columns through `field_schema.py`.
- `scripts/opencli_runtime.mjs`: shared OpenCLI command/session/tab/eval helpers.
- `scripts/check_opencli_runtime_backend.mjs`: OpenCLI backend readiness check.
- `scripts/opencli_extract_current_tab.mjs`: current-tab extraction reference route. It defaults to `--max-snapshots 32` and `--min-snapshots 6` to reduce under-capture from early stable virtualized-feed snapshots.
- `scripts/fb_dom_extractors.js`: page DOM candidate extraction.
- `scripts/fb_time_extractors.js`: exact Facebook time parsing and timestamp-target helpers.
- `scripts/prepare_capture_result.py`: normalize raw homepage capture and keep incomplete candidates as `needs_enrichment`.
- `scripts/run_account_job.py`: preferred resumable business entrypoint for account capture, scoped enrichment, and formal ledger sync. It supports `--resume-only`, `--force-recover-running`, `--status-only`, `--last-hours 24`, `--sync`, `--dry-run`, `--max-snapshots`, `--min-snapshots`, `--expected-post-count`, `--expected-labels`, `--resume-stale-running-seconds`, `--fail-on-incomplete`, and explicit quality threshold flags; it emits `run_status` such as `complete`, `coverage_incomplete`, `incomplete_pending_tasks`, `needs_codex_summary`, `human_intervention_required`, `blocked_opencli`, or `blocked_auth`, plus top-level `quality_summary` and `next_commands` for the first recovery action. Use `--fail-on-incomplete` for automation or Codex chaining that must treat non-`complete` job states as a hard failure even when ledger sync itself succeeded; use threshold flags when acceptance depends on minimum coverage or usable-rate targets.
- `scripts/opencli_enrich_post_details.mjs`: open detail pages, confirm exact time, expand comments/replies, resolve lead links, apply target-date filtering.
- `scripts/run_capture_pipeline.py`: lower-level fast partial capture/import helper. It discovers visible candidates, prepares/imports them as partial records, and queues enrichment, but does not own full job completion. It supports `--max-snapshots`, `--min-snapshots`, `--expected-post-count`, `--expected-labels`, `--fail-on-incomplete`, and explicit quality threshold flags; success payloads include `quality_summary`, top-level `completion_blockers`, `feishu_sync`, and `enrichment_tasks` so callers can distinguish ledger import from final usable completion. Failure branches must emit `run_status`, `complete=false`, `completion_blockers`, and `next_actions`. Do not use it as the final business “抓取并写入飞书” path.
- `scripts/enrichment_worker.py`: resumes queued `detail_time`, `lead_link`, `engagement`, `post_type`, and `article_material` tasks with local concurrency limits. Its `summary` stage no longer generates story summaries; it only verifies that a Codex-written Chinese summary has been applied, otherwise it leaves `requires_codex_chinese_summary`.
- `scripts/enrich_article_summaries.py`: fetch article/landing material for summarization.
- `scripts/export_summary_requests.py`: export SQLite rows and article material that need Codex-written Chinese summaries. It supports `--date`, `--account-url`, `--account-name`, and `--account-type`; account-job `next_commands` must keep these scopes so summary work does not mix unrelated accounts.
- `scripts/apply_article_summaries.py`: apply Codex-written Chinese summaries and recompute `output_status`.
- `scripts/audit_story_summaries.py`: audit invalid local summaries and optionally downgrade them to `needs_enrichment`.
- `scripts/audit_fields.py`: audit missing/refetchable output fields, write `field_audit_*` markers, and queue refetch tasks when run with `--fix`.
- `scripts/import_existing_result.py`: import JSON/CSV into SQLite and optionally upsert auditable candidates; it reports `enrichment_tasks.stage_counts` / `open_stage_counts` so callers can see which补抓 stages block final usable rows. Use `--strict-ready-only` for ready-only sync.
- `scripts/filter_posts.py`: filter local SQLite records and optionally upsert auditable candidates; use `--strict-ready-only` for ready-only sync.
- `scripts/sync_feishu.py`: sync local records to Feishu using candidate upsert by default; use `--strict-ready-only` for ready-only sync. Direct sync results from import/filter/sync paths include `completion_blockers`; `ok=true` with `run_status=synced_ledger_incomplete` means ledger rows were written but final usable rows still need补抓.
- `scripts/output_quality.py`: final output gate.
- `scripts/store.py`: SQLite schema/upsert/query logic.

## Standard Command Flow

Environment check:

```bash
python3 scripts/check_env.py --config config/settings.yaml
```

Read accounts:

```bash
python3 scripts/read_accounts.py --config config/settings.yaml
```

Preferred resumable account capture and ledger sync:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url <facebook-account-url> --account-name "<account-name>" --last-hours 24 --sync
```

If the operator can see a known number of target-window posts or a visible label checklist, include the expected coverage signal:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url <facebook-account-url> --account-name "<account-name>" --target-date YYMMDD --sync --expected-post-count 13 --expected-labels "38m,1h,2h,3h"
```

Resume after Codex interruption, token refresh, or partial run:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url <facebook-account-url> --account-name "<account-name>" --target-date YYMMDD --resume-only --force-recover-running --sync
```

Automation hard gate, fail the shell command unless the scoped job is fully complete:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url <facebook-account-url> --account-name "<account-name>" --target-date YYMMDD --resume-only --status-only --sync --dry-run --fail-on-incomplete
```

Status-only check without opening Facebook detail pages:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url <facebook-account-url> --account-name "<account-name>" --target-date YYMMDD --resume-only --status-only --sync --dry-run
```

Prepare raw OpenCLI homepage capture:

```bash
python3 scripts/prepare_capture_result.py --input exports/raw.json --output exports/prepared.json --target-date YYMMDD
```

Detail enrichment:

```bash
node scripts/opencli_enrich_post_details.mjs --input exports/prepared.json --output exports/detail_enriched.json --target-date YYMMDD
```

Article material and summary application:

```bash
python3 scripts/enrich_article_summaries.py --input exports/detail_enriched.json --output exports/with_article_material.json
python3 scripts/apply_article_summaries.py --input exports/with_article_material.json --summaries exports/article_summaries.json --output exports/ready.json
```

SQLite summary request flow:

```bash
python3 scripts/enrichment_worker.py --config config/settings.yaml --stages article_material --limit 50
python3 scripts/export_summary_requests.py --config config/settings.yaml --output exports/summary_requests.json
python3 scripts/apply_article_summaries.py --config config/settings.yaml --summaries exports/article_summaries.json
```

Audit invalid local summaries:

```bash
python3 scripts/audit_story_summaries.py --config config/settings.yaml
python3 scripts/audit_story_summaries.py --config config/settings.yaml --fix
```

Import without Feishu write:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input exports/ready.json --no-sync
```

Fast partial capture/import:

```bash
python3 scripts/run_capture_pipeline.py --config config/settings.yaml --account-url <facebook-account-url> --target-date YYMMDD --partial --max-snapshots 32
```

Use this only as a lower-level partial/import helper; for business output, prefer `run_account_job.py` so pending enrichment and coverage state are reported in the final run summary.

Resume queued enrichment:

```bash
python3 scripts/enrichment_worker.py --config config/settings.yaml --stages detail_time,lead_link,engagement,post_type,article_material --limit 50
```

Sync candidates to the formal table and mark missing fields:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input exports/prepared.json --sync --dry-run
```

Strict ready-only sync, only when explicitly requested:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input exports/ready.json --sync --strict-ready-only --dry-run
```

## Current Runtime Notes From Last Check

Last observed on 2026-06-02:

- Platform: `darwin`.
- `lark-cli` resolved to `/Users/a1/.npm-global/bin/lark-cli`.
- `lark-cli` identity was `user`; `tokenStatus=needs_refresh` was observed, and `lark-cli doctor` could refresh it to `valid` on the next auth status call.
- OpenCLI command resolved through `npx -y @jackwener/opencli`, version `1.8.1`.
- OpenCLI daemon can be started by `opencli doctor`, but Browser Bridge live capture remains blocked if the Chrome extension is not connected to the business Chrome profile.
- `recommended_capture_route.route` was `blocked_until_opencli_ready`.

These are runtime observations, not permanent project facts. Re-run `check_env.py` before live capture or Feishu writes.

## Validation Commands

Run these before committing code changes:

```bash
python3 tests/test_local_pipeline.py
PYTHONPYCACHEPREFIX=/private/tmp/fb-competitor-pycache python3 -m py_compile scripts/*.py tests/test_local_pipeline.py
node -c scripts/fb_dom_extractors.js
node -c scripts/check_opencli_runtime_backend.mjs
node -c scripts/opencli_enrich_post_details.mjs
node -c scripts/opencli_extract_current_tab.mjs
node -c scripts/opencli_runtime.mjs
node -c scripts/opencli_verify_exact_time.mjs
```

Current latest passing commit before this file: `365608ba38d090f9b5f8f88e530baecd257d2ed6`.

## Performance Notes

- The accuracy contract is unchanged: `ready_for_output` still requires detail-confirmed time, qualified account-owned comment/comment-reply lead link, external landing URL, and article-based Chinese summary. Audited candidate sync is traceable preview/update output, not proof that a row is complete.
- Article material extraction is not summary generation. Do not treat article title, meta description, or source text excerpt as `story_summary`; export summary requests and apply Codex-written Chinese summaries instead.
- Detail enrichment uses bounded readiness waits. `open_tab_wait_seconds`, `detail_navigation_wait_seconds`, `synthetic_tooltip_wait_ms`, and `real_mouse_tooltip_wait_ms` are maximum waits; the script should continue earlier once the detail DOM, tooltip, or comment expansion signal is available.
- Do not replace these readiness waits with fixed sleeps unless OpenCLI/Facebook behavior changes and tests are updated. Fixed waits directly increase per-post latency.
- `enrichment_worker.py` should keep grouping detail tasks by canonical post URL. Splitting `detail_time` and `lead_link` into separate page opens is a regression for the sub-two-minute-per-post target.
- `opencli_enrich_post_details.mjs` writes `performance_summary`, including `average_ms`, `max_ms`, and `over_two_minute_posts`, so real capture runs can verify the per-post target without relaxing quality gates.
- `opencli_enrich_post_details.mjs` tracks tabs opened by automation and closes those detail tabs once the batch finishes, including blocked/error exits. Do not close the user's original Facebook homepage tab; `--keep-opened-tabs` is for debugging only.
- `performance.detail_session_lock_stale_seconds` controls stale OpenCLI detail-session lock recovery. Keep it long enough to avoid duplicate live detail navigation, but finite so interrupted Node processes do not block future补抓 indefinitely.
- If `opencli_extract_current_tab.mjs` reports `coverage_incomplete=true`, do not treat the run as complete coverage. Increase `--max-snapshots` or restart from the account homepage top before concluding older in-window posts were absent.

## Git/Workspace Notes

- `data/` is ignored local runtime data. Do not stage `data/posts.sqlite`.
- The previous push attempt failed because this worktree has no `origin` remote configured.
- If future work needs push, configure the correct remote first or report that no remote exists.
