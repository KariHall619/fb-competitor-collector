# Data Contract

## Minimum Importable Record

```json
{
  "post_url": "https://www.facebook.com/...",
  "article_url": "https://...",
  "story_summary": "简述"
}
```

This minimum is for manual/imported candidates only. Formal Feishu output requires the strict quality gate below.

## Preferred Fields

- `account_name`
- `account_url`
- `account_type`: `competitor` or `internal`
- `post_url`
- `canonical_post_url`
- `raw_fb_url`
- `parent_post_url`
- `fb_link_kind`: `parent_post`, `reel`, `photo`, `video`, `share`, `group_post`, or `facebook`
- `post_type`
- `posted_date`: `YYMMDD`
- `posted_at`: detail-confirmed hour-level or better time, e.g. `2026年5月29日 12:32`
- `relative_time_text`: homepage label such as `1h` or `12h`; never formal output time
- `article_url`
- `lead_url_raw`
- `landing_url`
- `lead_link_status`: `qualified` only after resolving outside Facebook/Meta
- `lead_link_source`: `comment`, `comment_reply`, or valid current-post `post_cta`
- `story_summary`
- `summary_source`: `article`
- `views`
- `likes`
- `engagement_raw`
- `crawl_status`
- `output_status`
- `field_audit_*`
- `raw_payload`

## Identity And Dedupe

- `scripts/models.py::facebook_content_key` is the Python source of truth for Facebook content identity.
- `opencli/clis/facebook/fb-competitor-posts.js::postKey` must stay aligned with `facebook_content_key`.
- Preserve `photo.php`, `/photo/`, `/photos/`, `/reel/`, `/watch/`, `/video/`, `/videos/`, `/share/`, and group-post candidates.
- Parent post links are best-effort dedupe helpers. If absent, keep the original media/share URL.
- Re-imported partial rows must not downgrade stronger stored fields.

## Strict Feishu Output Gate

Normal `--sync` writes only current complete rows. A formal output row requires:

- valid Facebook content URL
- detail-confirmed `posted_at`
- `time_confirmed=true`
- `time_source` is not `relative_estimated`, `relative_hour`, or `relative_label`
- qualified account-owned comment/reply/current-post CTA lead link
- external `landing_url` or `article_url`
- article-based Chinese `story_summary`
- `summary_source=article`
- required post type and engagement checks pass under current config
- coverage blockers are clear

Rows that fail remain local `needs_enrichment` / pending tasks. Do not force-sync them to the formal table.

## Feishu Output Format

The output table `FB竞品帖子链接` uses A-K columns:

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

`config/settings.yaml` and `config/settings.yaml.example` store this under `feishu.field_schema.output_headers`. `scripts/field_schema.py` owns header aliases, account-source roles, output row ordering, and engagement formatting.

## Status Meanings

- `complete`: scoped business job is done.
- `coverage_incomplete`: visible/account-window coverage is not proven complete.
- `incomplete_pending_tasks`: known candidates still need enrichment.
- `needs_codex_summary`: article material exists but valid Chinese summary is not applied.
- `summary_auto_apply_failed`: summary generation/application did not clear the scoped gap.
- `captured_not_synced` / `resumed_not_synced`: local work is done but Feishu formal sync has not completed.
- `blocked_auth`: Feishu user auth must be restored.
- `blocked_opencli`: OpenCLI/Browser Bridge must be restored.
- `human_intervention_required`: Facebook/profile/page state requires human action.
- `worker_failed`: enrichment worker or script contract failed; fix before treating as ordinary missing fields.

Non-`complete` means the business workflow is not fully finished, even if some local rows or audit rows exist.
