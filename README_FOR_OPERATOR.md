# FB Competitor Collector Operator Notes

## Goal

This package is the single entry point for FB competitor/internal-page collection:

```text
normal Chrome Facebook tab -> Codex Chrome Extension -> normalize -> SQLite dedupe -> Feishu sync -> filter
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
- `codex_home: auto` resolves to the current user's `.codex` directory.
- `codex_chrome_plugin_base: auto` resolves from `codex_home/plugins/cache/openai-bundled/chrome`.
- Real Feishu writes must use user identity, not bot identity.
- Identity policy is forced with `default-as user` and `strict-mode user`.
- If `tokenStatus` is `needs_refresh`, run `lark-cli auth login` before writing Feishu.
- Live Facebook capture requires Codex Chrome Extension in the same normal Chrome profile where Facebook is logged in.
- A normal shell can confirm that the extension is installed, but live capture also needs the trusted Chrome backend/nativePipe in the current Codex session. If browser setup reports only `Codex In-app Browser` and no `extension` backend, live Facebook tooltip verification cannot run in that session.

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
-> Codex Chrome Extension reads the current tab DOM
-> normalize -> SQLite dedupe -> Feishu sync
```

If the environment check says the extension is missing or disabled, stop and fix the Chrome Extension/profile setup first. Do not use another browser route for live Facebook capture.

If the page shows a login prompt, visitor preview, or only one preview post, stop immediately with `human_intervention_required`. The operator must manually log in or confirm the Chrome profile before retrying. Do not keep scrolling, import, or sync from that state.

## Chrome Extension Troubleshooting

Use these checks when Codex cannot verify exact Facebook time from the normal Chrome tab:

```bash
node ~/.codex/plugins/cache/openai-bundled/chrome/latest/scripts/chrome-is-running.js --json
node ~/.codex/plugins/cache/openai-bundled/chrome/latest/scripts/check-extension-installed.js --json
node ~/.codex/plugins/cache/openai-bundled/chrome/latest/scripts/check-native-host-manifest.js --json
```

If all three pass but Codex still reports only `Codex In-app Browser` and no `extension` backend, the blocker is the current Codex browser session connection, not Facebook login, the Chrome profile, or the project code. Restart/reconnect the Codex Chrome Extension session, then rerun:

```bash
node scripts/chrome_extension_verify_exact_time.mjs --run --account-url "<facebook-account-url>"
```

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

Before syncing live FB capture results, the quality gate requires:

- hour-level or better post time in `posted_at`, formatted like `2026年5月19日 17:00`
- `posted_at` should be confirmed from Facebook's exact timestamp tooltip or timestamp DOM attributes when available. If Facebook exposes only relative time, estimate from crawl time, keep `time_source=relative_estimated`, and write the Feishu time with a leading `约`.
- Timestamp tooltip capture is automated. The skill first tries synthetic hover through the Codex Chrome Extension, then can fall back to automated extension mouse movement. Operators should not manually hover timestamps except when debugging with Codex.
- a lead link posted by the account in the comment area or a comment reply. The link must resolve to an external non-Facebook site and be stored as `landing_url` with `lead_link_status=qualified`.
- story summary generated from the landing page/article, with `summary_source=article`
- short posts are kept if they have a valid FB content URL, but remain `needs_enrichment` until lead link, landing URL, summary, and at least exact-or-estimated time are available

Capture should preserve all real FB content candidates. If the capture sees `photo.php`, `/photo/`, `/reel/`, `/watch/`, or `/videos/`, keep the original content link and mark `fb_link_kind`.

Media handling rule:

- If a parent post link such as `/posts/`, `story.php`, or `permalink.php` is available, store it in `parent_post_url` and use it as the preferred dedupe key.
- If no parent post link is available, keep the original `reel/photo/watch/video` link as the FB content link. Do not drop the candidate.
- Parent-link absence does not block capture or local storage. Final Feishu output is blocked only when required output fields are missing: exact-or-estimated time, qualified comment/reply lead link, landing URL, and article-based summary.

If a candidate has no share count, add a coverage warning. It may still be a valid post, and the detail enrichment step should continue to check comments/replies for lead links.

Relative FB labels such as `19m`, `1h`, `16h`, or `1d` are stored as `relative_time_text`. The workflow first tries to replace them with exact `posted_at` from Facebook's timestamp tooltip or DOM attributes such as `aria-label`, `title`, `datetime`, or `data-tooltip-*`. If exact time is unavailable, the label is converted from crawl time into an approximate `posted_at`; Feishu output must show it with `约`. The hover step is performed automatically by the skill; human intervention is reserved for login/risk-control/page-loading blockers.

Prepare raw Chrome Extension capture output:

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

If all time signals are missing, do not sync. If only a relative time label exists, sync is allowed after estimating the time and marking it with `约` in Feishu.

Validate exact Facebook time capture before removing any legacy relative-time fallback. This check must be run from Codex's trusted Chrome Extension runtime. The module exposes `verifyExactTimeCapture({ browser })` for that runtime; the shell command below is only a wrapper and will fail outside trusted Chrome runtime:

```bash
node scripts/chrome_extension_verify_exact_time.mjs --run --account-url "<facebook-account-url>"
```

Passing output contains `status=exact_time_confirmed` and at least one `confirmed_examples[].posted_at` such as `2026年5月27日 15:11`. If it reports `facebook_tab_missing`, `login_required`, or `visitor_preview`, keep the row as `needs_enrichment`. If it reports `exact_time_not_found` but a relative label exists, use an estimated time and mark Feishu output with `约`.

## Date Filtering Policy

Facebook often shows relative labels on feed pages. The workflow therefore uses two layers:

- `posted_at`: hour-level or better time. Exact time is preferred; estimated time from a relative label is accepted only when Feishu output shows `约`.
- `relative_time_text`: visible label such as `1h` or `1d`. This remains the evidence for approximate time when exact time is unavailable.

For a specific calendar day request, the reliable process is:

1. collect enough visible posts around the target day;
2. open/detail-check each candidate when possible;
3. keep records without any time signal in local SQLite as `needs_enrichment`;
4. sync rows whose `posted_at` exists, article summary is generated, and required lead link fields are present; estimated times must be visibly marked with `约`.

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
node -c scripts/chrome_extension_extract_current_tab.mjs
node -c scripts/chrome_extension_verify_exact_time.mjs
```

## Mac/Windows Handoff

The project should normally run with:

```yaml
lark_cli_path: auto
codex_home: auto
codex_chrome_plugin_base: auto
```

Windows business machines only need extra configuration when `lark-cli.cmd` is not in PATH or Codex uses a non-default home directory:

```yaml
platform_overrides:
  windows:
    lark_cli_path: "C:\\Users\\<user>\\AppData\\Roaming\\npm\\lark-cli.cmd"
    codex_home: "C:\\Users\\<user>\\.codex"
```

The remaining handoff checks are:

- Codex Chrome Extension is installed and enabled in the same Chrome profile where Facebook is logged in.
- `lark-cli auth status` reports `identity=user` and `tokenStatus=valid`.
- Scheduler setup is configured only if daily automation is enabled later.
