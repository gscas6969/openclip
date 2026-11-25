"""
clip_boundaries.py — Sentence- and pause-aware clip boundary alignment.

Prevents clips from ending abruptly mid-sentence or mid-word by:
  1. Breaking overly long merged transcript segments into short, timestamped
     sentence/clause segments before sending them to the LLM.
  2. Snapping LLM-suggested clip start/end timestamps to natural transcript
     boundaries (sentence punctuation, speech pauses, and word endings) instead
     of blindly chopping or extending to min/max duration.
  3. Optionally checking the actual audio waveform around each clip's end via
     FFmpeg so clips whose transcript timestamps slightly lead speech still
     wait for the speaker to finish their final word/thought and land cleanly
     inside a natural pause.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from typing import Any, Optional

import numpy as np  # type: ignore

import ffbin  # type: ignore

logger = logging.getLogger(__name__)

_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.",
    "vs.", "etc.", "e.g.", "i.e.", "u.s.", "u.k.", "u.s.a.",
    "a.m.", "p.m.", "no.", "fig.", "inc.", "ltd.", "corp.",
}

_SENTENCE_END_RE = re.compile(r"[.!?]+[\"'”’)\]]*$")
_CLAUSE_END_RE = re.compile(r"[,;:—–-]+[\"'”’)\]]*$")
_DECIMAL_RE = re.compile(r"^\d+\.\d+$")

_AUDIO_SAMPLE_RATE = 16000
_AUDIO_FRAME_S = 0.02  # 20 ms RMS frames


def _is_sentence_end_token(word: str) -> bool:
    tok = (word or "").strip()
    if not tok or _DECIMAL_RE.match(tok):
        return False
    if not _SENTENCE_END_RE.search(tok):
        return False
    bare = tok.strip("\"'”’()[]").lower()
    if bare in _ABBREVIATIONS:
        return False
    # Ignore single-letter initials like "J." unless followed by pause (handled separately)
    if len(bare) == 2 and bare[0].isalpha() and bare[1] == ".":
        return False
    return True


def _is_clause_end_token(word: str) -> bool:
    tok = (word or "").strip()
    if not tok:
        return False
    return bool(_CLAUSE_END_RE.search(tok))


def prepare_segments_for_llm(
    transcript: list[dict[str, Any]],
    max_segment_seconds: float = 6.5,
    max_segment_words: int = 20,
    pause_break_seconds: float = 0.45,
) -> list[dict[str, Any]]:
    """
    Normalize a transcript so every segment is short and accurately timestamped.

    Older cached JSON3 transcripts (or long rolling segments) can merge minutes
    of speech into a single segment. Splitting them along their word timings at
    pauses and sentence boundaries ensures the LLM always sees timestamps every
    few seconds.
    """
    if not transcript:
        return []

    ordered = sorted(
        (seg for seg in transcript if str(seg.get("text", "")).strip()),
        key=lambda s: (float(s.get("start", 0.0)), float(s.get("end", 0.0))),
    )

    out: list[dict[str, Any]] = []
    for seg in ordered:
        st = float(seg.get("start", 0.0))
        en = max(st + 0.05, float(seg.get("end", st + 0.2)))
        text = str(seg.get("text", "")).strip()
        words = seg.get("words")

        if (
            words
            and isinstance(words, list)
            and (en - st > max_segment_seconds or len(words) > max_segment_words)
        ):
            clean_words = []
            for w in words:
                wt = str(w.get("word", "")).strip()
                if not wt:
                    continue
                ws = float(w.get("start", st))
                we = max(ws + 0.04, float(w.get("end", ws + 0.2)))
                clean_words.append({"word": wt, "start": round(ws, 3), "end": round(we, 3)})

            if not clean_words:
                continue

            clean_words.sort(key=lambda w: (w["start"], w["end"]))
            for i in range(len(clean_words) - 1):
                nxt_s = clean_words[i + 1]["start"]
                if clean_words[i]["end"] > nxt_s and nxt_s > clean_words[i]["start"] + 0.04:
                    clean_words[i]["end"] = round(nxt_s, 3)

            cur: list[dict[str, Any]] = []
            for w in clean_words:
                if cur:
                    gap = w["start"] - cur[-1]["end"]
                    span = w["end"] - cur[0]["start"]
                    if (
                        gap >= pause_break_seconds
                        or span > max_segment_seconds
                        or len(cur) >= max_segment_words
                    ):
                        out.append({
                            "start": cur[0]["start"],
                            "end": cur[-1]["end"],
                            "text": " ".join(x["word"] for x in cur),
                            "words": cur,
                        })
                        cur = []
                cur.append(w)
                if len(cur) >= 3 and _is_sentence_end_token(w["word"]):
                    out.append({
                        "start": cur[0]["start"],
                        "end": cur[-1]["end"],
                        "text": " ".join(x["word"] for x in cur),
                        "words": cur,
                    })
                    cur = []
            if cur:
                out.append({
                    "start": cur[0]["start"],
                    "end": cur[-1]["end"],
                    "text": " ".join(x["word"] for x in cur),
                    "words": cur,
                })
        else:
            entry: dict[str, Any] = {
                "start": round(st, 3),
                "end": round(en, 3),
                "text": text,
            }
            if words and isinstance(words, list):
                entry["words"] = words
            out.append(entry)

    # De-overlap segment boundaries without pushing start times forward
    out.sort(key=lambda s: (s["start"], s["end"]))
    for i in range(len(out) - 1):
        nxt_start = out[i + 1]["start"]
        if out[i]["end"] > nxt_start and nxt_start > out[i]["start"] + 0.05:
            out[i]["end"] = round(nxt_start, 3)

    return out


def _build_word_units(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Build a flat, strictly chronological list of word-level timing units from
    the transcript, annotated with pause gaps and punctuation boundary tiers.

    For segments lacking word-level timestamps, proportionally interpolates word
    positions inside the segment while marking segment boundaries explicitly.
    """
    if not transcript:
        return []

    ordered = sorted(
        (seg for seg in transcript if str(seg.get("text", "")).strip()),
        key=lambda s: (float(s.get("start", 0.0)), float(s.get("end", 0.0))),
    )

    units: list[dict[str, Any]] = []
    for seg_idx, seg in enumerate(ordered):
        seg_st = float(seg.get("start", 0.0))
        seg_en = max(seg_st + 0.05, float(seg.get("end", seg_st + 0.2)))
        if seg_idx + 1 < len(ordered):
            nxt_seg_st = float(ordered[seg_idx + 1].get("start", seg_en))
            if nxt_seg_st > seg_st + 0.05:
                seg_en = min(seg_en, nxt_seg_st)

        raw_words = seg.get("words")
        if raw_words and isinstance(raw_words, list):
            seg_words: list[dict[str, Any]] = []
            for w in raw_words:
                wt = str(w.get("word", "")).strip()
                if not wt:
                    continue
                ws = float(w.get("start", seg_st))
                we = max(ws + 0.04, float(w.get("end", ws + 0.2)))
                seg_words.append({"word": wt, "start": ws, "end": we})

            if not seg_words:
                continue
            seg_words.sort(key=lambda w: (w["start"], w["end"]))
            for idx, w in enumerate(seg_words):
                units.append({
                    "word": w["word"],
                    "start": round(w["start"], 3),
                    "end": round(w["end"], 3),
                    "is_seg_start": idx == 0,
                    "is_seg_end": idx == len(seg_words) - 1,
                    "exact": True,
                })
        else:
            tokens = str(seg.get("text", "")).strip().split()
            if not tokens:
                continue
            weights = [max(1, len(t.strip(".,!?;:\"'"))) + 2 for t in tokens]
            tot_w = float(sum(weights))
            span = max(0.08, seg_en - seg_st)
            cursor = seg_st
            for idx, (tok, wt) in enumerate(zip(tokens, weights)):
                dur = span * (wt / tot_w)
                w_end = seg_en if idx == len(tokens) - 1 else cursor + dur
                units.append({
                    "word": tok,
                    "start": round(cursor, 3),
                    "end": round(max(cursor + 0.04, w_end), 3),
                    "is_seg_start": idx == 0,
                    "is_seg_end": idx == len(tokens) - 1,
                    "exact": False,
                })
                cursor += dur

    if not units:
        return []

    units.sort(key=lambda u: (u["start"], u["end"]))

    # Clamp overlapping ends and cap runaway rolling-caption holds on last words
    for i in range(len(units)):
        if i + 1 < len(units):
            nxt_s = units[i + 1]["start"]
            if units[i]["end"] > nxt_s and nxt_s > units[i]["start"] + 0.04:
                units[i]["end"] = round(nxt_s, 3)
        # A single spoken word almost never lasts > 1.4s; cap stretched rolling holds
        # so real pauses after the word remain visible.
        if units[i]["end"] - units[i]["start"] > 1.4:
            units[i]["end"] = round(units[i]["start"] + 0.75, 3)

    # Annotate gaps and boundary quality tiers
    n = len(units)
    for i, u in enumerate(units):
        gap_before = u["start"] - units[i - 1]["end"] if i > 0 else 999.0
        gap_after = units[i + 1]["start"] - u["end"] if i + 1 < n else 999.0
        u["gap_before"] = round(max(0.0, gap_before), 3)
        u["gap_after"] = round(max(0.0, gap_after), 3)
        u["next_start"] = units[i + 1]["start"] if i + 1 < n else None

        sent_end = _is_sentence_end_token(u["word"])
        clause_end = _is_clause_end_token(u["word"])

        if sent_end or gap_after >= 0.55 or i == n - 1:
            end_tier = 3
        elif (clause_end and gap_after >= 0.10) or gap_after >= 0.28 or (not u["exact"] and u["is_seg_end"] and gap_after >= 0.15):
            end_tier = 2
        elif clause_end or gap_after >= 0.12 or u["is_seg_end"]:
            end_tier = 1
        else:
            end_tier = 0
        u["end_tier"] = end_tier

        prev_sent_end = (i == 0) or (units[i - 1]["end_tier"] >= 3)
        prev_clause_end = (i > 0) and (units[i - 1]["end_tier"] >= 2)
        if prev_sent_end or gap_before >= 0.55:
            start_tier = 3
        elif prev_clause_end or gap_before >= 0.25 or u["is_seg_start"]:
            start_tier = 2
        else:
            start_tier = 1
        u["start_tier"] = start_tier

    return units


def _safe_tail_pad(unit: dict[str, Any], video_duration: float) -> float:
    """
    Compute a clean ending timestamp after ``unit`` completes, adding natural
    breathing room without bleeding into the next spoken word.
    """
    u_end = float(unit["end"])
    gap_after = float(unit["gap_after"])
    next_start = unit.get("next_start")

    if next_start is None:
        tail = 0.35
    elif gap_after >= 0.45:
        tail = min(0.32, gap_after - 0.12)
    elif gap_after >= 0.14:
        tail = min(0.22, gap_after * 0.6)
    else:
        tail = min(0.04, gap_after * 0.5)

    snapped = u_end + max(0.0, tail)
    if next_start is not None:
        snapped = min(snapped, float(next_start) - 0.02)
    return round(min(video_duration, max(u_end, snapped)), 3)


def _safe_lead_in(unit: dict[str, Any]) -> float:
    """Add a subtle 0.06-0.10s pre-roll before a word without clipping into the previous word."""
    u_start = float(unit["start"])
    gap_before = float(unit["gap_before"])
    if gap_before >= 0.25:
        lead = 0.08
    elif gap_before >= 0.08:
        lead = min(0.06, gap_before * 0.45)
    else:
        lead = 0.0
    return round(max(0.0, u_start - lead), 3)


def _pick_best_end_unit(
    units: list[dict[str, Any]],
    target_end: float,
    lo_time: float,
    hi_time: float,
    prefer_forward: bool = True,
    soft_max_time: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """
    Select the highest-quality boundary unit whose ``end`` lies in [lo_time, hi_time].
    Strongly prefers sentence boundaries (tier 3) and pauses (tier 2) over mid-sentence cuts.
    """
    candidates = [u for u in units if lo_time <= u["end"] <= hi_time]
    if not candidates:
        return None

    def _score(u: dict[str, Any]) -> float:
        tier = u["end_tier"]
        dist = u["end"] - target_end
        # Strong tier hierarchy: finishing a sentence is far better than being 2s closer
        score = tier * 20.0
        # Extra reward for actual sentence punctuation combined with a pause
        if _is_sentence_end_token(u["word"]):
            score += 4.0
        if u["gap_after"] >= 0.35:
            score += min(4.0, u["gap_after"] * 3.0)

        # Distance penalty
        if dist >= 0:
            # Slight preference for completing the active sentence forward vs cutting early
            weight = 0.75 if prefer_forward else 1.2
            score -= dist * weight
        else:
            weight = 1.25 if prefer_forward else 0.85
            score -= abs(dist) * weight

        # Small penalty for exceeding soft_max_time (only cross it if no clean boundary exists inside)
        if soft_max_time is not None and u["end"] > soft_max_time:
            score -= 6.0 + (u["end"] - soft_max_time) * 1.5

        return score

    return max(candidates, key=_score)


def snap_clip_boundaries(
    start: float,
    end: float,
    transcript: list[dict[str, Any]],
    video_duration: float,
    min_dur: float,
    target_min: float,
    target_max: float,
    units: Optional[list[dict[str, Any]]] = None,
) -> tuple[float, float]:
    """
    Align a clip's [start, end] to natural speech boundaries while respecting
    duration targets without blindly chopping or extending mid-sentence.
    """
    start = max(0.0, float(start))
    end = min(float(video_duration), float(end))
    if end <= start:
        return round(start, 3), round(end, 3)

    if units is None:
        units = _build_word_units(transcript)
    if not units:
        # No transcript available: fall back to basic clamping
        dur = end - start
        if dur < target_min:
            end = min(start + target_min, video_duration)
        elif dur > target_max:
            end = start + target_max
        return round(start, 3), round(end, 3)

    grace = min(8.0, max(5.0, (target_max - target_min) * 0.2))
    hard_min = max(5.0, float(min_dur))
    soft_max = float(target_max)
    grace_max = float(target_max) + grace

    # --- 1. Snap start to avoid cutting into the middle of an opening word/sentence ---
    start_cands = [u for u in units if start - 1.5 <= u["start"] <= start + 1.2]
    # Check if start is currently mid-word
    mid_word = next((u for u in units if u["start"] < start < u["end"]), None)
    if start_cands:
        def _start_score(u: dict[str, Any]) -> float:
            dist = abs(u["start"] - start)
            s = u["start_tier"] * 6.0 - dist * 4.0
            if mid_word is not None and u is mid_word and (start - u["start"]) <= 0.5:
                s += 2.0
            return s

        best_start_u = max(start_cands, key=_start_score)
        candidate_start = _safe_lead_in(best_start_u)
        # Only adopt if it leaves enough room to reach hard_min
        if video_duration - candidate_start >= hard_min:
            start = candidate_start

    # --- 2. Determine target ending window based on raw duration ---
    raw_dur = end - start

    chosen_end_u: Optional[dict[str, Any]] = None

    if raw_dur > soft_max:
        # Clip is too long: instead of blindly chopping at start + target_max,
        # find the best sentence/pause boundary near target_max (allowing a small
        # grace finish if the original end itself finishes a sentence just past soft_max).
        if raw_dur <= grace_max:
            # Check if a clean boundary sits right around target_max or right at raw end
            lo = max(start + hard_min, start + soft_max - 12.0)
            hi = min(video_duration, max(end + 2.5, start + grace_max))
            chosen_end_u = _pick_best_end_unit(
                units,
                target_end=min(end, start + soft_max),
                lo_time=lo,
                hi_time=hi,
                prefer_forward=True,
                soft_max_time=start + soft_max,
            )
        else:
            # Clearly over max: trim back to the best sentence end <= soft_max (+ small grace)
            lo = max(start + hard_min, start + soft_max - 16.0)
            hi = min(video_duration, start + soft_max + 3.0)
            chosen_end_u = _pick_best_end_unit(
                units,
                target_end=start + soft_max,
                lo_time=lo,
                hi_time=hi,
                prefer_forward=False,
                soft_max_time=start + soft_max,
            )
    elif raw_dur < target_min:
        # Clip is shorter than target_min: try extending to the next clean sentence
        # boundary >= target_min without blindly stopping mid-sentence.
        ext_lo = start + target_min - 1.5
        ext_hi = min(video_duration, start + soft_max)
        ext_u = _pick_best_end_unit(
            units,
            target_end=start + target_min + 2.0,
            lo_time=ext_lo,
            hi_time=ext_hi,
            prefer_forward=True,
            soft_max_time=start + soft_max,
        )
        # Also check if original end is already a clean sentence end >= hard_min
        orig_u = _pick_best_end_unit(
            units,
            target_end=end,
            lo_time=max(start + hard_min, end - 1.5),
            hi_time=min(video_duration, end + 3.5),
            prefer_forward=True,
            soft_max_time=start + soft_max,
        )
        if ext_u is not None and ext_u["end_tier"] >= 2 and (ext_u["end"] - start) <= soft_max:
            chosen_end_u = ext_u
        elif orig_u is not None and orig_u["end_tier"] >= 3 and (orig_u["end"] - start) >= hard_min:
            # Keep the natural shorter ending rather than extending into a broken half-sentence
            chosen_end_u = orig_u
        else:
            chosen_end_u = ext_u or orig_u
    else:
        # Raw duration is within [target_min, target_max]: check whether `end`
        # already rests cleanly after a sentence end, or if we should finish
        # the active sentence cleanly.
        already_clean = next(
            (
                u for u in reversed(units)
                if u["end"] <= end + 0.15
                and end - u["end"] <= 0.75
                and u["end_tier"] >= 3
                and (u["next_start"] is None or u["next_start"] >= end - 0.05)
            ),
            None,
        )
        if already_clean is not None:
            chosen_end_u = already_clean
        else:
            # Search forward up to +10s (capped by grace_max) to finish the active sentence,
            # or slightly backward if there's an immediate sentence end right before `end`.
            lo = max(start + hard_min, end - 5.0)
            hi = min(video_duration, min(end + 10.0, start + grace_max))
            chosen_end_u = _pick_best_end_unit(
                units,
                target_end=end,
                lo_time=lo,
                hi_time=hi,
                prefer_forward=True,
                soft_max_time=start + soft_max,
            )

    if chosen_end_u is not None:
        snapped_end = _safe_tail_pad(chosen_end_u, video_duration)
        if snapped_end - start >= hard_min:
            return round(start, 3), round(snapped_end, 3)

    # Fallback: never cut mid-word even if no tier>=2 boundary was found
    covering = next((u for u in units if u["start"] <= end <= u["end"]), None)
    if covering is not None:
        end = _safe_tail_pad(covering, video_duration)

    return round(start, 3), round(min(video_duration, end), 3)


def _decode_audio_window(video_path: str, win_start: float, win_dur: float) -> np.ndarray:
    """Decode a short mono 16kHz PCM audio slice using FFmpeg."""
    if win_dur <= 0.1 or not os.path.isfile(video_path):
        return np.zeros(0, dtype=np.int16)

    cmd = [
        ffbin.ffmpeg(), "-v", "error",
        "-ss", f"{max(0.0, win_start):.3f}",
        "-i", video_path,
        "-t", f"{win_dur:.3f}",
        "-vn", "-ac", "1", "-ar", str(_AUDIO_SAMPLE_RATE),
        "-f", "s16le", "pipe:1",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=20)
        if res.returncode != 0 or not res.stdout:
            return np.zeros(0, dtype=np.int16)
        n_bytes = len(res.stdout) - (len(res.stdout) % 2)
        return np.frombuffer(res.stdout[:n_bytes], dtype=np.int16)
    except Exception as exc:
        logger.debug("Audio boundary probe failed: %s", exc)
        return np.zeros(0, dtype=np.int16)


def refine_end_with_audio(
    video_path: str,
    start: float,
    end: float,
    video_duration: float,
    max_end: Optional[float] = None,
    max_extend: float = 2.5,
) -> float:
    """
    Inspect the actual audio waveform around ``end``.

    If ``end`` lands while the speaker is still mid-word / mid-utterance (common
    when captions slightly lead audio or lack punctuation), wait for the speech
    energy to drop into the immediately following pause and place the cut cleanly
    inside that pause. If ``end`` is already in a pause or the track has uniform
    loud background music with no clear valleys, leaves ``end`` untouched.
    """
    hard_cap = min(float(video_duration), float(max_end) if max_end is not None else float(video_duration))
    if end >= hard_cap - 0.05 or not os.path.isfile(video_path):
        return round(end, 3)

    look_back = min(2.0, max(0.5, end - start))
    look_ahead = min(max_extend + 0.6, hard_cap - end)
    if look_ahead < 0.25:
        return round(end, 3)

    win_start = max(0.0, end - look_back)
    win_dur = (end - win_start) + look_ahead
    pcm = _decode_audio_window(video_path, win_start, win_dur)
    if pcm.size < int(_AUDIO_SAMPLE_RATE * 0.4):
        return round(end, 3)

    samples_per_frame = int(_AUDIO_FRAME_S * _AUDIO_SAMPLE_RATE)
    n_frames = pcm.size // samples_per_frame
    if n_frames < 10:
        return round(end, 3)

    x = pcm[: n_frames * samples_per_frame].astype(np.float32).reshape(n_frames, samples_per_frame)
    rms = np.sqrt((x * x).mean(axis=1))
    # 100ms moving average smooths brief consonant dips without hiding real pauses
    kernel = np.ones(5, dtype=np.float32) / 5.0
    smooth = np.convolve(rms, kernel, mode="same")

    p15 = float(np.percentile(smooth, 15))
    p85 = float(np.percentile(smooth, 85))
    # Skip refinement on pure silence or flat continuous loud music
    if p85 < 60.0 or p85 <= p15 * 1.6:
        return round(end, 3)

    speech_thresh = max(p15 + 0.22 * (p85 - p15), 45.0)

    end_idx = int(round((end - win_start) / _AUDIO_FRAME_S))
    end_idx = max(0, min(n_frames - 1, end_idx))

    # Check a small ±60ms neighborhood around `end` to see if speech is active right at the cut
    lo_chk = max(0, end_idx - 2)
    hi_chk = min(n_frames, end_idx + 3)
    at_end_energy = float(smooth[lo_chk:hi_chk].mean())

    if at_end_energy <= speech_thresh:
        # Already ending cleanly in a quiet pause!
        return round(end, 3)

    # Speech is active right at `end` -> walk forward to find the first clean pause (>= 140ms)
    min_pause_frames = 7  # 140 ms
    search_hi = min(n_frames, int(round((min(hard_cap, end + max_extend) - win_start) / _AUDIO_FRAME_S)))

    run_start: Optional[int] = None
    for idx in range(end_idx, search_hi):
        if smooth[idx] <= speech_thresh:
            if run_start is None:
                run_start = idx
        else:
            if run_start is not None and (idx - run_start) >= min_pause_frames:
                drop_t = win_start + run_start * _AUDIO_FRAME_S
                resume_t = win_start + idx * _AUDIO_FRAME_S
                pause_len = resume_t - drop_t
                refined = drop_t + min(0.28, max(0.10, pause_len * 0.55))
                refined = min(hard_cap, resume_t - 0.04, refined)
                if refined > end:
                    logger.info(
                        "Audio end refinement extended clip end %.2fs -> %.2fs (pause %.2fs)",
                        end, refined, pause_len,
                    )
                    return round(refined, 3)
            run_start = None

    # Handle pause that continues to the end of the search window
    if run_start is not None and (search_hi - run_start) >= min_pause_frames:
        drop_t = win_start + run_start * _AUDIO_FRAME_S
        pause_len = (win_start + search_hi * _AUDIO_FRAME_S) - drop_t
        refined = min(hard_cap, drop_t + min(0.28, max(0.12, pause_len * 0.6)))
        if refined > end:
            logger.info(
                "Audio end refinement extended clip end %.2fs -> %.2fs (trailing pause)",
                end, refined,
            )
            return round(refined, 3)

    return round(end, 3)


async def refine_suggestions_with_audio(
    suggestions: list[dict[str, Any]],
    video_path: Optional[str],
    video_duration: float,
    max_duration: int = 60,
    min_duration: int = 30,
) -> list[dict[str, Any]]:
    """Refine each validated clip's end timestamp against the source audio track."""
    if not suggestions or not video_path or not os.path.isfile(video_path):
        return suggestions

    grace = min(8.0, max(5.0, (max_duration - min_duration) * 0.2))
    ordered = sorted(suggestions, key=lambda c: float(c["start"]))

    for i, clip in enumerate(ordered):
        st = float(clip["start"])
        en = float(clip["end"])
        next_start = float(ordered[i + 1]["start"]) - 0.1 if i + 1 < len(ordered) else video_duration
        cap = min(video_duration, next_start, st + max_duration + grace + 1.5)
        if cap <= en + 0.1:
            continue
        new_end = await asyncio.to_thread(
            refine_end_with_audio, video_path, st, en, video_duration, cap, 2.5
        )
        if new_end > en:
            clip["end"] = new_end

    return ordered
