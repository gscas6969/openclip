"""
api.py — FastAPI application for OpenClip (Studio Edition).

Wires together database, downloader, clipper, transcriber, LLM, multi-layout
reframer, kinetic captioner, non-destructive editor render compiler, Brand Kits,
and WebSocket progress streaming.
"""

import os
import re
import sys
import json
import uuid
import time
import asyncio
import shutil
import logging
from typing import Optional, Any

from fastapi import (  # type: ignore
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Request,
    BackgroundTasks,
    UploadFile,
    File,
    Form,
)
from fastapi.responses import FileResponse, RedirectResponse  # type: ignore
from fastapi.staticfiles import StaticFiles  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from pydantic import BaseModel  # type: ignore
from dotenv import load_dotenv  # type: ignore

load_dotenv()

import ffbin  # type: ignore

_ffmpeg_dirs = [ffbin.LOCAL_FFMPEG_DIR]
if ffbin.find_ffmpeg():
    _ffmpeg_dirs.append(ffbin.ffmpeg_dir())
os.environ["PATH"] = os.pathsep.join(_ffmpeg_dirs + [os.environ.get("PATH", "")])

import database  # type: ignore
import downloader  # type: ignore
import clipper  # type: ignore
import transcriber  # type: ignore
import highlight_signals  # type: ignore
import llm  # type: ignore
import reframer  # type: ignore
import captioner  # type: ignore
import editor_engine  # type: ignore
import batch_queue  # type: ignore
import quick_tools  # type: ignore
import mcp_server  # type: ignore
import discover  # type: ignore
import youtube_uploader  # type: ignore
import storage_manager  # type: ignore
import settings as settings_mod  # type: ignore
import system_setup  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="OpenClip API",
    description="Local-first AI video clipping & multi-track studio engine",
    version="2.0.0",
)

_allowed_origins = [
    "http://localhost:3000",
    "http://localhost:5000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
]
_replit_domain = os.environ.get("REPLIT_DEV_DOMAIN")
if _replit_domain:
    _allowed_origins.append(f"https://{_replit_domain}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=r"https://.*\.replit\.dev",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TMP_DIR = os.path.abspath(os.getenv("TMP_DIR", os.path.join(os.path.dirname(__file__), "..", "tmp")))
os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(os.path.join(TMP_DIR, "assets", "logos"), exist_ok=True)

app.mount("/files", StaticFiles(directory=TMP_DIR), name="files")

# ---------------------------------------------------------------------------
# WebSocket connection registry
# ---------------------------------------------------------------------------

_ws_connections: dict[str, set[WebSocket]] = {}
_last_progress: dict[str, dict] = {}
_active_pipelines: set[str] = set()


async def _broadcast(project_id: str, stage: str, percent: float, message: str) -> None:
    payload = {"stage": stage, "percent": percent, "message": message}
    _last_progress[project_id] = payload
    dead: list[WebSocket] = []
    for ws in _ws_connections.get(project_id, set()):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_connections.get(project_id, set()).discard(ws)


@app.on_event("startup")
async def on_startup() -> None:
    await database.init_db()
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(os.path.join(TMP_DIR, "assets", "logos"), exist_ok=True)
    os.makedirs(os.path.join(TMP_DIR, "tools"), exist_ok=True)
    batch_queue.manager.configure(_run_pipeline)
    n = await database.mark_interrupted_projects(INTERRUPTED_MESSAGE)
    if n:
        logger.warning("Marked %d interrupted project(s) as failed", n)
    saved_encoder = await settings_mod.get_setting("video_encoder")
    ffbin.set_preferred_encoder(saved_encoder or "auto")
    asyncio.create_task(asyncio.to_thread(ffbin.detect_video_encoders))
    logger.info("Database initialised, tmp dir ready at %s", TMP_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INTERRUPTED_MESSAGE = (
    "Interrupted: the backend stopped while this project was running. "
    "Click Retry to continue where it left off."
)


class PipelineError(Exception):
    """A pipeline failure with a user-facing message."""


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _files_url_to_disk(files_url: str) -> str:
    rel = str(files_url or "").split("?")[0].lstrip("/")
    if rel.startswith("files/"):
        rel = rel[len("files/"):]
    return os.path.join(TMP_DIR, rel)


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    youtube_url: str
    clip_min_duration: int = 30
    clip_max_duration: int = 60
    custom_prompt: Optional[str] = None
    layout_preference: str = "auto"
    brand_kit_id: Optional[str] = None


class CreateProjectResponse(BaseModel):
    project_id: str
    status: str


class ProjectListItem(BaseModel):
    id: str
    title: Optional[str] = None
    youtube_url: str
    status: str
    source_type: str = "youtube"
    created_at: str
    error_message: Optional[str] = None
    clip_count: int


class ClipResponse(BaseModel):
    id: str
    project_id: str
    file_path: str
    clean_file_path: Optional[str] = None
    start_time: float
    end_time: float
    duration: float
    reframed: bool
    captioned: bool
    title: Optional[str] = None
    reason: Optional[str] = None
    viral_score: Optional[int] = None
    face_count: Optional[int] = None
    layout_mode: Optional[str] = None
    caption_style: Optional[str] = None
    needs_user_confirm: bool = False
    hashtags: list[str] = []
    tags: list[str] = []
    edit_state: Optional[dict[str, Any]] = None
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    youtube_status: Optional[str] = None
    youtube_scheduled_at: Optional[str] = None
    created_at: str


class ProjectDetailResponse(BaseModel):
    id: str
    title: Optional[str] = None
    youtube_url: str
    status: str
    source_type: str = "youtube"
    clip_min_duration: int = 30
    clip_max_duration: int = 60
    custom_prompt: Optional[str] = None
    layout_preference: str = "auto"
    brand_kit_id: Optional[str] = None
    created_at: str
    error_message: Optional[str] = None
    clips: list[ClipResponse] = []


class SaveEditStateRequest(BaseModel):
    edit_state: dict[str, Any]
    title: Optional[str] = None


class RenderClipRequest(BaseModel):
    edit_state: Optional[dict[str, Any]] = None
    title: Optional[str] = None


class RelayoutClipRequest(BaseModel):
    layout_mode: str


class RelayoutProjectRequest(BaseModel):
    layout_mode: str


class BrandKitRequest(BaseModel):
    name: str
    logo_path: Optional[str] = None
    caption_style: str = "mozi_pop"
    caption_overrides: dict[str, Any] = {}
    logo_config: dict[str, Any] = {}


class CreateBatchRequest(BaseModel):
    urls: list[str]
    clip_min_duration: int = 30
    clip_max_duration: int = 60
    custom_prompt: Optional[str] = None
    layout_preference: str = "auto"
    brand_kit_id: Optional[str] = None


class AiScoutRequest(BaseModel):
    videos: list[dict[str, Any]]
    goal: Optional[str] = None


class YouTubeClientSecretRequest(BaseModel):
    client_secret_json: str


class PublishYouTubeRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    privacy_status: str = "private"
    publish_at: Optional[str] = None
    category_id: str = "22"
    made_for_kids: bool = False


class ScheduleProjectYouTubeRequest(BaseModel):
    start_at: str  # ISO-8601 UTC timestamp, e.g. 2026-11-26T18:00:00Z
    interval_hours: int = 24
    description_footer: str = ""
    skip_already_posted: bool = True
    max_uploads: int = 6


class StorageCleanupRequest(BaseModel):
    target: str  # orphans | tools | sources | project_source | failed_projects
    project_id: Optional[str] = None


class UpdateSettingsRequest(BaseModel):
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    whisper_model: Optional[str] = None
    caption_style: Optional[str] = None
    video_encoder: Optional[str] = None


# ---------------------------------------------------------------------------
# Background Processing Pipeline
# ---------------------------------------------------------------------------

async def _run_pipeline(project_id: str, youtube_url: str) -> None:
    if project_id in _active_pipelines:
        return
    _active_pipelines.add(project_id)

    project_dir = os.path.join(TMP_DIR, project_id)
    clips_dir = os.path.join(project_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    video_ckpt = os.path.join(project_dir, "video.json")
    transcript_ckpt = os.path.join(project_dir, "transcript.json")
    signals_ckpt = os.path.join(project_dir, "signals.json")
    suggestions_ckpt = os.path.join(project_dir, "suggestions.json")

    async def fail(message: str) -> None:
        await database.update_project_status(project_id, "error", message)
        await _broadcast(project_id, "error", 0, message)

    try:
        project = await database.get_project(project_id)
        if project is None:
            return

        loop = asyncio.get_running_loop()

        # ---- STEP 1: Download or use uploaded local file ----
        video = _read_json(video_ckpt)
        if video and os.path.isfile(os.path.join(project_dir, video.get("file", ""))):
            await _broadcast(project_id, "downloading", 25, "Source video ready")
        elif project.get("source_type") == "upload" and project.get("source_file"):
            src_abs = os.path.join(project_dir, project["source_file"])
            if not os.path.isfile(src_abs):
                raise PipelineError("Uploaded source video file is missing on disk.")
            dur = await asyncio.to_thread(clipper.get_video_duration, src_abs)
            video = {
                "file": project["source_file"],
                "title": project.get("title") or os.path.splitext(project["source_file"])[0],
                "duration": dur,
            }
            _write_json(video_ckpt, video)
            await _broadcast(project_id, "downloading", 25, "Local upload verified")
        else:
            await database.update_project_status(project_id, "downloading")
            await _broadcast(project_id, "downloading", 5, "Starting download…")

            def download_progress(percent: float, msg: str):
                asyncio.run_coroutine_threadsafe(
                    _broadcast(project_id, "downloading", min(percent * 0.2 + 5, 25), msg),
                    loop,
                )

            try:
                result = await asyncio.to_thread(
                    downloader.download_video, youtube_url, project_dir, download_progress,
                )
            except Exception as exc:
                logger.exception("Download failed for %s", project_id)
                raise PipelineError(f"Download failed: {exc}") from exc
            video = {
                "file": os.path.basename(result["file_path"]),
                "title": result["title"],
                "duration": result["duration_seconds"],
            }
            _write_json(video_ckpt, video)
            await database.update_project_title(project_id, video["title"])
            await _broadcast(project_id, "downloading", 25, "Download complete")

        file_path = os.path.join(project_dir, video["file"])
        video_duration = float(video["duration"])

        provider = await settings_mod.get_setting("llm_provider") or "codex"
        api_key = await settings_mod.get_setting("llm_api_key") or ""
        model = await settings_mod.get_setting("llm_model") or ""

        # ---- STEP 2: Extract Transcript ----
        transcript_segments = _read_json(transcript_ckpt)
        if transcript_segments:
            await _broadcast(project_id, "transcribing", 50, "Transcript already extracted, skipping")
        else:
            await database.update_project_status(project_id, "transcribing")
            await _broadcast(project_id, "transcribing", 30, "Extracting transcript…")

            def transcript_progress(percent: float, msg: str):
                scaled = 30 + (percent / 100) * 20
                asyncio.run_coroutine_threadsafe(
                    _broadcast(project_id, "transcribing", scaled, msg),
                    loop,
                )

            gemini_key = api_key if provider == "gemini" else os.environ.get("GEMINI_API_KEY", "")
            transcript_segments = await transcriber.extract_captions(
                youtube_url,
                file_path,
                progress_callback=transcript_progress,
                gemini_api_key=gemini_key,
                whisper_model=await settings_mod.get_setting("whisper_model") or "base",
            )
            if not transcript_segments:
                raise PipelineError(
                    "Could not get a transcript: no captions found and speech-to-text failed. "
                    "Add a Gemini API key or ensure Whisper is installed in Settings."
                )
            _write_json(transcript_ckpt, transcript_segments)
            await _broadcast(project_id, "transcribing", 50, "Transcript ready")

        # ---- STEP 3: Highlight Signals + LLM Clip Suggestions ----
        suggestions = _read_json(suggestions_ckpt)
        if suggestions:
            await _broadcast(project_id, "analyzing", 75, f"Using the {len(suggestions)} clip moments found earlier")
        else:
            await database.update_project_status(project_id, "analyzing")

            signals = _read_json(signals_ckpt)
            if signals is None:
                def signal_progress(percent: float, msg: str):
                    asyncio.run_coroutine_threadsafe(
                        _broadcast(project_id, "analyzing", percent, msg),
                        loop,
                    )

                try:
                    signals = await highlight_signals.extract_highlight_signals(
                        youtube_url, file_path, video_duration, progress_callback=signal_progress,
                    )
                    _write_json(signals_ckpt, signals)
                    hotspots_n = len(signals.get("hotspots") or [])
                    chat_n = len(signals.get("chat_spikes") or [])
                    if hotspots_n:
                        extra = f" (including {chat_n} live chat spikes)" if chat_n else ""
                        await _broadcast(
                            project_id, "analyzing", 54,
                            f"Found {hotspots_n} audience/audio highlight signals{extra}",
                        )
                except Exception as sig_exc:
                    logger.warning("Highlight signal extraction skipped for %s: %s", project_id, sig_exc)
                    signals = {}

            await _broadcast(project_id, "analyzing", 55, "AI analyzing video…")

            def analyze_progress(stage: str, percent: float, msg: str):
                asyncio.run_coroutine_threadsafe(
                    _broadcast(project_id, stage, percent, msg),
                    loop,
                )

            try:
                suggestions = await llm.get_clip_suggestions(
                    transcript_segments,
                    provider,
                    api_key,
                    model,
                    video_duration,
                    progress_callback=analyze_progress,
                    min_duration=int(project.get("clip_min_duration") or 30),
                    max_duration=int(project.get("clip_max_duration") or 60),
                    custom_prompt=project.get("custom_prompt"),
                    video_path=file_path,
                    highlight_signals=signals,
                )
            except Exception as e:
                logger.exception("LLM step failed for %s", project_id)
                raise PipelineError(f"AI step failed: {llm.friendly_error(e)}") from e

            if not suggestions:
                raise PipelineError("AI could not find clip moments. Try a different video or another model.")
            _write_json(suggestions_ckpt, suggestions)
            await _broadcast(project_id, "analyzing", 75, f"Found {len(suggestions)} clip suggestions")

        # ---- STEP 4: Non-destructive Reframe + Initial Render ----
        await database.update_project_status(project_id, "processing")
        caption_style = await settings_mod.get_setting("caption_style") or "mozi_pop"
        brand_kit = None
        if project.get("brand_kit_id"):
            brand_kit = await database.get_brand_kit(project["brand_kit_id"])
            if brand_kit and brand_kit.get("caption_style"):
                caption_style = brand_kit["caption_style"]

        layout_pref = project.get("layout_preference") or "auto"
        done_starts = {round(c["start_time"], 2) for c in (await database.get_project(project_id) or {}).get("clips", [])}
        failed: list[str] = []
        total = len(suggestions)

        for i, suggestion in enumerate(suggestions):
            label = f"clip {i+1} of {total}"
            if round(suggestion["start"], 2) in done_starts:
                continue

            await _broadcast(project_id, "processing", 75 + (i / total * 18), f"Reframing {label}…")
            try:
                clip = await reframer.process_clip(
                    file_path,
                    suggestion,
                    clips_dir,
                    project_id,
                    forced_layout_mode=layout_pref,
                )
            except Exception as exc:
                logger.exception("Clip %d failed for %s", i + 1, project_id)
                failed.append(f"{i+1}")
                await _broadcast(
                    project_id, "processing", 75 + ((i + 1) / total * 18),
                    f"Warning: {label} failed and was skipped ({str(exc)[:120]})",
                )
                continue

            clean_abs = clip["clean_file_path"]
            clean_filename = os.path.basename(clean_abs)
            export_filename = clean_filename.replace("_clean.mp4", "_export.mp4")
            if export_filename == clean_filename:
                export_filename = f"export_{clean_filename}"
            export_abs = os.path.join(clips_dir, export_filename)

            edit_state = captioner.build_initial_edit_state(
                transcript_segments,
                float(suggestion["start"]),
                float(suggestion["end"]),
                layout_mode=clip.get("layout_mode", "dynamic_zoom"),
                caption_style=caption_style,
                brand_kit=brand_kit,
            )

            captioned = False
            final_filename = clean_filename
            if edit_state.get("caption_enabled") or (edit_state.get("logo") or {}).get("enabled"):
                await _broadcast(project_id, "processing", 90 + (i / total * 8), f"Rendering captions for {label}…")
                try:
                    render_res = await editor_engine.render_clip_from_edit_state(
                        clean_abs, export_abs, float(clip["duration"]), edit_state, TMP_DIR,
                    )
                    captioned = bool(render_res.get("captioned", False))
                    final_filename = export_filename
                except Exception as exc:
                    logger.exception("Initial render failed for clip %d of %s", i + 1, project_id)
                    await _broadcast(
                        project_id, "processing", 90 + (i / total * 8),
                        f"Warning: captions failed for {label}, keeping clean clip",
                    )

            await database.save_clip(
                project_id=project_id,
                file_path=f"/files/{project_id}/clips/{final_filename}",
                clean_file_path=f"/files/{project_id}/clips/{clean_filename}",
                start_time=clip["start_time"],
                end_time=clip["end_time"],
                title=clip.get("title"),
                reason=clip.get("reason"),
                viral_score=clip.get("viral_score"),
                face_count=clip.get("face_count"),
                layout_mode=clip.get("layout_mode"),
                caption_style=edit_state.get("caption_style") if edit_state.get("caption_enabled") else "none",
                needs_user_confirm=clip.get("needs_user_confirm", False),
                reframed=clip.get("reframed", True),
                captioned=captioned,
                hashtags=suggestion.get("hashtags", []),
                tags=suggestion.get("tags", []),
                edit_state=edit_state,
            )
            done_starts.add(round(suggestion["start"], 2))

        made = sum(1 for sgg in suggestions if round(sgg["start"], 2) in done_starts)
        if made == 0:
            raise PipelineError(f"None of the {total} clips could be made. Check the backend log, then click Retry.")

        note = None
        if failed:
            note = f"{made} of {total} clips were made; clip {', '.join(failed)} failed. Click Retry to try those again."
        await database.update_project_status(project_id, "done", note)
        await _broadcast(project_id, "done", 100, note or "Complete")

    except PipelineError as exc:
        await fail(str(exc))
    except Exception as exc:
        logger.exception("Pipeline failed for project %s", project_id)
        await fail(f"Pipeline error: {exc}")
    finally:
        _active_pipelines.discard(project_id)


# ---------------------------------------------------------------------------
# REST Routes — Projects & Uploads
# ---------------------------------------------------------------------------

@app.post("/api/projects", response_model=CreateProjectResponse, status_code=201)
async def create_project(body: CreateProjectRequest, background_tasks: BackgroundTasks):
    if not re.match(r"^(https?\:\/\/)?(www\.|m\.)?(youtube\.com|youtu\.be)\/.+$", body.youtube_url.strip()):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    result = await database.create_project(
        youtube_url=body.youtube_url.strip(),
        source_type="youtube",
        clip_min_duration=body.clip_min_duration,
        clip_max_duration=body.clip_max_duration,
        custom_prompt=body.custom_prompt,
        layout_preference=body.layout_preference,
        brand_kit_id=body.brand_kit_id,
    )
    project_id = result["project_id"]
    background_tasks.add_task(_run_pipeline, project_id, body.youtube_url.strip())
    return {"project_id": project_id, "status": "pending"}


@app.post("/api/projects/upload", response_model=CreateProjectResponse, status_code=201)
async def upload_project_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    clip_min_duration: int = Form(30),
    clip_max_duration: int = Form(60),
    custom_prompt: Optional[str] = Form(None),
    layout_preference: str = Form("auto"),
    brand_kit_id: Optional[str] = Form(None),
):
    """Upload a local video (MP4/MOV/MKV/WEBM) and run the clipping pipeline."""
    orig_name = file.filename or "upload.mp4"
    ext = os.path.splitext(orig_name)[1].lower()
    if ext not in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
        raise HTTPException(status_code=400, detail="Unsupported video format. Use MP4, MOV, MKV, or WEBM.")

    project_id = str(uuid.uuid4())
    project_dir = os.path.join(TMP_DIR, project_id)
    os.makedirs(project_dir, exist_ok=True)
    saved_name = f"source{ext}"
    dest_path = os.path.join(project_dir, saved_name)

    with open(dest_path, "wb") as out_f:
        while chunk := await file.read(1024 * 1024):
            out_f.write(chunk)

    clean_title = (title or "").strip() or os.path.splitext(orig_name)[0]
    pseudo_url = f"upload://{orig_name}"

    await database.create_project(
        project_id=project_id,
        youtube_url=pseudo_url,
        title=clean_title,
        source_type="upload",
        source_file=saved_name,
        clip_min_duration=clip_min_duration,
        clip_max_duration=clip_max_duration,
        custom_prompt=custom_prompt or None,
        layout_preference=layout_preference or "auto",
        brand_kit_id=brand_kit_id or None,
    )
    background_tasks.add_task(_run_pipeline, project_id, pseudo_url)
    return {"project_id": project_id, "status": "pending"}


@app.get("/api/projects", response_model=list[ProjectListItem])
async def list_projects():
    return await database.get_all_projects()


@app.get("/api/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: str):
    project = await database.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    project = await database.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await database.delete_project(project_id)
    _last_progress.pop(project_id, None)
    project_dir = os.path.join(TMP_DIR, project_id)
    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir, ignore_errors=True)
    return {"success": True}


@app.post("/api/projects/{project_id}/retry", status_code=202)
async def retry_project(project_id: str, background_tasks: BackgroundTasks):
    project = await database.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project_id in _active_pipelines:
        raise HTTPException(status_code=409, detail="This project is already running")
    await database.update_project_status(project_id, "pending")
    _last_progress.pop(project_id, None)
    background_tasks.add_task(_run_pipeline, project_id, project["youtube_url"])
    return {"project_id": project_id, "status": "pending"}


@app.get("/api/projects/{project_id}/export-all")
async def export_all_clips(project_id: str):
    project = await database.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    clips = project.get("clips") or []
    if not clips:
        raise HTTPException(status_code=400, detail="No clips available to export")
    zip_path = await asyncio.to_thread(
        editor_engine.build_project_zip, project_id, project.get("title") or "openclip", clips, TMP_DIR
    )
    return FileResponse(zip_path, media_type="application/zip", filename=os.path.basename(zip_path))


# ---------------------------------------------------------------------------
# REST Routes — Non-Destructive Clip Editor
# ---------------------------------------------------------------------------

async def _ensure_clip_edit_state(clip: dict) -> dict:
    """Hydrate edit_state on demand for legacy clips that predate the Studio Editor."""
    if clip.get("edit_state"):
        return clip
    project_dir = os.path.join(TMP_DIR, clip["project_id"])
    transcript = _read_json(os.path.join(project_dir, "transcript.json")) or []
    state = captioner.build_initial_edit_state(
        transcript,
        float(clip["start_time"]),
        float(clip["end_time"]),
        layout_mode=clip.get("layout_mode") or "dynamic_zoom",
        caption_style=clip.get("caption_style") or "mozi_pop",
    )
    updated = await database.update_clip(clip["id"], edit_state=state)
    return updated or {**clip, "edit_state": state}


@app.get("/api/clips/{clip_id}", response_model=ClipResponse)
async def get_clip_detail(clip_id: str):
    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    return await _ensure_clip_edit_state(clip)


@app.put("/api/clips/{clip_id}/edit-state", response_model=ClipResponse)
async def save_clip_edit_state(clip_id: str, body: SaveEditStateRequest):
    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    updates: dict[str, Any] = {"edit_state": body.edit_state}
    if body.title is not None:
        updates["title"] = body.title
    updated = await database.update_clip(clip_id, **updates)
    return updated


@app.post("/api/clips/{clip_id}/render", response_model=ClipResponse)
async def render_clip(clip_id: str, body: RenderClipRequest):
    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = await _ensure_clip_edit_state(clip)

    edit_state = body.edit_state if body.edit_state is not None else clip["edit_state"]
    clean_url = clip.get("clean_file_path") or clip["file_path"]
    clean_abs = _files_url_to_disk(clean_url)
    if not os.path.isfile(clean_abs):
        raise HTTPException(status_code=404, detail="Base clip video file not found on disk")

    base_dur = await asyncio.to_thread(clipper.get_video_duration, clean_abs)
    clips_dir = os.path.join(TMP_DIR, clip["project_id"], "clips")
    out_name = f"clip_{clip_id[:8]}_export.mp4"
    out_abs = os.path.join(clips_dir, out_name)

    try:
        res = await editor_engine.render_clip_from_edit_state(
            clean_abs, out_abs, base_dur, edit_state, TMP_DIR
        )
    except Exception as exc:
        logger.exception("Render failed for clip %s", clip_id)
        raise HTTPException(status_code=500, detail=str(exc))

    ver = int(time.time())
    old_file_abs = _files_url_to_disk(clip["file_path"])
    clean_file_abs_norm = os.path.normpath(clean_abs)
    out_abs_norm = os.path.normpath(out_abs)
    # Auto-clean superseded old export file if filename changed
    if (
        old_file_abs
        and os.path.isfile(old_file_abs)
        and os.path.normpath(old_file_abs) not in (clean_file_abs_norm, out_abs_norm)
    ):
        try:
            os.remove(old_file_abs)
        except OSError:
            pass

    updates: dict[str, Any] = {
        "file_path": f"/files/{clip['project_id']}/clips/{out_name}?v={ver}",
        "duration": res["duration"],
        "captioned": res["captioned"],
        "caption_style": edit_state.get("caption_style") if edit_state.get("caption_enabled") else "none",
        "edit_state": edit_state,
    }
    if body.title is not None:
        updates["title"] = body.title

    updated = await database.update_clip(clip_id, **updates)
    return updated


async def _ensure_project_source_video(project_id: str) -> str:
    """
    Locate the original 16:9 source video for a project. If it was cleaned up
    from disk and this is a YouTube project, automatically re-download it so
    on-demand re-layout always works seamlessly.
    """
    project_dir = os.path.join(TMP_DIR, project_id)
    os.makedirs(project_dir, exist_ok=True)
    video_ckpt = os.path.join(project_dir, "video.json")
    ckpt = _read_json(video_ckpt) or {}
    if ckpt.get("file"):
        candidate = os.path.join(project_dir, ckpt["file"])
        if os.path.isfile(candidate):
            return candidate

    project = await database.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.get("source_type") == "upload":
        if project.get("source_file"):
            cand = os.path.join(project_dir, project["source_file"])
            if os.path.isfile(cand):
                return cand
        raise HTTPException(
            status_code=404,
            detail="Uploaded local source video was deleted during cleanup and cannot be re-downloaded.",
        )

    yt_url = (project.get("youtube_url") or "").strip()
    if not yt_url or yt_url.startswith("upload://"):
        raise HTTPException(status_code=404, detail="No YouTube URL available to re-download source video.")

    logger.info("Source video missing for %s — auto re-downloading from %s", project_id, yt_url)
    try:
        result = await asyncio.to_thread(downloader.download_video, yt_url, project_dir, None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Auto re-download failed: {exc}")

    video = {
        "file": os.path.basename(result["file_path"]),
        "title": result["title"],
        "duration": result["duration_seconds"],
    }
    _write_json(video_ckpt, video)
    return os.path.join(project_dir, video["file"])


async def _relayout_single_clip_with_source(clip: dict, src_video: str, layout_mode: str) -> dict:
    clip = await _ensure_clip_edit_state(clip)
    project_dir = os.path.join(TMP_DIR, clip["project_id"])
    clips_dir = os.path.join(project_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    ver = int(time.time())
    clean_name = f"clip_{clip['id'][:8]}_{layout_mode}_{ver}_clean.mp4"
    suggestion = {
        "start": clip["start_time"],
        "end": clip["end_time"],
        "title": clip.get("title") or "",
        "reason": clip.get("reason") or "",
        "viral_score": clip.get("viral_score") or 0,
    }

    reframed = await reframer.process_clip(
        src_video,
        suggestion,
        clips_dir,
        clip["project_id"],
        forced_layout_mode=layout_mode,
        output_filename=clean_name,
    )

    # Remove previous superseded clean file so disk doesn't bloat
    old_clean = _files_url_to_disk(clip.get("clean_file_path") or "")
    new_clean_abs = os.path.normpath(reframed["clean_file_path"])
    if old_clean and os.path.isfile(old_clean) and os.path.normpath(old_clean) != new_clean_abs:
        try:
            os.remove(old_clean)
        except OSError:
            pass

    edit_state = dict(clip["edit_state"] or {})
    edit_state["layout_mode"] = reframed["layout_mode"]
    clean_url = f"/files/{clip['project_id']}/clips/{clean_name}?v={ver}"

    out_name = f"clip_{clip['id'][:8]}_export.mp4"
    out_abs = os.path.join(clips_dir, out_name)
    res = await editor_engine.render_clip_from_edit_state(
        reframed["clean_file_path"], out_abs, float(reframed["duration"]), edit_state, TMP_DIR
    )

    old_export = _files_url_to_disk(clip.get("file_path") or "")
    if (
        old_export
        and os.path.isfile(old_export)
        and os.path.normpath(old_export) not in (new_clean_abs, os.path.normpath(out_abs))
    ):
        try:
            os.remove(old_export)
        except OSError:
            pass

    updated = await database.update_clip(
        clip["id"],
        clean_file_path=clean_url,
        file_path=f"/files/{clip['project_id']}/clips/{out_name}?v={ver}",
        layout_mode=reframed["layout_mode"],
        face_count=reframed["face_count"],
        duration=res["duration"],
        captioned=res["captioned"],
        edit_state=edit_state,
    )
    return updated or clip


@app.post("/api/clips/{clip_id}/layout", response_model=ClipResponse)
async def relayout_clip(clip_id: str, body: RelayoutClipRequest):
    if body.layout_mode not in reframer.LAYOUT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid layout_mode: {body.layout_mode}")

    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    src_video = await _ensure_project_source_video(clip["project_id"])
    try:
        return await _relayout_single_clip_with_source(clip, src_video, body.layout_mode)
    except Exception as exc:
        logger.exception("Re-layout failed for clip %s", clip_id)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/projects/{project_id}/relayout", response_model=ProjectDetailResponse)
async def relayout_all_project_clips(project_id: str, body: RelayoutProjectRequest):
    """Re-reframe every clip in a project using the requested layout mode (auto re-downloading source if needed)."""
    if body.layout_mode not in reframer.LAYOUT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid layout_mode: {body.layout_mode}")

    project = await database.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    clips = project.get("clips") or []
    if not clips:
        raise HTTPException(status_code=400, detail="Project has no clips to re-reframe")

    src_video = await _ensure_project_source_video(project_id)
    for clip in clips:
        try:
            await _relayout_single_clip_with_source(clip, src_video, body.layout_mode)
        except Exception as exc:
            logger.exception("Project relayout failed on clip %s: %s", clip["id"], exc)

    refreshed = await database.get_project(project_id)
    return refreshed


@app.get("/api/clips/{clip_id}/waveform")
async def get_clip_waveform(clip_id: str, bars: int = 200):
    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clean_abs = _files_url_to_disk(clip.get("clean_file_path") or clip["file_path"])
    bars = max(40, min(400, bars))
    amplitudes = await editor_engine.extract_waveform(clean_abs, num_bars=bars)
    return {"bars": amplitudes}


@app.post("/api/clips/{clip_id}/auto-emojis")
async def auto_inject_emojis(clip_id: str, body: Optional[SaveEditStateRequest] = None):
    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = await _ensure_clip_edit_state(clip)
    state = body.edit_state if body and body.edit_state else clip["edit_state"]
    updated_blocks = captioner.inject_emojis(state.get("caption_blocks", []))
    return {"caption_blocks": updated_blocks}


@app.post("/api/clips/{clip_id}/detect-fillers")
async def detect_clip_fillers_and_silences(clip_id: str):
    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = await _ensure_clip_edit_state(clip)
    clean_abs = _files_url_to_disk(clip.get("clean_file_path") or clip["file_path"])
    silences = await editor_engine.detect_silences(clean_abs)
    filler_word_ids: list[str] = []
    for blk in (clip.get("edit_state") or {}).get("caption_blocks", []):
        for w in blk.get("words", []):
            if w.get("is_filler") or captioner._is_filler_word(str(w.get("word", ""))):
                filler_word_ids.append(w["id"])
    return {"silences": silences, "filler_word_ids": filler_word_ids}


@app.post("/api/clips/{clip_id}/resync-whisper")
async def resync_clip_with_whisper(clip_id: str):
    """
    Run local Whisper (word_timestamps=True) directly on this clip's audio
    to produce millisecond-accurate, perfectly synced caption blocks.
    """
    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = await _ensure_clip_edit_state(clip)

    clean_abs = _files_url_to_disk(clip.get("clean_file_path") or clip["file_path"])
    if not os.path.isfile(clean_abs):
        raise HTTPException(status_code=404, detail="Clip video file not found")

    whisper_model = await settings_mod.get_setting("whisper_model") or "base"
    segments = await transcriber._get_whisper_transcript(clean_abs, whisper_model)
    if not segments:
        raise HTTPException(status_code=500, detail="Whisper produced no speech segments.")

    base_dur = await asyncio.to_thread(clipper.get_video_duration, clean_abs)
    es = dict(clip.get("edit_state") or {})
    style_key = es.get("caption_style") or "mozi_pop"
    overrides = dict(es.get("caption_overrides") or {})
    overrides["sync_offset_ms"] = -40  # Whisper word timings are already tight!
    resolved = captioner.resolve_style(style_key, overrides)

    blocks = captioner.build_caption_blocks(
        segments,
        0.0,
        base_dur,
        max_chars=int(resolved.get("max_chars_per_line", 16)),
        max_words=int(resolved.get("max_words_per_line", 3)),
    )
    es["caption_overrides"] = overrides
    es["caption_blocks"] = blocks
    await database.update_clip(clip_id, edit_state=es)
    return {"caption_blocks": blocks, "sync_offset_ms": -40}


# ---------------------------------------------------------------------------
# REST Routes — Storage & Cleanup Manager
# ---------------------------------------------------------------------------

@app.get("/api/storage")
async def get_storage_usage():
    return await storage_manager.get_storage_stats(TMP_DIR)


@app.post("/api/storage/cleanup")
async def cleanup_storage(body: StorageCleanupRequest):
    try:
        return await storage_manager.run_cleanup(TMP_DIR, body.target, body.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# REST Routes — Assets, Styles, Layouts, Brand Kits
# ---------------------------------------------------------------------------

@app.post("/api/assets/logo")
async def upload_logo_asset(file: UploadFile = File(...)):
    orig = file.filename or "logo.png"
    ext = os.path.splitext(orig)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(status_code=400, detail="Logo must be PNG, JPG, or WEBP.")
    fname = f"logo_{uuid.uuid4().hex[:10]}{ext}"
    dest = os.path.join(TMP_DIR, "assets", "logos", fname)
    with open(dest, "wb") as f:
        while chunk := await file.read(256 * 1024):
            f.write(chunk)
    return {"asset_url": f"/files/assets/logos/{fname}"}


@app.get("/api/caption-styles")
async def list_caption_styles():
    return captioner.get_style_catalog()


@app.get("/api/layouts")
async def list_layouts():
    return [{"key": k, "name": v} for k, v in reframer.LAYOUT_MODES.items()]


@app.get("/api/brand-kits")
async def list_brand_kits():
    return await database.get_brand_kits()


@app.post("/api/brand-kits", status_code=201)
async def create_brand_kit(body: BrandKitRequest):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Brand kit name is required")
    return await database.create_brand_kit(
        name=body.name.strip(),
        logo_path=body.logo_path,
        caption_style=body.caption_style,
        caption_overrides=body.caption_overrides,
        logo_config=body.logo_config,
    )


@app.put("/api/brand-kits/{kit_id}")
async def update_brand_kit(kit_id: str, body: BrandKitRequest):
    updated = await database.update_brand_kit(
        kit_id,
        name=body.name.strip(),
        logo_path=body.logo_path,
        caption_style=body.caption_style,
        caption_overrides=body.caption_overrides,
        logo_config=body.logo_config,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    return updated


@app.delete("/api/brand-kits/{kit_id}")
async def delete_brand_kit(kit_id: str):
    await database.delete_brand_kit(kit_id)
    return {"success": True}


# ---------------------------------------------------------------------------
# REST Routes — Phase 4: Batch Queue, Local Quick-Tools, & MCP Config
# ---------------------------------------------------------------------------

_YT_URL_RE = re.compile(r"^(https?\:\/\/)?(www\.|m\.)?(youtube\.com|youtu\.be)\/.+$")


@app.post("/api/batch", status_code=201)
async def create_batch_job(body: CreateBatchRequest):
    cleaned = [u.strip() for u in body.urls if u.strip()]
    if not cleaned:
        raise HTTPException(status_code=400, detail="Provide at least one YouTube URL.")
    if len(cleaned) > 25:
        raise HTTPException(status_code=400, detail="Maximum 25 URLs per batch.")
    invalid = [u for u in cleaned if not _YT_URL_RE.match(u)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid YouTube URL(s): {', '.join(invalid[:3])}")

    batch_queue.manager.configure(_run_pipeline)
    return await batch_queue.manager.enqueue_batch(
        urls=cleaned,
        clip_min_duration=body.clip_min_duration,
        clip_max_duration=body.clip_max_duration,
        custom_prompt=body.custom_prompt,
        layout_preference=body.layout_preference,
        brand_kit_id=body.brand_kit_id,
    )


@app.get("/api/batch")
async def list_batches():
    return await batch_queue.manager.list_batches()


@app.get("/api/batch/{batch_id}")
async def get_batch_detail(batch_id: str):
    batch = await batch_queue.manager.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@app.post("/api/batch/{batch_id}/cancel/{project_id}")
async def cancel_batch_item(batch_id: str, project_id: str):
    ok = await batch_queue.manager.cancel_item(batch_id, project_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Item is not queued or batch not found")
    return {"success": True}


async def _save_tool_upload(file: UploadFile) -> str:
    tools_dir = os.path.join(TMP_DIR, "tools")
    os.makedirs(tools_dir, exist_ok=True)
    orig = file.filename or "input.mp4"
    ext = os.path.splitext(orig)[1].lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"):
        raise HTTPException(status_code=400, detail="Unsupported video file format.")
    dest = os.path.join(tools_dir, f"in_{uuid.uuid4().hex[:10]}{ext}")
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    return dest


@app.post("/api/tools/trim")
async def tool_trim_video(
    file: UploadFile = File(...),
    start: float = Form(0.0),
    end: float = Form(0.0),
    accurate: bool = Form(True),
):
    in_path = await _save_tool_upload(file)
    tools_dir = os.path.join(TMP_DIR, "tools")
    try:
        res = await quick_tools.run_trim(in_path, tools_dir, start=start, end=end, accurate=accurate)
        return {
            "download_url": f"/files/tools/{res['filename']}",
            "filename": res["filename"],
            "input": res["input"],
            "output": res["output"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)


@app.post("/api/tools/compress")
async def tool_compress_video(
    file: UploadFile = File(...),
    preset: str = Form("balanced"),
    crf: Optional[int] = Form(None),
    max_height: Optional[int] = Form(None),
):
    in_path = await _save_tool_upload(file)
    tools_dir = os.path.join(TMP_DIR, "tools")
    try:
        res = await quick_tools.run_compress(in_path, tools_dir, preset=preset, crf=crf, max_height=max_height)
        return {
            "download_url": f"/files/tools/{res['filename']}",
            "filename": res["filename"],
            "savings_percent": res["savings_percent"],
            "input": res["input"],
            "output": res["output"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)


@app.post("/api/tools/convert")
async def tool_convert_video(
    file: UploadFile = File(...),
    target_format: str = Form("mp4"),
):
    in_path = await _save_tool_upload(file)
    tools_dir = os.path.join(TMP_DIR, "tools")
    try:
        res = await quick_tools.run_convert(in_path, tools_dir, target_format=target_format)
        return {
            "download_url": f"/files/tools/{res['filename']}",
            "filename": res["filename"],
            "format": res["format"],
            "input": res["input"],
            "output": res["output"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)


@app.post("/api/tools/transform")
async def tool_transform_video(
    file: UploadFile = File(...),
    rotate: int = Form(0),
    flip_h: bool = Form(False),
    flip_v: bool = Form(False),
    aspect: str = Form("keep"),
):
    in_path = await _save_tool_upload(file)
    tools_dir = os.path.join(TMP_DIR, "tools")
    try:
        res = await quick_tools.run_transform(
            in_path, tools_dir, rotate=rotate, flip_h=flip_h, flip_v=flip_v, aspect=aspect
        )
        return {
            "download_url": f"/files/tools/{res['filename']}",
            "filename": res["filename"],
            "input": res["input"],
            "output": res["output"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)


@app.get("/api/codex/status")
async def get_codex_status():
    """Check whether the official Codex CLI is installed and logged in."""
    return await asyncio.to_thread(llm.check_codex_status)


@app.get("/api/claude/status")
async def get_claude_status():
    """Check whether the official Claude Code CLI is installed and logged in."""
    return await asyncio.to_thread(llm.check_claude_status)


# ---------------------------------------------------------------------------
# REST Routes — Trending Video Discovery & AI Scout
# ---------------------------------------------------------------------------

@app.get("/api/discover/niches")
async def list_discover_niches():
    return discover.NICHES


@app.get("/api/discover/trending")
async def discover_trending_videos(
    niche: str = "podcasts",
    query: Optional[str] = None,
    min_minutes: int = 3,
    limit: int = 15,
    sort: str = "score",
):
    try:
        videos = await discover.search_trending(
            niche=niche,
            query=query,
            min_minutes=min_minutes,
            limit=limit,
            sort_by=sort,
        )
        return {"videos": videos, "count": len(videos)}
    except Exception as exc:
        logger.exception("Discover trending failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/discover/ai-scout")
async def discover_ai_scout(body: AiScoutRequest):
    provider = await settings_mod.get_setting("llm_provider") or "codex"
    api_key = await settings_mod.get_setting("llm_api_key") or ""
    model = await settings_mod.get_setting("llm_model") or ""
    try:
        picks = await discover.ai_scout_videos(body.videos, body.goal, provider, api_key, model)
        return {"picks": picks, "provider": provider}
    except Exception as exc:
        logger.exception("AI Scout failed")
        raise HTTPException(status_code=500, detail=f"AI Scout failed: {llm.friendly_error(exc)}")


# ---------------------------------------------------------------------------
# REST Routes — YouTube Shorts OAuth, Auto-Post & Batch Scheduler
# ---------------------------------------------------------------------------

@app.get("/api/youtube/status")
async def youtube_status():
    return await youtube_uploader.get_status()


@app.post("/api/youtube/client-secret")
async def youtube_save_client_secret(body: YouTubeClientSecretRequest):
    try:
        return await youtube_uploader.save_client_secret(body.client_secret_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/youtube/client-secret/upload")
async def youtube_upload_client_secret_file(file: UploadFile = File(...)):
    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        return await youtube_uploader.save_client_secret(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/youtube/auth-url")
async def youtube_auth_url():
    try:
        return await youtube_uploader.build_auth_url()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/youtube/callback")
async def youtube_oauth_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5000")
    if error or not code or not state:
        return RedirectResponse(f"{frontend_url}/settings?youtube=error&reason={error or 'missing_code'}")
    try:
        await youtube_uploader.handle_oauth_callback(code, state)
        return RedirectResponse(f"{frontend_url}/settings?youtube=connected")
    except Exception as exc:
        logger.exception("YouTube OAuth callback failed")
        return RedirectResponse(f"{frontend_url}/settings?youtube=error")


@app.post("/api/youtube/disconnect")
async def youtube_disconnect():
    await youtube_uploader.disconnect()
    return {"success": True}


@app.post("/api/clips/{clip_id}/publish/youtube")
async def publish_clip_to_youtube(clip_id: str, body: PublishYouTubeRequest):
    clip = await database.get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    video_abs = _files_url_to_disk(clip["file_path"])
    if not os.path.isfile(video_abs):
        raise HTTPException(status_code=404, detail="Rendered clip MP4 not found on disk")

    title = (body.title or clip.get("title") or "OpenClip Short").strip()
    hashtags_str = " ".join(clip.get("hashtags") or [])
    default_desc = "\n\n".join(p for p in [clip.get("reason") or "", hashtags_str, "#Shorts"] if p).strip()
    description = body.description if body.description is not None else default_desc
    tags = body.tags if body.tags is not None else (clip.get("tags") or [])

    try:
        res = await youtube_uploader.upload_short(
            video_path=video_abs,
            title=title,
            description=description,
            tags=tags,
            privacy_status=body.privacy_status,
            publish_at=body.publish_at,
            category_id=body.category_id,
            made_for_kids=body.made_for_kids,
        )
    except Exception as exc:
        logger.exception("YouTube publish failed for clip %s", clip_id)
        raise HTTPException(status_code=400, detail=str(exc))

    await database.update_clip(
        clip_id,
        youtube_video_id=res["video_id"],
        youtube_url=res["url"],
        youtube_status=res["status"],
        youtube_scheduled_at=res["publish_at"],
    )
    return res


@app.post("/api/projects/{project_id}/schedule-youtube")
async def schedule_project_to_youtube(project_id: str, body: ScheduleProjectYouTubeRequest):
    """Auto-upload and natively schedule all unposted clips in a project at a fixed cadence."""
    from datetime import datetime, timedelta, timezone

    project = await database.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        start_dt = datetime.fromisoformat(body.start_at.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid start_at ISO-8601 timestamp.")

    clips = project.get("clips") or []
    candidates = [
        c for c in clips
        if not (body.skip_already_posted and c.get("youtube_video_id"))
    ][: max(1, min(10, int(body.max_uploads or 6)))]

    if not candidates:
        raise HTTPException(status_code=400, detail="No unposted clips available to schedule.")

    interval = max(1, int(body.interval_hours or 24))
    results = []
    errors = []

    for idx, clip in enumerate(candidates):
        slot_dt = start_dt + timedelta(hours=idx * interval)
        publish_iso = slot_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        video_abs = _files_url_to_disk(clip["file_path"])
        if not os.path.isfile(video_abs):
            errors.append({"clip_id": clip["id"], "error": "Clip file missing on disk"})
            continue

        hashtags_str = " ".join(clip.get("hashtags") or [])
        desc_parts = [clip.get("reason") or "", body.description_footer.strip(), hashtags_str, "#Shorts"]
        desc = "\n\n".join(p for p in desc_parts if p).strip()

        try:
            res = await youtube_uploader.upload_short(
                video_path=video_abs,
                title=clip.get("title") or f"Clip {idx + 1}",
                description=desc,
                tags=clip.get("tags") or [],
                privacy_status="private",
                publish_at=publish_iso,
            )
            await database.update_clip(
                clip["id"],
                youtube_video_id=res["video_id"],
                youtube_url=res["url"],
                youtube_status=res["status"],
                youtube_scheduled_at=res["publish_at"],
            )
            results.append({"clip_id": clip["id"], "title": clip.get("title"), **res})
        except Exception as exc:
            logger.exception("Auto-schedule upload failed for clip %s", clip["id"])
            errors.append({"clip_id": clip["id"], "title": clip.get("title"), "error": str(exc)})
            break  # Stop on quota/auth error rather than hammering YouTube API

    return {"scheduled": results, "errors": errors, "count": len(results)}


@app.get("/api/mcp/config")
async def get_mcp_config():
    """Return ready-to-paste local MCP client config for Codex (TOML) & Claude/Cursor/Antigravity (JSON)."""
    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "mcp_server.py"))
    python_path = os.path.abspath(sys.executable)
    config = {
        "mcpServers": {
            "openclip-studio": {
                "command": python_path,
                "args": [script_path],
                "env": {
                    "OPENCLIP_API_URL": "http://127.0.0.1:8000",
                },
            }
        }
    }
    py_toml = python_path.replace("\\", "\\\\")
    sc_toml = script_path.replace("\\", "\\\\")
    codex_toml = (
        "[mcp_servers.openclip-studio]\n"
        f'command = "{py_toml}"\n'
        f'args = ["{sc_toml}"]\n'
        "\n"
        "[mcp_servers.openclip-studio.env]\n"
        'OPENCLIP_API_URL = "http://127.0.0.1:8000"\n'
    )
    return {
        "python_path": python_path,
        "script_path": script_path,
        "config_json": json.dumps(config, indent=2),
        "codex_toml": codex_toml,
        "tools": [{"name": t["name"], "description": t["description"]} for t in mcp_server.TOOLS],
    }


# ---------------------------------------------------------------------------
# REST Routes — System Health & Settings
# ---------------------------------------------------------------------------

_LOCAL_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")


@app.get("/api/health")
async def health():
    status = await asyncio.to_thread(system_setup.check_status)
    return {**status, "install": system_setup.get_job()}


@app.post("/api/system/install", status_code=202)
async def install_requirements(request: Request):
    origin = request.headers.get("origin", "")
    if not _LOCAL_ORIGIN.match(origin) or request.headers.get("x-openclip-install") != "1":
        raise HTTPException(status_code=403, detail="Installs can only be started from the local OpenClip app")
    started, job = system_setup.start_install()
    return {"started": started, "install": job}


@app.get("/api/system/install")
async def install_progress():
    return system_setup.get_job()


@app.get("/api/system/encoders")
async def get_video_encoders(refresh: bool = False):
    saved_encoder = await settings_mod.get_setting("video_encoder")
    ffbin.set_preferred_encoder(saved_encoder or "auto")
    return await asyncio.to_thread(ffbin.get_encoder_status, refresh)


@app.get("/api/settings")
async def get_settings():
    return await settings_mod.get_all_settings()


@app.post("/api/settings")
async def update_settings(body: UpdateSettingsRequest):
    pairs = {
        "llm_provider": body.llm_provider,
        "llm_api_key": body.llm_api_key,
        "llm_model": body.llm_model,
        "whisper_model": body.whisper_model,
        "caption_style": body.caption_style,
        "video_encoder": body.video_encoder,
    }
    for key, value in pairs.items():
        if value is not None:
            if key == "video_encoder":
                value = ffbin.set_preferred_encoder(value)
            await settings_mod.set_setting(key, value)
    return {"success": True}


# ---------------------------------------------------------------------------
# WebSocket — Real-Time Progress
# ---------------------------------------------------------------------------

@app.websocket("/ws/progress/{project_id}")
async def websocket_progress(websocket: WebSocket, project_id: str):
    await websocket.accept()
    if project_id not in _ws_connections:
        _ws_connections[project_id] = set()
    _ws_connections[project_id].add(websocket)

    try:
        if project_id in _last_progress:
            await websocket.send_json(_last_progress[project_id])
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_connections.get(project_id, set()).discard(websocket)
        if project_id in _ws_connections and not _ws_connections[project_id]:
            _ws_connections.pop(project_id, None)


if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
