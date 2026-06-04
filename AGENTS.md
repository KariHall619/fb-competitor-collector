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
  10. Business “抓取全部/所有目标账号并写入飞书” runs should use `scripts/run_accounts_job.py`, which reads the Feishu account source sheet and runs the full `run_account_job.py` flow for each enabled account. Do not hand-stitch per-account commands in chat and then forget later accounts or pending detail fields.
  11. If homepage discovery or detail enrichment sees `login_required`, `visitor_preview`, `facebook_tab_missing`, or `human_intervention_required`, the account job must stop with `run_status=human_intervention_required`; do not import/sync visitor-preview rows or hide this as a generic failed enrichment task.

## Important User Feedback Already Incorporated

- The old problem was not only extraction logic. Codex Chrome Extension made browser operations hard to stabilize. OpenCLI is valuable because it exposes browser bind/tab/eval/scroll/hover operations as a more testable API.
- The OpenCLI built-in `facebook feed` adapter is not the business data contract. It is only a connectivity/reference layer. The project-owned extractor, enrichment, quality gate, SQLite dedupe, and Feishu sync remain authoritative.
- For "today's posts", always start from the top of the account homepage. Facebook virtualizes the feed DOM, so continuing from a low scroll position can miss newer posts above it.
- If the user reports visible labels like `38m, 1h, 2h ... 17h`, treat that list as a coverage checklist, then open each candidate detail/comment area.
- When the operator knows the visible checklist, pass it into `scripts/run_account_job.py` with `--expected-post-count <n>` and/or `--expected-labels "38m,1h,2h,..."`. A mismatch is a hard coverage signal: the job must report `coverage_incomplete`, write a coverage note, and keep rows as `待补抓：覆盖不足` instead of calling the run complete.
- Batch runs must preserve the same expected coverage contract. `run_accounts_job.py` accepts `--expected-post-count` and `--expected-labels`, passes them to each child `run_account_job.py`, and keeps them in batch retry commands after auth/OpenCLI blockers so a visible-count checklist is not lost mid-flow.
- Expected-label matching is tolerant of common relative-time variants: `1h`, `1 hour ago`, `1 小时`, and `1小时` are treated as the same coverage label, while reports still show the original operator-provided labels.
- Some homepage labels that look like "today" can resolve to the previous calendar date after detail-page exact-time confirmation. Formal output is gated on detail-page exact time, not homepage relative labels.
- Short posts, photo/reel/watch/video links, missing parent post links, missing share counts, missing engagement, or missing summary must not cause capture-time deletion. Keep them as `needs_enrichment`.
- Comment/reply lead links posted by the account are authoritative. Do not let detail-page right-column ads, suggested posts, feed ads, or unrelated external links overwrite a captured comment/reply lead link.
- Some posts expose the external story through a main-post CTA such as `Watch more` instead of an account comment/reply. Treat an external link behind that main-post CTA as `lead_link_source=post_cta` and a qualified lead only when it is anchored inside the current post article, resolves outside Facebook/Meta, and is not from right-column ads, suggested posts, feed ads, page shell, or unrelated surfaces.
- Quality gate is an output/sync gate, not an import gate. Valid candidates should enter SQLite first as `needs_enrichment`; later enrichment can promote them to `ready_for_output`.
- Detail-page engagement must be anchored to the current main post DOM. Do not parse `document.body.innerText` or broad page text for likes/comments/shares, because comment blocks, recommendations, and ads can bind the wrong number to labels such as `Like`.
- When a detail page exposes clustered metrics like `811 / 350 / 31`, treat them positionally as reactions/likes, comments, and shares after confirming the cluster belongs to the main post.
- Homepage capture should avoid stopping on a few stable DOM snapshots while Facebook can still scroll. `opencli_extract_current_tab.mjs` and the business entrypoint `run_account_job.py` default to `--max-snapshots 32 --min-snapshots 6` plus scroll-movement guards; stable no-new-post termination is normal completion, while `coverage_incomplete=true` means the last allowed snapshot still found new candidates. `run_account_job.py` and `run_capture_pipeline.py` automatically retry once from the page top with a higher snapshot budget when this raw snapshot-cap condition is detected; if coverage is still incomplete after that, report it and keep the rows marked `待补抓：覆盖不足`.
- `run_account_job.py` also retries homepage discovery once when operator-supplied `--expected-post-count` or `--expected-labels` is not satisfied, even if the first scroll pass ended with stable no-new-post. This prevents an initial shallow discovery from leaving visible account posts missing from SQLite and Feishu.
- `fb_dom_extractors.js` must split oversized DOM containers by time anchors/post links. When a multi-post container is split successfully, do not also emit the whole container as a candidate, because that pollutes one Feishu row with several posts' text and links.
- `fb_dom_extractors.js` must keep Facebook content links beyond `/posts/`, including path-style `/photos/...`, `/photo/...`, `/video/...`, `/videos/...`, `/watch/...`, `/reel/...`, `/share/...`, and group post links. If no parent post URL is present, keep the media/share URL as the auditable candidate instead of dropping it.
- `scripts/models.py::facebook_content_key` is the Python source of truth for Facebook content identity. SQLite canonical URLs and Feishu `post_url` upsert keys must reuse this rule so `/photo.php?fbid=...`, `/photos/.../<id>`, `/watch?v=...`, `/videos/<id>`, group posts, reels, and share links do not split into duplicate ledger rows.
- `scripts/opencli_extract_current_tab.mjs::postKey` is the JavaScript snapshot-dedupe mirror of `facebook_content_key`. Keep it aligned whenever new Facebook content URL forms are accepted, otherwise one scroll run can over-count or duplicate candidates before SQLite/Feishu upsert.
- SQLite upsert must be merge-oriented, not overwrite-oriented. Re-imported partial rows must not downgrade confirmed time, qualified comment/comment-reply lead links, external landing/article URLs, valid Chinese article summaries, engagement values, manual adoption decisions, or final statuses.
- Feishu upsert is also merge-oriented. Later partial/audit sync rows must not overwrite existing non-empty business fields such as `帖子类型` or `故事概要` with empty values or weaker homepage/context snippets. If the incoming row is still marked `待补抓：帖子类型` or `待补抓：文章概要/故事概要`, keep the existing non-empty business cell and only update the system marker in `是否采用`. Real non-empty improvements can still replace those cells. `是否采用` is special: preserve manual values, but allow system `待补抓：...` markers to update or clear.
- Real project status recomputation must pass loaded config into `output_status_for`/`crawl_status_for` and SQLite upsert/update helpers. `ready_for_output` is only valid after current `quality_audit` passes; rows missing `post_type` or valid article-sourced `story_summary` must stay incomplete and keep补抓/summary work visible.
- `output_synced` is a sync marker, not a permanent exemption from quality checks. If a previously synced SQLite row is later found to lack `帖子类型`, valid article-sourced `故事概要`, or other required fields, the next normalization/upsert must downgrade it back to an incomplete status and reopen the missing enrichment stages.
- `prepare_capture_result.py` must preserve an upstream/detail-confirmed `post_type` and explicit article summary fields (`article_summary`, `故事概要`, `文章摘要`, or `story_summary` with `summary_source=article`) during normalization. Homepage `story_summary` / `raw_text` is candidate context only and must not be promoted to a formal story summary unless an explicit article-summary source is present.
- Business-table aliases are part of the import contract: `内容类型` must map to `post_type`, and `内容摘要` / `文章摘要` / `摘要` / `故事概要` must map to article-sourced `story_summary`. If these aliases disappear from `models.py` or `prepare_capture_result.py`, rows can be written to Feishu with a post link but empty `帖子类型` / `故事概要`.
- SQLite raw-payload merging must preserve existing `article_material` when a later homepage/partial import for the same canonical post lacks article material; otherwise summary export can lose the source material needed for Codex-written Chinese `story_summary`.
- `output_synced` is not a permanent exclusion from ledger writes. If later enrichment improves a previously synced row, normal audit/ledger sync must include it again and upsert the same Feishu row by post URL.
- Missing or suspicious fields such as lead link, engagement, low likes, article summary, or post type can be marked with `待补抓：...` and queued for the relevant enrichment stage. Direct Feishu sync paths with a SQLite connection must self-heal current scoped rows before writing: recompute current config audit/status, then reopen missing `post_type`, `article_material`, and `summary` tasks as needed. These markers are operational audit hints, not permission to bypass the final quality gate.
- Import and sync summaries should expose enrichment queue pressure by stage. `enrichment_tasks.stage_counts` shows which stages newly needed work, and `open_stage_counts` shows currently open補抓 work for the scoped candidates; report these when explaining why final usable rate is low.
- `enrichment_tasks.status=done` is not a permanent exemption. If the current stored row again fails `missing_enrichment_stages()` or `field_audit` for a stage such as `post_type`, `engagement`, `article_material`, or `summary`, `enqueue_enrichment_tasks_for_posts()` must reopen that non-running task as `pending`; only active `running` tasks are protected from being stolen.
- The formal Feishu table is also the business capture ledger. If a Facebook post candidate is confirmed by URL and account context, normal sync should upsert it even when incomplete. Missing exact time, lead link, article summary, engagement, post type, or capture coverage is expressed in `是否采用` as `待补抓：...`; later enrichment updates the same row by post URL.
- Avoid manual stage stitching for business runs. If token refresh, OpenCLI recovery, Codex interruption, or user handoff interrupts the flow, follow the emitted `next_commands`. If the interruption happened before homepage discovery/import, the next command must rerun the full account job from the homepage top; if scoped candidates already exist or the operator explicitly used `--resume-only`, the next command may resume SQLite pending tasks with `--force-recover-running`.
- When `run_account_job.py` reports both coverage gaps and scoped field gaps, the first `next_commands` entry should be the scoped `pending_enrichment` resume. Already imported candidates should get detail fields and Feishu upserts before another homepage coverage pass; the coverage rerun command remains available immediately after it. If there are no scoped posts or no non-coverage open/missing stages, do not emit a resume-first command; rerun homepage discovery from the top instead.
- Login/profile interruptions are not normal补抓 failures. `run_account_job.py` promotes homepage and detail-page login/visitor-preview blockers to `run_status=human_intervention_required`; pre-import homepage blockers rerun full capture after the operator restores Chrome/Facebook state, while detail-stage blockers resume scoped local tasks.
- Malformed raw capture/import files are operational failures, not Python tracebacks. `prepare_capture_result.py` returns `run_status=prepare_failed` and `import_existing_result.py` returns `run_status=import_failed` with `stage=input_load`, paths, and `next_actions` before writing SQLite or Feishu.
- Malformed article-material or Codex-summary inputs are also structured recovery states. `enrich_article_summaries.py` returns `run_status=article_material_failed`; `apply_article_summaries.py` returns `run_status=summary_apply_failed`; both include the failing paths and do not update SQLite/output files before input validation passes.
- Feishu sync failures, empty output gates, and strict quality-gate stops must include `complete=false`, a `run_status`, and `next_actions`. A failed write keeps SQLite results intact and should be retried after fixing auth/table/config/network issues, not treated as a completed capture.
- `run_account_job.py` must promote Feishu write failures and output-gate stops into the account-level `run_status` after local completion is checked. A job with complete local enrichment but failed Feishu write is `sync_failed`/`quality_gate`, not `complete`.
- `captured_not_synced` and `resumed_not_synced` mean local discovery/enrichment is done but the formal Feishu ledger has not been updated. They must emit a scoped `run_account_job.py --resume-only --force-recover-running --sync` recovery command so newly filled `post_type` and generated `story_summary` can be upserted to the same Feishu rows. Do not let batch auto-follow execute this command unless the original batch/account command requested `--sync`.
- `run_account_job.py --resume-only` still checks OpenCLI Browser Bridge before running scoped `detail_time`, `lead_link`, `engagement`, or `post_type` tasks. If the bridge is unavailable, it returns `run_status=blocked_opencli` before calling `enrichment_worker.py`, so predictable environment issues do not increment task failure counts.
- If OpenCLI reports `browser_bridge_not_connected` while Chrome was not open or was just opened, treat it as a recoverable startup timing issue first: open the business Chrome profile, wait briefly for the Browser Bridge extension to reconnect, and retry the OpenCLI tab operation before reporting a hard blocker. Only call it human/profile intervention after bounded recovery still leaves the extension disconnected or the target Facebook tab/page unavailable.
- Do not use a tab opened only through the Codex Chrome plugin as proof that OpenCLI can see the same page. OpenCLI live capture operates through its own Browser Bridge session; the account page must be opened/listed through OpenCLI or visible to that session before capture starts.
- If `opencli_enrich_post_details.mjs` reports `opencli_session_busy` / `action_required=retry_later`, `enrichment_worker.py` must requeue the scoped detail tasks as `pending` without incrementing attempts or marking them failed. This is a self-recoverable contention state, not a data-quality failure.
- `opencli_enrich_post_details.mjs` owns a per-session lock to prevent overlapping detail navigation in the same OpenCLI session. Stale lock files are auto-recovered when the recorded PID is gone or the lock age exceeds `performance.detail_session_lock_stale_seconds`, so a crashed previous detail run should not permanently block补抓.
- Account jobs surface worker lock contention as top-level `worker_retry_later`, `worker_retry_later_count`, `worker_retry_later_reasons`, and matching `quality_summary` fields. Treat this as “已重排、继续续跑” rather than a completed run or a hard data failure.
- Account jobs surface non-structured enrichment worker failures as `run_status=worker_failed` with `worker_failure_reasons`. Treat this as a补抓执行器异常, not ordinary field incompleteness; fix the script/environment issue and then use `next_commands` to resume the scoped queue.
- For `blocked_opencli`, account-job `next_commands` must preserve context: pre-discovery blockers rerun full capture from the homepage top after OpenCLI is fixed, while `--resume-only` detail blockers resume the scoped SQLite queue with `--force-recover-running`.
- `run_account_job.py --resume-only` recovers scoped stale `running` enrichment tasks before worker passes. The default is intentionally conservative at 30 minutes to avoid duplicate detail navigation; account-job `next_commands` use `--force-recover-running` after known interruptions so the operator can continue immediately without waiting.
- `run_account_job.py` defaults to multiple automatic enrichment passes (`--max-resume-passes 8`, capped at 20) before reporting status. This is intentional: one pass may only import candidates or fetch article material, while later passes clear `post_type`, engagement, lead-link, and article-material gaps. Do not stop account-level worker passes merely because one or two passes did not improve aggregate metrics; continue until no machine-runnable work remains, a hard blocker appears, `retry_later` asks for a later retry, or the configured pass cap is reached. Generated resume commands must preserve this budget so business runs are not truncated after the first ledger write.
- Account-job recovery commands must preserve the full resume context: `--max-resume-passes`, `--enrichment-limit`, and `--resume-stale-running-seconds`. Dropping these options makes later automatic attempts fall back to defaults and can look like the pipeline stopped before detail fields or summaries finished.
- `run_accounts_job.py` defaults to multiple same-account auto-follow attempts (`--auto-follow-attempts 8`) and may run the same scoped resume command repeatedly. This is intentional: a large account can need several resume passes to clear detail fields, post type, article material, generated summaries, and final sync. Treat `--auto-follow-attempts` as the base budget, not a hard truncation point: if the account job still emits a same-account machine-runnable `next_commands` entry, the batch should keep following it up to the internal hard safety limit before surfacing the remaining `next_commands`, even when aggregate quality metrics did not improve in the immediately previous attempt.
- Batch auto-follow is driven by same-account machine-runnable `run_account_job.py` commands, not only by child exit codes. If an account job returns nonzero for a recoverable state such as `sync_failed`, `quality_gate`, or `no_work` but emits a scoped account-job recovery command, `run_accounts_job.py` should continue that command instead of stopping after the first attempt. Hard blockers (`blocked_auth`, `blocked_opencli`, `human_intervention_required`) remain explicit stops.
- If the original batch request included `--sync`, batch auto-follow must also continue same-account `captured_not_synced` / `resumed_not_synced` recovery commands. Otherwise a business “抓取并写入飞书” run can finish local enrichment but leave Feishu stale until the user asks again.
- If an account job emits both `coverage_incomplete` and `pending_enrichment` commands, batch auto-follow should prioritize `pending_enrichment` first so already-imported candidates get detail time, lead link, engagement, post type, article material, summaries, and Feishu upserts. Coverage remains a blocker and recovery command, but it must not starve field completion for rows already in SQLite/Feishu.
- Enrichment task selection must not let early detail stages starve article material. Keep `detail_time`, `lead_link`, `engagement`, and `post_type` moving, but each worker pass should also give `article_material` a chance within the configured limit so `story_summary` generation is not delayed behind a long post-type/detail backlog.
- `coverage_note` is a recoverable run marker, not a permanent post fact. When a later full homepage rerun for the same canonical post has complete coverage and sends an empty `coverage_note`, SQLite upsert must clear the old coverage note; otherwise the account can stay stuck in `coverage_incomplete` even after the rerun has found the full window.
- Batch jobs should auto-follow same-account `prepare_failed` and `import_failed` recovery commands when the child account job emits a full-capture rerun. These states are operational standardization/import interruptions; retrying the scoped account command lets field completion continue without waiting for the user to remind Codex. Keep `worker_failed` manual/fix-first because it means the补抓执行器 or environment returned an unstructured failure, but still surface the original batch rerun command first in top-level `next_commands` so the operator can resume the full business request after the worker issue is fixed.
- If `run_accounts_job.py` cannot read the Feishu account source sheet, it must return `run_status=accounts_load_failed` with a top-level `next_commands` entry that preserves the original batch scope/flags. Do not leave the operator with only a text `next_actions` hint.
- If the Feishu account source is readable but the current filters produce zero accounts, `run_accounts_job.py` must return `run_status=no_accounts` with commands to inspect `read_accounts.py` and rerun the original batch scope. Do not treat an empty target account set as a completed capture.
- If a child `run_account_job.py` crashes, prints non-JSON, or returns `complete=true` without a usable `quality_summary`, `run_accounts_job.py` must treat it as a script/output-contract failure and surface the original batch rerun command before any single-account retry. Do not let malformed child output turn a multi-account business request into a dead-end incomplete state.
- If a batch account-homepage open fails before an account job starts, `run_accounts_job.py` must still continue later accounts and surface both recovery steps in top-level `next_commands`: first `check_env.py --fix-opencli`, then the original scoped batch command with date/filter/sync/snapshot/resume budgets preserved. Do not leave the operator with only an environment-check command and no continuation command.
- If a child `run_account_job.py` reaches `blocked_opencli` during scoped detail补抓, `run_accounts_job.py` must still surface a top-level batch recovery path: first `check_env.py --fix-opencli`, then the original scoped batch command with all date/filter/sync/budget/threshold flags preserved, then any child single-account resume command. Do not leave the operator with only one account's `resume_after_opencli`, or a multi-account business request can stop before later accounts and rows get `帖子类型` / `故事概要`补齐.
- If batch real-write Feishu auth preflight fails before accounts are read or Facebook tabs are opened, `run_accounts_job.py` must stop without side effects and include a top-level `blocked_auth` `next_commands` entry that reruns the original scoped batch command after auth is restored. Preserve date/filter/sync/snapshot/resume/threshold flags.
- If a child `run_account_job.py` reaches `blocked_auth` during sync or scoped resume, `run_accounts_job.py` must also surface a top-level batch recovery path: first `check_env.py --fix-auth`, then the original scoped batch command with all date/filter/sync/budget/threshold flags preserved, then any child single-account resume command. Otherwise a token expiry mid-batch can leave later accounts or already imported rows without final `帖子类型` / `故事概要` updates until the user asks again.
- If a child `run_account_job.py` reaches `human_intervention_required`, `run_accounts_job.py` must not hide the original batch scope behind only a child resume command. The top-level `next_commands` should include the original scoped batch command for after the operator restores Facebook login/Profile/page visibility, then any child single-account resume command. Human intervention is still a hard stop for the blocked account, but later accounts should already have been attempted and the recovery path must preserve the full business request.
- When only article summaries remain and article material exists, `run_account_job.py` automatically exports scoped summary requests, runs `generate_article_summaries.py`, applies the generated Chinese summaries with `apply_article_summaries.py`, then recomputes completion before Feishu sync. `needs_codex_summary` should be a fallback for unavailable/failed summary generation, not the normal stopping point for business runs.
- Article-material availability, not `post_type`, engagement, exact time, or lead-link completion, is the precondition for automatic story-summary generation. If article material exists, `run_account_job.py` should generate/apply `story_summary` even while `post_type` remains a separate `待补抓：帖子类型` blocker; otherwise rows can sit in Feishu with both `帖子类型` and `故事概要` empty.
- Summary-only completion should bypass `enrichment_worker.py --stages summary`. That worker stage only verifies already-applied summaries and will otherwise report `requires_codex_chinese_summary`; account jobs should go straight to automatic summary export/generate/apply when `has_summary_only_work=true` and no automatic detail/article stages remain.
- Coverage gaps block declaring the account complete, but they must not block field completion for already imported rows. If scoped rows still have `coverage_incomplete_count` plus `requires_codex_summary_count`, `run_account_job.py` should still generate/apply summaries for the known rows, while keeping coverage as a separate blocker and recovery command.
- Partial summary generation should still move the pipeline forward. If `generate_article_summaries.py` returns rejected items but also writes usable summaries, `run_account_job.py` must apply the usable summaries, recompute completion, and leave only the rejected/missing rows as remaining blockers instead of failing the whole account.
- A successful `apply_article_summaries.py` process is not enough to clear the summary stage. If a scoped account job still requires Codex summaries but the apply result updates zero rows (`applied=0`), treat it as `summary_auto_apply_failed` / `summary_apply_noop` and keep the scoped job incomplete instead of reporting that story summaries were handled.
- `needs_codex_summary` and `summary_auto_apply_failed` recovery must include a scoped `run_account_job.py --resume-only --force-recover-running` command in addition to any `export_summary_requests.py` command. Batch jobs can only auto-follow account-job commands; leaving only an export command makes the “all accounts” flow stop at story-summary work until the user asks again.
- `run_account_job.py` and `run_accounts_job.py` now default to strict completion exits: if `run_status` is not `complete`, they return nonzero even when ledger rows were written. Use `--allow-incomplete-success` only for explicit preview/backward-compatibility cases where a caller will inspect JSON and not treat exit code 0 as finished.
- `run_accounts_job.py` is the batch orchestration entrypoint for “all target accounts.” It must continue iterating through the configured account list even when one account returns an incomplete ledger state, and it must aggregate each account's `run_status`, `completion_blockers`, and first `next_commands`. By default it opens each target account homepage through OpenCLI, runs each account job with strict completion, automatically follows same-account machine-runnable `next_commands` for at least the base `--auto-follow-attempts` budget and keeps going while quality metrics improve, and closes those automation-opened homepage tabs at the end of the batch; `--no-open-account-tabs` is only for cases where the operator intentionally pre-opened matching account tabs. Hard blockers such as Feishu auth, OpenCLI not connected, login/profile issues, or account-tab mismatch remain explicit per-account blockers; do not silently switch to another Facebook tab.
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
- Account source reads use `feishu.account_source_range`, default `A1:Z200`. Keep this wide enough for account name, competitor account, internal account, generic account, and future account columns; narrowing it can make batch jobs silently skip target accounts before capture starts.
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
- `lead_link_source` is `comment`, `comment_reply`, or `post_cta`.
- `landing_url` or `article_url` resolves outside Facebook/Meta.
- `story_summary` is a valid Chinese article summary and `summary_source=article`; copied article title, meta description, text excerpt, or English source text does not qualify.

Rows that fail the gate remain local `needs_enrichment`. Do not force-sync them.

## Script Map

- `scripts/check_env.py`: first command before testing, capture, or sync. Reports platform, `lark-cli`, Feishu config, OpenCLI command, daemon, Browser Bridge state, and recommended capture route. Add `--fix-auth` to actively run the same Feishu auth/config recovery used by real write paths; add `--fix-opencli` to try bounded OpenCLI daemon/doctor recovery before declaring browser bridge blocked.
- `scripts/config_loader.py`: owns platform/runtime resolution. Keep `lark_cli_path`, `opencli_path`, and `opencli_session` on `auto` unless a machine-specific override is necessary.
- `scripts/discovery_retry.py`: shared homepage discovery retry helpers. Use it for bounded automatic snapshot-budget retries; do not add separate retry policies in account or pipeline entrypoints.
- `scripts/models.py`: shared normalization logic. `facebook_content_key()` owns Facebook content identity for SQLite dedupe and Feishu upsert; `canonicalize_post_url()` turns that identity into a readable canonical URL.
- `scripts/read_accounts.py`: reads source Feishu accounts. It supports competitor/internal columns and generic account columns through `field_schema.py`.
- `scripts/run_accounts_job.py`: preferred batch business entrypoint for all configured accounts. It reads the Feishu account source sheet, calls `run_account_job.py` for each enabled target account, automatically follows same-account machine-runnable recovery commands for the base `--auto-follow-attempts` budget and extends while quality improves, aggregates account-level status, and returns nonzero by default if any account is incomplete or blocked.
- `scripts/opencli_runtime.mjs`: shared OpenCLI command/session/tab/eval helpers.
- `scripts/check_opencli_runtime_backend.mjs`: OpenCLI backend readiness check.
- `scripts/opencli_extract_current_tab.mjs`: current-tab extraction reference route. It defaults to `--max-snapshots 32` and `--min-snapshots 6` to reduce under-capture from early stable virtualized-feed snapshots.
- `scripts/fb_dom_extractors.js`: page DOM candidate extraction.
- `scripts/fb_time_extractors.js`: exact Facebook time parsing and timestamp-target helpers.
- `scripts/prepare_capture_result.py`: normalize raw homepage capture and keep incomplete candidates as `needs_enrichment`.
- `scripts/run_account_job.py`: preferred resumable business entrypoint for account capture, scoped enrichment, and formal ledger sync. It supports `--resume-only`, `--force-recover-running`, `--status-only`, `--last-hours 24`, `--sync`, `--dry-run`, `--max-snapshots`, `--min-snapshots`, `--expected-post-count`, `--expected-labels`, `--resume-stale-running-seconds`, `--allow-incomplete-success`, and explicit quality threshold flags; it emits `run_status` such as `complete`, `coverage_incomplete`, `incomplete_pending_tasks`, `needs_codex_summary`, `worker_failed`, `human_intervention_required`, `blocked_opencli`, or `blocked_auth`, plus top-level `quality_summary` and `next_commands` for the first recovery action. Non-`complete` states return nonzero by default so automation cannot mistake a partial ledger sync for a finished business run; use threshold flags when acceptance depends on minimum coverage or usable-rate targets.
- `scripts/run_accounts_job.py`: batch business entrypoint for all configured target accounts. It supports the same coverage checklist flags `--expected-post-count` and `--expected-labels` and must pass them to every child account job plus top-level retry commands.
- `scripts/opencli_enrich_post_details.mjs`: open detail pages, confirm exact time, expand comments/replies, resolve lead links, apply target-date filtering.
- `scripts/run_capture_pipeline.py`: lower-level fast partial capture/import helper. It discovers visible candidates, prepares/imports them as partial records, and queues enrichment, but does not own full job completion. It supports `--max-snapshots`, `--min-snapshots`, `--expected-post-count`, `--expected-labels`, `--fail-on-incomplete`, and explicit quality threshold flags; success payloads include `quality_summary`, top-level `completion_blockers`, `feishu_sync`, and `enrichment_tasks` so callers can distinguish ledger import from final usable completion. Failure branches must emit `run_status`, `complete=false`, `completion_blockers`, and `next_actions`. Do not use it as the final business “抓取并写入飞书” path.
- `scripts/enrichment_worker.py`: resumes queued `detail_time`, `lead_link`, `engagement`, `post_type`, and `article_material` tasks with local concurrency limits. Its `summary` stage no longer generates story summaries; it only verifies that a Codex-written Chinese summary has been applied. If article material exists but the Codex-written Chinese summary is still missing, it returns `run_status=needs_codex_summary` with exit code `2` instead of a generic worker failure.
- `enrichment_worker.py` must mark `detail_time` tasks done only when the same strict confirmed-time predicate used by final output passes. A relative-estimated `posted_at` is not enough, even if `time_confirmed` was accidentally truthy.
- `enrichment_worker.py` must mark `engagement` and `post_type` tasks done only when the same current-config field-audit reasons used for Feishu `待补抓：...` have cleared. Low likes, missing comments/shares, or unsupported post type should keep the task open/failed for refetch instead of inflating final usable rate.
- `scripts/enrich_article_summaries.py`: fetch article/landing material for summarization.
- `scripts/export_summary_requests.py`: export SQLite rows and article material that need Codex-written Chinese summaries. It supports `--date`, `--account-url`, `--account-name`, and `--account-type`; account-job `next_commands` must keep these scopes so summary work does not mix unrelated accounts.
- `scripts/generate_article_summaries.py`: generate `article_summaries.json` from exported article material. It creates Chinese summaries that pass `story_summary_policy.py` without copying article title/meta/excerpt. This is the automatic bridge used by account jobs before falling back to manual summary repair.
- `scripts/apply_article_summaries.py`: apply Codex-written Chinese summaries and recompute `output_status`.
- Field补抓 writers that change persisted post fields should refresh `output_status` and `field_audit_*` before Feishu sync. Use `store.update_post_fields_with_audit()` for article material and summary application so resolved `帖子类型` / `故事概要` gaps do not leave stale `待补抓` markers in SQLite or Feishu.
- `scripts/audit_story_summaries.py`: audit invalid local summaries and optionally downgrade them to `needs_enrichment`.
- `scripts/audit_fields.py`: audit missing/refetchable output fields, write `field_audit_*` markers, and queue refetch tasks when run with `--fix`.
- `scripts/import_existing_result.py`: import JSON/CSV into SQLite and optionally upsert auditable candidates; it reports `enrichment_tasks.stage_counts` / `open_stage_counts` so callers can see which补抓 stages block final usable rows. Use `--strict-ready-only` for ready-only sync.
- `scripts/filter_posts.py`: filter local SQLite records and optionally upsert auditable candidates; use `--strict-ready-only` for ready-only sync.
- `scripts/sync_feishu.py`: sync local records to Feishu using candidate upsert by default; use `--strict-ready-only` for ready-only sync. When called with `conn`, it first refreshes the scoped SQLite rows with current config audit/status and queues missing business-field enrichment tasks, so historical rows with a post link but empty `帖子类型` or `故事概要` cannot remain silent. Direct sync results from import/filter/sync paths include `completion_blockers`; `ok=true` with `run_status=synced_ledger_incomplete` means ledger rows were written but final usable rows still need补抓.
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

Preferred batch capture for all configured target accounts:

```bash
python3 scripts/run_accounts_job.py --config config/settings.yaml --last-hours 24 --sync
```

Batch acceptance gate, fail the shell command unless every configured account is fully complete:

```bash
python3 scripts/run_accounts_job.py --config config/settings.yaml --target-date YYMMDD --sync --fail-on-incomplete
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
python3 scripts/generate_article_summaries.py --input exports/summary_requests.json --output exports/article_summaries.json
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
