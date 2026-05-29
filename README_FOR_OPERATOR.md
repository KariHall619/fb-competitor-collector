# FB Competitor Collector Operator Notes

## Goal

This package is the single entry point for FB competitor/internal-page collection:

```text
normal Chrome Facebook tab -> OpenCLI Browser Bridge -> normalize -> SQLite dedupe -> Feishu sync -> filter
```

Business users should operate it through natural language in Codex.

## Setup

```bash
cp config/settings.yaml.example config/settings.yaml
python3 scripts/check_env.py --config config/settings.yaml
```

Cross-platform runtime detection:

- `lark_cli_path: auto` lets the project detect the current OS and resolve the real command.
- On Mac, the current validated override is `/Users/a1/.npm-global/bin/lark-cli`.
- On Windows, the default command is `lark-cli.cmd`; keep it available in PATH, or set `platform_overrides.windows.lark_cli_path` to the installed full path.
- `opencli_path: auto` resolves a global `opencli` command first and falls back to `npx -y @jackwener/opencli` when npx is available.
- `opencli_session: fb-competitor` keeps the Browser Bridge tab lease scoped to this project.
- Real Feishu writes must use user identity, not bot identity.
- Identity policy is forced with `default-as user` and `strict-mode user`.
- If `tokenStatus` is `needs_refresh`, run `lark-cli auth login` before writing Feishu.
- Live Facebook capture requires OpenCLI Browser Bridge in the same normal Chrome profile where Facebook is logged in.
- A normal shell can confirm OpenCLI readiness with `opencli doctor` or `npx -y @jackwener/opencli doctor`.

Configured Feishu workbooks:

```text
Account source wiki URL: https://pic6ktmsyi.feishu.cn/wiki/QzfUwyYyTi3zt7kl7TDcSzZKn3f?sheet=oZg2HR
Account source spreadsheet URL: https://pic6ktmsyi.feishu.cn/sheets/QkRSshqQDh2dfWtfLtLcikWKnIb
Account source sheet id: oZg2HR

Output wiki URL: https://pic6ktmsyi.feishu.cn/wiki/BqkSw67zgiYlbikZWx3cqwZ5nAf
Output spreadsheet URL: https://pic6ktmsyi.feishu.cn/sheets/Md8As2SJzhyuBHtMuOmcLqy3nyf
Output sheet id: 44013b
```

## Live Facebook Capture

Supported route:

```text
business user opens the visible Facebook page in normal Chrome
-> OpenCLI Browser Bridge reads the current tab DOM
-> homepage relative labels such as 3h/12h/1d define the candidate window
-> each candidate post detail page confirms exact posted_at and comment lead link
-> normalize -> SQLite dedupe -> Feishu sync
```

If the environment check says OpenCLI or Browser Bridge is not ready, stop and fix the OpenCLI CLI/daemon/extension/profile setup first. Do not use another browser route for live Facebook capture.

If the page shows a login prompt, visitor preview, or only one preview post, stop immediately with `human_intervention_required`. The operator must manually log in or confirm the Chrome profile before retrying. Do not keep scrolling, import, or sync from that state.

## OpenCLI Browser Bridge Troubleshooting

Use these checks when Codex cannot verify exact Facebook time from the normal Chrome tab:

```bash
opencli doctor
curl -H 'X-OpenCLI: 1' http://127.0.0.1:19825/status
node scripts/check_opencli_runtime_backend.mjs
```

If `opencli doctor` reports `Extension: not connected`, the blocker is the OpenCLI Browser Bridge extension/profile connection, not Facebook login or the project code. Install or enable the OpenCLI extension in the business Chrome profile, then rerun:

```bash
node scripts/opencli_verify_exact_time.mjs --run --account-url "<facebook-account-url>"
```


## OpenCLI Facebook Boundary

OpenCLI is the browser runtime and Facebook connectivity dependency for live capture. The project does not use the built-in `opencli facebook feed` output as the business result, because that adapter returns generic feed columns. This project still evaluates `scripts/fb_dom_extractors.js`, then runs the existing normalization, detail enrichment, quality gate, SQLite dedupe, and Feishu sync flow.

## Local Import Test

```bash
python3 scripts/import_existing_result.py \
  --config config/settings.yaml \
  --input samples/sample_posts.json \
  --no-sync
```

Run it twice to verify post-link dedupe.

Expected second run result:

```json
{
  "input": 1,
  "inserted": 0,
  "updated": 1,
  "errors": 0
}
```

## Filter Test

```bash
python3 scripts/filter_posts.py \
  --config config/settings.yaml \
  --date 260521 \
  --account-type competitor
```

## Feishu Sync

After `lark-cli auth status` reports `identity=user` and `tokenStatus=valid`:

```bash
python3 scripts/import_existing_result.py \
  --config config/settings.yaml \
  --input samples/sample_posts.json \
  --sync
```

Important: write only to the output workbook `FB竞品帖子链接`. The account source workbook is read-only for this tool.
The current output workbook uses A-K columns: `账号`, `账户类型`, `帖子链接`, `帖子类型`, `发帖时间`, `文章链接`, `故事概要`, `互动数据（点赞量）`, `浏览量`, `是否采用`, `对应站内链接`.

Before syncing live FB capture results, the quality gate requires:

- hour-level or better post time in `posted_at`, formatted like `2026年5月19日 17:00`
- `posted_at` must be confirmed from Facebook's exact timestamp tooltip or timestamp DOM attributes. Estimated relative-time sources such as `relative_estimated`, `relative_hour`, or `relative_label` are rejected at sync time.
- Timestamp tooltip capture is automated. The skill first tries synthetic hover through the OpenCLI Browser Bridge, then can fall back to automated extension mouse movement. Operators should not manually hover timestamps except when debugging with Codex.
- a lead link posted by the account in the comment area or a comment reply. The link must resolve to an external non-Facebook site and be stored as `landing_url` with `lead_link_status=qualified`.
- The homepage/comment lead link is authoritative. If the post detail page also exposes unrelated right-column/feed ads, those ad links must not overwrite a previously captured comment/reply lead link.
- story summary generated from the landing page/article, with `summary_source=article`
- short posts are kept if they have a valid FB content URL, but remain `needs_enrichment` until lead link, landing URL, summary, and time are confirmed

Capture should preserve all real FB content candidates. If the capture sees `photo.php`, `/photo/`, `/reel/`, `/watch/`, or `/videos/`, keep the original content link and mark `fb_link_kind`.

Media handling rule:

- If a parent post link such as `/posts/`, `story.php`, or `permalink.php` is available, store it in `parent_post_url` and use it as the preferred dedupe key.
- If no parent post link is available, keep the original `reel/photo/watch/video` link as the FB content link. Do not drop the candidate.
- Parent-link absence does not block capture or local storage. Final Feishu output is blocked only when required output fields are missing: exact time, qualified comment/reply lead link, landing URL, and article-based summary.

If a candidate has no share count, add a coverage warning. It may still be a valid post, and the detail enrichment step should continue to check comments/replies for lead links.

Relative FB labels such as `19m`, `1h`, `16h`, or `1d` are stored as `relative_time_text` and used to decide how far the homepage scroll should go. They are not converted into `posted_at` for formal output. `posted_at` must come from Facebook's exact timestamp tooltip or DOM attributes such as `aria-label`, `title`, `datetime`, or `data-tooltip-*`. The hover step is performed automatically by the skill; human intervention is reserved for login/risk-control/page-loading blockers.

Prepare raw OpenCLI Browser Bridge capture output:

```bash
python3 scripts/prepare_capture_result.py \
  --input exports/raw.json \
  --output exports/prepared.json \
  --target-date 260527
```

Attach article material for Codex summarization:

```bash
python3 scripts/enrich_article_summaries.py \
  --input exports/prepared.json \
  --output exports/with_article_material.json
```

Apply Codex-written Chinese summaries:

```bash
python3 scripts/apply_article_summaries.py \
  --input exports/with_article_material.json \
  --summaries exports/article_summaries.json \
  --output exports/ready_for_time_confirmation.json
```

If hour-level post time is still missing, do not sync. Ask the operator to confirm the time from Facebook UI or accept a `time_unconfirmed` non-output record.

Validate exact Facebook time capture before removing any legacy relative-time fallback. This check runs through OpenCLI Browser Bridge:

```bash
node scripts/opencli_verify_exact_time.mjs --run --account-url "<facebook-account-url>"
```

Passing output contains `status=exact_time_confirmed` and at least one `confirmed_examples[].posted_at` such as `2026年5月27日 15:11`. If it reports `facebook_tab_missing`, `login_required`, `visitor_preview`, or `exact_time_not_found`, keep the row as `needs_enrichment` and do not sync formal output for that post time.

## Date Filtering Policy

Facebook often shows relative labels on feed pages. The proven workflow uses two layers:

- `posted_at`: hour-level or better time. This is the time field accepted for final Feishu output.
- `relative_time_text`: visible label such as `3h`, `12h`, or `1d`. This is the homepage candidate-window clue and must not be used as the final output time.

For a specific calendar day request, the reliable process is:

1. On the account homepage, scroll and collect visible posts while using relative labels to keep a broad candidate window around the target day. For "today", labels such as `3h` or `12h` are included; the first stable `1d` boundary is a stopping clue, not a final date proof.
2. Preserve every plausible parent post/reel/photo/watch/video candidate; do not drop short-text posts at homepage capture time.
3. Open each candidate detail page through OpenCLI Browser Bridge and confirm exact `posted_at` from tooltip/DOM time attributes.
4. Expand comments/replies and prefer the account-owned comment/reply lead link. Resolve that link to the final external landing page.
5. Generate the Chinese story summary from the resolved landing page/article, not from Facebook text and not from unrelated ad pages.
6. Keep records without exact time, qualified lead link, or article summary in local SQLite as `needs_enrichment`.
7. Sync only rows whose `posted_at`, qualified lead link, landing URL, and article summary are confirmed.

In the GLAS Story validation run, several homepage labels that looked like "today" resolved to the previous calendar date after detail-page exact-time confirmation. This is expected and is why formal sync is gated on exact detail time rather than homepage relative labels.

Dry-run example:

```bash
python3 scripts/filter_posts.py \
  --config config/settings.yaml \
  --date 260521 \
  --account-type competitor \
  --sync \
  --dry-run
```

## Local Test Suite

```bash
python3 tests/test_local_pipeline.py
PYTHONPYCACHEPREFIX=/private/tmp/fb-competitor-pycache python3 -m py_compile scripts/*.py tests/test_local_pipeline.py
node -c scripts/fb_dom_extractors.js
node -c scripts/opencli_extract_current_tab.mjs
node -c scripts/opencli_verify_exact_time.mjs
```

## Mac/Windows Handoff

The project should normally run with:

```yaml
lark_cli_path: auto
opencli_path: auto
opencli_session: fb-competitor
```

Windows business machines only need extra configuration when `lark-cli.cmd` or `opencli.cmd` is not in PATH:

```yaml
platform_overrides:
  windows:
    lark_cli_path: "C:\\Users\\<user>\\AppData\\Roaming\\npm\\lark-cli.cmd"
    opencli_path: "C:\\Users\\<user>\\AppData\\Roaming\\npm\\opencli.cmd"
```

The remaining handoff checks are:

- OpenCLI Browser Bridge is installed and enabled in the same Chrome profile where Facebook is logged in.
- `lark-cli auth status` reports `identity=user` and `tokenStatus=valid`.
- Scheduler setup is configured only if daily automation is enabled later.
