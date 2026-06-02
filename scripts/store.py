#!/usr/bin/env python3
"""SQLite storage for FB competitor posts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import json
from datetime import datetime, timedelta
from typing import Any

from field_audit import audit_fields_for_storage
from field_audit import is_system_audit_marker
from models import has_qualified_comment_lead_link
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
PROTECTED_FINAL_STATUSES = {"ready_for_output", "output_synced"}


def has_confirmed_time_value(post: dict[str, Any]) -> bool:
    return bool(
        post.get("posted_at")
        and parse_bool(post.get("time_confirmed"))
        and str(post.get("time_source") or "") not in ESTIMATED_TIME_SOURCES
    )


def has_qualified_lead_value(post: dict[str, Any]) -> bool:
    return has_qualified_comment_lead_link(post)


def has_engagement_value(post: dict[str, Any]) -> bool:
    return any(post.get(field) is not None for field in ("likes", "comments", "shares", "views"))


def non_empty(value: Any) -> bool:
    return value not in (None, "")


def choose_value(existing: dict[str, Any], incoming: dict[str, Any], column: str) -> Any:
    current = existing.get(column)
    new_value = incoming.get(column)
    if column in {"first_seen_at", "created_at"}:
        return current or new_value
    if column in {"last_seen_at", "crawled_at", "raw_payload", "coverage_note"}:
        return new_value if non_empty(new_value) else current
    if column in {"field_audit_status", "field_audit_reasons", "field_audit_note"}:
        return new_value if new_value is not None else current
    if column == "adoption_status":
        if current and not is_system_audit_marker(current):
            return current
        return new_value if non_empty(new_value) else current
    if column in {"posted_at", "posted_date", "time_source", "time_confirmed"}:
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
    if column in {"views", "likes", "comments", "shares", "engagement_raw"}:
        if has_engagement_value(existing) and not has_engagement_value(incoming):
            return current
        return new_value if new_value is not None else current
    if column in {"output_status", "crawl_status"}:
        if existing.get("output_status") in PROTECTED_FINAL_STATUSES and incoming.get("output_status") not in PROTECTED_FINAL_STATUSES:
            return current
        return new_value if non_empty(new_value) else current
    return new_value if non_empty(new_value) else current


def merged_post(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {column: choose_value(existing, incoming, column) for column in POST_COLUMNS}
    if not merged.get("canonical_post_url"):
        merged["canonical_post_url"] = incoming.get("canonical_post_url") or existing.get("canonical_post_url") or incoming.get("post_url")
    if existing.get("output_status") != "output_synced":
        computed_output = output_status_for(merged)
        if merged.get("output_status") != "ready_for_output" or computed_output == "ready_for_output":
            merged["output_status"] = computed_output
        merged["crawl_status"] = crawl_status_for(merged)
    merged.update(audit_fields_for_storage(merged))
    return merged


def upsert_post(conn: sqlite3.Connection, post: dict[str, Any]) -> str:
    if not post.get("post_url"):
        raise ValueError("post_url is required")
    if not post.get("canonical_post_url"):
        post["canonical_post_url"] = post["post_url"]
    post.update(audit_fields_for_storage(post))
    existing = conn.execute(
        "SELECT * FROM posts WHERE canonical_post_url = ? OR post_url = ?",
        (post["canonical_post_url"], post["post_url"]),
    ).fetchone()
    if existing:
        post = merged_post(dict(existing), post)
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


def upsert_posts(conn: sqlite3.Connection, posts: list[dict[str, Any]]) -> dict[str, Any]:
    inserted: list[dict[str, Any]] = []
    synced_candidates: list[dict[str, Any]] = []
    updated = 0
    errors = 0
    for post in posts:
        try:
            result = upsert_post(conn, post)
            stored = row_for_post(conn, post) or post
            if result == "inserted":
                inserted.append(stored)
            else:
                updated += 1
            if stored.get("output_status") != "output_synced":
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
) -> int:
    canonical = post.get("canonical_post_url") or post.get("post_url")
    post_url = post.get("post_url") or canonical
    if not canonical or not post_url:
        return 0
    wanted = list(stages or missing_enrichment_stages(post))
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
                    WHEN enrichment_tasks.status = 'done' THEN enrichment_tasks.status
                    WHEN enrichment_tasks.status = 'running' THEN enrichment_tasks.status
                    ELSE 'pending'
                END,
                next_run_at = CASE
                    WHEN enrichment_tasks.status = 'done' THEN enrichment_tasks.next_run_at
                    WHEN enrichment_tasks.status = 'running' THEN enrichment_tasks.next_run_at
                    ELSE CURRENT_TIMESTAMP
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (canonical, post_url, stage, json.dumps({"post_url": post_url}, ensure_ascii=False)),
        )
    conn.commit()
    return len([stage for stage in wanted if stage in ENRICHMENT_STAGES])


def enqueue_enrichment_tasks_for_posts(conn: sqlite3.Connection, posts: list[dict[str, Any]]) -> dict[str, Any]:
    before = conn.total_changes
    for post in posts:
        enqueue_enrichment_tasks(conn, post)
    return {"queued_or_refreshed": conn.total_changes - before}


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
    if stages:
        placeholders = ", ".join("?" for _ in stages)
        clauses.append(f"stage IN ({placeholders})")
        params.extend(stages)
    rows = conn.execute(
        f"""
        SELECT * FROM enrichment_tasks
        WHERE {' AND '.join(clauses)}
        ORDER BY
            CASE stage
                WHEN 'detail_time' THEN 1
                WHEN 'lead_link' THEN 2
                WHEN 'engagement' THEN 3
                WHEN 'post_type' THEN 4
                WHEN 'article_material' THEN 5
                WHEN 'summary' THEN 6
                ELSE 7
            END,
            attempts ASC,
            id ASC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return [dict(row) for row in rows]


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
    if stages:
        placeholders = ", ".join("?" for _ in stages)
        clauses.append(f"stage IN ({placeholders})")
        params.extend(stages)
    rows = conn.execute(
        f"""
        SELECT * FROM enrichment_tasks
        WHERE {' AND '.join(clauses)}
        ORDER BY
            CASE stage
                WHEN 'detail_time' THEN 1
                WHEN 'lead_link' THEN 2
                WHEN 'engagement' THEN 3
                WHEN 'post_type' THEN 4
                WHEN 'article_material' THEN 5
                WHEN 'summary' THEN 6
                ELSE 7
            END,
            attempts ASC,
            id ASC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return [dict(row) for row in rows]


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
        clauses.append("account_url = ?")
        params.append(account_url)
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
