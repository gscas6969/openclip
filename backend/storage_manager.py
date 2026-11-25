"""
storage_manager.py — Disk usage inspection and safe cleanup for OpenClip Studio.

Gives creators visibility and 1-click cleanup over:
  - Large downloaded/uploaded source videos (while preserving finished clips)
  - Superseded/orphaned clip exports, ZIP bundles, and temp files
  - Quick Tools (/tools) output folder
  - Failed/interrupted projects
"""

import json
import os
import shutil
from typing import Any, Dict, List, Optional

import database  # type: ignore


def _files_url_to_abs(files_url: Optional[str], tmp_dir: str) -> Optional[str]:
    if not files_url:
        return None
    rel = str(files_url).split("?")[0].lstrip("/")
    if rel.startswith("files/"):
        rel = rel[len("files/"):]
    return os.path.normpath(os.path.join(tmp_dir, rel))


def _dir_size(path: str) -> tuple[int, int]:
    total = 0
    count = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
                count += 1
            except OSError:
                pass
    return total, count


def _find_source_video_files(project_dir: str) -> List[str]:
    """Identify large top-level source video files inside a project directory."""
    if not os.path.isdir(project_dir):
        return []
    found: List[str] = []
    # Check video.json checkpoint first
    ckpt = os.path.join(project_dir, "video.json")
    if os.path.isfile(ckpt):
        try:
            data = json.load(open(ckpt, encoding="utf-8"))
            vname = data.get("file")
            if vname:
                vp = os.path.join(project_dir, vname)
                if os.path.isfile(vp):
                    found.append(os.path.normpath(vp))
        except Exception:
            pass
    for name in os.listdir(project_dir):
        fp = os.path.normpath(os.path.join(project_dir, name))
        if os.path.isfile(fp) and os.path.splitext(name)[1].lower() in (
            ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".part"
        ):
            if fp not in found:
                found.append(fp)
    return found


async def get_storage_stats(tmp_dir: str) -> Dict[str, Any]:
    projects = await database.get_all_projects()
    project_ids = {p["id"] for p in projects}

    source_bytes = 0
    source_count = 0
    clips_bytes = 0
    clips_count = 0
    orphan_bytes = 0
    orphan_count = 0
    failed_bytes = 0
    failed_count = 0
    per_project: List[Dict[str, Any]] = []

    for p in projects:
        pid = p["id"]
        p_detail = await database.get_project(pid)
        p_dir = os.path.join(tmp_dir, pid)
        if not os.path.isdir(p_dir):
            continue

        if p["status"] == "error":
            sz, cnt = _dir_size(p_dir)
            failed_bytes += sz
            failed_count += 1
            per_project.append({
                "id": pid,
                "title": p.get("title") or p.get("youtube_url") or pid[:8],
                "status": p["status"],
                "source_type": p.get("source_type", "youtube"),
                "source_bytes": sz,
                "clips_bytes": 0,
                "total_bytes": sz,
                "has_source": sz > 0,
                "clip_count": p.get("clip_count", 0),
            })
            continue

        active_clip_paths: set[str] = set()
        for c in (p_detail or {}).get("clips", []):
            for key in ("file_path", "clean_file_path"):
                ap = _files_url_to_abs(c.get(key), tmp_dir)
                if ap:
                    active_clip_paths.add(ap)

        p_source_files = set(_find_source_video_files(p_dir))
        p_src_sz = 0
        p_clip_sz = 0
        p_orphan_sz = 0

        for root, _, files in os.walk(p_dir):
            for fname in files:
                fp = os.path.normpath(os.path.join(root, fname))
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue

                if fp in p_source_files:
                    p_src_sz += sz
                    source_bytes += sz
                    source_count += 1
                elif fp in active_clip_paths:
                    p_clip_sz += sz
                    clips_bytes += sz
                    clips_count += 1
                elif fname.endswith((".zip", ".ass", ".sendcmd", ".tmp", ".mp4", ".mov", ".webm")):
                    p_orphan_sz += sz
                    orphan_bytes += sz
                    orphan_count += 1

        per_project.append({
            "id": pid,
            "title": p.get("title") or p.get("youtube_url") or pid[:8],
            "status": p["status"],
            "source_type": p.get("source_type", "youtube"),
            "source_bytes": p_src_sz,
            "clips_bytes": p_clip_sz,
            "orphan_bytes": p_orphan_sz,
            "total_bytes": p_src_sz + p_clip_sz + p_orphan_sz,
            "has_source": p_src_sz > 0,
            "clip_count": p.get("clip_count", 0),
        })

    # Orphaned folders in tmp/ that no longer exist in DB
    if os.path.isdir(tmp_dir):
        for name in os.listdir(tmp_dir):
            if name in ("assets", "tools") or name in project_ids:
                continue
            fp = os.path.join(tmp_dir, name)
            if os.path.isdir(fp):
                sz, cnt = _dir_size(fp)
                orphan_bytes += sz
                orphan_count += cnt

    tools_bytes, tools_count = _dir_size(os.path.join(tmp_dir, "tools"))
    total_bytes = source_bytes + clips_bytes + orphan_bytes + failed_bytes + tools_bytes

    per_project.sort(key=lambda x: x["total_bytes"], reverse=True)

    return {
        "total_bytes": total_bytes,
        "source_videos_bytes": source_bytes,
        "source_videos_count": source_count,
        "clips_bytes": clips_bytes,
        "clips_count": clips_count,
        "orphan_bytes": orphan_bytes,
        "orphan_count": orphan_count,
        "tools_bytes": tools_bytes,
        "tools_count": tools_count,
        "failed_projects_bytes": failed_bytes,
        "failed_projects_count": failed_count,
        "projects": per_project,
    }


async def run_cleanup(
    tmp_dir: str,
    target: str,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a targeted cleanup:
      - orphans         : delete superseded clip exports, ZIP bundles, temp files, orphaned folders
      - tools           : clear tmp/tools/
      - sources         : delete large source videos for completed ('done') projects (keeps clips!)
      - project_source  : delete source video for one specific project_id (keeps clips!)
      - failed_projects : delete all 'error' projects from DB and disk
    """
    freed = 0
    removed = 0

    if target == "tools":
        tools_dir = os.path.join(tmp_dir, "tools")
        if os.path.isdir(tools_dir):
            for fname in os.listdir(tools_dir):
                fp = os.path.join(tools_dir, fname)
                try:
                    if os.path.isfile(fp):
                        freed += os.path.getsize(fp)
                        os.remove(fp)
                        removed += 1
                except OSError:
                    pass

    elif target == "failed_projects":
        projects = await database.get_all_projects()
        for p in projects:
            if p["status"] == "error":
                p_dir = os.path.join(tmp_dir, p["id"])
                sz, cnt = _dir_size(p_dir)
                await database.delete_project(p["id"])
                if os.path.isdir(p_dir):
                    shutil.rmtree(p_dir, ignore_errors=True)
                freed += sz
                removed += max(1, cnt)

    elif target in ("sources", "project_source"):
        projects = await database.get_all_projects()
        for p in projects:
            if target == "project_source" and p["id"] != project_id:
                continue
            if target == "sources" and p["status"] != "done":
                continue
            p_dir = os.path.join(tmp_dir, p["id"])
            for sf in _find_source_video_files(p_dir):
                try:
                    freed += os.path.getsize(sf)
                    os.remove(sf)
                    removed += 1
                except OSError:
                    pass

    elif target == "orphans":
        projects = await database.get_all_projects()
        project_ids = {p["id"] for p in projects}

        for p in projects:
            if p["status"] == "error":
                continue
            p_detail = await database.get_project(p["id"])
            p_dir = os.path.join(tmp_dir, p["id"])
            if not os.path.isdir(p_dir):
                continue

            active: set[str] = set(_find_source_video_files(p_dir))
            for c in (p_detail or {}).get("clips", []):
                for key in ("file_path", "clean_file_path"):
                    ap = _files_url_to_abs(c.get(key), tmp_dir)
                    if ap:
                        active.add(ap)

            for root, _, files in os.walk(p_dir):
                for fname in files:
                    fp = os.path.normpath(os.path.join(root, fname))
                    if fp in active:
                        continue
                    if fname.endswith((".zip", ".ass", ".sendcmd", ".tmp", ".mp4", ".mov", ".webm")):
                        try:
                            freed += os.path.getsize(fp)
                            os.remove(fp)
                            removed += 1
                        except OSError:
                            pass

        if os.path.isdir(tmp_dir):
            for name in os.listdir(tmp_dir):
                if name in ("assets", "tools") or name in project_ids:
                    continue
                fp = os.path.join(tmp_dir, name)
                if os.path.isdir(fp):
                    sz, cnt = _dir_size(fp)
                    shutil.rmtree(fp, ignore_errors=True)
                    freed += sz
                    removed += max(1, cnt)
    else:
        raise ValueError(f"Unknown cleanup target: {target}")

    stats = await get_storage_stats(tmp_dir)
    return {"freed_bytes": freed, "removed_files": removed, "stats": stats}
