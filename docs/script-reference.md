# Script Reference

Run commands from the repository root.

## Primary Commands

Environment and dependency check:

```bash
python3 scripts/check_env.py --config config/settings.yaml --fix-auth --fix-opencli
```

Read Feishu account source:

```bash
python3 scripts/read_accounts.py --config config/settings.yaml
```

Collect all configured accounts:

```bash
python3 scripts/run_accounts_job.py --config config/settings.yaml --last-hours 24 --sync
```

Collect one account:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url <facebook-account-url> --last-hours 24 --sync
```

Resume one account:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url <facebook-account-url> --target-date YYMMDD --resume-only --force-recover-running --sync
```

Hard completion gate:

```bash
python3 scripts/run_account_job.py --config config/settings.yaml --account-url <facebook-account-url> --target-date YYMMDD --resume-only --status-only --sync --dry-run --fail-on-incomplete
```

## Lower-Level Recovery And Debug Commands

Prepare raw OpenCLI capture:

```bash
python3 scripts/prepare_capture_result.py --input exports/raw.json --output exports/prepared.json --target-date YYMMDD
```

Detail enrichment:

```bash
node scripts/opencli_enrich_post_details.mjs --input exports/prepared.json --output exports/detail_enriched.json --target-date YYMMDD
```

Fetch article material:

```bash
python3 scripts/enrich_article_summaries.py --input exports/detail_enriched.json --output exports/with_article_material.json
```

SQLite summary flow:

```bash
python3 scripts/export_summary_requests.py --config config/settings.yaml --output exports/summary_requests.json --date YYMMDD --account-url <url> --account-type competitor
python3 scripts/generate_article_summaries.py --input exports/summary_requests.json --output exports/article_summaries.json
python3 scripts/apply_article_summaries.py --config config/settings.yaml --summaries exports/article_summaries.json
```

Resume enrichment queue:

```bash
python3 scripts/enrichment_worker.py --config config/settings.yaml --stages detail_time,lead_link,engagement,post_type,article_material --limit 50
```

Audit missing fields and queue refetch:

```bash
python3 scripts/audit_fields.py --config config/settings.yaml --fix
```

Audit invalid local summaries:

```bash
python3 scripts/audit_story_summaries.py --config config/settings.yaml
python3 scripts/audit_story_summaries.py --config config/settings.yaml --fix
```

Import existing JSON/CSV:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input <file> --no-sync
```

Strict sync complete imported rows:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input <file> --sync
```

Filter local library:

```bash
python3 scripts/filter_posts.py --config config/settings.yaml --date YYMMDD --account-type competitor
```

Explicit audit/ledger sync for incomplete rows, only when requested:

```bash
python3 scripts/import_existing_result.py --config config/settings.yaml --input <file> --sync-audit --dry-run
```

## Validation

Documentation-only changes:

```bash
git diff --check
```

Code changes:

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
