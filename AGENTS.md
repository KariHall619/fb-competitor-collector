# Agent Project Rules

This repository is `fb-competitor-collector`. Keep this file short: Codex reads `AGENTS.md` before work starts, so durable rules live here and detailed references live in `docs/`.

## Read First

- Quick start and directory map: `README.md`
- Operator runbook: `README_FOR_OPERATOR.md`
- Skill execution model: `docs/skill-execution.md`
- Architecture: `docs/architecture.md`
- Data and Feishu contract: `docs/data-contract.md`
- Script commands: `docs/script-reference.md`
- Recovery order: `docs/troubleshooting.md`

## Core Invariants

- OpenCLI Browser Bridge is the only live Facebook capture route.
- Do not reintroduce Playwright scraping, CDP-only collectors, old Codex Chrome Extension collectors, userscripts, downloaded skill bundles, or OpenCLI's generic `facebook feed` output as the business capture route.
- The account source workbook is read-only. Write only to the output workbook/sheet for `FB竞品帖子链接`.
- `scripts/run_accounts_job.py` is the all-account business entrypoint. `scripts/run_account_job.py` is the single-account business entrypoint. Do not hand-stitch per-account loops in chat.
- Normal `--sync` is strict final output. Incomplete candidates stay in SQLite and enrichment queues unless the operator explicitly asks for audit/ledger output.
- `run_status=complete` is the completion signal. Any other status must be reported as incomplete, blocked, or recoverable.

## Capture Rules

- Start account capture from the homepage top.
- Relative labels such as `1h`, `12h`, or `1d` are coverage clues only, never formal output time.
- Preserve valid Facebook content candidates, including photo, video, watch, reel, share, and group-post links, even when fields are incomplete.
- Detail enrichment must confirm exact time, account-owned lead link, engagement, post type, article material, and story summary readiness.
- Comment/comment-reply lead links are authoritative. Do not overwrite them with right-column ads, suggested posts, feed ads, or unrelated external links.
- Story summaries must be Chinese and based on landing/article material, not Facebook text, article titles, meta descriptions, excerpts, or English source text.

## Runtime And Human Blockers

- Run preflight before live capture, real Feishu write, deployment/share, or integration tests: `python3 scripts/check_env.py --config config/settings.yaml --fix-auth --fix-opencli`.
- Codex may silently recover lark-cli user-mode settings, refreshable Feishu tokens, and bounded OpenCLI daemon startup.
- Stop with human intervention for Facebook logout, visitor preview, CAPTCHA/risk control, wrong Chrome profile, disconnected Browser Bridge after bounded recovery, or pages without visible real posts.
- Do not import or sync visitor-preview data.
- Use emitted `next_commands` as the first recovery path after interruptions.

## Workspace Rules

- `config/settings.yaml` is local live config; `config/settings.yaml.example` is the portable template.
- `samples/` contains committed fixtures only.
- `data/` and `exports/` are ignored runtime state. Do not stage SQLite databases, Chrome profiles, raw captures, generated summaries, or debug screenshots.
- Long-lived examples may move from `exports/` to `samples/` only after removing private/customer-specific data.
- Keep docs scoped: update `AGENTS.md` only for durable repo rules; put workflow detail in `SKILL.md`; put long contracts and troubleshooting in `docs/`.

## Validation

- Documentation-only changes: run `git diff --check`.
- Code changes: run `python3 tests/test_local_pipeline.py`, Python compile checks, and Node syntax checks listed in `docs/script-reference.md`.
- If live capture or Feishu write cannot be tested because auth/browser/runtime is missing, say exactly which preflight dependency is blocked.
