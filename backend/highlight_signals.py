"""
highlight_signals.py — Multi-signal highlight detector for videos & ended livestreams.

Combines three lightweight, local/YouTube signals to pinpoint funny, chaotic,
and high-engagement moments even when comedy or hype isn't obvious from plain
transcript text alone:
  1. YouTube "Most Replayed" heatmap peaks (via yt-dlp metadata JSON)
  2. YouTube Live Chat replay velocity & humor/reaction keyword spikes
     (e.g., "LMAO", "HAHA", "KEKW", "CLIP IT", "💀", "😂") for ended VODs
  3. Audio energy / laughter / hype peak detection (via fast 4 kHz FFmpeg PCM)
     that works on every video including local uploads and streams without chat

Every extractor degrades gracefully — if a signal isn't available or times out,
the remaining signals still run and the pipeline never fails.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from typing import Any, Callable, Optional

import numpy as np  # type: ignore

import ffbin  # type: ignore

logger = logging.getLogger(__name__)

_REACTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("lmao", re.compile(r"\b(?:lmf?ao+|rofl)\b", re.I)),
    ("lol", re.compile(r"\b(?:lo+l+|lul+z?|omegalul|kekw?)\b", re.I)),
    ("haha", re.compile(r"\b(?:(?:ha){2,}|(?:he){2,}|bahaha+|hahaha+|jaja+)\b", re.I)),
    ("clip it", re.compile(r"\b(?:clip\s*(?:it|that|this)|clipped)\b", re.I)),
    ("no way", re.compile(r"\b(?:no\s+way+|ain'?t\s+no\s+way|wtf+|bro+|bruh+)\b", re.I)),
    ("dead/crying", re.compile(r"\b(?:i'?m\s+dead|i'?m\s+crying|so\s+funny|hilarious|cooked|wild|insane)\b", re.I)),
    ("emoji-laugh", re.compile(r"[😂🤣💀😭🔥😱🤯]")),
    ("W", re.compile(r"\b(?:w{2,}|W\s+stream|W\s+clip)\b")),
]

_AUDIO_SAMPLE_RATE = 4000


def _ytdlp_env() -> dict[str, str]:
    env = os.environ.copy()
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    bin_dir = os.path.join(backend_dir, "bin")
    env["PATH"] = env.get("PATH", "") + os.pathsep + bin_dir
    return env


# ---------------------------------------------------------------------------
# Signal 1: YouTube metadata + "Most Replayed" heatmap
# ---------------------------------------------------------------------------

def _fetch_youtube_metadata(youtube_url: str) -> dict[str, Any]:
    """Fetch lightweight yt-dlp JSON metadata (was_live, is_live, heatmap, live_chat)."""
    if not youtube_url or youtube_url.startswith("upload://"):
        return {}

    cmd = [
        ffbin.ytdlp(),
        "--dump-single-json",
        "--skip-download",
        "--no-playlist",
        "--ignore-config",
        "--no-warnings",
        "--js-runtimes", "node",
        "--extractor-args", "youtube:player_client=web,default",
        "--socket-timeout", "25",
        youtube_url,
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            env=_ytdlp_env(),
        )
        if res.returncode != 0 or not res.stdout.strip():
            logger.debug("yt-dlp metadata fetch skipped/failed (code %d)", res.returncode)
            return {}
        data = json.loads(res.stdout)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("yt-dlp metadata fetch failed: %s", exc)
        return {}


def parse_heatmap_peaks(
    heatmap: Optional[list[dict[str, Any]]],
    video_duration: float,
    max_peaks: int = 20,
) -> list[dict[str, Any]]:
    """Find standout 'Most Replayed' peaks from YouTube heatmap bins."""
    if not heatmap or not isinstance(heatmap, list):
        return []

    bins: list[tuple[float, float, float]] = []
    for item in heatmap:
        if not isinstance(item, dict):
            continue
        try:
            st = float(item.get("start_time", 0.0))
            en = float(item.get("end_time", st))
            val = float(item.get("value", 0.0))
            if en > st and 0.0 <= val <= 1.05:
                bins.append((st, min(en, video_duration) if video_duration > 0 else en, val))
        except (TypeError, ValueError):
            continue

    if len(bins) < 3:
        return []

    values = np.array([b[2] for b in bins], dtype=np.float32)
    med = float(np.median(values))
    threshold = max(0.55, min(0.82, med + 0.22))

    raw_peaks: list[dict[str, Any]] = []
    for st, en, val in bins:
        if val < threshold:
            continue
        if raw_peaks and st <= raw_peaks[-1]["end"] + 15.0:
            raw_peaks[-1]["end"] = max(raw_peaks[-1]["end"], en)
            if val > raw_peaks[-1]["value"]:
                raw_peaks[-1]["value"] = round(val, 3)
                raw_peaks[-1]["peak_time"] = round((st + en) / 2.0, 1)
        else:
            raw_peaks.append({
                "start": round(st, 1),
                "end": round(en, 1),
                "peak_time": round((st + en) / 2.0, 1),
                "value": round(val, 3),
                "label": f"MOST REPLAYED ({int(round(val * 100))}%)",
            })

    for p in raw_peaks:
        p["label"] = f"MOST REPLAYED ({int(round(p['value'] * 100))}%)"

    raw_peaks.sort(key=lambda p: p["value"], reverse=True)
    return sorted(raw_peaks[:max_peaks], key=lambda p: p["start"])


# ---------------------------------------------------------------------------
# Signal 2: YouTube Live Chat replay velocity & humor spikes
# ---------------------------------------------------------------------------

def _extract_chat_message_text(action: dict[str, Any]) -> tuple[Optional[float], str]:
    """Extract (offset_seconds, message_text) from one live_chat replay JSON line."""
    replay = action.get("replayChatItemAction")
    if not isinstance(replay, dict):
        return None, ""

    raw_msec = replay.get("videoOffsetTimeMsec")
    if raw_msec is None:
        return None, ""
    try:
        offset_s = float(raw_msec) / 1000.0
    except (TypeError, ValueError):
        return None, ""

    parts: list[str] = []
    for sub in replay.get("actions") or []:
        if not isinstance(sub, dict):
            continue
        add_item = sub.get("addChatItemAction")
        if not isinstance(add_item, dict):
            continue
        item = add_item.get("item")
        if not isinstance(item, dict):
            continue
        renderer = (
            item.get("liveChatTextMessageRenderer")
            or item.get("liveChatPaidMessageRenderer")
            or item.get("liveChatMembershipItemRenderer")
        )
        if not isinstance(renderer, dict):
            continue
        msg = renderer.get("message")
        if not isinstance(msg, dict):
            continue
        for run in msg.get("runs") or []:
            if not isinstance(run, dict):
                continue
            if "text" in run:
                parts.append(str(run["text"]))
            elif "emoji" in run and isinstance(run["emoji"], dict):
                shortcuts = run["emoji"].get("shortcuts") or []
                if shortcuts:
                    parts.append(str(shortcuts[0]))
                elif run["emoji"].get("emojiId"):
                    parts.append(str(run["emoji"]["emojiId"]))

    return offset_s, " ".join(parts).strip()


def parse_live_chat_spikes(
    chat_json_path: str,
    video_duration: float,
    bin_seconds: int = 10,
    max_spikes: int = 25,
) -> list[dict[str, Any]]:
    """
    Stream-parse a yt-dlp ``.live_chat.json`` NDJSON file and find moments
    where chat velocity or laughter/reaction keywords surge above baseline.

    Chat messages typically trail the on-stream joke/event by 5–12 seconds,
    so each returned hotspot window starts ~12s before the chat burst.
    """
    if not os.path.isfile(chat_json_path):
        return []

    bin_counts: Counter[int] = Counter()
    bin_reactions: Counter[int] = Counter()
    bin_keywords: dict[int, Counter[str]] = {}
    max_bin = int(max(video_duration, 10.0) // bin_seconds) + 2

    total_msgs = 0
    try:
        with open(chat_json_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                offset_s, text = _extract_chat_message_text(obj)
                if offset_s is None or offset_s < 0 or (video_duration > 0 and offset_s > video_duration + 30):
                    continue
                b = int(offset_s // bin_seconds)
                if b < 0 or b > max_bin:
                    continue
                bin_counts[b] += 1
                total_msgs += 1

                if text:
                    matched_any = False
                    for kw_label, pat in _REACTION_PATTERNS:
                        if pat.search(text):
                            matched_any = True
                            bin_keywords.setdefault(b, Counter())[kw_label] += 1
                    if matched_any:
                        bin_reactions[b] += 1
    except OSError as exc:
        logger.debug("Failed reading live chat file: %s", exc)
        return []

    if total_msgs < 25 or len(bin_counts) < 4:
        return []

    n_bins = max(bin_counts.keys()) + 1
    counts_arr = np.zeros(n_bins, dtype=np.float32)
    react_arr = np.zeros(n_bins, dtype=np.float32)
    for b, c in bin_counts.items():
        counts_arr[b] = float(c)
    for b, r in bin_reactions.items():
        react_arr[b] = float(r)

    nonzero = counts_arr[counts_arr > 0]
    base_rate = max(2.0, float(np.median(nonzero))) if nonzero.size else 2.0
    nz_react = react_arr[react_arr > 0]
    base_react = max(1.0, float(np.median(nz_react))) if nz_react.size else 1.0

    raw_spikes: list[dict[str, Any]] = []
    for b in range(n_bins):
        c = float(counts_arr[b])
        r = float(react_arr[b])
        ratio = c / base_rate
        react_ratio = r / base_react
        is_vel_spike = c >= max(6.0, base_rate * 2.2)
        is_react_spike = r >= max(4.0, base_react * 2.5) and c >= 4.0
        if not (is_vel_spike or is_react_spike):
            continue

        b_start = float(b * bin_seconds)
        b_end = float((b + 1) * bin_seconds)
        strength = max(ratio, react_ratio * 1.15)
        kws = bin_keywords.get(b, Counter())

        if raw_spikes and b_start <= raw_spikes[-1]["_raw_end"] + bin_seconds:
            cur = raw_spikes[-1]
            cur["_raw_end"] = b_end
            cur["count"] += int(c)
            cur["reaction_count"] += int(r)
            cur["_kw_counter"].update(kws)
            if strength > cur["ratio"]:
                cur["ratio"] = round(strength, 2)
                cur["peak_time"] = round(b_start + bin_seconds / 2.0, 1)
        else:
            raw_spikes.append({
                "_raw_start": b_start,
                "_raw_end": b_end,
                "peak_time": round(b_start + bin_seconds / 2.0, 1),
                "ratio": round(strength, 2),
                "count": int(c),
                "reaction_count": int(r),
                "_kw_counter": Counter(kws),
            })

    spikes: list[dict[str, Any]] = []
    for sp in raw_spikes:
        top_kws = [k for k, _ in sp["_kw_counter"].most_common(3)]
        win_start = max(0.0, sp["_raw_start"] - 12.0)
        win_end = min(video_duration, sp["_raw_end"] + 2.0) if video_duration > 0 else sp["_raw_end"] + 2.0
        kw_note = f": {', '.join(top_kws)}" if top_kws else ""
        spikes.append({
            "start": round(win_start, 1),
            "end": round(win_end, 1),
            "peak_time": sp["peak_time"],
            "ratio": sp["ratio"],
            "count": sp["count"],
            "reaction_count": sp["reaction_count"],
            "top_keywords": top_kws,
            "label": f"CHAT SPIKE ({sp['ratio']:.1f}x{kw_note})",
        })

    spikes.sort(key=lambda s: (s["ratio"] + min(2.0, s["reaction_count"] / 15.0)), reverse=True)
    return sorted(spikes[:max_spikes], key=lambda s: s["start"])


def _download_and_parse_live_chat(youtube_url: str, video_duration: float) -> list[dict[str, Any]]:
    """Download live_chat replay via yt-dlp into a temp folder and extract spikes."""
    work_dir = tempfile.mkdtemp(prefix="openclip_chat_")
    try:
        output_template = os.path.join(work_dir, "%(id)s")
        cmd = [
            ffbin.ytdlp(),
            "--no-playlist",
            "--ignore-config",
            "--no-warnings",
            "--skip-download",
            "--write-subs",
            "--sub-langs", "live_chat",
            "--js-runtimes", "node",
            "--extractor-args", "youtube:player_client=web,default",
            "--socket-timeout", "30",
            "-o", output_template,
            youtube_url,
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=75,
            env=_ytdlp_env(),
        )
        if res.returncode != 0:
            logger.debug("yt-dlp live_chat download exited with code %d", res.returncode)
            return []
        chat_files = glob.glob(os.path.join(work_dir, "*.live_chat.json"))
        if not chat_files:
            return []
        spikes = parse_live_chat_spikes(chat_files[0], video_duration)
        logger.info("Detected %d live chat reaction spikes", len(spikes))
        return spikes
    except subprocess.TimeoutExpired:
        logger.info("Live chat replay download timed out after 75s; continuing without chat spikes")
        return []
    except Exception as exc:
        logger.debug("Live chat replay extraction skipped: %s", exc)
        return []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Signal 3: Audio hype / laughter / loud reaction peaks
# ---------------------------------------------------------------------------

def detect_audio_peaks_from_rms(
    rms_per_sec: np.ndarray,
    video_duration: float,
    max_peaks: int = 20,
) -> list[dict[str, Any]]:
    """Detect sudden loud audio bursts (laughter, shouting, hype reactions) from 1s RMS values."""
    n = len(rms_per_sec)
    if n < 15:
        return []

    global_med = float(np.median(rms_per_sec))
    global_p90 = float(np.percentile(rms_per_sec, 90))
    if global_p90 < 80.0 or global_p90 <= global_med * 1.15:
        return []

    zscores = np.zeros(n, dtype=np.float32)
    half_win = 30
    for i in range(n):
        lo = max(0, i - half_win)
        hi = min(n, i + half_win + 1)
        win = rms_per_sec[lo:hi]
        med = float(np.median(win))
        mad = float(np.median(np.abs(win - med)))
        scale = max(mad * 1.4826, global_med * 0.25, 40.0)
        zscores[i] = (float(rms_per_sec[i]) - med) / scale

    raw_peaks: list[dict[str, Any]] = []
    for sec in range(n):
        z = float(zscores[sec])
        if z < 2.2 or float(rms_per_sec[sec]) < global_med * 1.35:
            continue
        if raw_peaks and sec <= raw_peaks[-1]["_raw_end"] + 5:
            cur = raw_peaks[-1]
            cur["_raw_end"] = sec + 1
            if z > cur["zscore"]:
                cur["zscore"] = round(z, 2)
                cur["peak_time"] = float(sec)
        else:
            raw_peaks.append({
                "_raw_start": float(sec),
                "_raw_end": float(sec + 1),
                "peak_time": float(sec),
                "zscore": round(z, 2),
            })

    peaks: list[dict[str, Any]] = []
    for p in raw_peaks:
        dur = p["_raw_end"] - p["_raw_start"]
        if dur < 2.0 and p["zscore"] < 3.4:
            continue
        st = max(0.0, p["_raw_start"] - 6.0)
        en = min(video_duration, p["_raw_end"] + 2.0) if video_duration > 0 else p["_raw_end"] + 2.0
        peaks.append({
            "start": round(st, 1),
            "end": round(en, 1),
            "peak_time": p["peak_time"],
            "zscore": p["zscore"],
            "label": f"AUDIO HYPE/LAUGHTER (+{p['zscore']:.1f}σ)",
        })

    cap = max(max_peaks, int(video_duration // 240)) if video_duration > 0 else max_peaks
    peaks.sort(key=lambda p: p["zscore"], reverse=True)
    return sorted(peaks[:cap], key=lambda p: p["start"])


def _extract_audio_peaks(video_path: str, video_duration: float) -> list[dict[str, Any]]:
    """Decode lightweight 4 kHz mono PCM from video_path and detect audio reaction spikes."""
    if not video_path or not os.path.isfile(video_path):
        return []

    cmd = [
        ffbin.ffmpeg(), "-v", "error",
        "-i", video_path,
        "-vn", "-ac", "1", "-ar", str(_AUDIO_SAMPLE_RATE),
        "-f", "s16le", "pipe:1",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=180)
        if res.returncode != 0 or not res.stdout:
            return []
        n_bytes = len(res.stdout) - (len(res.stdout) % 2)
        pcm = np.frombuffer(res.stdout[:n_bytes], dtype=np.int16)
        n_secs = pcm.size // _AUDIO_SAMPLE_RATE
        if n_secs < 15:
            return []
        blocks = pcm[: n_secs * _AUDIO_SAMPLE_RATE].astype(np.float32).reshape(n_secs, _AUDIO_SAMPLE_RATE)
        rms = np.sqrt((blocks * blocks).mean(axis=1))
        peaks = detect_audio_peaks_from_rms(rms, video_duration)
        logger.info("Detected %d audio hype/laughter peaks", len(peaks))
        return peaks
    except Exception as exc:
        logger.debug("Audio peak detection skipped: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Unified signal fusion & public API
# ---------------------------------------------------------------------------

def fuse_hotspots(
    heatmap_peaks: list[dict[str, Any]],
    chat_spikes: list[dict[str, Any]],
    audio_peaks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge overlapping/nearby signals from all sources into ranked highlight hotspots."""
    items: list[dict[str, Any]] = []
    for p in heatmap_peaks:
        items.append({
            "start": float(p["start"]),
            "end": float(p["end"]),
            "score": float(p.get("value", 0.7)) * 3.0,
            "source": "most_replayed",
            "label": p["label"],
        })
    for c in chat_spikes:
        bonus = min(4.0, float(c.get("ratio", 2.0))) + (1.0 if c.get("top_keywords") else 0.0)
        items.append({
            "start": float(c["start"]),
            "end": float(c["end"]),
            "score": bonus,
            "source": "chat_spike",
            "label": c["label"],
        })
    for a in audio_peaks:
        items.append({
            "start": float(a["start"]),
            "end": float(a["end"]),
            "score": min(3.0, float(a.get("zscore", 2.2)) * 0.75),
            "source": "audio_hype",
            "label": a["label"],
        })

    if not items:
        return []

    items.sort(key=lambda x: x["start"])
    merged: list[dict[str, Any]] = []
    for it in items:
        if merged and it["start"] <= merged[-1]["end"] + 10.0:
            cur = merged[-1]
            cur["end"] = max(cur["end"], it["end"])
            cur["score"] += it["score"]
            if it["source"] not in cur["sources"]:
                cur["sources"].append(it["source"])
                cur["score"] += 1.5
            if it["label"] not in cur["labels"]:
                cur["labels"].append(it["label"])
        else:
            merged.append({
                "start": it["start"],
                "end": it["end"],
                "score": it["score"],
                "sources": [it["source"]],
                "labels": [it["label"]],
            })

    hotspots: list[dict[str, Any]] = []
    for m in merged:
        hotspots.append({
            "start": round(m["start"], 1),
            "end": round(m["end"], 1),
            "score": round(m["score"], 2),
            "sources": m["sources"],
            "summary": " + ".join(m["labels"][:3]),
        })
    return hotspots


async def extract_highlight_signals(
    youtube_url: str,
    video_path: str,
    video_duration: float,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> dict[str, Any]:
    """
    Gather Most Replayed heatmap, Live Chat spikes (for ended livestreams),
    and audio hype/laughter peaks in parallel where possible.
    """
    def _notify(pct: float, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)

    _notify(51, "Scanning audio reactions & YouTube engagement signals…")

    meta_task = asyncio.to_thread(_fetch_youtube_metadata, youtube_url)
    audio_task = asyncio.to_thread(_extract_audio_peaks, video_path, video_duration)
    meta, audio_peaks = await asyncio.gather(meta_task, audio_task)

    was_live = bool(meta.get("was_live", False))
    is_live = bool(meta.get("is_live", False))
    heatmap_peaks = parse_heatmap_peaks(meta.get("heatmap"), video_duration)

    has_live_chat = "live_chat" in (meta.get("subtitles") or {})
    chat_spikes: list[dict[str, Any]] = []
    if youtube_url and not youtube_url.startswith("upload://") and (was_live or has_live_chat):
        _notify(53, "Livestream VOD detected — analyzing live chat reaction spikes…")
        chat_spikes = await asyncio.to_thread(_download_and_parse_live_chat, youtube_url, video_duration)

    hotspots = fuse_hotspots(heatmap_peaks, chat_spikes, audio_peaks)
    logger.info(
        "Highlight signals: was_live=%s heatmap=%d chat_spikes=%d audio_peaks=%d fused=%d",
        was_live, len(heatmap_peaks), len(chat_spikes), len(audio_peaks), len(hotspots),
    )

    return {
        "was_live": was_live,
        "is_live": is_live,
        "heatmap_peaks": heatmap_peaks,
        "chat_spikes": chat_spikes,
        "audio_peaks": audio_peaks,
        "hotspots": hotspots,
    }


# ---------------------------------------------------------------------------
# LLM Prompt Annotation & Candidate Enrichment Helpers
# ---------------------------------------------------------------------------

def get_chunk_hotspots(
    chunk_start: float,
    chunk_end: float,
    signals: Optional[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return top hotspots overlapping [chunk_start, chunk_end]."""
    if not signals:
        return []
    hotspots = signals.get("hotspots") or []
    matching = [
        h for h in hotspots
        if float(h["end"]) >= chunk_start and float(h["start"]) <= chunk_end
    ]
    matching.sort(key=lambda h: float(h.get("score", 0.0)), reverse=True)
    return sorted(matching[:limit], key=lambda h: float(h["start"]))


def format_chunk_signals_block(
    chunk_start: float,
    chunk_end: float,
    signals: Optional[dict[str, Any]],
) -> str:
    """Format a compact HIGHLIGHT SIGNALS block for the LLM chunk prompt."""
    chunk_hotspots = get_chunk_hotspots(chunk_start, chunk_end, signals)
    if not chunk_hotspots:
        return ""
    lines = [
        "AUDIENCE & AUDIO HIGHLIGHT SIGNALS IN THIS SECTION (pay special attention to moments near these timestamps):"
    ]
    for h in chunk_hotspots:
        lines.append(f"- [{h['start']:.1f}s-{h['end']:.1f}s] {h['summary']}")
    return "\n".join(lines) + "\n\n"


def line_signal_tag(
    seg_start: float,
    seg_end: float,
    chunk_hotspots: list[dict[str, Any]],
) -> str:
    """Return an inline marker if a transcript line overlaps a strong hotspot."""
    if not chunk_hotspots:
        return ""
    for h in chunk_hotspots:
        if float(h["end"]) >= seg_start and float(h["start"]) <= seg_end:
            return f" [HOTSPOT: {h['summary']}]"
    return ""


def enrich_candidates_with_signals(
    candidates: list[dict[str, Any]],
    signals: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach matching highlight signal tags & boost scores for the Reduce step."""
    if not candidates or not signals:
        return candidates

    hotspots = signals.get("hotspots") or []
    if not hotspots:
        return candidates

    enriched: list[dict[str, Any]] = []
    for c in candidates:
        item = dict(c)
        st = float(item.get("start", 0.0))
        en = float(item.get("end", 0.0))
        matched = [
            h for h in hotspots
            if float(h["end"]) >= st - 4.0 and float(h["start"]) <= en + 6.0
        ]
        if matched:
            best = max(matched, key=lambda h: float(h.get("score", 0.0)))
            item["signal_summary"] = best["summary"]
            item["signal_boost"] = round(min(2.5, float(best.get("score", 0.0)) * 0.4), 2)
        else:
            item.setdefault("signal_summary", "")
            item.setdefault("signal_boost", 0.0)
        enriched.append(item)
    return enriched
