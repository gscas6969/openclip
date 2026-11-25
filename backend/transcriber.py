"""Transcript extraction pipeline.

Priority order:
1) User-uploaded/manual YouTube captions
2) Auto-generated YouTube captions
3) yt-dlp subtitle extraction fallback
4) Gemini Transcribe Live (cloud speech-to-text, needs a Gemini key)
5) Local Whisper fallback
"""

import asyncio
import glob
import json
import logging
import os
import subprocess
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

import ffbin  # type: ignore
import gemini_transcriber  # type: ignore

logger = logging.getLogger(__name__)


def _resolve_ytdlp_exe() -> str:
    """Resolve yt-dlp from the running virtualenv first, then PATH."""
    return ffbin.ytdlp()


def _notify(
    progress_callback: Optional[Callable[[float, str], None]],
    percent: float,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(percent, message)


def _extract_video_id(youtube_url: str) -> str:
    """Extract canonical YouTube video id from common URL formats."""
    parsed = urlparse(youtube_url)
    host = parsed.netloc.lower()

    if "youtu.be" in host:
        return parsed.path.strip("/")

    if "youtube.com" in host or "m.youtube.com" in host:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/shorts/")[-1].split("/")[0]
        if parsed.path.startswith("/live/"):
            return parsed.path.split("/live/")[-1].split("/")[0]
        if parsed.path.startswith("/embed/"):
            return parsed.path.split("/embed/")[-1].split("/")[0]

    return ""


def _normalize_transcript_items(items: list[object]) -> list[dict]:
    segments: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            start = float(item.get("start", 0.0))
            duration = float(item.get("duration", 0.0))
        else:
            text = str(getattr(item, "text", "")).strip()
            start = float(getattr(item, "start", 0.0))
            duration = float(getattr(item, "duration", 0.0))

        if not text:
            continue

        end = max(start + duration, start + 0.2)
        segments.append({"start": start, "end": end, "text": text})

    # De-overlap rolling YouTube 2-line caption display durations so segment[i]
    # ends when segment[i+1] begins instead of staying onscreen on top of it.
    segments.sort(key=lambda s: (s["start"], s["end"]))
    for i in range(len(segments) - 1):
        next_start = segments[i + 1]["start"]
        if next_start > segments[i]["start"] + 0.05 and segments[i]["end"] > next_start:
            segments[i]["end"] = next_start

    return segments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_captions(
    youtube_url: str,
    video_path: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    gemini_api_key: str = "",
    whisper_model: str = "base",
) -> list[dict]:
    """
    Extract a transcript for the given video.

    Strategy: try YouTube manual captions first, then auto captions,
    fall back to local Whisper transcription if captions are unavailable.

    Args:
        youtube_url:       Original YouTube URL (used to fetch captions).
        video_path:        Absolute path to the downloaded video file (used for Whisper fallback).

    Returns:
        A list of segment dicts::

            [{"start": 0.0, "end": 5.2, "text": "Hey welcome back..."}, ...]
    """
    if youtube_url and not youtube_url.startswith("upload://"):
        # Prefer yt-dlp JSON3 subtitles first because they include exact
        # word-level tOffsetMs timings (tighter karaoke/highlight sync!).
        _notify(progress_callback, 10, "Checking word-level YouTube captions...")
        result = await _get_manual_captions(youtube_url)
        if result:
            _notify(progress_callback, 40, "Using word-level manual YouTube captions")
            return result

        _notify(progress_callback, 35, "Checking word-level auto captions...")
        result = await _get_auto_captions(youtube_url)
        if result:
            _notify(progress_callback, 65, "Using word-level auto YouTube captions")
            return result

        _notify(progress_callback, 70, "Fallback: Transcript API manual captions...")
        logger.info("Trying transcript API manual captions...")
        result = await _get_transcript_api_captions(youtube_url, generated=False)
        if result:
            logger.info("Found transcript API manual captions (%d segments)", len(result))
            _notify(progress_callback, 82, "Using user-provided captions")
            return result

        _notify(progress_callback, 85, "Fallback: Transcript API auto captions...")
        logger.info("Trying transcript API auto captions...")
        result = await _get_transcript_api_captions(youtube_url, generated=True)
        if result:
            logger.info("Found transcript API auto captions (%d segments)", len(result))
            _notify(progress_callback, 90, "Using auto-generated captions")
            return result

    # Local upload or no YouTube captions: transcribe audio directly.
    # Transcribe Live runs in the cloud (fast, nothing heavy to install);
    # local Whisper is the fallback when there's no Gemini key or it fails.
    gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    if gemini_api_key:
        _notify(progress_callback, 91, "No YouTube captions found. Transcribing with Gemini Live...")
        try:
            def live_progress(pct: float, msg: str) -> None:
                _notify(progress_callback, 91 + pct * 0.09, msg)

            segments = await gemini_transcriber.transcribe(video_path, gemini_api_key, live_progress)
            if segments:
                _notify(progress_callback, 100, "Gemini Live transcription complete")
                return segments
            logger.warning("Gemini Live returned no speech")
        except Exception as exc:
            logger.warning("Gemini Live transcription failed: %s", exc)
            _notify(progress_callback, 92, f"Warning: Gemini Live failed ({exc}). Trying Whisper...")

    _notify(progress_callback, 92, "Running Whisper speech-to-text...")
    logger.info("Falling back to Whisper transcription...")
    segments = await _get_whisper_transcript(video_path, whisper_model)
    if segments:
        _notify(progress_callback, 100, "Whisper transcription complete")
    return segments


async def _get_transcript_api_captions(youtube_url: str, generated: bool) -> list[dict]:
    """Fetch captions via YouTube Transcript API (fast path)."""

    def _fetch() -> list[dict]:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
        except Exception:
            logger.info("youtube-transcript-api not installed")
            return []

        video_id = _extract_video_id(youtube_url)
        if not video_id:
            logger.warning("Could not parse YouTube video id from URL: %s", youtube_url)
            return []

        try:
            if hasattr(YouTubeTranscriptApi, "list_transcripts"):
                # youtube-transcript-api < 1.0 (class method)
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            else:
                # 1.x removed list_transcripts(); calling it raised and was
                # swallowed, so this fast path silently never worked.
                transcript_list = YouTubeTranscriptApi().list(video_id)
        except Exception as exc:
            logger.info("Transcript API list failed: %s", exc)
            return []

        language_candidates = ["en", "en-US", "en-GB"]

        try:
            if generated:
                transcript = transcript_list.find_generated_transcript(language_candidates)
            else:
                transcript = transcript_list.find_manually_created_transcript(language_candidates)
            return _normalize_transcript_items(list(transcript.fetch()))
        except Exception:
            pass

        # Fallback: use any transcript matching generated/manual mode.
        try:
            for transcript in transcript_list:
                is_generated = bool(getattr(transcript, "is_generated", False))
                if is_generated == generated:
                    return _normalize_transcript_items(list(transcript.fetch()))
        except Exception:
            return []

        return []

    return await asyncio.to_thread(_fetch)


# ---------------------------------------------------------------------------
# Strategy 1: YouTube captions via yt-dlp
# ---------------------------------------------------------------------------

async def _get_manual_captions(youtube_url: str) -> list[dict]:
    """
    Use yt-dlp to download manual creator captions as a ``.vtt`` file.
    """
    import tempfile
    work_dir = tempfile.mkdtemp(prefix="openclip_subs_")
    output_template = os.path.join(work_dir, "%(id)s")

    cmd = [
        _resolve_ytdlp_exe(),
        "--no-playlist",
        "--ignore-config",
        "--no-warnings",
        "--write-sub",
        "--skip-download",
        "--js-runtimes", "node",
        "--extractor-args", "youtube:player_client=web,default",
        "--sub-format", "json3",
        "--sub-langs", "en.*,en",
        "--socket-timeout", "30",
        "-o", output_template,
        youtube_url,
    ]

    env = os.environ.copy()
    # Add local bin directory to PATH for bundled ffmpeg
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    bin_dir = os.path.join(backend_dir, "bin")
    env["PATH"] = env.get("PATH", "") + os.pathsep + bin_dir

    try:
        logger.info("Running yt-dlp for manual captions...")
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=60,  # type: ignore
            encoding="utf-8", errors="replace", env=env
        )
        logger.info("yt-dlp manual captions exited with code %d", result.returncode)
        if result.returncode != 0:
            return []
        json_files = glob.glob(os.path.join(work_dir, "*.json3"))
        if not json_files:
            logger.info("No manual caption files found")
            return []
        return _parse_json3(json_files[0])
    except subprocess.TimeoutExpired:
        logger.warning("YouTube manual caption extraction timed out")
        return []
    except Exception as exc:
        logger.warning("YouTube manual caption extraction failed: %s", exc)
        return []

async def _get_auto_captions(youtube_url: str) -> list[dict]:
    """
    Use yt-dlp to download auto-generated captions as a ``.vtt`` file.
    """
    import tempfile
    work_dir = tempfile.mkdtemp(prefix="openclip_subs_")
    output_template = os.path.join(work_dir, "%(id)s")

    cmd = [
        _resolve_ytdlp_exe(),
        "--no-playlist",
        "--ignore-config",
        "--no-warnings",
        "--write-auto-sub",
        "--skip-download",
        "--js-runtimes", "node",
        "--extractor-args", "youtube:player_client=web,default",
        "--sub-format", "json3",
        "--sub-langs", "en.*,en",
        "--socket-timeout", "30",
        "-o", output_template,
        youtube_url,
    ]

    env = os.environ.copy()
    # Add local bin directory to PATH for bundled ffmpeg
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    bin_dir = os.path.join(backend_dir, "bin")
    env["PATH"] = env.get("PATH", "") + os.pathsep + bin_dir

    try:
        logger.info("Running yt-dlp for auto-captions: %s", " ".join(cmd))
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=90,  # type: ignore
            encoding="utf-8", errors="replace", env=env,
        )
        logger.info("yt-dlp auto-captions exited with code %d", result.returncode)
        if result.returncode != 0:
            logger.warning("yt-dlp stderr: %s", result.stderr[:500] if result.stderr else "none")
            return []
        json_files = glob.glob(os.path.join(work_dir, "*.json3"))
        if not json_files:
            logger.warning("No JSON3 subtitle files found in %s", work_dir)
            return []
        return _parse_json3(json_files[0])
    except subprocess.TimeoutExpired:
        logger.warning("YouTube auto caption extraction timed out after 90s")
        return []
    except Exception as exc:
        logger.warning("YouTube auto caption extraction failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# JSON3 parser
# ---------------------------------------------------------------------------

def _estimate_word_duration(word: str) -> float:
    """Estimate natural spoken duration for a single word when exact end time is missing."""
    clean_len = len(word.strip(".,!?;:\"'()[]"))
    return max(0.22, min(0.85, 0.14 + clean_len * 0.065))


def _group_words_into_segments(
    words: list[dict],
    max_segment_seconds: float = 6.0,
    max_segment_words: int = 18,
    pause_break_seconds: float = 0.45,
) -> list[dict]:
    """
    Group a chronological word list into short, sentence- and pause-aligned
    transcript segments so downstream LLM prompting gets frequent, accurate
    timestamps instead of giant multi-minute merged walls of text.
    """
    if not words:
        return []

    segments: list[dict] = []
    cur: list[dict] = []

    def _flush() -> None:
        nonlocal cur
        if not cur:
            return
        segments.append({
            "start": round(float(cur[0]["start"]), 3),
            "end": round(float(cur[-1]["end"]), 3),
            "text": " ".join(w["word"] for w in cur),
            "words": cur,
        })
        cur = []

    for w in words:
        if cur:
            gap = float(w["start"]) - float(cur[-1]["end"])
            span = float(w["end"]) - float(cur[0]["start"])
            if (
                gap >= pause_break_seconds
                or span > max_segment_seconds
                or len(cur) >= max_segment_words
            ):
                _flush()

        cur.append(w)

        # Break immediately after sentence-ending punctuation (avoiding short abbreviations)
        tok = w["word"]
        if len(cur) >= 3 and tok.rstrip("\"'”’)]").endswith((".", "!", "?")):
            bare = tok.strip("\"'”’()[]").lower()
            if bare not in {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "u.s.", "u.k."}:
                _flush()

    _flush()
    return segments


def _parse_json3(json_path: str) -> list[dict]:
    """
    Parse a YouTube JSON3 subtitle file into clean, de-overlapped transcript
    segments with accurate word timings and preserved speech pauses.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_events = [
        ev for ev in data.get("events", [])
        if isinstance(ev, dict) and ev.get("segs")
    ]
    raw_events.sort(key=lambda ev: float(ev.get("tStartMs", 0)))

    all_words: list[dict] = []

    for ev_idx, event in enumerate(raw_events):
        t_start_ms = float(event.get("tStartMs", 0))
        d_duration_ms = float(event.get("dDurationMs", 0))

        # Look ahead to next non-empty event start so rolling display holds
        # (typically 4-6s on YouTube auto-captions) don't smear across pauses.
        next_ev_start_s: Optional[float] = None
        for nxt in raw_events[ev_idx + 1:]:
            nxt_segs = nxt.get("segs") or []
            if any(str(s.get("utf8", "")).strip() for s in nxt_segs):
                cand = float(nxt.get("tStartMs", 0)) / 1000.0
                if cand > t_start_ms / 1000.0 + 0.02:
                    next_ev_start_s = cand
                    break

        # Filter out pure-whitespace / newline tokens before lookahead
        valid_segs: list[tuple[str, float, bool]] = []
        for s in event["segs"]:
            raw_text = str(s.get("utf8", "")).replace("\n", " ").strip()
            if not raw_text:
                continue
            has_offset = "tOffsetMs" in s and s["tOffsetMs"] is not None
            offset_ms = float(s.get("tOffsetMs", 0)) if has_offset else 0.0
            valid_segs.append((raw_text, offset_ms, has_offset))

        if not valid_segs:
            continue

        has_word_offsets = any(has_off for _, _, has_off in valid_segs) and len(valid_segs) > 1

        if has_word_offsets:
            for i, (tok_text, offset_ms, _) in enumerate(valid_segs):
                word_start = (t_start_ms + offset_ms) / 1000.0
                tokens = tok_text.split()
                if not tokens:
                    continue

                if i + 1 < len(valid_segs):
                    next_word_start = (t_start_ms + valid_segs[i + 1][1]) / 1000.0
                    # Cap gap inside a single word so an internal pause stays visible
                    est_span = sum(_estimate_word_duration(t) for t in tokens)
                    word_end = min(next_word_start, word_start + max(est_span * 1.4, 0.9))
                else:
                    est_span = sum(_estimate_word_duration(t) for t in tokens)
                    # Do NOT stretch to YouTube's rolling display dDurationMs
                    max_allowed = word_start + est_span
                    if d_duration_ms > offset_ms + 80:
                        ev_end = (t_start_ms + d_duration_ms) / 1000.0
                        max_allowed = min(max_allowed, ev_end)
                    if next_ev_start_s is not None and next_ev_start_s > word_start + 0.05:
                        max_allowed = min(max_allowed, next_ev_start_s)
                    word_end = max_allowed

                word_end = max(word_start + 0.06, word_end)
                if len(tokens) == 1:
                    all_words.append({
                        "word": tokens[0],
                        "start": round(word_start, 3),
                        "end": round(word_end, 3),
                    })
                else:
                    weights = [max(1, len(t)) + 1 for t in tokens]
                    tot_w = sum(weights)
                    cursor = word_start
                    span = word_end - word_start
                    for tok, wt in zip(tokens, weights):
                        dur = span * (wt / tot_w)
                        all_words.append({
                            "word": tok,
                            "start": round(cursor, 3),
                            "end": round(max(cursor + 0.05, cursor + dur), 3),
                        })
                        cursor += dur
        else:
            # Manual/block caption event without per-word offsets
            joined = " ".join(txt for txt, _, _ in valid_segs)
            tokens = joined.split()
            if not tokens:
                continue
            ev_start_s = t_start_ms / 1000.0
            est_total = sum(_estimate_word_duration(t) for t in tokens)
            if d_duration_ms > 100:
                ev_end_s = min(ev_start_s + d_duration_ms / 1000.0, ev_start_s + max(est_total * 1.6, 1.0))
            else:
                ev_end_s = ev_start_s + est_total
            if next_ev_start_s is not None and next_ev_start_s > ev_start_s + 0.08:
                ev_end_s = min(ev_end_s, next_ev_start_s)
            ev_end_s = max(ev_start_s + 0.15, ev_end_s)

            weights = [max(1, len(t)) + 1 for t in tokens]
            tot_w = sum(weights)
            cursor = ev_start_s
            span = ev_end_s - ev_start_s
            for tok, wt in zip(tokens, weights):
                dur = span * (wt / tot_w)
                all_words.append({
                    "word": tok,
                    "start": round(cursor, 3),
                    "end": round(max(cursor + 0.05, cursor + dur), 3),
                })
                cursor += dur

    # Strictly chronological, non-overlapping words
    all_words.sort(key=lambda w: (w["start"], w["end"]))
    for i in range(len(all_words) - 1):
        nxt_start = all_words[i + 1]["start"]
        if all_words[i]["end"] > nxt_start and nxt_start > all_words[i]["start"] + 0.04:
            all_words[i]["end"] = round(nxt_start, 3)

    segments = _group_words_into_segments(all_words)
    logger.info("Parsed %d segments (%d words) from JSON3 file", len(segments), len(all_words))
    return segments


# ---------------------------------------------------------------------------
# Strategy 2: Local Whisper transcription
# ---------------------------------------------------------------------------

async def _get_whisper_transcript(
    video_path: str,
    whisper_model: str = "base",
) -> list[dict]:
    """
    Transcribe a video locally using OpenAI's open-source Whisper model.
    """
    def _do_transcribe() -> list[dict]:
        try:
            import whisper  # type: ignore[import-untyped]
        except ImportError:
            logger.error("openai-whisper is not installed — cannot transcribe")
            return []
            
        model = whisper.load_model(whisper_model or "base")
        try:
            result = model.transcribe(video_path, word_timestamps=True)
        except Exception:
            result = model.transcribe(video_path)

        segments: list[dict] = []
        for seg in result.get("segments", []):
            entry: dict = {
                "start": float(seg["start"]),
                "end": float(seg["end"]),
                "text": seg["text"].strip(),
            }
            raw_words = seg.get("words") or []
            if raw_words:
                words = []
                for w in raw_words:
                    w_text = str(w.get("word", "")).strip()
                    if not w_text:
                        continue
                    words.append({
                        "word": w_text,
                        "start": float(w.get("start", entry["start"])),
                        "end": float(w.get("end", entry["end"])),
                    })
                if words:
                    entry["words"] = words
            segments.append(entry)

        return segments

    segments: list[dict] = await asyncio.to_thread(_do_transcribe)  # type: ignore
    logger.info("Whisper produced %d segments", len(segments))
    return segments
