"""
editor_engine.py — Non-destructive FFmpeg clip render compiler, waveform
extractor, silence detector, and batch ZIP exporter.
"""

import asyncio
import math
import os
import re
import struct
import subprocess
import uuid
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import ffbin  # type: ignore
import captioner  # type: ignore


def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted((min(a, b), max(a, b)) for a, b in intervals if b - a > 0.03)
    merged: List[Tuple[float, float]] = [ordered[0]]
    for st, en in ordered[1:]:
        prev_st, prev_en = merged[-1]
        if st <= prev_en + 0.04:
            merged[-1] = (prev_st, max(prev_en, en))
        else:
            merged.append((st, en))
    return merged


def compute_kept_segments(
    duration: float,
    edit_state: Dict[str, Any],
) -> List[Tuple[float, float]]:
    """
    Compute kept [start, end] timeline segments after applying trim_start,
    trim_end, explicit cuts, and any deleted words inside caption_blocks.
    """
    trim_start = max(0.0, float(edit_state.get("trim_start", 0.0)))
    trim_end = min(duration, float(edit_state.get("trim_end", duration)))
    if trim_end <= trim_start + 0.1:
        trim_start, trim_end = 0.0, max(0.5, duration)

    cut_intervals: List[Tuple[float, float]] = []
    for c in edit_state.get("cuts", []) or []:
        c_st = max(trim_start, float(c.get("start", 0.0)))
        c_en = min(trim_end, float(c.get("end", 0.0)))
        if c_en - c_st > 0.04:
            cut_intervals.append((c_st, c_en))

    # Also collect any individually deleted words from the transcript editor
    for blk in edit_state.get("caption_blocks", []) or []:
        for w in blk.get("words", []) or []:
            if w.get("deleted"):
                w_st = max(trim_start, float(w.get("start", 0.0)))
                w_en = min(trim_end, float(w.get("end", 0.0)))
                if w_en - w_st > 0.04:
                    cut_intervals.append((w_st, w_en))

    merged_cuts = _merge_intervals(cut_intervals)
    if not merged_cuts:
        return [(round(trim_start, 3), round(trim_end, 3))]

    kept: List[Tuple[float, float]] = []
    cursor = trim_start
    for c_st, c_en in merged_cuts:
        if c_st > cursor + 0.05:
            kept.append((round(cursor, 3), round(c_st, 3)))
        cursor = max(cursor, c_en)
    if trim_end > cursor + 0.05:
        kept.append((round(cursor, 3), round(trim_end, 3)))

    return kept or [(round(trim_start, 3), round(trim_end, 3))]


def _map_time(t: float, kept: List[Tuple[float, float]]) -> Optional[float]:
    """Map an original clip timestamp onto the post-cut timeline."""
    offset = 0.0
    for k_st, k_en in kept:
        if k_st <= t <= k_en:
            return round(offset + (t - k_st), 3)
        if t < k_st:
            return None
        offset += (k_en - k_st)
    return None


def remap_blocks_to_kept(
    blocks: List[Dict[str, Any]],
    kept: List[Tuple[float, float]],
) -> List[Dict[str, Any]]:
    """Shift & filter caption blocks onto the kept timeline."""
    remapped: List[Dict[str, Any]] = []
    for blk in blocks:
        new_words = []
        for w in blk.get("words", []) or []:
            if w.get("deleted"):
                continue
            w_st = float(w["start"])
            w_en = float(w["end"])
            # Clamp slightly overlapping word edges to the kept segment they fall in
            mapped_st = _map_time(w_st, kept)
            mapped_en = _map_time(w_en, kept)
            if mapped_st is None and mapped_en is not None:
                mapped_st = max(0.0, mapped_en - max(0.08, w_en - w_st))
            elif mapped_st is not None and mapped_en is None:
                mapped_en = mapped_st + max(0.08, min(0.5, w_en - w_st))
            if mapped_st is not None and mapped_en is not None and mapped_en > mapped_st:
                new_words.append({
                    **w,
                    "start": mapped_st,
                    "end": mapped_en,
                })
        if new_words:
            remapped.append({
                **blk,
                "start": new_words[0]["start"],
                "end": new_words[-1]["end"],
                "text": " ".join(w["word"] for w in new_words),
                "words": new_words,
            })
    return remapped


async def render_clip_from_edit_state(
    clean_video_path: str,
    output_path: str,
    duration: float,
    edit_state: Dict[str, Any],
    tmp_dir: str,
) -> Dict[str, Any]:
    """
    Compile edit_state (trims + cuts + kinetic ASS captions + brand logo)
    against clean_video_path into output_path in a single FFmpeg pass.
    """
    if not os.path.isfile(clean_video_path):
        raise FileNotFoundError(f"Clean base clip not found: {clean_video_path}")

    kept = compute_kept_segments(duration, edit_state)
    new_duration = round(sum(en - st for st, en in kept), 3)

    # Prepare ASS caption file if captions are enabled
    ass_path: Optional[str] = None
    caption_enabled = bool(edit_state.get("caption_enabled", True))
    style_key = str(edit_state.get("caption_style") or "mozi_pop")
    if caption_enabled and style_key != "none":
        blocks = remap_blocks_to_kept(edit_state.get("caption_blocks", []) or [], kept)
        if blocks:
            resolved = captioner.resolve_style(style_key, edit_state.get("caption_overrides"))
            ass_path = os.path.join(os.path.dirname(output_path), f"render_{uuid.uuid4().hex[:8]}.ass")
            captioner.write_ass_from_blocks(ass_path, blocks, resolved)

    # Resolve optional logo file
    logo_cfg = edit_state.get("logo") or {}
    logo_file: Optional[str] = None
    if logo_cfg.get("enabled") and logo_cfg.get("asset_url"):
        rel = str(logo_cfg["asset_url"]).lstrip("/")
        if rel.startswith("files/"):
            rel = rel[len("files/"):]
        candidate = os.path.join(tmp_dir, rel)
        if os.path.isfile(candidate):
            logo_file = candidate

    ffmpeg_path = ffbin.ffmpeg()
    cmd = [ffmpeg_path, "-y", "-i", clean_video_path]
    if logo_file:
        cmd.extend(["-i", logo_file])

    filter_parts: List[str] = []
    needs_trim_concat = (
        len(kept) > 1
        or kept[0][0] > 0.05
        or kept[0][1] < duration - 0.05
    )

    if needs_trim_concat:
        seg_labels = []
        for idx, (st, en) in enumerate(kept):
            filter_parts.append(
                f"[0:v]trim=start={st:.3f}:end={en:.3f},setpts=PTS-STARTPTS[v{idx}]"
            )
            filter_parts.append(
                f"[0:a]atrim=start={st:.3f}:end={en:.3f},asetpts=PTS-STARTPTS[a{idx}]"
            )
            seg_labels.append(f"[v{idx}][a{idx}]")
        filter_parts.append(
            f"{''.join(seg_labels)}concat=n={len(kept)}:v=1:a=1[vbase][aout]"
        )
        v_cur = "[vbase]"
        a_out = "[aout]"
    else:
        v_cur = "[0:v]"
        a_out = "0:a?"

    # Burn kinetic ASS captions
    if ass_path:
        safe_ass = ass_path.replace("\\", "/").replace(":", "\\:")
        filter_parts.append(f"{v_cur}ass='{safe_ass}'[vcap]")
        v_cur = "[vcap]"

    # Composite Brand Logo overlay
    if logo_file:
        scale_pct = max(4, min(60, int(logo_cfg.get("scale", 18))))
        logo_w = max(32, int(1080 * (scale_pct / 100.0)))
        opacity = max(0.05, min(1.0, float(logo_cfg.get("opacity", 92)) / 100.0))
        x_pct = max(0, min(100, float(logo_cfg.get("x", 84)))) / 100.0
        y_pct = max(0, min(100, float(logo_cfg.get("y", 7)))) / 100.0
        x_expr = f"max(0,min(W-w,(W*{x_pct:.3f})-w/2))"
        y_expr = f"max(0,min(H-h,(H*{y_pct:.3f})-h/2))"
        filter_parts.append(
            f"[1:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={opacity:.2f}[logo]"
        )
        filter_parts.append(f"{v_cur}[logo]overlay=x='{x_expr}':y='{y_expr}'[vlogo]")
        v_cur = "[vlogo]"

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_out = output_path + ".rendering.mp4"

    if filter_parts:
        cmd.extend([
            "-filter_complex", ";".join(filter_parts),
            "-map", v_cur,
            "-map", a_out,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            tmp_out,
        ])
    else:
        # No edits at all -> clean fast copy
        cmd.extend(["-c", "copy", "-movflags", "+faststart", tmp_out])

    def _exec() -> tuple[int, str]:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        return res.returncode, str(res.stderr)

    try:
        code, stderr = await asyncio.to_thread(_exec)
        if code != 0:
            raise RuntimeError(f"FFmpeg render failed: {stderr[-1200:]}")
        os.replace(tmp_out, output_path)
    finally:
        if ass_path and os.path.exists(ass_path):
            os.remove(ass_path)
        if os.path.exists(tmp_out):
            os.remove(tmp_out)

    return {
        "output_path": output_path,
        "duration": new_duration,
        "captioned": bool(ass_path),
    }


async def extract_waveform(video_path: str, num_bars: int = 200) -> List[float]:
    """Decode audio track into normalized RMS bars [0.05..1.0] for the timeline UI."""
    if not os.path.isfile(video_path):
        return [0.2] * num_bars

    cmd = [
        ffbin.ffmpeg(),
        "-i", video_path,
        "-vn", "-ac", "1", "-ar", "4000",
        "-f", "s16le", "pipe:1",
    ]

    def _run() -> List[float]:
        res = subprocess.run(cmd, capture_output=True, timeout=60)
        raw = res.stdout
        if not raw or len(raw) < 4:
            return [0.25] * num_bars
        sample_count = len(raw) // 2
        samples = struct.unpack(f"<{sample_count}h", raw[: sample_count * 2])
        chunk = max(1, sample_count // num_bars)
        bars: List[float] = []
        for i in range(num_bars):
            start = i * chunk
            seg = samples[start : start + chunk]
            if not seg:
                bars.append(0.05)
                continue
            rms = math.sqrt(sum(s * s for s in seg) / len(seg))
            bars.append(rms)
        peak = max(bars) or 1.0
        return [round(max(0.06, min(1.0, b / peak)), 3) for b in bars]

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return [0.25] * num_bars


async def detect_silences(
    video_path: str,
    noise_db: float = -32.0,
    min_duration: float = 0.5,
) -> List[Dict[str, Any]]:
    """Run FFmpeg silencedetect to identify removable dead-air gaps."""
    if not os.path.isfile(video_path):
        return []

    cmd = [
        ffbin.ffmpeg(),
        "-i", video_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]

    def _run() -> List[Dict[str, Any]]:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        silences: List[Dict[str, Any]] = []
        current_start: Optional[float] = None
        for line in res.stderr.splitlines():
            m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
            if m_start:
                current_start = float(m_start.group(1))
            m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
            if m_end and current_start is not None:
                en = float(m_end.group(1))
                # Leave a tiny 0.12s breathing margin so speech doesn't feel clipped
                cut_st = round(current_start + 0.08, 3)
                cut_en = round(en - 0.08, 3)
                if cut_en - cut_st >= 0.25:
                    silences.append({
                        "id": f"sil_{uuid.uuid4().hex[:8]}",
                        "start": cut_st,
                        "end": cut_en,
                        "reason": "silence",
                    })
                current_start = None
        return silences

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return []


def build_project_zip(project_id: str, project_title: str, clips: List[Dict[str, Any]], tmp_dir: str) -> str:
    """Bundle all clips in a project into a ZIP archive and return its path."""
    project_dir = os.path.join(tmp_dir, project_id)
    os.makedirs(project_dir, exist_ok=True)
    safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", project_title or "openclip").strip("_") or "openclip"
    zip_path = os.path.join(project_dir, f"{safe_title}_clips.zip")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, clip in enumerate(clips, start=1):
            rel = str(clip.get("file_path", "")).split("?")[0].lstrip("/")
            if rel.startswith("files/"):
                rel = rel[len("files/"):]
            abs_path = os.path.join(tmp_dir, rel)
            if os.path.isfile(abs_path):
                clip_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", clip.get("title") or f"clip_{idx}").strip("_")[:48]
                arcname = f"{idx:02d}_{clip_slug}.mp4"
                zf.write(abs_path, arcname=arcname)

    return zip_path
