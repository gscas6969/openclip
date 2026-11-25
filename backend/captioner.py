"""
captioner.py — Kinetic ASS Caption Engine & Preset Library for OpenClip.

Provides 24 hosted-parity caption presets across Kinetic, Clean, and Vibrant
categories, 8 ASS animation modes (chase, sweep, stamp, jelly, glow,
highlight, word_by_word, one_word), contextual emoji injection, filler-word
tagging, and editable block generation for the browser Clip Editor.
"""

import asyncio
import os
import re
import subprocess
import uuid
from typing import Any, Dict, List, Optional

import ffbin  # type: ignore

# ---------------------------------------------------------------------------
# Color conversion helpers (#RRGGBB / #RRGGBBAA <-> ASS &HAABBGGRR)
# ---------------------------------------------------------------------------

def hex_to_ass(hex_color: Optional[str], default: str = "&H00FFFFFF") -> str:
    """Convert #RRGGBB or #RRGGBBAA (CSS) into ASS &HAABBGGRR (00=opaque, FF=transparent)."""
    if not hex_color:
        return default
    c = hex_color.strip()
    if c.startswith("&H"):
        return c
    if c.startswith("#"):
        c = c[1:]
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) == 6:
        r, g, b = c[0:2], c[2:4], c[4:6]
        return f"&H00{b}{g}{r}".upper()
    if len(c) == 8:
        r, g, b, a_hex = c[0:2], c[2:4], c[4:6], c[6:8]
        # CSS alpha FF = opaque -> ASS alpha 00 = opaque
        ass_alpha = 255 - int(a_hex, 16)
        return f"&H{ass_alpha:02X}{b}{g}{r}".upper()
    return default


def ass_to_hex(ass_color: Optional[str]) -> Optional[str]:
    """Convert ASS &HAABBGGRR into #RRGGBB (or #RRGGBBAA when translucent)."""
    if not ass_color:
        return None
    if ass_color.startswith("#"):
        return ass_color
    if ass_color.startswith("&H") and len(ass_color) >= 10:
        aa = int(ass_color[2:4], 16)
        bb = ass_color[4:6]
        gg = ass_color[6:8]
        rr = ass_color[8:10]
        if aa == 0:
            return f"#{rr}{gg}{bb}".upper()
        css_alpha = 255 - aa
        return f"#{rr}{gg}{bb}{css_alpha:02X}".upper()
    return None


# ---------------------------------------------------------------------------
# 24 Curated Caption Presets (Hosted OpenClip Parity)
# ---------------------------------------------------------------------------

CAPTION_STYLES: Dict[str, Dict[str, Any]] = {
    # --- KINETIC / MOTION ---
    "mozi_pop": {
        "name": "Mozi",
        "category": "kinetic",
        "font": "Arial Black",
        "fontsize": 96,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "outline_color": "#000000",
        "outline_width": 7,
        "shadow": 4,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "jelly",
        "max_chars_per_line": 14,
        "max_words_per_line": 3,
        "position_y": 74,
        "uppercase": True,
        "letter_spacing": 1,
    },
    "dan_bold": {
        "name": "Dan",
        "category": "kinetic",
        "font": "Impact",
        "fontsize": 102,
        "primary_color": "#FFFFFF",
        "highlight_color": "#3ECF8E",
        "outline_color": "#000000",
        "outline_width": 6,
        "shadow": 3,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "stamp",
        "max_chars_per_line": 15,
        "max_words_per_line": 3,
        "position_y": 75,
        "uppercase": True,
        "letter_spacing": 2,
    },
    "chase_pill": {
        "name": "Chase",
        "category": "kinetic",
        "font": "Arial Bold",
        "fontsize": 86,
        "primary_color": "#FFFFFF",
        "highlight_color": "#7C3AED",
        "outline_color": "#000000",
        "outline_width": 4,
        "shadow": 2,
        "background": True,
        "bg_color": "#0A0A0ACC",
        "animation": "chase",
        "max_chars_per_line": 18,
        "max_words_per_line": 4,
        "position_y": 76,
        "uppercase": False,
        "letter_spacing": 0,
    },
    "sweep_karaoke": {
        "name": "Sweep",
        "category": "kinetic",
        "font": "Arial Black",
        "fontsize": 90,
        "primary_color": "#E2E8F0",
        "highlight_color": "#38BDF8",
        "outline_color": "#0F172A",
        "outline_width": 6,
        "shadow": 2,
        "background": False,
        "bg_color": "#00000099",
        "animation": "sweep",
        "max_chars_per_line": 18,
        "max_words_per_line": 4,
        "position_y": 76,
        "uppercase": True,
        "letter_spacing": 1,
    },
    "stamp_impact": {
        "name": "Stamp",
        "category": "kinetic",
        "font": "Impact",
        "fontsize": 108,
        "primary_color": "#FFFFFF",
        "highlight_color": "#F97316",
        "outline_color": "#000000",
        "outline_width": 8,
        "shadow": 4,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "stamp",
        "max_chars_per_line": 12,
        "max_words_per_line": 2,
        "position_y": 72,
        "uppercase": True,
        "letter_spacing": 2,
    },
    "jelly_bounce": {
        "name": "Jelly",
        "category": "kinetic",
        "font": "Arial Black",
        "fontsize": 92,
        "primary_color": "#FFFFFF",
        "highlight_color": "#EC4899",
        "outline_color": "#1E1B4B",
        "outline_width": 6,
        "shadow": 3,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "jelly",
        "max_chars_per_line": 15,
        "max_words_per_line": 3,
        "position_y": 75,
        "uppercase": True,
        "letter_spacing": 1,
    },
    "neon_cyber": {
        "name": "Neon",
        "category": "kinetic",
        "font": "Arial Black",
        "fontsize": 92,
        "primary_color": "#E0F2FE",
        "highlight_color": "#22D3EE",
        "outline_color": "#083344",
        "outline_width": 5,
        "shadow": 5,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "glow",
        "max_chars_per_line": 16,
        "max_words_per_line": 3,
        "position_y": 75,
        "uppercase": True,
        "letter_spacing": 2,
    },
    "retro_gold": {
        "name": "Retro",
        "category": "kinetic",
        "font": "Georgia",
        "fontsize": 90,
        "primary_color": "#FEF3C7",
        "highlight_color": "#F59E0B",
        "outline_color": "#451A03",
        "outline_width": 6,
        "shadow": 4,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "stamp",
        "max_chars_per_line": 16,
        "max_words_per_line": 3,
        "position_y": 76,
        "uppercase": False,
        "letter_spacing": 1,
    },
    "chrome_ice": {
        "name": "Chrome",
        "category": "kinetic",
        "font": "Arial Black",
        "fontsize": 94,
        "primary_color": "#F8FAFC",
        "highlight_color": "#93C5FD",
        "outline_color": "#1E293B",
        "outline_width": 6,
        "shadow": 4,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "glow",
        "max_chars_per_line": 15,
        "max_words_per_line": 3,
        "position_y": 74,
        "uppercase": True,
        "letter_spacing": 2,
    },
    "viral_word": {
        "name": "Viral Word",
        "category": "kinetic",
        "font": "Arial Black",
        "fontsize": 106,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "outline_color": "#000000",
        "outline_width": 8,
        "shadow": 4,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "one_word",
        "max_chars_per_line": 10,
        "max_words_per_line": 1,
        "position_y": 72,
        "uppercase": True,
        "letter_spacing": 2,
    },
    "tiktok_style": {
        "name": "TikTok",
        "category": "kinetic",
        "font": "Arial Bold",
        "fontsize": 98,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "outline_color": "#000000",
        "outline_width": 7,
        "shadow": 2,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "highlight",
        "max_chars_per_line": 14,
        "max_words_per_line": 3,
        "position_y": 75,
        "uppercase": True,
        "letter_spacing": 1,
    },
    # --- CLEAN & EDITORIAL ---
    "classic_white": {
        "name": "Clean",
        "category": "clean",
        "font": "Arial Bold",
        "fontsize": 82,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 5,
        "shadow": 2,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "word_by_word",
        "max_chars_per_line": 18,
        "max_words_per_line": 4,
        "position_y": 78,
        "uppercase": False,
        "letter_spacing": 0,
    },
    "minimal_clean": {
        "name": "Minimal",
        "category": "clean",
        "font": "Arial",
        "fontsize": 58,
        "primary_color": "#FFFFFF",
        "highlight_color": "#E2E8F0",
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": 2,
        "background": False,
        "bg_color": "#00000088",
        "animation": "word_by_word",
        "max_chars_per_line": 28,
        "max_words_per_line": 6,
        "position_y": 82,
        "uppercase": False,
        "letter_spacing": 0,
    },
    "plain_box": {
        "name": "Plain Box",
        "category": "clean",
        "font": "Arial Bold",
        "fontsize": 74,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": 0,
        "background": True,
        "bg_color": "#000000CC",
        "animation": "highlight",
        "max_chars_per_line": 22,
        "max_words_per_line": 5,
        "position_y": 80,
        "uppercase": False,
        "letter_spacing": 0,
    },
    "quiet_lower": {
        "name": "Quiet",
        "category": "clean",
        "font": "Helvetica",
        "fontsize": 62,
        "primary_color": "#F1F5F9",
        "highlight_color": "#FFFFFF",
        "outline_color": "#0F172A",
        "outline_width": 3,
        "shadow": 1,
        "background": True,
        "bg_color": "#0F172AB8",
        "animation": "sweep",
        "max_chars_per_line": 26,
        "max_words_per_line": 5,
        "position_y": 83,
        "uppercase": False,
        "letter_spacing": 0,
    },
    "white_box": {
        "name": "White Box",
        "category": "clean",
        "font": "Arial Bold",
        "fontsize": 80,
        "primary_color": "#09090B",
        "highlight_color": "#2E1DED",
        "outline_color": "#FFFFFF",
        "outline_width": 0,
        "shadow": 0,
        "background": True,
        "bg_color": "#FFFFFFF2",
        "animation": "highlight",
        "max_chars_per_line": 16,
        "max_words_per_line": 4,
        "position_y": 78,
        "uppercase": False,
        "letter_spacing": 0,
    },
    "editorial_serif": {
        "name": "Editorial",
        "category": "clean",
        "font": "Georgia",
        "fontsize": 76,
        "primary_color": "#FAFAF9",
        "highlight_color": "#FDE68A",
        "outline_color": "#1C1917",
        "outline_width": 4,
        "shadow": 3,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "highlight",
        "max_chars_per_line": 20,
        "max_words_per_line": 4,
        "position_y": 79,
        "uppercase": False,
        "letter_spacing": 1,
    },
    "news_ticker": {
        "name": "Broadcast",
        "category": "clean",
        "font": "Arial Bold",
        "fontsize": 70,
        "primary_color": "#FFFFFF",
        "highlight_color": "#EF4444",
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": 0,
        "background": True,
        "bg_color": "#111827E6",
        "animation": "chase",
        "max_chars_per_line": 24,
        "max_words_per_line": 5,
        "position_y": 82,
        "uppercase": True,
        "letter_spacing": 1,
    },
    # --- VIBRANT / CREATOR ---
    "yellow_bold": {
        "name": "Yellow Bold",
        "category": "vibrant",
        "font": "Arial Black",
        "fontsize": 96,
        "primary_color": "#FFE600",
        "highlight_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 6,
        "shadow": 3,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "word_by_word",
        "max_chars_per_line": 14,
        "max_words_per_line": 3,
        "position_y": 75,
        "uppercase": True,
        "letter_spacing": 1,
    },
    "neon_green": {
        "name": "Neon Green",
        "category": "vibrant",
        "font": "Arial Black",
        "fontsize": 92,
        "primary_color": "#FFFFFF",
        "highlight_color": "#22FF66",
        "outline_color": "#052E16",
        "outline_width": 6,
        "shadow": 3,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "glow",
        "max_chars_per_line": 15,
        "max_words_per_line": 3,
        "position_y": 75,
        "uppercase": True,
        "letter_spacing": 1,
    },
    "highlight_yellow": {
        "name": "Highlight Yellow",
        "category": "vibrant",
        "font": "Arial Bold",
        "fontsize": 86,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "outline_color": "#000000",
        "outline_width": 4,
        "shadow": 0,
        "background": True,
        "bg_color": "#000000B3",
        "animation": "highlight",
        "max_chars_per_line": 16,
        "max_words_per_line": 4,
        "position_y": 76,
        "uppercase": False,
        "letter_spacing": 0,
    },
    "purple_glow": {
        "name": "Purple Glow",
        "category": "vibrant",
        "font": "Arial Black",
        "fontsize": 94,
        "primary_color": "#F5D0FE",
        "highlight_color": "#C084FC",
        "outline_color": "#2E1065",
        "outline_width": 6,
        "shadow": 4,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "glow",
        "max_chars_per_line": 15,
        "max_words_per_line": 3,
        "position_y": 75,
        "uppercase": True,
        "letter_spacing": 1,
    },
    "sunset_coral": {
        "name": "Sunset",
        "category": "vibrant",
        "font": "Arial Black",
        "fontsize": 92,
        "primary_color": "#FFF7ED",
        "highlight_color": "#FB7185",
        "outline_color": "#4C0519",
        "outline_width": 6,
        "shadow": 3,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "jelly",
        "max_chars_per_line": 15,
        "max_words_per_line": 3,
        "position_y": 75,
        "uppercase": True,
        "letter_spacing": 1,
    },
    "fire_red": {
        "name": "Fire",
        "category": "vibrant",
        "font": "Impact",
        "fontsize": 100,
        "primary_color": "#FFFFFF",
        "highlight_color": "#EF4444",
        "outline_color": "#000000",
        "outline_width": 7,
        "shadow": 4,
        "background": False,
        "bg_color": "#000000AA",
        "animation": "stamp",
        "max_chars_per_line": 13,
        "max_words_per_line": 3,
        "position_y": 74,
        "uppercase": True,
        "letter_spacing": 2,
    },
}

FILLER_WORDS = {
    "um", "umm", "uh", "uhh", "erm", "hmm", "hm", "ah", "ahh", "er",
}

EMOJI_KEYWORDS: Dict[str, str] = {
    "money": "💰", "dollar": "💵", "million": "💰", "billion": "💎", "rich": "🤑", "cash": "💸",
    "fire": "🔥", "crazy": "🤯", "insane": "🤯", "mind": "🧠", "brain": "🧠", "smart": "🧠",
    "secret": "🤫", "hack": "⚡", "fast": "⚡", "speed": "⚡", "power": "⚡", "energy": "⚡",
    "win": "🏆", "winner": "🏆", "best": "🏆", "champion": "🏆", "success": "🚀", "grow": "📈",
    "growth": "📈", "scale": "🚀", "launch": "🚀", "rocket": "🚀", "skyrocket": "🚀",
    "love": "❤️", "heart": "❤️", "happy": "😄", "funny": "😂", "laugh": "😂", "joke": "😂",
    "stop": "🛑", "mistake": "❌", "wrong": "❌", "fail": "💀", "dead": "💀", "warning": "⚠️",
    "truth": "💯", "fact": "💯", "real": "💯", "percent": "💯", "100": "💯",
    "time": "⏰", "hours": "⏰", "minutes": "⏱️", "wait": "⏳", "future": "🔮",
    "idea": "💡", "tip": "💡", "learn": "📚", "book": "📚", "ai": "🤖", "code": "💻",
    "video": "🎬", "camera": "🎥", "podcast": "🎙️", "music": "🎵", "phone": "📱",
    "world": "🌍", "global": "🌍", "star": "⭐", "gold": "🥇", "king": "👑", "boss": "👑",
    "eyes": "👀", "watch": "👀", "look": "👀", "listen": "👂", "talk": "🗣️",
    "build": "🛠️", "work": "💼", "business": "💼", "startup": "🚀", "team": "🤝",
}


def get_style_catalog() -> List[Dict[str, Any]]:
    """Return full style catalog formatted for the frontend editor & settings."""
    items = [
        {
            "key": "none",
            "name": "No Captions",
            "category": "clean",
            "animation": "none",
            "config": {},
            "preview_colors": {"text": "#64748B", "highlight": None, "background": None},
        }
    ]
    for key, s in CAPTION_STYLES.items():
        items.append({
            "key": key,
            "name": s["name"],
            "category": s.get("category", "kinetic"),
            "animation": s["animation"],
            "config": dict(s),
            "preview_colors": {
                "text": s["primary_color"],
                "highlight": s.get("highlight_color"),
                "background": s.get("bg_color") if s.get("background") else None,
            },
        })
    return items


def resolve_style(style_key: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = dict(CAPTION_STYLES.get(style_key, CAPTION_STYLES["mozi_pop"]))
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                base[k] = v
    return base


def _is_filler_word(word: str) -> bool:
    cleaned = re.sub(r"[^a-zA-Z]", "", word).lower()
    return cleaned in FILLER_WORDS


_NOISE_TAG_RE = re.compile(r"\[[^\]]+\]|\([^)]*music[^)]*\)|>>+", re.IGNORECASE)


def _clean_caption_text(text: str) -> str:
    cleaned = _NOISE_TAG_RE.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_clip_segments(transcript: list[dict], clip_start: float, clip_end: float) -> list[dict]:
    """
    De-overlap the FULL transcript BEFORE slicing to [clip_start, clip_end] so
    stale YouTube rolling captions spoken *before* clip_start are discarded
    instead of snowballing early clip captions late.
    """
    clip_dur = max(0.1, clip_end - clip_start)

    # Step 1: Clean and chronologically sort full transcript, then clamp rolling
    # end-times BEFORE slicing (crucial so a line spoken at clip_start - 3s whose
    # YouTube hold ends at clip_start + 0.4s is recognized as pre-clip!).
    full_clean: list[dict] = []
    for seg in transcript:
        txt = _clean_caption_text(str(seg.get("text", "")))
        if not txt:
            continue
        st = float(seg["start"])
        en = float(seg["end"])
        if en <= st + 0.03:
            continue
        entry: dict = {"start": st, "end": en, "text": txt}
        if seg.get("words"):
            entry["words"] = seg["words"]
        full_clean.append(entry)

    full_clean.sort(key=lambda s: (s["start"], s["end"]))
    for i in range(len(full_clean)):
        seg = full_clean[i]
        if i + 1 < len(full_clean):
            next_st = full_clean[i + 1]["start"]
            if next_st > seg["start"] + 0.05:
                seg["end"] = min(seg["end"], next_st)
        if not seg.get("words"):
            wc = max(1, len(seg["text"].split()))
            seg["end"] = min(seg["end"], seg["start"] + max(1.0, wc * 0.42))

    # Step 2: Slice strictly to what is actually spoken inside [clip_start, clip_end]
    result: list[dict] = []
    for seg in full_clean:
        st, en = seg["start"], seg["end"]
        if en <= clip_start + 0.05 or st >= clip_end:
            continue

        if seg.get("words"):
            adj_words = []
            for w in seg["words"]:
                w_txt = _clean_caption_text(str(w.get("word", "")))
                if not w_txt:
                    continue
                w_st = float(w["start"])
                w_en = float(w["end"])
                # Only keep words actually spoken inside the clip window
                if w_st < clip_start - 0.15 or w_st >= clip_end:
                    continue
                rel_st = round(max(0.0, w_st - clip_start), 3)
                rel_en = round(min(clip_dur, max(rel_st + 0.06, w_en - clip_start)), 3)
                adj_words.append({"word": w_txt, "start": rel_st, "end": rel_en})
            if adj_words:
                result.append({
                    "start": adj_words[0]["start"],
                    "end": adj_words[-1]["end"],
                    "text": " ".join(w["word"] for w in adj_words),
                    "words": adj_words,
                })
            continue

        # Segment-only (no word timestamps): drop segments spoken mostly before clip_start
        seg_dur = max(0.05, en - st)
        if st < clip_start:
            overlap_ratio = (en - clip_start) / seg_dur
            # If the segment started > 0.45s before the clip or less than half remains,
            # trim proportional leading words rather than shoving pre-clip words onto 0.0s.
            tokens = seg["text"].split()
            if overlap_ratio < 0.35 or (clip_start - st) > 1.2 or len(tokens) <= 1:
                continue
            skip_count = int(round((1.0 - overlap_ratio) * len(tokens)))
            kept_tokens = tokens[skip_count:]
            if not kept_tokens:
                continue
            rel_st = 0.0
            rel_en = round(min(clip_dur, en - clip_start), 3)
            result.append({"start": rel_st, "end": rel_en, "text": " ".join(kept_tokens)})
        else:
            rel_st = round(max(0.0, st - clip_start), 3)
            rel_en = round(min(clip_dur, en - clip_start), 3)
            if rel_en > rel_st + 0.05:
                result.append({"start": rel_st, "end": rel_en, "text": seg["text"]})

    # Step 3: Clamp backwards ONLY (never push a later segment's true start forward!)
    result.sort(key=lambda s: (s["start"], s["end"]))
    final_segs: list[dict] = []
    for seg in result:
        if final_segs and seg["start"] < final_segs[-1]["end"]:
            final_segs[-1]["end"] = round(max(final_segs[-1]["start"] + 0.05, seg["start"]), 3)
        if seg["end"] > seg["start"] + 0.05:
            final_segs.append(seg)

    return final_segs


def build_caption_blocks(
    transcript: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    max_chars: int = 16,
    max_words: int = 4,
) -> List[Dict[str, Any]]:
    """Build strictly monotonic, non-overlapping editable caption blocks."""
    segments = _extract_clip_segments(transcript, clip_start, clip_end)
    if not segments:
        return []

    raw_words: List[Dict[str, Any]] = []
    for seg in segments:
        if seg.get("words"):
            for w in seg["words"]:
                txt = _clean_caption_text(str(w["word"]))
                if not txt:
                    continue
                st = round(float(w["start"]), 3)
                en = round(max(float(w["end"]), st + 0.06), 3)
                raw_words.append({
                    "id": f"w_{uuid.uuid4().hex[:8]}",
                    "word": txt,
                    "start": st,
                    "end": en,
                    "deleted": False,
                    "is_filler": _is_filler_word(txt),
                })
        else:
            tokens = seg["text"].split()
            if not tokens:
                continue
            weights = [0.65 if len(t) <= 2 else (1.3 if len(t) >= 8 else 1.0) for t in tokens]
            total_w = sum(weights) or 1.0
            seg_dur = max(0.1, float(seg["end"]) - float(seg["start"]))
            t = float(seg["start"])
            for tok, weight in zip(tokens, weights):
                w_dur = seg_dur * (weight / total_w)
                raw_words.append({
                    "id": f"w_{uuid.uuid4().hex[:8]}",
                    "word": tok,
                    "start": round(t, 3),
                    "end": round(t + max(w_dur, 0.05), 3),
                    "deleted": False,
                    "is_filler": _is_filler_word(tok),
                })
                t += w_dur

    # Enforce strictly monotonic, non-overlapping word timeline
    raw_words.sort(key=lambda w: (w["start"], w["end"]))
    for i in range(len(raw_words)):
        if i > 0 and raw_words[i]["start"] < raw_words[i - 1]["end"]:
            # Clamp previous word end to current word start if possible, else advance start
            if raw_words[i]["start"] > raw_words[i - 1]["start"] + 0.04:
                raw_words[i - 1]["end"] = raw_words[i]["start"]
            else:
                raw_words[i]["start"] = raw_words[i - 1]["end"]
        if raw_words[i]["end"] <= raw_words[i]["start"] + 0.04:
            raw_words[i]["end"] = round(raw_words[i]["start"] + 0.08, 3)

    blocks: List[Dict[str, Any]] = []
    cur_words: List[Dict[str, Any]] = []
    cur_text = ""

    def _flush():
        nonlocal cur_words, cur_text
        if not cur_words:
            return
        blocks.append({
            "id": f"blk_{uuid.uuid4().hex[:8]}",
            "start": cur_words[0]["start"],
            "end": cur_words[-1]["end"],
            "text": " ".join(w["word"] for w in cur_words),
            "emoji": "",
            "words": cur_words,
        })
        cur_words = []
        cur_text = ""

    for w in raw_words:
        if cur_words and (w["start"] - cur_words[-1]["end"]) > 0.55:
            _flush()
        candidate = f"{cur_text} {w['word']}".strip()
        if cur_words and (len(candidate) > max_chars or len(cur_words) >= max_words):
            _flush()
        cur_words.append(w)
        cur_text = f"{cur_text} {w['word']}".strip()
        if len(cur_words) >= 2 and w["word"][-1:] in (".", "!", "?"):
            _flush()

    _flush()
    return blocks


def inject_emojis(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach contextual emojis to matching caption blocks (non-spammy cadence)."""
    updated = []
    last_emoji_idx = -3
    for idx, blk in enumerate(blocks):
        new_blk = dict(blk)
        if not new_blk.get("emoji") and (idx - last_emoji_idx) >= 2:
            tokens = re.findall(r"[a-zA-Z0-9]+", str(blk.get("text", "")).lower())
            matched = ""
            for tok in tokens:
                if tok in EMOJI_KEYWORDS:
                    matched = EMOJI_KEYWORDS[tok]
                    break
            if matched:
                new_blk["emoji"] = matched
                last_emoji_idx = idx
        updated.append(new_blk)
    return updated


def build_initial_edit_state(
    transcript: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    layout_mode: str = "dynamic_zoom",
    caption_style: str = "mozi_pop",
    brand_kit: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create the initial non-destructive edit_state JSON for a generated clip."""
    duration = round(max(0.5, clip_end - clip_start), 3)
    chosen_style = (brand_kit.get("caption_style") if brand_kit else None) or caption_style or "mozi_pop"
    overrides = dict(brand_kit.get("caption_overrides") or {}) if brand_kit else {}
    resolved = resolve_style(chosen_style if chosen_style != "none" else "mozi_pop", overrides)

    blocks = build_caption_blocks(
        transcript,
        clip_start,
        clip_end,
        max_chars=int(resolved.get("max_chars_per_line", 16)),
        max_words=int(resolved.get("max_words_per_line", 4)),
    )

    logo_cfg = dict(brand_kit.get("logo_config") or {}) if brand_kit else {}
    logo_path = brand_kit.get("logo_path") if brand_kit else None

    return {
        "trim_start": 0.0,
        "trim_end": duration,
        "cuts": [],
        "layout_mode": layout_mode or "dynamic_zoom",
        "caption_enabled": chosen_style != "none",
        "caption_style": chosen_style if chosen_style != "none" else "mozi_pop",
        "caption_overrides": {
            "font": resolved["font"],
            "fontsize": resolved["fontsize"],
            "primary_color": resolved["primary_color"],
            "highlight_color": resolved.get("highlight_color") or "#FFE600",
            "outline_color": resolved["outline_color"],
            "outline_width": resolved["outline_width"],
            "shadow": resolved["shadow"],
            "background": bool(resolved.get("background", False)),
            "bg_color": resolved.get("bg_color") or "#000000CC",
            "animation": resolved["animation"],
            "position_y": int(resolved.get("position_y", 75)),
            "uppercase": bool(resolved.get("uppercase", True)),
            "max_words_per_line": int(resolved.get("max_words_per_line", 3)),
            "letter_spacing": int(resolved.get("letter_spacing", 1)),
            "sync_offset_ms": int(resolved.get("sync_offset_ms", -120)),
        },
        "caption_blocks": blocks,
        "logo": {
            "enabled": bool(logo_path),
            "asset_url": logo_path,
            "x": int(logo_cfg.get("x", 84)),
            "y": int(logo_cfg.get("y", 7)),
            "scale": int(logo_cfg.get("scale", 18)),
            "opacity": int(logo_cfg.get("opacity", 92)),
        },
        "brand_kit_id": brand_kit.get("id") if brand_kit else None,
    }


# ---------------------------------------------------------------------------
# ASS File Generation (8 Kinetic Animations + Safe Positioning + Emojis)
# ---------------------------------------------------------------------------

def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def write_ass_from_blocks(
    ass_path: str,
    blocks: List[Dict[str, Any]],
    style: Dict[str, Any],
) -> None:
    def seconds_to_ass(t: float) -> str:
        t = max(0.0, float(t))
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    primary = hex_to_ass(style.get("primary_color"), "&H00FFFFFF")
    highlight = hex_to_ass(style.get("highlight_color"), "&H0000FFFF")
    outline = hex_to_ass(style.get("outline_color"), "&H00000000")
    bg_color = hex_to_ass(style.get("bg_color"), "&H66000000")
    outline_w = int(style.get("outline_width", 5))
    shadow = int(style.get("shadow", 2))
    fontsize = int(style.get("fontsize", 92))
    font = str(style.get("font", "Arial Black"))
    spacing = int(style.get("letter_spacing", 1))
    border_style = 4 if style.get("background") else 1
    back_colour = bg_color if style.get("background") else "&H66000000"
    uppercase = bool(style.get("uppercase", False))
    animation = str(style.get("animation", "highlight"))
    pos_y_pct = max(10, min(92, int(style.get("position_y", 75))))
    pos_y = int(1920 * (pos_y_pct / 100.0))
    sync_offset_s = float(style.get("sync_offset_ms", -120)) / 1000.0

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},{primary},{highlight},{outline},{back_colour},-1,0,0,0,100,100,{spacing},0,{border_style},{outline_w},{shadow},5,60,60,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # Collect raw (start, end, text) events first so we can strictly guarantee
    # zero overlapping Dialogue lines at the same screen coordinates.
    raw_events: List[tuple[float, float, str]] = []

    ordered_blocks = sorted(blocks, key=lambda b: float(b.get("start", 0.0)))

    for b_idx, block in enumerate(ordered_blocks):
        active_words = [w for w in block.get("words", []) if not w.get("deleted") and str(w.get("word", "")).strip()]
        if not active_words:
            continue
        active_words.sort(key=lambda w: float(w["start"]))

        emoji = str(block.get("emoji") or "").strip()
        next_block_start: Optional[float] = None
        for nb in ordered_blocks[b_idx + 1 :]:
            nb_words = [w for w in nb.get("words", []) if not w.get("deleted")]
            if nb_words:
                next_block_start = min(float(w["start"]) for w in nb_words)
                break

        def _fmt(w_str: str) -> str:
            t = w_str.upper() if uppercase else w_str
            return _ass_escape(t)

        def _word_end(i: int) -> float:
            w_start = float(active_words[i]["start"])
            if i + 1 < len(active_words):
                return max(w_start + 0.04, float(active_words[i + 1]["start"]))
            end_t = float(block.get("end", active_words[i]["end"]))
            if next_block_start is not None:
                end_t = min(end_t, next_block_start)
            return max(w_start + 0.06, min(end_t, w_start + 0.65))

        emoji_suffix = f" {emoji}" if emoji else ""

        if animation == "sweep":
            blk_start = max(0.0, float(active_words[0]["start"]) + sync_offset_s)
            blk_end = max(blk_start + 0.06, _word_end(len(active_words) - 1) + sync_offset_s)
            k_parts = []
            for i, wd in enumerate(active_words):
                dur_cs = max(4, int(round((_word_end(i) - float(wd["start"])) * 100)))
                k_parts.append(f"{{\\kf{dur_cs}}}{_fmt(wd['word'])}")
            text = f"{{\\pos(540,{pos_y})\\1c{highlight}&\\2c{primary}&}}{' '.join(k_parts)}{emoji_suffix}"
            raw_events.append((blk_start, blk_end, text))
            continue

        if animation == "one_word":
            for i, wd in enumerate(active_words):
                st = max(0.0, float(wd["start"]) + sync_offset_s)
                en = max(st + 0.05, _word_end(i) + sync_offset_s)
                e_tag = emoji_suffix if i == len(active_words) - 1 else ""
                text = (
                    f"{{\\pos(540,{pos_y})\\fscx85\\fscy85"
                    f"\\t(0,90,\\fscx112\\fscy112)\\t(90,170,\\fscx100\\fscy100)}}"
                    f"{_fmt(wd['word'])}{e_tag}"
                )
                raw_events.append((st, en, text))
            continue

        if animation == "word_by_word":
            shown: list[str] = []
            for i, wd in enumerate(active_words):
                shown.append(_fmt(wd["word"]))
                st = max(0.0, float(wd["start"]) + sync_offset_s)
                en = max(st + 0.05, _word_end(i) + sync_offset_s)
                e_tag = emoji_suffix if i == len(active_words) - 1 else ""
                text = f"{{\\pos(540,{pos_y})}}{' '.join(shown)}{e_tag}"
                raw_events.append((st, en, text))
            continue

        # Multi-word kinetic animations: highlight, chase, stamp, jelly, glow
        for i, wd in enumerate(active_words):
            st = max(0.0, float(wd["start"]) + sync_offset_s)
            en = max(st + 0.05, _word_end(i) + sync_offset_s)
            parts: list[str] = []
            for j, w_item in enumerate(active_words):
                w_fmt = _fmt(w_item["word"])
                if j == i:
                    if animation == "stamp":
                        parts.append(
                            f"{{\\1c{highlight}&\\fscx130\\fscy130\\t(0,100,\\fscx106\\fscy106)}}{w_fmt}"
                            f"{{\\1c{primary}&\\fscx100\\fscy100}}"
                        )
                    elif animation == "jelly":
                        parts.append(
                            f"{{\\1c{highlight}&\\fscx78\\fscy78\\t(0,75,\\fscx118\\fscy118)\\t(75,160,\\fscx104\\fscy104)}}{w_fmt}"
                            f"{{\\1c{primary}&\\fscx100\\fscy100}}"
                        )
                    elif animation == "glow":
                        parts.append(
                            f"{{\\1c{highlight}&\\3c{highlight}&\\blur5\\fscx108\\fscy108}}{w_fmt}"
                            f"{{\\1c{primary}&\\3c{outline}&\\blur0\\fscx100\\fscy100}}"
                        )
                    elif animation == "chase":
                        parts.append(
                            f"{{\\1c{highlight}&\\bord{outline_w + 2}\\fscx110\\fscy110\\t(0,80,\\fscx105\\fscy105)}}{w_fmt}"
                            f"{{\\1c{primary}&\\bord{outline_w}\\fscx100\\fscy100}}"
                        )
                    else:  # highlight
                        parts.append(
                            f"{{\\1c{highlight}&\\bord{outline_w + 1}\\fscx106\\fscy106}}{w_fmt}"
                            f"{{\\1c{primary}&\\bord{outline_w}\\fscx100\\fscy100}}"
                        )
                else:
                    parts.append(w_fmt)

            text = f"{{\\pos(540,{pos_y})}}{' '.join(parts)}{emoji_suffix}"
            raw_events.append((st, en, text))

    # Strict non-overlap pass: guarantee at most ONE Dialogue line at any timestamp
    raw_events.sort(key=lambda ev: ev[0])
    lines = [header]
    for idx, (st, en, txt) in enumerate(raw_events):
        if idx + 1 < len(raw_events):
            next_st = raw_events[idx + 1][0]
            if en > next_st:
                en = next_st
        if en - st < 0.03:
            continue
        lines.append(f"Dialogue: 0,{seconds_to_ass(st)},{seconds_to_ass(en)},Default,,0,0,0,,{txt}\n")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


async def _burn_ass_to_video(input_path: str, ass_path: str, output_path: str) -> None:
    safe_ass_path = ass_path.replace("\\", "/").replace(":", "\\:")
    prefix_cmd = [
        ffbin.ffmpeg(), "-y",
        "-i", input_path,
        "-vf", f"ass='{safe_ass_path}'",
    ]
    suffix_cmd = [
        "-c:a", "copy",
        output_path,
    ]

    def _run_ffmpeg() -> tuple[int, str]:
        res = ffbin.run_ffmpeg_encode(
            prefix_cmd,
            suffix_cmd,
            quality="standard",
            timeout=900,
            text=True,
        )
        return res.returncode, str(res.stderr)

    returncode, stderr_output = await asyncio.to_thread(_run_ffmpeg)
    if returncode != 0:
        raise RuntimeError(f"Caption burn failed: {stderr_output[-1000:]}")


async def burn_captions(
    video_path: str,
    full_transcript: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    style_key: str,
    overrides: Optional[Dict[str, Any]] = None,
    output_path: Optional[str] = None,
) -> str:
    """Burn captions non-destructively into a separate output path."""
    if style_key == "none":
        return video_path

    style = resolve_style(style_key, overrides)
    blocks = build_caption_blocks(
        full_transcript,
        clip_start,
        clip_end,
        max_chars=int(style.get("max_chars_per_line", 16)),
        max_words=int(style.get("max_words_per_line", 4)),
    )
    if not blocks:
        return video_path

    ass_path = video_path + ".ass"
    write_ass_from_blocks(ass_path, blocks, style)

    out = output_path or video_path.replace("_clean.mp4", "_export.mp4")
    if out == video_path:
        out = video_path.replace(".mp4", "_captioned.mp4")

    try:
        await _burn_ass_to_video(video_path, ass_path, out)
    finally:
        if os.path.exists(ass_path):
            os.remove(ass_path)

    return out
