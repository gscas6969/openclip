"""
quick_tools.py — Local no-watermark FFmpeg video utilities for OpenClip Studio.

Implements the local quick-tools suite:
  - trim     : Frame-accurate or stream-copy video trimming
  - compress : CRF quality or social-size compression
  - convert  : Container / format conversion (mp4, mov, webm, mkv, gif, mp3, wav)
  - transform: Rotate (90/180/270), Flip (horizontal/vertical), Resize/Aspect (9:16, 16:9, 1:1)
"""

import asyncio
import json
import os
import subprocess
import uuid
from typing import Any, Dict, Optional

import ffbin  # type: ignore


def probe_media(file_path: str) -> Dict[str, Any]:
    """Return duration, size_bytes, width, height, and codec info via ffprobe."""
    size_bytes = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
    cmd = [
        ffbin.ffprobe(),
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        return {"size_bytes": size_bytes, "duration": 0.0, "width": 0, "height": 0}

    try:
        data = json.loads(res.stdout)
    except Exception:
        return {"size_bytes": size_bytes, "duration": 0.0, "width": 0, "height": 0}

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or 0.0)
    width = 0
    height = 0
    video_codec = None
    audio_codec = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not width:
            width = int(s.get("width") or 0)
            height = int(s.get("height") or 0)
            video_codec = s.get("codec_name")
        elif s.get("codec_type") == "audio" and not audio_codec:
            audio_codec = s.get("codec_name")

    return {
        "size_bytes": size_bytes,
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
    }


async def _run_ffmpeg(cmd: list[str], timeout: int = 600) -> None:
    def _exec() -> tuple[int, str]:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return res.returncode, str(res.stderr)

    code, stderr = await asyncio.to_thread(_exec)
    if code != 0:
        raise RuntimeError(f"FFmpeg tool failed: {stderr[-1000:]}")


async def _run_ffmpeg_encode(
    prefix_cmd: list[str],
    suffix_cmd: list[str],
    *,
    quality: str = "standard",
    crf: Optional[int] = None,
    timeout: int = 600,
) -> None:
    res = await asyncio.to_thread(
        ffbin.run_ffmpeg_encode,
        prefix_cmd,
        suffix_cmd,
        quality=quality,
        crf=crf,
        timeout=timeout,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg tool failed: {str(res.stderr)[-1000:]}")


async def run_trim(
    input_path: str,
    output_dir: str,
    start: float,
    end: float,
    accurate: bool = True,
) -> Dict[str, Any]:
    os.makedirs(output_dir, exist_ok=True)
    in_meta = await asyncio.to_thread(probe_media, input_path)
    total_dur = in_meta["duration"] or 60.0
    st = max(0.0, float(start))
    en = min(total_dur, float(end)) if end > 0 else total_dur
    if en <= st + 0.1:
        raise ValueError("End time must be greater than start time.")

    out_name = f"trim_{uuid.uuid4().hex[:10]}.mp4"
    out_path = os.path.join(output_dir, out_name)

    if accurate:
        prefix_cmd = [
            ffbin.ffmpeg(), "-y",
            "-ss", f"{st:.3f}",
            "-i", input_path,
            "-t", f"{en - st:.3f}",
        ]
        suffix_cmd = [
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_path,
        ]
        await _run_ffmpeg_encode(prefix_cmd, suffix_cmd, quality="standard")
    else:
        cmd = [
            ffbin.ffmpeg(), "-y",
            "-ss", f"{st:.3f}",
            "-i", input_path,
            "-t", f"{en - st:.3f}",
            "-c", "copy",
            "-movflags", "+faststart",
            out_path,
        ]
        await _run_ffmpeg(cmd)

    out_meta = await asyncio.to_thread(probe_media, out_path)
    return {"filename": out_name, "output_path": out_path, "input": in_meta, "output": out_meta}


async def run_compress(
    input_path: str,
    output_dir: str,
    preset: str = "balanced",
    crf: Optional[int] = None,
    max_height: Optional[int] = None,
) -> Dict[str, Any]:
    """Compress video by social preset (high/balanced/small/discord) or explicit CRF."""
    os.makedirs(output_dir, exist_ok=True)
    in_meta = await asyncio.to_thread(probe_media, input_path)

    preset_map = {
        "high": (22, 1080, "160k"),
        "balanced": (26, 1080, "128k"),
        "small": (30, 720, "96k"),
        "discord": (32, 720, "80k"),
    }
    def_crf, def_h, audio_br = preset_map.get(preset, (26, 1080, "128k"))
    final_crf = max(16, min(40, int(crf if crf is not None else def_crf)))
    target_h = int(max_height) if max_height else def_h

    vf_parts = []
    if in_meta["height"] and in_meta["height"] > target_h:
        vf_parts.append(f"scale=-2:{target_h}")

    out_name = f"compressed_{uuid.uuid4().hex[:10]}.mp4"
    out_path = os.path.join(output_dir, out_name)

    prefix_cmd = [ffbin.ffmpeg(), "-y", "-i", input_path]
    if vf_parts:
        prefix_cmd.extend(["-vf", ",".join(vf_parts)])
    suffix_cmd = [
        "-c:a", "aac", "-b:a", audio_br,
        "-movflags", "+faststart",
        out_path,
    ]

    await _run_ffmpeg_encode(prefix_cmd, suffix_cmd, quality="compress", crf=final_crf)
    out_meta = await asyncio.to_thread(probe_media, out_path)
    savings = 0.0
    if in_meta["size_bytes"] > 0:
        savings = round((1.0 - (out_meta["size_bytes"] / in_meta["size_bytes"])) * 100.0, 1)
    return {
        "filename": out_name,
        "output_path": out_path,
        "input": in_meta,
        "output": out_meta,
        "savings_percent": savings,
    }


async def run_convert(
    input_path: str,
    output_dir: str,
    target_format: str = "mp4",
) -> Dict[str, Any]:
    """Convert video into mp4, mov, webm, mkv, gif, mp3, or wav."""
    fmt = target_format.lower().strip().lstrip(".")
    allowed = {"mp4", "mov", "webm", "mkv", "gif", "mp3", "wav"}
    if fmt not in allowed:
        raise ValueError(f"Unsupported target format '{fmt}'. Allowed: {', '.join(sorted(allowed))}")

    os.makedirs(output_dir, exist_ok=True)
    in_meta = await asyncio.to_thread(probe_media, input_path)
    out_name = f"converted_{uuid.uuid4().hex[:10]}.{fmt}"
    out_path = os.path.join(output_dir, out_name)

    if fmt in {"mp3", "wav", "gif", "webm"}:
        if fmt == "mp3":
            cmd = [ffbin.ffmpeg(), "-y", "-i", input_path, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out_path]
        elif fmt == "wav":
            cmd = [ffbin.ffmpeg(), "-y", "-i", input_path, "-vn", "-c:a", "pcm_s16le", out_path]
        elif fmt == "gif":
            cmd = [
                ffbin.ffmpeg(), "-y", "-i", input_path,
                "-vf", "fps=12,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                "-loop", "0",
                out_path,
            ]
        else:
            cmd = [
                ffbin.ffmpeg(), "-y", "-i", input_path,
                "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0",
                "-c:a", "libopus",
                out_path,
            ]
        await _run_ffmpeg(cmd)
    else:
        await _run_ffmpeg_encode(
            [ffbin.ffmpeg(), "-y", "-i", input_path],
            ["-c:a", "aac", "-b:a", "192k", out_path],
            quality="standard",
        )
    out_meta = await asyncio.to_thread(probe_media, out_path)
    return {"filename": out_name, "output_path": out_path, "format": fmt, "input": in_meta, "output": out_meta}


async def run_transform(
    input_path: str,
    output_dir: str,
    rotate: int = 0,
    flip_h: bool = False,
    flip_v: bool = False,
    aspect: str = "keep",
) -> Dict[str, Any]:
    """Rotate (0/90/180/270), flip horizontally/vertically, or resize to 9:16 / 16:9 / 1:1."""
    os.makedirs(output_dir, exist_ok=True)
    in_meta = await asyncio.to_thread(probe_media, input_path)

    vf: list[str] = []
    if rotate == 90:
        vf.append("transpose=1")
    elif rotate == 180:
        vf.append("transpose=1,transpose=1")
    elif rotate == 270:
        vf.append("transpose=2")

    if flip_h:
        vf.append("hflip")
    if flip_v:
        vf.append("vflip")

    if aspect == "9:16":
        vf.append("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    elif aspect == "16:9":
        vf.append("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    elif aspect == "1:1":
        vf.append("scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")

    if not vf:
        raise ValueError("Select at least one rotation, flip, or aspect ratio transform.")

    out_name = f"transformed_{uuid.uuid4().hex[:10]}.mp4"
    out_path = os.path.join(output_dir, out_name)

    prefix_cmd = [
        ffbin.ffmpeg(), "-y",
        "-i", input_path,
        "-vf", ",".join(vf),
    ]
    suffix_cmd = [
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]

    await _run_ffmpeg_encode(prefix_cmd, suffix_cmd, quality="standard")
    out_meta = await asyncio.to_thread(probe_media, out_path)
    return {"filename": out_name, "output_path": out_path, "input": in_meta, "output": out_meta}
