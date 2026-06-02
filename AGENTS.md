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

## Important User Feedback Already Incorporated

- The old problem was not only extraction logic. Codex Chrome Extension made browser operations hard to stabilize. OpenCLI is valuable because it exposes browser bind/tab/eval/scroll/hover operations as a more testable API.
- The OpenCLI built-in `facebook feed` adapter is not the business data contract. It is only a connectivity/reference layer. The project-owned extractor, enrichment, quality gate, SQLite dedupe, and Feishu sync remain authoritative.
- For "today's posts", always start from the top of the account homepage. Facebook virtualizes the feed DOM, so continuing from a low scroll position can miss newer posts above it.
- If the user reports visible labels like `38m, 1h, 2h ... 17h`, treat that list as a coverage checklist, then open each candidate detail/comment area.
- Some homepage labels that look like "today" can resolve to the previous calendar date after detail-page exact-time confirmation. Formal output is gated on detail-page exact time, not homepage relative labels.
- Short posts, photo/reel/watch/video links, missing parent post links, missing share counts, missing engagement, or missing summary must not cause capture-time deletion. Keep them as `needs_enrichment`.
- Comment/reply lead links posted by the account are authoritative. Do not let detail-page right-column ads, suggested posts, feed ads, or unrelated external links overwrite a captured comment/reply lead link.
- Quality gate is an output/sync gate, not an import gate. Valid candidates should enter SQLite first as `needs_enrichment`; later enrichment can promote them to `ready_for_output`.
- Detail-page engagement must be anchored to the current main post DOM. Do not parse `document.body.innerText` or broad page text for likes/comments/shares, because comment blocks, recommendations, and ads can bind the wrong number to labels such as `Like`.
- When a detail page exposes clustered metrics like `811 / 350 / 31`, treat them positionally as reactions/likes, comments, and shares after confirming the cluster belongs to the main post.
- Homepage capture should avoid stopping on a few stable DOM snapshots while Facebook can still scroll. `opencli_extract_current_tab.mjs` uses a higher default snapshot budget, a minimum snapshot count, and scroll-movement guards; `coverage_incomplete=true` means the last allowed snapshot still found new candidates and the operator should raise `--max-snapshots` or retry from the page top.
- SQLite upsert must be merge-oriented, not overwrite-oriented. Re-imported partial rows must not downgrade confirmed time, qualified comment/comment-reply lead links, external landing/article URLs, valid Chinese article summaries, engagement values, manual adoption decisions, or final statuses.
- Missing or suspicious fields such as lead link, engagement, low likes, or post type can be marked with `待补抓：...` and queued for `lead_link`, `engagement`, or `post_type` refetch. These markers are operational audit hints, not permission to bypass the final quality gate.

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
- `lark-cli auth status` must report `identity=user` and `tokenStatus=valid` before real writes.
- Enforce `lark-cli config default-as user` and `lark-cli config strict-mode user`.
- If `tokenStatus=needs_refresh`, stop real writes and ask the operator to refresh login with `lark-cli auth login` or an equivalent token-refreshing command.

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

- `scripts/check_env.py`: first command before testing, capture, or sync. Reports platform, `lark-cli`, Feishu config, OpenCLI command, daemon, Browser Bridge state, and recommended capture route.
- `scripts/config_loader.py`: owns platform/runtime resolution. Keep `lark_cli_path`, `opencli_path`, and `opencli_session` on `auto` unless a machine-specific override is necessary.
- `scripts/read_accounts.py`: reads source Feishu accounts. It supports competitor/internal columns and generic account columns through `field_schema.py`.
- `scripts/opencli_runtime.mjs`: shared OpenCLI command/session/tab/eval helpers.
- `scripts/check_opencli_runtime_backend.mjs`: OpenCLI backend readiness check.
- `scripts/opencli_extract_current_tab.mjs`: current-tab extraction reference route. It defaults to `--max-snapshots 16` and `--min-snapshots 4` to reduce under-capture from early stable virtualized-feed snapshots.
- `scripts/fb_dom_extractors.js`: page DOM candidate extraction.
- `scripts/fb_time_extractors.js`: exact Facebook time parsing and timestamp-target helpers.
- `scripts/prepare_capture_result.py`: normalize raw homepage capture and keep incomplete candidates as `needs_enrichment`.
- `scripts/opencli_enrich_post_details.mjs`: open detail pages, confirm exact time, expand comments/replies, resolve lead links, apply target-date filtering.
- `scripts/run_capture_pipeline.py`: fast account-level entrypoint that discovers visible candidates, prepares/imports them as partial records, and queues enrichment.
- `scripts/enrichment_worker.py`: resumes queued `detail_time`, `lead_link`, `engagement`, `post_type`, and `article_material` tasks with local concurrency limits. Its `summary` stage no longer generates story summaries; it only verifies that a Codex-written Chinese summary has been applied, otherwise it leaves `requires_codex_chinese_summary`.
- `scripts/enrich_article_summaries.py`: fetch article/landing material for summarization.
- `scripts/export_summary_requests.py`: export SQLite rows and article material that need Codex-written Chinese summaries.
- `scripts/apply_article_summaries.py`: apply Codex-written Chinese summaries and recompute `output_status`.
- `scripts/audit_story_summaries.py`: audit invalid local summaries and optionally downgrade them to `needs_enrichment`.
- `scripts/audit_fields.py`: audit missing/refetchable output fields, write `field_audit_*` markers, and queue refetch tasks when run with `--fix`.
- `scripts/import_existing_result.py`: import JSON/CSV into SQLite and optionally upsert auditable candidates; use `--strict-ready-only` for ready-only sync.
- `scripts/filter_posts.py`: filter local SQLite records and optionally upsert auditable candidates; use `--strict-ready-only` for ready-only sync.
- `scripts/sync_feishu.py`: sync local records to Feishu using candidate upsert by default; use `--strict-ready-only` for ready-only sync.
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
python3 scripts/run_capture_pipeline.py --config config/settings.yaml --account-url <facebook-account-url> --target-date YYMMDD --partial
```

Resume queued enrichment:

```bash
python3 scripts/enrichment_worker.py --config config/settings.yaml --stages detail_time,lead_link,engagement,post_type,article_material --limit 50
```

Sync candidates to the formal table and mark missing fields:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input exports/prepared.json --sync --dry-run
```

Strict ready-only sync:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input exports/ready.json --sync --strict-ready-only --dry-run
```

## Current Runtime Notes From Last Check

Last observed on 2026-06-02:

- Platform: `darwin`.
- `lark-cli` resolved to `/Users/a1/.npm-global/bin/lark-cli`.
- `lark-cli` identity was `user`, but token status was `needs_refresh`; real writes require refresh.
- OpenCLI command resolved through `npx -y @jackwener/opencli`, version `1.8.1`.
- OpenCLI daemon was not running on port `19825`.
- Browser Bridge live capture was blocked until the OpenCLI daemon/extension/profile connection is fixed.
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
- If `opencli_extract_current_tab.mjs` reports `coverage_incomplete=true`, do not treat the run as complete coverage. Increase `--max-snapshots` or restart from the account homepage top before concluding older in-window posts were absent.

## Git/Workspace Notes

- `data/` is ignored local runtime data. Do not stage `data/posts.sqlite`.
- The previous push attempt failed because this worktree has no `origin` remote configured.
- If future work needs push, configure the correct remote first or report that no remote exists.
