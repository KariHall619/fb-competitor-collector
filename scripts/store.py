#!/usr/bin/env python3
"""SQLite storage for FB competitor posts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

from field_audit import audit_fields_for_storage
from field_audit import is_system_audit_marker
from models import clean_post_url, has_qualified_comment_lead_link
from pipeline_status import crawl_status_for, missing_enrichment_stages, output_status_for
from story_summary_policy import has_valid_story_summary
from value_utils import parse_bool


POST_COLUMNS = [
    "account_name",
    "account_url",
    "account_type",
    "post_url",
    "canonical_post_url",
    "raw_fb_url",
    "parent_post_url",
    "fb_link_kind",
    "post_type",
    "posted_date",
    "posted_at",
    "relative_time_text",
    "article_url",
    "lead_url_raw",
    "landing_url",
    "lead_link_status",
    "lead_link_source",
    "story_summary",
    "views",
    "likes",
    "comments",
    "shares",
    "crawled_at",
    "source_skill",
    "note",
    "engagement_raw",
    "crawl_status",
    "output_status",
    "time_confirmed",
    "time_source",
    "summary_source",
    "adoption_status",
    "field_audit_status",
    "field_audit_reasons",
    "field_audit_note",
    "coverage_note",
    "first_seen_at",
    "last_seen_at",
    "raw_payload",
]

ENRICHMENT_STAGES = ("detail_time", "lead_link", "engagement", "post_type", "article_material", "summary")
TASK_OPEN_STATUSES = ("pending", "failed")
TASK_STAGE_ORDER = {stage: index for index, stage in enumerate(ENRICHMENT_STAGES, 1)}
TASK_FETCH_LIMIT_MULTIPLIER = 2


SCHEMA_COLUMNS: dict[str, str] = {
    "canonical_post_url": "TEXT",
    "raw_fb_url": "TEXT",
    "parent_post_url": "TEXT",
    "fb_link_kind": "TEXT",
    "lead_url_raw": "TEXT",
    "landing_url": "TEXT",
    "lead_link_status": "TEXT",
    "lead_link_source": "TEXT",
    "crawl_status": "TEXT",
    "output_status": "TEXT",
    "time_confirmed": "INTEGER DEFAULT 0",
    "posted_at": "TEXT",
    "relative_time_text": "TEXT",
    "summary_source": "TEXT",
    "time_source": "TEXT",
    "adoption_status": "TEXT",
    "field_audit_status": "TEXT",
    "field_audit_reasons": "TEXT",
    "field_audit_note": "TEXT",
    "comments": "INTEGER",
    "shares": "INTEGER",
    "coverage_note": "TEXT",
    "first_seen_at": "TEXT",
    "last_seen_at": "TEXT",
}


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_name TEXT,
            account_url TEXT,
            account_type TEXT,
            post_url TEXT NOT NULL UNIQUE,
            canonical_post_url TEXT,
            raw_fb_url TEXT,
            parent_post_url TEXT,
            fb_link_kind TEXT,
            post_type TEXT,
            posted_date TEXT,
            posted_at TEXT,
            relative_time_text TEXT,
            article_url TEXT,
            lead_url_raw TEXT,
            landing_url TEXT,
            lead_link_status TEXT,
            lead_link_source TEXT,
            story_summary TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            crawled_at TEXT,
            source_skill TEXT,
            note TEXT,
            engagement_raw TEXT,
            crawl_status TEXT,
            output_status TEXT,
            time_confirmed INTEGER DEFAULT 0,
            summary_source TEXT,
            adoption_status TEXT,
            field_audit_status TEXT,
            field_audit_reasons TEXT,
            field_audit_note TEXT,
            coverage_note TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            raw_payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(posts)").fetchall()
    }
    for column, column_type in SCHEMA_COLUMNS.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {column} {column_type}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_canonical_post_url ON posts(canonical_post_url)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS enrichment_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_post_url TEXT NOT NULL,
            post_url TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_run_at TEXT,
            locked_at TEXT,
            duration_ms INTEGER,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(canonical_post_url, stage)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_enrichment_tasks_status_stage ON enrichment_tasks(status, stage, next_run_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_material_cache (
            url TEXT PRIMARY KEY,
            ok INTEGER NOT NULL DEFAULT 0,
            material_json TEXT NOT NULL,
            error TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_name TEXT,
            account_url TEXT,
            account_type TEXT,
            stage TEXT,
            error_message TEXT,
            raw_payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


ESTIMATED_TIME_SOURCES = {"relative_hour", "relative_estimated", "relative_label"}
RECHECKABLE_TIME_SOURCES = {"synthetic_hover_tooltip"}
STRONG_TIME_SOURCES = {"real_mouse_tooltip", "embedded_publish_time"}
PROTECTED_FINAL_STATUSES = {"ready_for_output", "output_synced"}


def has_confirmed_time_value(post: dict[str, Any]) -> bool:
    return bool(
        post.get("posted_at")
        and parse_bool(post.get("time_confirmed"))
        and str(post.get("time_source") or "") not in ESTIMATED_TIME_SOURCES
    )


def has_stronger_time_value(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    return bool(
        incoming.get("posted_at")
        and parse_bool(incoming.get("time_confirmed"))
        and str(existing.get("time_source") or "") in RECHECKABLE_TIME_SOURCES
        and (str(incoming.get("time_source") or "").startswith("dom_") or str(incoming.get("time_source") or "") in STRONG_TIME_SOURCES)
    )


def has_qualified_lead_value(post: dict[str, Any]) -> bool:
    return has_qualified_comment_lead_link(post)


def has_engagement_value(post: dict[str, Any]) -> bool:
    return any(post.get(field) is not None for field in ("likes", "comments", "shares", "views"))


def parse_int_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def engagement_raw_conflicts_with_numbers(raw: Any, post: dict[str, Any]) -> bool:
    text = str(raw or "")
    if not text:
        return False
    likes = parse_int_value(post.get("likes"))
    if likes is None:
        return False
    match = re.search(r"点赞量：\s*(\d+)", text)
    if not match:
        return False
    raw_likes = int(match.group(1))
    return raw_likes >= 100000 and likes <= 100


def non_empty(value: Any) -> bool:
    return value not in (None, "")


def payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def has_article_material_payload(value: Any) -> bool:
    material = payload_dict(value).get("article_material")
    return isinstance(material, dict) and bool(material)


def merge_raw_payload(existing_value: Any, incoming_value: Any) -> Any:
    if not non_empty(incoming_value):
        return existing_value
    existing_payload = payload_dict(existing_value)
    incoming_payload = payload_dict(incoming_value)
    if existing_payload and incoming_payload:
        merged = {**existing_payload, **incoming_payload}
        if "coverage_note" in incoming_payload and not incoming_payload.get("coverage_note"):
            merged.pop("coverage_note", None)
        if has_article_material_payload(existing_value) and not has_article_material_payload(incoming_value):
            merged["article_material"] = existing_payload["article_material"]
        return json.dumps(merged, ensure_ascii=False)
    if has_article_material_payload(existing_value) and not has_article_material_payload(incoming_value):
        return existing_value
    return incoming_value


def choose_value(existing: dict[str, Any], incoming: dict[str, Any], column: str) -> Any:
    current = existing.get(column)
    new_value = incoming.get(column)
    if column in {"first_seen_at", "created_at"}:
        return current or new_value
    if column == "raw_payload":
        return merge_raw_payload(current, new_value)
    if column in {"last_seen_at", "crawled_at"}:
        return new_value if non_empty(new_value) else current
    if column == "coverage_note":
        if "coverage_note" in incoming:
            return new_value or ""
        return current
    if column in {"field_audit_status", "field_audit_reasons", "field_audit_note"}:
        return new_value if new_value is not None else current
    if column == "adoption_status":
        if current and not is_system_audit_marker(current):
            return current
        return new_value if non_empty(new_value) else current
    if column in {"posted_at", "posted_date", "time_source", "time_confirmed"}:
        if has_stronger_time_value(existing, incoming):
            return new_value if non_empty(new_value) else current
        if has_confirmed_time_value(existing) and not has_confirmed_time_value(incoming):
            return current
        return new_value if non_empty(new_value) else current
    if column in {"lead_url_raw", "landing_url", "article_url", "lead_link_status", "lead_link_source"}:
        if has_qualified_lead_value(existing) and not has_qualified_lead_value(incoming):
            return current
        return new_value if non_empty(new_value) else current
    if column in {"story_summary", "summary_source"}:
        if has_valid_story_summary(existing) and not has_valid_story_summary(incoming):
            return current
        return new_value if non_empty(new_value) else current
    if column in {"views", "likes", "comments", "shares"}:
        if has_engagement_value(existing) and not has_engagement_value(incoming):
            return current
        return new_value if new_value is not None else current
    if column == "engagement_raw":
        if non_empty(new_value):
            return new_value
        if engagement_raw_conflicts_with_numbers(current, {**existing, **incoming}):
            return ""
        if has_engagement_value(existing) and not has_engagement_value(incoming):
            return current
        return current
    if column in {"output_status", "crawl_status"}:
        if existing.get("output_status") in PROTECTED_FINAL_STATUSES and incoming.get("output_status") not in PROTECTED_FINAL_STATUSES:
            return current
        return new_value if non_empty(new_value) else current
    return new_value if non_empty(new_value) else current


def merged_post(existing: dict[str, Any], incoming: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {column: choose_value(existing, incoming, column) for column in POST_COLUMNS}
    if not merged.get("canonical_post_url"):
        merged["canonical_post_url"] = incoming.get("canonical_post_url") or existing.get("canonical_post_url") or incoming.get("post_url")
    computed_output = output_status_for(merged, config)
    merged["output_status"] = computed_output
    merged["crawl_status"] = crawl_status_for(merged, config)
    merged.update(audit_fields_for_storage(merged, config))
    return merged


def upsert_post(conn: sqlite3.Connection, post: dict[str, Any], config: dict[str, Any] | None = None) -> str:
    if not post.get("post_url"):
        raise ValueError("post_url is required")
    if not post.get("canonical_post_url"):
        post["canonical_post_url"] = post["post_url"]
    post["output_status"] = output_status_for(post, config)
    post["crawl_status"] = crawl_status_for(post, config)
    post.update(audit_fields_for_storage(post, config))
    existing = conn.execute(
        "SELECT * FROM posts WHERE canonical_post_url = ? OR post_url = ?",
        (post["canonical_post_url"], post["post_url"]),
    ).fetchone()
    if existing:
        post = merged_post(dict(existing), post, config)
        assignments = ", ".join([f"{column} = ?" for column in POST_COLUMNS if column != "post_url"])
        update_values = [post.get(column) for column in POST_COLUMNS if column != "post_url"]
        update_values.extend([post["canonical_post_url"], post["post_url"]])
        conn.execute(
            f"UPDATE posts SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE canonical_post_url = ? OR post_url = ?",
            update_values,
        )
        conn.commit()
        return "updated"
    values = [post.get(column) for column in POST_COLUMNS]
    placeholders = ", ".join(["?"] * len(POST_COLUMNS))
    conn.execute(
        f"INSERT INTO posts ({', '.join(POST_COLUMNS)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return "inserted"


def row_for_post(conn: sqlite3.Connection, post: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM posts WHERE canonical_post_url = ? OR post_url = ?",
        (post.get("canonical_post_url"), post.get("post_url")),
    ).fetchone()
    return dict(row) if row else None


def upsert_posts(
    conn: sqlite3.Connection,
    posts: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inserted: list[dict[str, Any]] = []
    synced_candidates: list[dict[str, Any]] = []
    updated = 0
    errors = 0
    for post in posts:
        try:
            result = upsert_post(conn, post, config)
            stored = row_for_post(conn, post) or post
            if result == "inserted":
                inserted.append(stored)
            else:
                updated += 1
            synced_candidates.append(stored)
        except Exception as exc:
            errors += 1
            log_error(conn, post, "upsert", str(exc))
    return {"inserted": inserted, "sync_candidates": synced_candidates, "updated": updated, "errors": errors}


def enqueue_enrichment_tasks(
    conn: sqlite3.Connection,
    post: dict[str, Any],
    *,
    stages: list[str] | tuple[str, ...] | None = None,
    config: dict[str, Any] | None = None,
) -> int:
    canonical = post.get("canonical_post_url") or post.get("post_url")
    post_url = post.get("post_url") or canonical
    if not canonical or not post_url:
        return 0
    wanted = list(stages or missing_enrichment_stages(post, config))
    for stage in wanted:
        if stage not in ENRICHMENT_STAGES:
            continue
        conn.execute(
            """
            INSERT INTO enrichment_tasks
            (canonical_post_url, post_url, stage, status, payload, next_run_at)
            VALUES (?, ?, ?, 'pending', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(canonical_post_url, stage) DO UPDATE SET
                post_url = excluded.post_url,
                status = CASE
                    WHEN enrichment_tasks.status = 'running' THEN enrichment_tasks.status
                    ELSE 'pending'
                END,
                last_error = CASE
                    WHEN enrichment_tasks.status = 'running' THEN enrichment_tasks.last_error
                    ELSE NULL
                END,
                next_run_at = CASE
                    WHEN enrichment_tasks.status = 'running' THEN enrichment_tasks.next_run_at
                    ELSE CURRENT_TIMESTAMP
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (canonical, post_url, stage, json.dumps({"post_url": post_url}, ensure_ascii=False)),
        )
    conn.commit()
    return len([stage for stage in wanted if stage in ENRICHMENT_STAGES])


def enqueue_enrichment_tasks_for_posts(
    conn: sqlite3.Connection,
    posts: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    before = conn.total_changes
    planned_stage_counts: dict[str, int] = {}
    for post in posts:
        for stage in missing_enrichment_stages(post, config):
            if stage in ENRICHMENT_STAGES:
                planned_stage_counts[stage] = planned_stage_counts.get(stage, 0) + 1
        enqueue_enrichment_tasks(conn, post, config=config)
    open_stage_counts: dict[str, int] = {}
    scoped_tasks = enrichment_tasks_for_posts(conn, posts)
    for task in scoped_tasks:
        if task.get("status") not in {"pending", "failed", "running"}:
            continue
        stage = str(task.get("stage") or "")
        if not stage:
            continue
        open_stage_counts[stage] = open_stage_counts.get(stage, 0) + 1
    return {
        "queued_or_refreshed": conn.total_changes - before,
        "candidate_count": len(posts),
        "stage_counts": dict(sorted(planned_stage_counts.items())),
        "open_stage_counts": dict(sorted(open_stage_counts.items())),
        "open_task_count": sum(open_stage_counts.values()),
    }


def order_pending_tasks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            TASK_STAGE_ORDER.get(str(row.get("stage") or ""), 99),
            int(row.get("attempts") or 0),
            int(row.get("id") or 0),
        ),
    )


def limit_pending_tasks(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    ordered = order_pending_tasks(rows)
    if len(ordered) <= limit:
        return ordered

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        stage = str(row.get("stage") or "")
        grouped.setdefault(stage, []).append(row)
    stage_order = [
        stage
        for stage in ENRICHMENT_STAGES
        if grouped.get(stage)
    ]
    stage_order.extend(
        sorted(stage for stage in grouped if stage not in set(stage_order))
    )

    selected: list[dict[str, Any]] = []
    indexes = {stage: 0 for stage in stage_order}
    while len(selected) < limit:
        made_progress = False
        for stage in stage_order:
            index = indexes[stage]
            bucket = grouped[stage]
            if index >= len(bucket):
                continue
            selected.append(bucket[index])
            indexes[stage] = index + 1
            made_progress = True
            if len(selected) >= limit:
                break
        if not made_progress:
            break
    return selected


def pending_task_fetch_limit(limit: int) -> int:
    return max(1, limit) * TASK_FETCH_LIMIT_MULTIPLIER


def fetch_pending_task_rows(
    conn: sqlite3.Connection,
    *,
    clauses: list[str],
    params: list[Any],
    stages: list[str] | tuple[str, ...] | None,
    limit: int,
) -> list[dict[str, Any]]:
    wanted_stages = [stage for stage in ENRICHMENT_STAGES if not stages or stage in stages]
    if stages:
        wanted_stages.extend(str(stage) for stage in stages if str(stage) not in set(wanted_stages))
    rows: list[dict[str, Any]] = []
    per_stage_limit = pending_task_fetch_limit(limit)
    for stage in wanted_stages:
        stage_clauses = [*clauses, "stage = ?"]
        stage_params = [*params, stage]
        stage_rows = conn.execute(
            f"""
            SELECT * FROM enrichment_tasks
            WHERE {' AND '.join(stage_clauses)}
            ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, attempts ASC, id ASC
            LIMIT ?
            """,
            [*stage_params, per_stage_limit],
        ).fetchall()
        rows.extend(dict(row) for row in stage_rows)
    return rows


def pending_enrichment_tasks(
    conn: sqlite3.Connection,
    *,
    stages: list[str] | tuple[str, ...] | None = None,
    limit: int = 50,
    stale_running_seconds: int = 1800,
) -> list[dict[str, Any]]:
    recover_stale_running_tasks(conn, stale_running_seconds=stale_running_seconds)
    clauses = ["status IN ('pending', 'failed')", "(next_run_at IS NULL OR next_run_at <= CURRENT_TIMESTAMP)"]
    params: list[Any] = []
    rows = fetch_pending_task_rows(conn, clauses=clauses, params=params, stages=stages, limit=limit)
    return limit_pending_tasks(rows, limit)


def recover_stale_running_tasks(conn: sqlite3.Connection, *, stale_running_seconds: int = 1800) -> int:
    stale_cutoff = (datetime.utcnow() - timedelta(seconds=stale_running_seconds)).isoformat(timespec="seconds")
    before = conn.total_changes
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'pending',
            locked_at = NULL,
            next_run_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'running'
          AND locked_at IS NOT NULL
          AND replace(locked_at, ' ', 'T') <= ?
        """,
        (stale_cutoff,),
    )
    conn.commit()
    return conn.total_changes - before


def recover_stale_running_tasks_for_posts(
    conn: sqlite3.Connection,
    posts: list[dict[str, Any]],
    *,
    stale_running_seconds: int = 1800,
) -> int:
    scope_clause, params = task_scope_clause(posts)
    if scope_clause == "1 = 0":
        return 0
    stale_cutoff = (datetime.utcnow() - timedelta(seconds=stale_running_seconds)).isoformat(timespec="seconds")
    before = conn.total_changes
    conn.execute(
        f"""
        UPDATE enrichment_tasks
        SET status = 'pending',
            locked_at = NULL,
            next_run_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE {scope_clause}
          AND status = 'running'
          AND locked_at IS NOT NULL
          AND replace(locked_at, ' ', 'T') <= ?
        """,
        [*params, stale_cutoff],
    )
    conn.commit()
    return conn.total_changes - before


def task_scope_keys(posts: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    canonical_keys = sorted(
        {
            str(post.get("canonical_post_url") or post.get("post_url") or "").strip()
            for post in posts
            if str(post.get("canonical_post_url") or post.get("post_url") or "").strip()
        }
    )
    post_urls = sorted(
        {
            str(post.get("post_url") or "").strip()
            for post in posts
            if str(post.get("post_url") or "").strip()
        }
    )
    return canonical_keys, post_urls


def task_scope_clause(posts: list[dict[str, Any]]) -> tuple[str, list[Any]]:
    canonical_keys, post_urls = task_scope_keys(posts)
    clauses: list[str] = []
    params: list[Any] = []
    if canonical_keys:
        placeholders = ", ".join("?" for _ in canonical_keys)
        clauses.append(f"canonical_post_url IN ({placeholders})")
        params.extend(canonical_keys)
    if post_urls:
        placeholders = ", ".join("?" for _ in post_urls)
        clauses.append(f"post_url IN ({placeholders})")
        params.extend(post_urls)
    if not clauses:
        return "1 = 0", []
    return "(" + " OR ".join(clauses) + ")", params


def pending_enrichment_tasks_for_posts(
    conn: sqlite3.Connection,
    posts: list[dict[str, Any]],
    *,
    stages: list[str] | tuple[str, ...] | None = None,
    limit: int = 50,
    stale_running_seconds: int = 1800,
) -> list[dict[str, Any]]:
    scope_clause, params = task_scope_clause(posts)
    recover_stale_running_tasks_for_posts(conn, posts, stale_running_seconds=stale_running_seconds)
    clauses = ["status IN ('pending', 'failed')", "(next_run_at IS NULL OR next_run_at <= CURRENT_TIMESTAMP)"]
    clauses.append(scope_clause)
    rows = fetch_pending_task_rows(conn, clauses=clauses, params=params, stages=stages, limit=limit)
    return limit_pending_tasks(rows, limit)


def enrichment_tasks_for_posts(conn: sqlite3.Connection, posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scope_clause, params = task_scope_clause(posts)
    rows = conn.execute(
        f"""
        SELECT * FROM enrichment_tasks
        WHERE {scope_clause}
        ORDER BY id ASC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def mark_task_running(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'running', locked_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (utc_now(), task_id),
    )
    conn.commit()


def mark_task_done(conn: sqlite3.Connection, task_id: int, *, duration_ms: int | None = None) -> None:
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'done',
            duration_ms = COALESCE(?, duration_ms),
            last_error = NULL,
            locked_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (duration_ms, task_id),
    )
    conn.commit()


def mark_stage_done(conn: sqlite3.Connection, post: dict[str, Any], stage: str, *, duration_ms: int | None = None) -> None:
    canonical = post.get("canonical_post_url") or post.get("post_url")
    post_url = post.get("post_url") or canonical
    if not canonical or not post_url:
        return
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'done',
            duration_ms = COALESCE(?, duration_ms),
            last_error = NULL,
            locked_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE stage = ? AND (canonical_post_url = ? OR post_url = ?)
        """,
        (duration_ms, stage, canonical, post_url),
    )
    conn.commit()


def mark_task_failed(
    conn: sqlite3.Connection,
    task_id: int,
    error: str,
    *,
    duration_ms: int | None = None,
    retry_seconds: int = 900,
) -> None:
    next_run_at = (datetime.utcnow() + timedelta(seconds=retry_seconds)).isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'failed',
            attempts = attempts + 1,
            last_error = ?,
            next_run_at = ?,
            duration_ms = COALESCE(?, duration_ms),
            locked_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (error[:1000], next_run_at, duration_ms, task_id),
    )
    conn.commit()


def mark_task_pending(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    reason: str = "",
    retry_seconds: int = 0,
) -> None:
    next_run_at = (datetime.utcnow() + timedelta(seconds=max(0, retry_seconds))).isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'pending',
            last_error = ?,
            next_run_at = ?,
            locked_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (reason[:1000], next_run_at, task_id),
    )
    conn.commit()


def task_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT stage, status, COUNT(*) AS count FROM enrichment_tasks GROUP BY stage, status"
    ).fetchall()
    return {f"{row['stage']}:{row['status']}": int(row["count"]) for row in rows}


def task_counts_for_posts(conn: sqlite3.Connection, posts: list[dict[str, Any]]) -> dict[str, int]:
    scope_clause, params = task_scope_clause(posts)
    rows = conn.execute(
        f"""
        SELECT stage, status, COUNT(*) AS count
        FROM enrichment_tasks
        WHERE {scope_clause}
        GROUP BY stage, status
        """,
        params,
    ).fetchall()
    return {f"{row['stage']}:{row['status']}": int(row["count"]) for row in rows}


def posts_for_tasks(conn: sqlite3.Connection, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    for task in tasks:
        row = conn.execute(
            "SELECT * FROM posts WHERE canonical_post_url = ? OR post_url = ?",
            (task.get("canonical_post_url"), task.get("post_url")),
        ).fetchone()
        if row:
            posts.append(dict(row))
    return posts


def post_for_task(conn: sqlite3.Connection, task: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM posts WHERE canonical_post_url = ? OR post_url = ?",
        (task.get("canonical_post_url"), task.get("post_url")),
    ).fetchone()
    return dict(row) if row else None


def update_post_fields(conn: sqlite3.Connection, post: dict[str, Any], fields: dict[str, Any]) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    values.extend([post.get("canonical_post_url"), post.get("post_url")])
    conn.execute(
        f"UPDATE posts SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE canonical_post_url = ? OR post_url = ?",
        values,
    )
    conn.commit()


def update_post_fields_with_audit(
    conn: sqlite3.Connection,
    post: dict[str, Any],
    fields: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> None:
    """Update a post and refresh status/audit columns from the stored row."""

    if not fields:
        return
    update_post_fields(conn, post, fields)
    stored = row_for_post(conn, post)
    if not stored:
        return
    refreshed = {
        **stored,
        "output_status": output_status_for(stored, config),
        "crawl_status": crawl_status_for(stored, config),
    }
    refreshed.update(audit_fields_for_storage(refreshed, config))
    update_post_fields(
        conn,
        stored,
        {
            "output_status": refreshed["output_status"],
            "crawl_status": refreshed["crawl_status"],
            "field_audit_status": refreshed["field_audit_status"],
            "field_audit_reasons": refreshed["field_audit_reasons"],
            "field_audit_note": refreshed["field_audit_note"],
        },
    )


def cached_article_material(conn: sqlite3.Connection, url: str) -> dict[str, Any] | None:
    if not url:
        return None
    row = conn.execute("SELECT material_json FROM article_material_cache WHERE url = ?", (url,)).fetchone()
    if not row:
        return None
    return json.loads(row["material_json"])


def upsert_article_material(conn: sqlite3.Connection, url: str, material: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO article_material_cache (url, ok, material_json, error)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            ok = excluded.ok,
            material_json = excluded.material_json,
            error = excluded.error,
            updated_at = CURRENT_TIMESTAMP
        """,
        (url, int(bool(material.get("ok"))), json.dumps(material, ensure_ascii=False), material.get("error")),
    )
    conn.commit()


def mark_output_synced(conn: sqlite3.Connection, posts: list[dict[str, Any]]) -> None:
    for post in posts:
        conn.execute(
            """
            UPDATE posts
            SET output_status = 'output_synced',
                crawl_status = 'output_synced',
                updated_at = CURRENT_TIMESTAMP
            WHERE canonical_post_url = ? OR post_url = ?
            """,
            (post.get("canonical_post_url"), post.get("post_url")),
        )
    conn.commit()


def log_error(conn: sqlite3.Connection, payload: dict[str, Any], stage: str, message: str) -> None:
    conn.execute(
        """
        INSERT INTO crawl_errors
        (account_name, account_url, account_type, stage, error_message, raw_payload)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payload.get("account_name", ""),
            payload.get("account_url", ""),
            payload.get("account_type", ""),
            stage,
            message,
            str(payload),
        ),
    )
    conn.commit()


def account_url_variants(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    variants: list[str] = []

    def add(item: str) -> None:
        normalized = item.strip()
        if normalized and normalized not in variants:
            variants.append(normalized)

    for candidate in (text, clean_post_url(text)):
        if not candidate:
            continue
        add(candidate)
        parsed = urlparse(candidate)
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        query = parsed.query
        if netloc.startswith("www."):
            add(urlunparse((parsed.scheme or "https", netloc[4:], path, "", query, "")))
        elif netloc == "facebook.com":
            add(urlunparse((parsed.scheme or "https", f"www.{netloc}", path, "", query, "")))
        if query:
            add(urlunparse((parsed.scheme or "https", netloc or "facebook.com", path, "", "", "")))
    return variants


def query_posts(
    conn: sqlite3.Connection,
    *,
    date: str = "",
    start_date: str = "",
    end_date: str = "",
    include_unknown_date: bool = False,
    account_name: str = "",
    account_url: str = "",
    account_type: str = "",
    post_type: str = "",
    min_views: int | None = None,
    min_likes: int | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    date_clauses: list[str] = []
    date_params: list[Any] = []
    if date:
        date_clauses.append("posted_date = ?")
        date_params.append(date)
    else:
        if start_date:
            date_clauses.append("posted_date >= ?")
            date_params.append(start_date)
        if end_date:
            date_clauses.append("posted_date <= ?")
            date_params.append(end_date)
    if date_clauses:
        date_expr = " AND ".join(date_clauses)
        if include_unknown_date:
            clauses.append(f"(({date_expr}) OR posted_date IS NULL OR posted_date = '')")
        else:
            clauses.append(date_expr)
        params.extend(date_params)
    if account_name:
        clauses.append("account_name = ?")
        params.append(account_name)
    if account_url:
        variants = account_url_variants(account_url)
        placeholders = ", ".join("?" for _ in variants)
        clauses.append(f"account_url IN ({placeholders})")
        params.extend(variants)
    if account_type:
        clauses.append("account_type = ?")
        params.append(account_type)
    if post_type:
        clauses.append("post_type = ?")
        params.append(post_type)
    if min_views is not None:
        clauses.append("views >= ?")
        params.append(min_views)
    if min_likes is not None:
        clauses.append("likes >= ?")
        params.append(min_likes)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(f"SELECT * FROM posts{where} ORDER BY posted_date DESC, id DESC", params).fetchall()
    return [dict(row) for row in rows]


def all_posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM posts ORDER BY id").fetchall()
    return [dict(row) for row in rows]
