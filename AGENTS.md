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
  7. Only `ready_for_output` rows sync to Feishu; incomplete candidates stay in SQLite as `needs_enrichment`.

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
- `story_summary` exists and `summary_source=article`.

Rows that fail the gate remain local `needs_enrichment`. Do not force-sync them.

## Script Map

- `scripts/check_env.py`: first command before testing, capture, or sync. Reports platform, `lark-cli`, Feishu config, OpenCLI command, daemon, Browser Bridge state, and recommended capture route.
- `scripts/config_loader.py`: owns platform/runtime resolution. Keep `lark_cli_path`, `opencli_path`, and `opencli_session` on `auto` unless a machine-specific override is necessary.
- `scripts/read_accounts.py`: reads source Feishu accounts. It supports competitor/internal columns and generic account columns through `field_schema.py`.
- `scripts/opencli_runtime.mjs`: shared OpenCLI command/session/tab/eval helpers.
- `scripts/check_opencli_runtime_backend.mjs`: OpenCLI backend readiness check.
- `scripts/opencli_extract_current_tab.mjs`: current-tab extraction reference route.
- `scripts/fb_dom_extractors.js`: page DOM candidate extraction.
- `scripts/fb_time_extractors.js`: exact Facebook time parsing and timestamp-target helpers.
- `scripts/prepare_capture_result.py`: normalize raw homepage capture and keep incomplete candidates as `needs_enrichment`.
- `scripts/opencli_enrich_post_details.mjs`: open detail pages, confirm exact time, expand comments/replies, resolve lead links, apply target-date filtering.
- `scripts/run_capture_pipeline.py`: fast account-level entrypoint that discovers visible candidates, prepares/imports them as partial records, and queues enrichment.
- `scripts/enrichment_worker.py`: resumes queued `detail_time`, `lead_link`, `article_material`, and `summary` tasks with local concurrency limits.
- `scripts/enrich_article_summaries.py`: fetch article/landing material for summarization.
- `scripts/apply_article_summaries.py`: apply Codex-written Chinese summaries and recompute `output_status`.
- `scripts/import_existing_result.py`: import JSON/CSV into SQLite and optionally sync ready rows.
- `scripts/filter_posts.py`: filter local SQLite records and optionally sync ready rows.
- `scripts/sync_feishu.py`: sync local ready rows to Feishu.
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
python3 scripts/enrichment_worker.py --config config/settings.yaml --stages detail_time,lead_link,article_material --limit 50
```

Sync only ready rows:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input exports/ready.json --sync --dry-run
```

## Current Runtime Notes From Last Check

Last observed on 2026-05-29:

- Platform: `darwin`.
- `lark-cli` resolved to `/Users/a1/.npm-global/bin/lark-cli`.
- `lark-cli` identity was `user`, but token status was `needs_refresh`; real writes require refresh.
- OpenCLI command resolved through `npx -y @jackwener/opencli`, version `1.8.0`.
- OpenCLI daemon was running on port `19825`.
- Browser Bridge extension was not connected to the current Chrome profile, so live capture was blocked until the OpenCLI extension/profile connection is fixed.
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

Current latest passing commit before this file: `0bd5ca0ac1ebdc87da95883159d36b74c9ba22ec`.

## Git/Workspace Notes

- `data/` is ignored local runtime data. Do not stage `data/posts.sqlite`.
- The previous push attempt failed because this worktree has no `origin` remote configured.
- If future work needs push, configure the correct remote first or report that no remote exists.
