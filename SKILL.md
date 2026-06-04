---
name: fb-competitor-collector
description: Use when a business user asks Codex to collect, resume, import, filter, or sync visible Facebook competitor/internal-page posts to the Feishu sheet FB竞品帖子链接. Use OpenCLI Browser Bridge only for live Facebook capture, with SQLite dedupe/enrichment and strict Feishu output gating.
---

# FB Competitor Collector

This skill turns business-language requests into the collector workflow:

```text
request scope
-> preflight and automatic recovery where safe
-> OpenCLI Browser Bridge account/post capture
-> SQLite dedupe and enrichment queue
-> strict quality gate
-> Feishu output
-> Codex business summary
```

Keep user-facing interaction natural-language first. Do not ask business users to run shell commands unless they explicitly want commands.

## References

Read only when needed:

- `README_FOR_OPERATOR.md`: business-facing explanations and sample prompts.
- `docs/architecture.md`: browser/runtime architecture and account flow.
- `docs/data-contract.md`: fields, Feishu A-K output, status meanings, and quality gate.
- `docs/script-reference.md`: full command reference and validation commands.
- `docs/troubleshooting.md`: recovery order for auth, OpenCLI, Facebook login, and incomplete jobs.

## Standard Flow

For every capture/write request unless the user asks for a read-only check:

1. Interpret scope: all configured accounts, one account, direct account URL, direct post URL, existing SQLite rows, or local JSON/CSV import.
2. If scope is ambiguous and cannot be inferred from config or user text, ask one concise question.
3. Run preflight before live capture, import with real sync, or Feishu write:
   `python3 scripts/check_env.py --config config/settings.yaml --fix-auth --fix-opencli`
4. Silently recover what Codex can recover: lark-cli user-mode settings, refreshable user token, bounded OpenCLI daemon startup.
5. Stop only for human blockers: Facebook logout, visitor preview, CAPTCHA/risk control, wrong Chrome profile, disconnected Browser Bridge after bounded recovery, or page without visible real posts.
6. Use the correct business entrypoint. Do not manually stitch stages unless debugging.
7. Follow emitted `next_commands` first when a job is interrupted, incomplete, or recoverable.
8. Finish with account-level counts, final usable/synced count, completeness, blockers, and special posts with almost no fields.

## Request Routing

- Check readiness:
  `python3 scripts/check_env.py --config config/settings.yaml --fix-auth --fix-opencli`

- Read configured accounts:
  `python3 scripts/read_accounts.py --config config/settings.yaml`

- Capture all configured accounts and sync complete rows:
  `python3 scripts/run_accounts_job.py --config config/settings.yaml --last-hours 24 --sync`

- Capture all configured accounts for a date:
  `python3 scripts/run_accounts_job.py --config config/settings.yaml --target-date YYMMDD --sync`

- Capture one account:
  `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --last-hours 24 --sync`

- Capture one account for a date:
  `python3 scripts/run_account_job.py --config config/settings.yaml --account-url <url> --target-date YYMMDD --sync`

- Include visible coverage expectations when the user provides them:
  add `--expected-post-count <n>` and/or `--expected-labels "38m,1h,2h"`.

- Resume interrupted account work:
  use the emitted `next_commands`; if needed, rerun scoped account job with `--resume-only --force-recover-running --sync`.

- Capture or补抓 a direct post URL:
  infer account/date from SQLite when possible; otherwise ask for account URL or visible account name. Import as a candidate if missing, then run the account-scoped resume/enrichment path before sync.

- Import existing JSON/CSV:
  `python3 scripts/import_existing_result.py --config config/settings.yaml --input <file> --no-sync`

- Sync existing/imported rows:
  prefer scoped `run_account_job.py --resume-only --force-recover-running --sync` when rows belong to an account/date. Direct sync/import/filter commands write only strict complete rows unless explicit audit/ledger mode is requested.

- Filter local library:
  `python3 scripts/filter_posts.py --config config/settings.yaml ...`

## Hard Rules

- Live Facebook capture uses OpenCLI Browser Bridge only.
- The job must operate on a tab/page it opened or explicitly matched; do not bind or occupy the user's active tab unconditionally.
- Do not import or sync logged-out, visitor-preview, empty-shell, or one-preview-post pages.
- Start account capture from the homepage top.
- Keep valid media/share candidates even if exact time, parent post, lead link, post type, engagement, article material, or summary is missing.
- Missing fields are enrichment work, not capture-time deletion.
- Normal `--sync` writes only rows passing the current strict quality gate.
- Use `--sync-audit` / `--ledger-sync` only when the operator explicitly asks for incomplete audit output.
- Never write to the Feishu account source workbook.
- Never invent metrics, article summaries, lead links, or post times.
- Never store passwords, cookies, API keys, or tokens.

## Completion And Reporting

A capture job is complete only when `run_status=complete`.

Report these fields in plain business language:

- accounts attempted
- candidates found per account
- final usable/synced rows per account
- whether required fields are complete
- top blockers or `next_commands`
- stage pressure when useful: coverage, exact time, lead link, engagement, post type, article material, summary, or Feishu sync
- extreme special cases such as a post with almost no fields captured

Treat high local/ledger candidate count with low final usable count as a补抓 state, not success.
