"""
discover.py — Trending YouTube Video Discovery & AI Clip-Potential Scout.

Uses local yt-dlp flat-playlist search (zero YouTube Data API keys needed)
to surface clippable long-form videos by niche or keyword, scores each video
for short-form clip potential, and optionally asks the user's active LLM
(Codex by default) to scout the best viral angles + focus prompts.
"""

import asyncio
import json
import logging
import math
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

import ffbin  # type: ignore
import llm  # type: ignore

logger = logging.getLogger(__name__)

NICHES: List[Dict[str, str]] = [
    {
        "key": "podcasts",
        "name": "Podcasts & Interviews",
        "query": "best podcast interview clips discussion 2026",
        "default_layout": "podcast_split",
    },
    {
        "key": "ai_tech",
        "name": "AI & Tech",
        "query": "AI tech founder interview breakdown podcast 2026",
        "default_layout": "dynamic_zoom",
    },
    {
        "key": "business",
        "name": "Business & Startups",
        "query": "entrepreneur founder interview business advice podcast 2026",
        "default_layout": "podcast_split",
    },
    {
        "key": "finance",
        "name": "Finance & Investing",
        "query": "finance investing economy market breakdown podcast 2026",
        "default_layout": "dynamic_zoom",
    },
    {
        "key": "gaming",
        "name": "Gaming & Creators",
        "query": "gaming creator podcast stream highlights debate 2026",
        "default_layout": "tutorial",
    },
    {
        "key": "health",
        "name": "Health & Fitness",
        "query": "health longevity fitness science podcast interview 2026",
        "default_layout": "podcast_split",
    },
    {
        "key": "sports",
        "name": "Sports & Hot Takes",
        "query": "sports debate podcast interview hot takes 2026",
        "default_layout": "podcast_split",
    },
    {
        "key": "self_improvement",
        "name": "Mindset & Productivity",
        "query": "productivity psychology mindset interview podcast 2026",
        "default_layout": "dynamic_zoom",
    },
]

_NICHE_MAP = {n["key"]: n for n in NICHES}

# Simple 10-minute in-memory cache keyed by (search_query, limit, min_dur)
_CACHE: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL = 600.0

_HOOK_WORDS = {
    "why", "how", "secret", "secrets", "truth", "mistake", "mistakes", "never",
    "stop", "exposed", "warning", "insane", "crazy", "interview", "podcast",
    "debate", "future", "million", "billion", "ai", "crash", "changed", "best",
    "worst", "rules", "nobody", "actually", "reacts", "explains",
}


def _score_clip_potential(entry: Dict[str, Any]) -> tuple[int, List[str]]:
    """Compute a 0-100 heuristic score for how clip-worthy a long-form video is."""
    score = 38.0
    signals: List[str] = []

    views = int(entry.get("view_count") or 0)
    duration = float(entry.get("duration") or 0)
    title = str(entry.get("title") or "")
    title_lower = title.lower()
    verified = bool(entry.get("channel_is_verified"))

    # 1. Views velocity / proven audience demand (up to +30)
    if views > 0:
        log_v = math.log10(max(views, 10))
        view_pts = min(30.0, max(0.0, (log_v - 3.0) * 10.0))
        score += view_pts
        if views >= 500_000:
            signals.append("Viral Reach")
        elif views >= 50_000:
            signals.append("Strong Views")

    # 2. Duration sweet spot for rich multi-clip extraction (up to +18)
    mins = duration / 60.0
    if 12 <= mins <= 95:
        score += 18.0
        signals.append("Ideal Clip Source")
    elif 6 <= mins < 12 or 95 < mins <= 180:
        score += 12.0
    elif 3 <= mins < 6:
        score += 6.0

    # 3. Title hook & format signals (up to +14)
    tokens = set(re.findall(r"[a-zA-Z0-9]+", title_lower))
    hook_hits = len(tokens & _HOOK_WORDS)
    has_question = "?" in title
    has_number = bool(re.search(r"\b\d+\b", title))
    title_pts = min(14.0, hook_hits * 3.5 + (3.0 if has_question else 0.0) + (2.5 if has_number else 0.0))
    score += title_pts
    if hook_hits >= 2 or has_question:
        signals.append("High-Hook Title")

    if any(w in title_lower for w in ("podcast", "interview", "episode", "ep.", "ep ", "conversation", "debate")):
        signals.append("Talk Format")

    if verified:
        score += 4.0

    final_score = int(max(10, min(99, round(score))))
    return final_score, signals[:3]


def _pick_thumbnail(entry: Dict[str, Any], vid: str) -> str:
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        best = max(thumbs, key=lambda t: int(t.get("width") or 0))
        if best.get("url"):
            return str(best["url"])
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"


def _guess_layout(title: str, default_layout: str) -> str:
    tl = title.lower()
    if any(k in tl for k in ("podcast", "interview", "debate", "conversation", "vs", "panel")):
        return "podcast_split"
    if any(k in tl for k in ("tutorial", "walkthrough", "coding", "build", "demo")):
        return "tutorial"
    return "auto"


async def search_trending(
    niche: str = "podcasts",
    query: Optional[str] = None,
    min_minutes: int = 3,
    limit: int = 15,
    sort_by: str = "score",
) -> List[Dict[str, Any]]:
    """Fetch trending/high-signal long-form YouTube videos via yt-dlp search."""
    niche_cfg = _NICHE_MAP.get(niche, NICHES[0])
    search_term = (query or "").strip() or niche_cfg["query"]
    default_layout = niche_cfg["default_layout"]
    min_sec = max(60, int(min_minutes) * 60)
    limit = max(5, min(25, int(limit)))

    # Fetch extra items so filtering out Shorts / livestreams still fills limit
    fetch_count = min(30, limit + 10)
    cache_key = f"{search_term.lower()}|{min_sec}|{fetch_count}"
    now = time.time()

    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        videos = [dict(v) for v in cached[1]]
    else:
        cmd = [
            ffbin.ytdlp(),
            "--flat-playlist",
            "--no-warnings",
            "--ignore-config",
            "--dump-single-json",
            f"ytsearch{fetch_count}:{search_term}",
        ]

        def _run() -> Dict[str, Any]:
            res = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45
            )
            if res.returncode != 0:
                raise RuntimeError(f"yt-dlp search failed: {res.stderr[:300]}")
            return json.loads(res.stdout)

        raw = await asyncio.to_thread(_run)
        entries = raw.get("entries") or []

        videos = []
        seen_ids: set[str] = set()
        for e in entries:
            if not isinstance(e, dict):
                continue
            vid = str(e.get("id") or "")
            if not vid or vid in seen_ids:
                continue
            if e.get("live_status") in ("is_live", "is_upcoming"):
                continue
            dur = float(e.get("duration") or 0)
            if dur < min_sec:
                continue

            seen_ids.add(vid)
            title = str(e.get("title") or "Untitled")
            score, signals = _score_clip_potential(e)
            videos.append({
                "id": vid,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "channel": str(e.get("channel") or e.get("uploader") or "Unknown"),
                "channel_verified": bool(e.get("channel_is_verified")),
                "duration": int(dur),
                "view_count": int(e.get("view_count") or 0),
                "thumbnail": _pick_thumbnail(e, vid),
                "clip_score": score,
                "signals": signals,
                "recommended_layout": _guess_layout(title, default_layout),
            })

        _CACHE[cache_key] = (now, videos)

    if sort_by == "views":
        videos.sort(key=lambda v: v["view_count"], reverse=True)
    elif sort_by == "duration":
        videos.sort(key=lambda v: v["duration"], reverse=True)
    else:
        videos.sort(key=lambda v: (v["clip_score"], v["view_count"]), reverse=True)

    return videos[:limit]


async def ai_scout_videos(
    videos: List[Dict[str, Any]],
    goal: Optional[str],
    provider: str,
    api_key: str,
    model: str,
) -> List[Dict[str, Any]]:
    """
    Ask the user's configured LLM (Codex by default) to pick the top 5 most
    clippable videos and generate tailored hook angles & AI focus prompts.
    """
    if not videos:
        return []

    compact_lines = []
    for i, v in enumerate(videos[:15], start=1):
        mins = round(float(v.get("duration", 0)) / 60.0, 1)
        views = int(v.get("view_count") or 0)
        compact_lines.append(
            f'{i}. id="{v["id"]}" | "{v["title"]}" | channel="{v.get("channel", "")}" | {mins}m | {views:,} views'
        )

    goal_line = (
        f"Creator goal / target audience: {goal.strip()}"
        if goal and goal.strip()
        else "Creator goal: high-retention TikTok, Reels, and YouTube Shorts clips."
    )

    prompt = f"""You are a viral short-form video producer scouting YouTube for the highest-potential source videos to clip.
{goal_line}

CANDIDATE VIDEOS:
{chr(10).join(compact_lines)}

Pick the TOP 5 best videos from the list above to repurpose into viral 30-60s clips.
For each pick, explain the specific hook angle AND write a concise AI clipping focus prompt we can pass directly to our clipping pipeline.

Return ONLY a valid JSON array (no markdown fences, no commentary):
[
  {{
    "video_id": "<exact id from list>",
    "ai_score": <integer 1-10>,
    "hook_angle": "<1 concise sentence explaining why clips from this video will go viral>",
    "suggested_prompt": "<concise focus instruction for what moments to extract>",
    "recommended_layout": "<one of: dynamic_zoom, podcast_split, podcast_switch, tutorial, original>"
  }}
]"""

    raw = await llm._call_provider_with_retry(provider, api_key, model, prompt)

    # Parse JSON array cleanly
    cleaned = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    if not cleaned.startswith("["):
        idx = cleaned.find("[")
        if idx != -1:
            cleaned = cleaned[idx:]
    if not cleaned.endswith("]"):
        idx = cleaned.rfind("]")
        if idx != -1:
            cleaned = cleaned[: idx + 1]
    cleaned = re.sub(r",\s*]", "]", cleaned)
    cleaned = re.sub(r",\s*}", "}", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse AI Scout JSON: %s | raw=%s", exc, raw[:400])
        return []

    if not isinstance(parsed, list):
        return []

    valid_ids = {v["id"] for v in videos}
    valid_layouts = {"dynamic_zoom", "podcast_split", "podcast_switch", "tutorial", "original"}
    picks: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("video_id") or "")
        if vid not in valid_ids:
            continue
        layout = str(item.get("recommended_layout") or "dynamic_zoom")
        if layout not in valid_layouts:
            layout = "dynamic_zoom"
        picks.append({
            "video_id": vid,
            "ai_score": max(1, min(10, int(item.get("ai_score") or 8))),
            "hook_angle": str(item.get("hook_angle") or ""),
            "suggested_prompt": str(item.get("suggested_prompt") or ""),
            "recommended_layout": layout,
        })
    return picks[:5]
