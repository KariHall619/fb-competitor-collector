# FB Competitor Collector

Facebook competitor/internal-page post collection for Codex-operated workflows.

The project is intentionally a thin automation layer around the real browser and Feishu:

```text
Codex natural-language request
-> environment/auth preflight and automatic recovery where possible
-> OpenCLI Browser Bridge opens or uses an isolated same-profile Chrome tab
-> homepage discovery and detail enrichment
-> SQLite dedupe and resumable enrichment queue
-> strict final quality gate
-> Feishu output sheet
-> Codex business summary
```

## Start Here

Run commands from this repository root.

Check whether the project is ready:

```bash
python3 scripts/check_env.py --config config/settings.yaml --fix-auth --fix-opencli
```

Collect all configured accounts for the last 24 hours and write complete rows to Feishu:

```bash
python3 scripts/run_accounts_job.py --config config/settings.yaml --last-hours 24 --sync
```

Collect one account:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url "<facebook-account-url>" --last-hours 24 --sync
```

Resume an interrupted account:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url "<facebook-account-url>" --target-date YYMMDD --resume-only --force-recover-running --sync
```

Inspect configured accounts:

```bash
python3 scripts/read_accounts.py --config config/settings.yaml
```

## Completion Rules

A business capture job is complete only when the account-level or batch-level JSON reports `run_status=complete`.

Rows are written to the formal Feishu sheet only after the strict output gate passes:

- Facebook content URL is valid.
- `posted_at` is detail-confirmed, not estimated from relative labels.
- account-owned comment, reply, or current-post CTA lead link resolves outside Facebook/Meta.
- landing/article material exists.
- Chinese story summary is based on article material.
- required post type and engagement checks pass under current config.
- coverage blockers are clear.

Incomplete candidates stay in local SQLite and enrichment queues. They are not forced into the formal Feishu table by normal `--sync`.

## Human Blockers

Codex can recover routine setup issues such as `lark-cli` user-mode settings, refreshable Feishu tokens, and bounded OpenCLI daemon startup.

Stop and ask the operator to fix the browser/profile when any of these appears:

- Facebook logged out.
- visitor preview or only one preview post.
- CAPTCHA/risk-control page.
- wrong Chrome profile.
- OpenCLI Browser Bridge extension not connected after bounded recovery.
- target account page does not visibly load real posts.

Do not import or sync visitor-preview data.

## Directory Map

```text
.
├── README.md                  # developer/operator quick start
├── README_FOR_OPERATOR.md     # no-code business runbook
├── SKILL.md                   # concise Codex skill routing contract
├── AGENTS.md                  # concise repo rules for Codex
├── docs/
│   ├── skill-execution.md     # why docs are split this way
│   ├── architecture.md        # runtime and data flow
│   ├── data-contract.md       # fields, Feishu output, quality gate
│   ├── script-reference.md    # command reference and validation
│   └── troubleshooting.md     # recovery order and blockers
├── config/
│   ├── settings.yaml          # local live config
│   └── settings.yaml.example  # portable template
├── scripts/                   # business entrypoints and helpers
├── samples/                   # committed fixtures for import/tests
├── tests/                     # local regression tests
├── data/                      # ignored runtime SQLite/profile data
└── exports/                   # ignored run outputs and debug artifacts
```

`data/` and `exports/` are local runtime state, not source. Keep long-lived examples in `samples/`.

## Feishu Boundary

The account source workbook is read-only. The output workbook is the only write target.

The formal output table uses these A-K headers:

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

`scripts/field_schema.py` owns header aliases and row ordering. Do not duplicate Feishu row mapping elsewhere.

## Validation

For documentation-only changes:

```bash
git diff --check
```

For code changes:

```bash
python3 tests/test_local_pipeline.py
PYTHONPYCACHEPREFIX=/private/tmp/fb-competitor-pycache python3 -m py_compile scripts/*.py tests/test_local_pipeline.py
node -c scripts/fb_dom_extractors.js
node -c scripts/fb_detail_extractors.js
node -c scripts/fb_time_extractors.js
node -c opencli/clis/facebook/fb-competitor-posts.js
```
