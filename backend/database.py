"""
database.py — Async SQLite database layer for OpenClip.

Uses aiosqlite to provide non-blocking database access. Manages the
connection to the local SQLite database and provides CRUD helpers for
the `projects`, `clips`, `settings`, and `brand_kits` tables.
"""

import aiosqlite  # type: ignore
import uuid
import os
import json
from typing import Optional, Any
from dotenv import load_dotenv  # type: ignore

load_dotenv()

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "openclip.db"))


async def _get_connection() -> aiosqlite.Connection:
    """Return an async SQLite connection with row-factory enabled."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    return conn


async def init_db() -> None:
    """
    Create tables if they do not exist and run idempotent column migrations.
    """
    conn = await _get_connection()
    try:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id                TEXT PRIMARY KEY,
                youtube_url       TEXT NOT NULL,
                title             TEXT,
                status            TEXT NOT NULL DEFAULT 'pending',
                error_message     TEXT,
                source_type       TEXT NOT NULL DEFAULT 'youtube',
                source_file       TEXT,
                clip_min_duration INTEGER DEFAULT 30,
                clip_max_duration INTEGER DEFAULT 60,
                custom_prompt     TEXT,
                layout_preference TEXT DEFAULT 'auto',
                brand_kit_id      TEXT,
                created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS clips (
                id                 TEXT PRIMARY KEY,
                project_id         TEXT NOT NULL,
                file_path          TEXT NOT NULL,
                clean_file_path    TEXT,
                start_time         REAL NOT NULL,
                end_time           REAL NOT NULL,
                duration           REAL NOT NULL,
                reframed           BOOLEAN NOT NULL DEFAULT 1,
                captioned          BOOLEAN NOT NULL DEFAULT 0,
                title              TEXT,
                reason             TEXT,
                viral_score        INTEGER,
                face_count         INTEGER,
                layout_mode        TEXT,
                caption_style      TEXT,
                needs_user_confirm BOOLEAN NOT NULL DEFAULT 0,
                hashtags           TEXT,
                tags               TEXT,
                edit_state         TEXT,
                created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS brand_kits (
                id                TEXT PRIMARY KEY,
                name              TEXT NOT NULL,
                logo_path         TEXT,
                caption_style     TEXT DEFAULT 'mozi_pop',
                caption_overrides TEXT,
                logo_config       TEXT,
                created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.commit()

        # --- Idempotent Migrations: clips table ---
        clip_migrations = [
            ("title", "TEXT"),
            ("reason", "TEXT"),
            ("viral_score", "INTEGER"),
            ("face_count", "INTEGER"),
            ("layout_mode", "TEXT"),
            ("caption_style", "TEXT"),
            ("needs_user_confirm", "BOOLEAN"),
            ("hashtags", "TEXT"),
            ("tags", "TEXT"),
            ("clean_file_path", "TEXT"),
            ("edit_state", "TEXT"),
            ("youtube_video_id", "TEXT"),
            ("youtube_url", "TEXT"),
            ("youtube_status", "TEXT"),
            ("youtube_scheduled_at", "TEXT"),
        ]
        for col, col_type in clip_migrations:
            try:
                await conn.execute(f"ALTER TABLE clips ADD COLUMN {col} {col_type}")
                await conn.commit()
            except Exception:
                pass

        # --- Idempotent Migrations: projects table ---
        project_migrations = [
            ("error_message", "TEXT"),
            ("source_type", "TEXT DEFAULT 'youtube'"),
            ("source_file", "TEXT"),
            ("clip_min_duration", "INTEGER DEFAULT 30"),
            ("clip_max_duration", "INTEGER DEFAULT 60"),
            ("custom_prompt", "TEXT"),
            ("layout_preference", "TEXT DEFAULT 'auto'"),
            ("brand_kit_id", "TEXT"),
        ]
        for col, col_type in project_migrations:
            try:
                await conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {col_type}")
                await conn.commit()
            except Exception:
                pass

    except Exception as exc:
        raise RuntimeError(f"Failed to initialise database: {exc}") from exc
    finally:
        await conn.close()


def _serialize_clip_row(c: aiosqlite.Row) -> dict:
    keys = c.keys()
    raw_edit = c["edit_state"] if "edit_state" in keys else None
    edit_state = None
    if raw_edit:
        try:
            edit_state = json.loads(raw_edit)
        except Exception:
            edit_state = None

    return {
        "id": c["id"],
        "project_id": c["project_id"],
        "file_path": c["file_path"],
        "clean_file_path": c["clean_file_path"] if "clean_file_path" in keys and c["clean_file_path"] else c["file_path"],
        "start_time": c["start_time"],
        "end_time": c["end_time"],
        "duration": c["duration"],
        "reframed": bool(c["reframed"]),
        "captioned": bool(c["captioned"]),
        "title": c["title"],
        "reason": c["reason"],
        "viral_score": c["viral_score"],
        "face_count": c["face_count"],
        "layout_mode": c["layout_mode"] if "layout_mode" in keys else "dynamic_zoom",
        "caption_style": c["caption_style"] if "caption_style" in keys else "none",
        "needs_user_confirm": bool(c["needs_user_confirm"]) if "needs_user_confirm" in keys else False,
        "hashtags": json.loads(c["hashtags"]) if "hashtags" in keys and c["hashtags"] else [],
        "tags": json.loads(c["tags"]) if "tags" in keys and c["tags"] else [],
        "edit_state": edit_state,
        "youtube_video_id": c["youtube_video_id"] if "youtube_video_id" in keys else None,
        "youtube_url": c["youtube_url"] if "youtube_url" in keys else None,
        "youtube_status": c["youtube_status"] if "youtube_status" in keys else None,
        "youtube_scheduled_at": c["youtube_scheduled_at"] if "youtube_scheduled_at" in keys else None,
        "created_at": c["created_at"],
    }


def _serialize_brand_kit_row(row: aiosqlite.Row) -> dict:
    def _parse_json(val: Optional[str]) -> dict:
        if not val:
            return {}
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {
        "id": row["id"],
        "name": row["name"],
        "logo_path": row["logo_path"],
        "caption_style": row["caption_style"] or "mozi_pop",
        "caption_overrides": _parse_json(row["caption_overrides"]),
        "logo_config": _parse_json(row["logo_config"]),
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

async def create_project(
    youtube_url: str,
    title: Optional[str] = None,
    source_type: str = "youtube",
    source_file: Optional[str] = None,
    clip_min_duration: int = 30,
    clip_max_duration: int = 60,
    custom_prompt: Optional[str] = None,
    layout_preference: str = "auto",
    brand_kit_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> dict:
    """Insert a new project row and return its id + initial status."""
    pid = project_id or str(uuid.uuid4())
    conn = await _get_connection()
    try:
        await conn.execute(
            """INSERT INTO projects
               (id, youtube_url, title, source_type, source_file, clip_min_duration, clip_max_duration, custom_prompt, layout_preference, brand_kit_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                youtube_url,
                title,
                source_type,
                source_file,
                clip_min_duration,
                clip_max_duration,
                custom_prompt,
                layout_preference,
                brand_kit_id,
            ),
        )
        await conn.commit()
        return {"project_id": pid, "status": "pending"}
    except Exception as exc:
        raise RuntimeError(f"Failed to create project: {exc}") from exc
    finally:
        await conn.close()


RUNNING_STATUSES = ("pending", "downloading", "transcribing", "analyzing", "processing")


async def update_project_status(
    project_id: str, status: str, error_message: Optional[str] = None
) -> None:
    conn = await _get_connection()
    try:
        await conn.execute(
            "UPDATE projects SET status = ?, error_message = ? WHERE id = ?",
            (status, error_message, project_id),
        )
        await conn.commit()
    except Exception as exc:
        raise RuntimeError(f"Failed to update project status: {exc}") from exc
    finally:
        await conn.close()


async def mark_interrupted_projects(message: str) -> int:
    conn = await _get_connection()
    try:
        placeholders = ",".join("?" for _ in RUNNING_STATUSES)
        cursor = await conn.execute(
            f"UPDATE projects SET status = 'error', error_message = ? WHERE status IN ({placeholders})",
            (message, *RUNNING_STATUSES),
        )
        await conn.commit()
        return cursor.rowcount or 0
    finally:
        await conn.close()


async def update_project_title(project_id: str, title: str) -> None:
    conn = await _get_connection()
    try:
        await conn.execute(
            "UPDATE projects SET title = ? WHERE id = ?",
            (title, project_id),
        )
        await conn.commit()
    except Exception as exc:
        raise RuntimeError(f"Failed to update project title: {exc}") from exc
    finally:
        await conn.close()


async def get_all_projects() -> list[dict]:
    conn = await _get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT p.id, p.title, p.youtube_url, p.status, p.created_at,
                   p.error_message, p.source_type, COUNT(c.id) AS clip_count
            FROM projects p
            LEFT JOIN clips c ON c.project_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "youtube_url": row["youtube_url"],
                "status": row["status"],
                "created_at": row["created_at"],
                "error_message": row["error_message"],
                "source_type": row["source_type"] if "source_type" in row.keys() and row["source_type"] else "youtube",
                "clip_count": row["clip_count"],
            }
            for row in rows
        ]
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch projects: {exc}") from exc
    finally:
        await conn.close()


async def get_project(project_id: str) -> Optional[dict]:
    conn = await _get_connection()
    try:
        cursor = await conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if not row:
            return None

        keys = row.keys()
        project = {
            "id": row["id"],
            "title": row["title"],
            "youtube_url": row["youtube_url"],
            "status": row["status"],
            "created_at": row["created_at"],
            "error_message": row["error_message"],
            "source_type": row["source_type"] if "source_type" in keys and row["source_type"] else "youtube",
            "source_file": row["source_file"] if "source_file" in keys else None,
            "clip_min_duration": row["clip_min_duration"] if "clip_min_duration" in keys and row["clip_min_duration"] else 30,
            "clip_max_duration": row["clip_max_duration"] if "clip_max_duration" in keys and row["clip_max_duration"] else 60,
            "custom_prompt": row["custom_prompt"] if "custom_prompt" in keys else None,
            "layout_preference": row["layout_preference"] if "layout_preference" in keys and row["layout_preference"] else "auto",
            "brand_kit_id": row["brand_kit_id"] if "brand_kit_id" in keys else None,
        }

        cursor = await conn.execute(
            "SELECT * FROM clips WHERE project_id = ? ORDER BY start_time",
            (project_id,),
        )
        clip_rows = await cursor.fetchall()
        project["clips"] = [_serialize_clip_row(c) for c in clip_rows]

        return project
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch project: {exc}") from exc
    finally:
        await conn.close()


async def save_clip(
    project_id: str,
    file_path: str,
    start_time: float,
    end_time: float,
    clean_file_path: Optional[str] = None,
    title: Optional[str] = None,
    reason: Optional[str] = None,
    viral_score: Optional[int] = None,
    face_count: Optional[int] = None,
    layout_mode: Optional[str] = None,
    caption_style: Optional[str] = None,
    needs_user_confirm: bool = False,
    reframed: bool = True,
    captioned: bool = False,
    hashtags: Optional[list] = None,
    tags: Optional[list] = None,
    edit_state: Optional[dict[str, Any]] = None,
) -> dict:
    clip_id = str(uuid.uuid4())
    duration = end_time - start_time
    hashtags_json = json.dumps(hashtags) if hashtags else None
    tags_json = json.dumps(tags) if tags else None
    edit_state_json = json.dumps(edit_state) if edit_state is not None else None
    clean_path = clean_file_path or file_path
    conn = await _get_connection()
    try:
        await conn.execute(
            """INSERT INTO clips
               (id, project_id, file_path, clean_file_path, start_time, end_time, duration,
                title, reason, viral_score, face_count, layout_mode, caption_style,
                needs_user_confirm, reframed, captioned, hashtags, tags, edit_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                clip_id,
                project_id,
                file_path,
                clean_path,
                start_time,
                end_time,
                duration,
                title,
                reason,
                viral_score,
                face_count,
                layout_mode,
                caption_style,
                int(needs_user_confirm),
                int(reframed),
                int(captioned),
                hashtags_json,
                tags_json,
                edit_state_json,
            ),
        )
        await conn.commit()
        return {
            "id": clip_id,
            "project_id": project_id,
            "file_path": file_path,
            "clean_file_path": clean_path,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "reframed": reframed,
            "captioned": captioned,
            "title": title,
            "reason": reason,
            "viral_score": viral_score,
            "face_count": face_count,
            "layout_mode": layout_mode,
            "caption_style": caption_style,
            "needs_user_confirm": needs_user_confirm,
            "hashtags": hashtags or [],
            "tags": tags or [],
            "edit_state": edit_state,
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to save clip: {exc}") from exc
    finally:
        await conn.close()


async def update_clip(clip_id: str, **fields: Any) -> Optional[dict]:
    """Update allowed fields on a clip record and return the updated clip."""
    allowed = {
        "file_path",
        "clean_file_path",
        "start_time",
        "end_time",
        "duration",
        "reframed",
        "captioned",
        "title",
        "reason",
        "viral_score",
        "face_count",
        "layout_mode",
        "caption_style",
        "needs_user_confirm",
        "hashtags",
        "tags",
        "edit_state",
        "youtube_video_id",
        "youtube_url",
        "youtube_status",
        "youtube_scheduled_at",
    }
    updates = {}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("hashtags", "tags", "edit_state") and v is not None and not isinstance(v, str):
            updates[k] = json.dumps(v)
        elif k in ("reframed", "captioned", "needs_user_confirm"):
            updates[k] = int(bool(v))
        else:
            updates[k] = v

    if not updates:
        return await get_clip(clip_id)

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [clip_id]

    conn = await _get_connection()
    try:
        await conn.execute(f"UPDATE clips SET {set_clause} WHERE id = ?", values)
        await conn.commit()
    except Exception as exc:
        raise RuntimeError(f"Failed to update clip: {exc}") from exc
    finally:
        await conn.close()

    return await get_clip(clip_id)


async def delete_project(project_id: str) -> None:
    conn = await _get_connection()
    try:
        await conn.execute("DELETE FROM clips WHERE project_id = ?", (project_id,))
        await conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await conn.commit()
    except Exception as exc:
        raise RuntimeError(f"Failed to delete project: {exc}") from exc
    finally:
        await conn.close()


async def get_clip(clip_id: str) -> Optional[dict]:
    """Fetch a single clip by its ID (deserialised)."""
    conn = await _get_connection()
    try:
        cursor = await conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
        row = await cursor.fetchone()
        return _serialize_clip_row(row) if row else None
    except Exception as exc:
        raise RuntimeError(f"Failed to get clip: {exc}") from exc
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Brand Kits CRUD
# ---------------------------------------------------------------------------

async def get_brand_kits() -> list[dict]:
    conn = await _get_connection()
    try:
        cursor = await conn.execute("SELECT * FROM brand_kits ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [_serialize_brand_kit_row(r) for r in rows]
    finally:
        await conn.close()


async def get_brand_kit(kit_id: str) -> Optional[dict]:
    conn = await _get_connection()
    try:
        cursor = await conn.execute("SELECT * FROM brand_kits WHERE id = ?", (kit_id,))
        row = await cursor.fetchone()
        return _serialize_brand_kit_row(row) if row else None
    finally:
        await conn.close()


async def create_brand_kit(
    name: str,
    logo_path: Optional[str] = None,
    caption_style: str = "mozi_pop",
    caption_overrides: Optional[dict] = None,
    logo_config: Optional[dict] = None,
) -> dict:
    kit_id = str(uuid.uuid4())
    conn = await _get_connection()
    try:
        await conn.execute(
            """INSERT INTO brand_kits (id, name, logo_path, caption_style, caption_overrides, logo_config)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                kit_id,
                name,
                logo_path,
                caption_style,
                json.dumps(caption_overrides or {}),
                json.dumps(logo_config or {}),
            ),
        )
        await conn.commit()
    finally:
        await conn.close()
    kit = await get_brand_kit(kit_id)
    assert kit is not None
    return kit


async def update_brand_kit(kit_id: str, **fields: Any) -> Optional[dict]:
    allowed = {"name", "logo_path", "caption_style", "caption_overrides", "logo_config"}
    updates = {}
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k in ("caption_overrides", "logo_config") and not isinstance(v, str):
            updates[k] = json.dumps(v)
        else:
            updates[k] = v

    if not updates:
        return await get_brand_kit(kit_id)

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [kit_id]
    conn = await _get_connection()
    try:
        await conn.execute(f"UPDATE brand_kits SET {set_clause} WHERE id = ?", values)
        await conn.commit()
    finally:
        await conn.close()
    return await get_brand_kit(kit_id)


async def delete_brand_kit(kit_id: str) -> None:
    conn = await _get_connection()
    try:
        await conn.execute("DELETE FROM brand_kits WHERE id = ?", (kit_id,))
        await conn.commit()
    finally:
        await conn.close()
