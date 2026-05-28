#!/usr/bin/env python3
"""SQLite storage for FB competitor posts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


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
    "coverage_note",
    "first_seen_at",
    "last_seen_at",
    "raw_payload",
]


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


def upsert_post(conn: sqlite3.Connection, post: dict[str, Any]) -> str:
    if not post.get("post_url"):
        raise ValueError("post_url is required")
    if not post.get("canonical_post_url"):
        post["canonical_post_url"] = post["post_url"]
    existing = conn.execute(
        "SELECT id FROM posts WHERE canonical_post_url = ? OR post_url = ?",
        (post["canonical_post_url"], post["post_url"]),
    ).fetchone()
    values = [post.get(column) for column in POST_COLUMNS]
    if existing:
        assignments = ", ".join([f"{column} = ?" for column in POST_COLUMNS if column != "post_url"])
        update_values = [post.get(column) for column in POST_COLUMNS if column != "post_url"]
        update_values.extend([post["canonical_post_url"], post["post_url"]])
        conn.execute(
            f"UPDATE posts SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE canonical_post_url = ? OR post_url = ?",
            update_values,
        )
        conn.commit()
        return "updated"
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
    account_type: str = "",
    post_type: str = "",
    min_views: int | None = None,
    min_likes: int | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if date:
        clauses.append("posted_date = ?")
        params.append(date)
    if start_date:
        clauses.append("posted_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("posted_date <= ?")
        params.append(end_date)
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
