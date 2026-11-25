"""
ffbin.py — Locate the ffmpeg / ffprobe / yt-dlp executables in a cross-platform way.

Search order for ffmpeg / ffprobe:
    1. ``backend/bin/ffmpeg/`` — where the in-app installer puts them
    2. ``PATH`` (macOS / Linux / any PATH install)
    3. the winget install location on Windows (any FFmpeg version)

yt-dlp is looked up next to the running Python first (the project's
virtualenv, where ``pip install yt-dlp`` puts it), then on ``PATH``.
"""

import glob
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EXE = ".exe" if os.name == "nt" else ""

#: Folder the in-app installer downloads FFmpeg into (gitignored via backend/bin/).
LOCAL_FFMPEG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg")

_WINGET_GLOB = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin"
)


def _find(name: str) -> str | None:
    local = os.path.join(LOCAL_FFMPEG_DIR, name + _EXE)
    if os.path.isfile(local):
        return local
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for bin_dir in sorted(glob.glob(_WINGET_GLOB), reverse=True):
            candidate = os.path.join(bin_dir, f"{name}.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def find_ffmpeg() -> str | None:
    """Full path to ffmpeg, or None if it isn't installed."""
    return _find("ffmpeg")


def find_ffprobe() -> str | None:
    """Full path to ffprobe, or None if it isn't installed."""
    return _find("ffprobe")


def find_ytdlp() -> str | None:
    """Full path to yt-dlp, or None if it isn't installed."""
    scripts_dir = os.path.dirname(sys.executable)  # venv/Scripts or venv/bin
    local = os.path.join(scripts_dir, "yt-dlp" + _EXE)
    if os.path.isfile(local):
        return local
    return shutil.which("yt-dlp")


# The helpers below always return something runnable so callers can pass the
# result straight to subprocess; if the tool is missing, subprocess raises a
# clear "not found" error (and /api/system/status reports it to the UI).

def ffmpeg() -> str:
    return find_ffmpeg() or "ffmpeg"


def ffprobe() -> str:
    return find_ffprobe() or "ffprobe"


def ytdlp() -> str:
    return find_ytdlp() or "yt-dlp"


def ffmpeg_dir() -> str:
    return os.path.dirname(ffmpeg()) or "."


# ---------------------------------------------------------------------------
# Hardware-accelerated H.264 video encoder detection & automatic CPU fallback
# ---------------------------------------------------------------------------

_HW_ENCODER_SPECS: list[tuple[str, str]] = [
    ("h264_nvenc", "NVIDIA NVENC"),
    ("h264_amf", "AMD AMF"),
    ("h264_qsv", "Intel Quick Sync"),
    ("h264_videotoolbox", "Apple VideoToolbox"),
]

_ENCODER_LABELS: dict[str, str] = {
    **dict(_HW_ENCODER_SPECS),
    "libx264": "CPU (libx264)",
}

_VALID_ENCODER_PREFS = {"auto", "cpu", "libx264", *(name for name, _ in _HW_ENCODER_SPECS)}

_probe_lock = threading.Lock()
_cached_available_encoders: Optional[list[dict[str, str]]] = None
_preferred_encoder: str = "auto"


def _list_ffmpeg_encoders(ffmpeg_exe: str) -> set[str]:
    """Return encoder names compiled into the local FFmpeg binary."""
    try:
        res = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if res.returncode != 0:
            return set()
        found: set[str] = set()
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0].startswith("V"):
                found.add(parts[1])
        return found
    except Exception as exc:
        logger.debug("Failed listing FFmpeg encoders: %s", exc)
        return set()


def _probe_single_encoder(ffmpeg_exe: str, encoder_name: str) -> bool:
    """Verify an encoder can actually initialize and encode a frame on this machine."""
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", "color=c=black:s=256x256:d=0.04",
        "-frames:v", "1",
        "-pix_fmt", "yuv420p",
        "-c:v", encoder_name,
        "-f", "null",
        "-",
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return res.returncode == 0
    except Exception as exc:
        logger.debug("Probe failed for %s: %s", encoder_name, exc)
        return False


def detect_video_encoders(force_refresh: bool = False) -> list[dict[str, str]]:
    """
    Detect usable H.264 video encoders on this machine, ordered by priority.
    Always ends with CPU ``libx264`` as a guaranteed fallback.
    """
    global _cached_available_encoders
    with _probe_lock:
        if _cached_available_encoders is not None and not force_refresh:
            return list(_cached_available_encoders)

        ffmpeg_exe = ffmpeg()
        compiled = _list_ffmpeg_encoders(ffmpeg_exe)
        available: list[dict[str, str]] = []

        for enc_name, label in _HW_ENCODER_SPECS:
            if enc_name not in compiled:
                continue
            if _probe_single_encoder(ffmpeg_exe, enc_name):
                logger.info("Detected hardware video encoder: %s (%s)", label, enc_name)
                available.append({"id": enc_name, "label": label, "kind": "gpu"})
            else:
                logger.debug("Hardware encoder %s compiled in FFmpeg but unavailable on this GPU", enc_name)

        available.append({"id": "libx264", "label": _ENCODER_LABELS["libx264"], "kind": "cpu"})
        _cached_available_encoders = available
        return list(available)


def set_preferred_encoder(mode: Optional[str]) -> str:
    """Set runtime encoder preference ('auto', 'cpu', or a specific encoder id)."""
    global _preferred_encoder
    cleaned = (mode or "auto").strip().lower()
    if cleaned not in _VALID_ENCODER_PREFS:
        cleaned = "auto"
    if cleaned == "libx264":
        cleaned = "cpu"
    _preferred_encoder = cleaned
    return _preferred_encoder


def get_preferred_encoder() -> str:
    return _preferred_encoder


def resolve_video_encoder(preferred: Optional[str] = None, force_cpu: bool = False) -> str:
    """Resolve the concrete FFmpeg H.264 encoder name to use right now."""
    if force_cpu:
        return "libx264"
    pref = (preferred if preferred is not None else _preferred_encoder).strip().lower()
    if pref in ("cpu", "libx264"):
        return "libx264"

    available = detect_video_encoders()
    available_ids = [item["id"] for item in available]

    if pref != "auto" and pref in available_ids:
        return pref

    # Auto (or requested GPU encoder not available): pick first usable encoder
    return available_ids[0] if available_ids else "libx264"


def get_encoder_status(force_refresh: bool = False) -> dict[str, Any]:
    """Return full encoder capability & active selection info for Settings UI."""
    available = detect_video_encoders(force_refresh=force_refresh)
    active = resolve_video_encoder()
    gpu_encoders = [e for e in available if e["kind"] == "gpu"]
    return {
        "preferred": _preferred_encoder,
        "active": active,
        "active_label": _ENCODER_LABELS.get(active, active),
        "gpu_available": len(gpu_encoders) > 0,
        "available": available,
    }


def video_encode_args(
    quality: str = "standard",
    crf: Optional[int] = None,
    encoder: Optional[str] = None,
    force_cpu: bool = False,
) -> list[str]:
    """
    Build FFmpeg H.264 video encoder arguments for ``quality``:
      - ``"fast"``     : ultrafast / high quality intermediate cut (default crf=16)
      - ``"standard"`` : balanced fast render for clips/edits (default crf=20)
      - ``"compress"`` : size-efficient compression with custom crf (default crf=26)
    """
    enc = "libx264" if force_cpu else (encoder or resolve_video_encoder())
    if crf is not None:
        q = max(14, min(42, int(crf)))
    elif quality == "fast":
        q = 16
    elif quality == "compress":
        q = 26
    else:
        q = 20

    if enc == "h264_nvenc":
        preset = "p1" if quality == "fast" else ("p5" if quality == "compress" else "p4")
        # NVENC -cq runs ~3 points higher than x264 -crf for comparable file size
        nvenc_cq = min(51, q + 3)
        return [
            "-c:v", "h264_nvenc",
            "-preset", preset,
            "-rc", "vbr",
            "-cq", str(nvenc_cq),
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]

    if enc == "h264_amf":
        amf_q = "speed" if quality == "fast" else ("quality" if quality == "compress" else "balanced")
        return [
            "-c:v", "h264_amf",
            "-quality", amf_q,
            "-rc", "cqp",
            "-qp_i", str(q),
            "-qp_p", str(q),
            "-pix_fmt", "yuv420p",
        ]

    if enc == "h264_qsv":
        qsv_preset = "veryfast" if quality == "fast" else ("medium" if quality == "compress" else "fast")
        return [
            "-c:v", "h264_qsv",
            "-preset", qsv_preset,
            "-global_quality", str(q),
            "-pix_fmt", "yuv420p",
        ]

    if enc == "h264_videotoolbox":
        # VideoToolbox -q:v uses 1..100 (higher = better quality)
        vt_q = max(25, min(85, int(round(105 - q * 1.9))))
        return [
            "-c:v", "h264_videotoolbox",
            "-q:v", str(vt_q),
            "-pix_fmt", "yuv420p",
        ]

    # Default CPU libx264
    x264_preset = "ultrafast" if quality == "fast" else ("fast" if quality == "compress" else "veryfast")
    return [
        "-c:v", "libx264",
        "-preset", x264_preset,
        "-crf", str(q),
        "-pix_fmt", "yuv420p",
    ]


def run_ffmpeg_encode(
    prefix_args: list[str],
    suffix_args: list[str],
    *,
    quality: str = "standard",
    crf: Optional[int] = None,
    timeout: float = 900,
    text: bool = True,
    encoder: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """
    Execute an FFmpeg H.264 encode command using the active encoder (GPU when
    available), automatically retrying once on CPU ``libx264`` if hardware
    encoding fails at runtime (e.g. session limit or driver issue).
    """
    chosen = encoder or resolve_video_encoder()
    cmd = [*prefix_args, *video_encode_args(quality=quality, crf=crf, encoder=chosen), *suffix_args]

    kwargs: dict[str, Any] = {"capture_output": True, "timeout": timeout}
    if text:
        kwargs.update({"text": True, "encoding": "utf-8", "errors": "replace"})

    try:
        res = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg took longer than {timeout:.0f}s and was stopped") from exc

    if res.returncode != 0 and chosen != "libx264":
        err_tail = (
            res.stderr[-300:] if isinstance(res.stderr, str)
            else (res.stderr or b"").decode(errors="replace")[-300:]
        )
        logger.warning(
            "Hardware encoder '%s' failed (code %d); falling back to CPU libx264. Detail: %s",
            chosen,
            res.returncode,
            err_tail.strip(),
        )
        fallback_cmd = [
            *prefix_args,
            *video_encode_args(quality=quality, crf=crf, force_cpu=True),
            *suffix_args,
        ]
        try:
            res = subprocess.run(fallback_cmd, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ffmpeg took longer than {timeout:.0f}s and was stopped") from exc

    return res
