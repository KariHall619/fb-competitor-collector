# Troubleshooting

## Preflight First

Before live capture, import with real sync, deployment/share, or Feishu write, run:

```bash
python3 scripts/check_env.py --config config/settings.yaml --fix-auth --fix-opencli
```

Fix configuration and runtime dependencies first. Do not add business-code fallbacks to hide missing auth, missing OpenCLI, disconnected Browser Bridge, or unreachable Feishu output.

## Feishu Auth

Expected real-write state:

- `lark-cli` resolves for the current OS.
- identity is `user`.
- token status is `valid`.
- output workbook URL/token/sheet id are configured.
- source workbook is read-only.

Recovery order:

1. Let project scripts enforce `lark-cli config default-as user` and `lark-cli config strict-mode user`.
2. If `tokenStatus=needs_refresh`, let the script run CLI recovery and re-check.
3. If silent recovery cannot restore a valid user token, use the emitted login payload/instructions.
4. After auth is restored, rerun the original scoped command or its `next_commands`.

Do not fall back to bot identity for real writes.

## OpenCLI / Browser Bridge

Expected live-capture state:

- OpenCLI command resolves from PATH or npx.
- daemon is reachable.
- Browser Bridge extension is connected to the same normal Chrome profile where Facebook is logged in.
- `fb-competitor-posts` adapter exists in the real OpenCLI home at `~/.opencli/clis/facebook/fb-competitor-posts.js`; refresh it with `python3 scripts/install_opencli_adapter.py --config config/settings.yaml`.
- target Facebook account is reachable in the logged-in Chrome profile; the adapter opens/navigates its own background browser session.

Recovery order:

1. Run `check_env.py --fix-opencli`.
2. If only daemon startup failed, let bounded recovery retry.
3. If Browser Bridge remains disconnected, the operator must enable/install/connect the extension in the business Chrome profile.
4. If account homepage opening fails, rerun the original batch/account command after recovery, not a partial manual loop.

Do not switch to Playwright, CDP-only scraping, old Chrome Extension scripts, or random existing Facebook tabs.

## Facebook Login And Page State

Stop with `human_intervention_required` when the page shows:

- login form or logged-out shell
- visitor preview
- only one preview post
- CAPTCHA/risk control
- empty feed shell
- wrong account/profile
- no real posts visibly loaded

The user must restore Facebook login/profile/page visibility. After that, continue with emitted `next_commands` or rerun the original scoped command.

Do not keep scrolling, import, or sync visitor-preview data.

## Incomplete Jobs

If `run_status` is not `complete`, do not present the job as finished.

Use the first relevant `next_commands` entry. Typical order:

- `pending_enrichment`: resume known SQLite candidates before another coverage rerun.
- `coverage_incomplete`: rerun homepage discovery from the top with preserved expected count/labels.
- `needs_codex_summary` / `summary_auto_apply_failed`: use the scoped account-job recovery command, not an unscoped all-database export.
- `captured_not_synced` / `resumed_not_synced`: rerun scoped account job with `--resume-only --force-recover-running --sync`.
- `blocked_auth`: restore Feishu user auth, then rerun original scoped command.
- `blocked_opencli`: restore OpenCLI/Browser Bridge, then rerun original scoped command.
- `worker_failed`: inspect worker failure reasons and fix script/runtime issue first.

## Coverage Problems

A visible expected count or label checklist is a hard coverage signal. If the operator says there are 13 posts or labels such as `38m,1h,2h`, pass them through `--expected-post-count` and/or `--expected-labels` and preserve them in retries.

Relative homepage labels are coverage clues only. Formal output uses detail-confirmed `posted_at`.

## Summary Problems

Article material extraction is not story-summary generation. Do not treat title, meta description, source text excerpt, or English source text as `story_summary`.

When article material exists, account jobs should generate/apply Chinese story summaries even if independent OpenCLI stages such as post type or engagement still have blockers.

## Runtime Artifacts

`data/` and `exports/` are ignored local runtime state. Do not stage SQLite files, Chrome profiles, raw captures, generated summary payloads, or debug screenshots.
