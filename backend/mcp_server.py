"""
mcp_server.py — Local Model Context Protocol (MCP) stdio server for OpenClip Studio.

Zero external MCP SDK required — speaks standard JSON-RPC 2.0 over stdio
(compatible with Claude Desktop, Cursor, Windsurf, and Antigravity) and
proxies tool calls directly to the running local OpenClip FastAPI backend
(default http://127.0.0.1:8000, override via OPENCLIP_API_URL).

Usage:
    python mcp_server.py
"""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

API_BASE = os.environ.get("OPENCLIP_API_URL", "http://127.0.0.1:8000").rstrip("/")
SERVER_INFO = {"name": "openclip-studio", "version": "2.0.0"}
PROTOCOL_VERSION = "2024-11-05"

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "openclip_list_projects",
        "description": "List all OpenClip video projects with their status, source type, and generated clip counts.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "openclip_get_project",
        "description": "Get detailed metadata and all generated clips (titles, viral scores, file URLs, layouts) for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the OpenClip project."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "openclip_create_project",
        "description": "Submit a YouTube URL to OpenClip Studio to automatically download, transcribe, find viral moments, reframe to 9:16, and add kinetic captions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "youtube_url": {"type": "string", "description": "YouTube video URL."},
                "clip_min_duration": {"type": "integer", "default": 30, "description": "Minimum clip length in seconds."},
                "clip_max_duration": {"type": "integer", "default": 60, "description": "Maximum clip length in seconds."},
                "layout_preference": {
                    "type": "string",
                    "enum": ["auto", "dynamic_zoom", "podcast_split", "podcast_switch", "tutorial", "original"],
                    "default": "auto",
                },
                "custom_prompt": {"type": "string", "description": "Optional AI focus prompt (e.g. 'Pick funny moments and bold claims')."},
                "brand_kit_id": {"type": "string", "description": "Optional Brand Kit UUID."},
            },
            "required": ["youtube_url"],
        },
    },
    {
        "name": "openclip_batch_submit",
        "description": "Queue multiple YouTube URLs for sequential background clipping with shared settings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of YouTube URLs to process sequentially.",
                },
                "clip_min_duration": {"type": "integer", "default": 30},
                "clip_max_duration": {"type": "integer", "default": 60},
                "layout_preference": {"type": "string", "default": "auto"},
                "custom_prompt": {"type": "string"},
                "brand_kit_id": {"type": "string"},
            },
            "required": ["urls"],
        },
    },
    {
        "name": "openclip_get_batch_status",
        "description": "Check progress of all batch queues (or a specific batch_id).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string", "description": "Optional specific batch ID."},
            },
            "required": [],
        },
    },
    {
        "name": "openclip_list_styles",
        "description": "List all 24 built-in kinetic, clean, and vibrant caption presets available in OpenClip Studio.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "openclip_list_brand_kits",
        "description": "List saved Brand Kits (logo + caption style bundles).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "openclip_render_clip",
        "description": "Update a clip's caption preset, animation, auto-emojis, or trim range and re-render its MP4 non-destructively.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clip_id": {"type": "string", "description": "Clip UUID."},
                "caption_style": {"type": "string", "description": "Caption preset key (e.g. mozi_pop, dan_bold, chase_pill, sweep_karaoke, none)."},
                "animation": {"type": "string", "description": "Optional animation override (jelly, stamp, chase, sweep, glow, highlight, word_by_word, one_word)."},
                "auto_emojis": {"type": "boolean", "default": False, "description": "Inject contextual emojis into caption blocks before rendering."},
                "trim_start": {"type": "number", "description": "Optional trim start time in seconds."},
                "trim_end": {"type": "number", "description": "Optional trim end time in seconds."},
                "title": {"type": "string", "description": "Optional updated clip title."},
            },
            "required": ["clip_id"],
        },
    },
    {
        "name": "openclip_relayout_clip",
        "description": "Re-reframe a clip from its original source video using a specific 9:16 layout mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clip_id": {"type": "string", "description": "Clip UUID."},
                "layout_mode": {
                    "type": "string",
                    "enum": ["dynamic_zoom", "podcast_split", "podcast_switch", "tutorial", "original"],
                },
            },
            "required": ["clip_id", "layout_mode"],
        },
    },
    {
        "name": "openclip_discover_trending",
        "description": "Search YouTube for trending clip-worthy long-form videos by niche or keyword, scored for short-form clip potential.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "niche": {
                    "type": "string",
                    "enum": ["podcasts", "ai_tech", "business", "finance", "gaming", "health", "sports", "self_improvement"],
                    "default": "podcasts",
                },
                "query": {"type": "string", "description": "Optional custom search keyword or creator name."},
                "min_minutes": {"type": "integer", "default": 3},
                "limit": {"type": "integer", "default": 10},
            },
            "required": [],
        },
    },
    {
        "name": "openclip_publish_youtube",
        "description": "Upload and optionally schedule a rendered OpenClip clip directly to the user's connected YouTube channel as a Short.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clip_id": {"type": "string", "description": "Clip UUID to publish."},
                "title": {"type": "string", "description": "Optional custom Short title (max 100 chars)."},
                "description": {"type": "string", "description": "Optional description (#Shorts added automatically)."},
                "privacy_status": {"type": "string", "enum": ["private", "unlisted", "public"], "default": "private"},
                "publish_at": {"type": "string", "description": "Optional ISO-8601 UTC timestamp to auto-schedule (e.g. 2026-11-26T18:00:00Z)."},
            },
            "required": ["clip_id"],
        },
    },
]


def _http_json(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenClip API {method} {path} failed ({e.code}): {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach OpenClip backend at {API_BASE}. Make sure OpenClip is running. ({e})"
        ) from e


def _execute_tool(name: str, args: Dict[str, Any]) -> Any:
    if name == "openclip_list_projects":
        return _http_json("GET", "/api/projects")

    if name == "openclip_get_project":
        return _http_json("GET", f"/api/projects/{args['project_id']}")

    if name == "openclip_create_project":
        return _http_json("POST", "/api/projects", {
            "youtube_url": args["youtube_url"],
            "clip_min_duration": args.get("clip_min_duration", 30),
            "clip_max_duration": args.get("clip_max_duration", 60),
            "layout_preference": args.get("layout_preference", "auto"),
            "custom_prompt": args.get("custom_prompt"),
            "brand_kit_id": args.get("brand_kit_id"),
        })

    if name == "openclip_batch_submit":
        return _http_json("POST", "/api/batch", {
            "urls": args["urls"],
            "clip_min_duration": args.get("clip_min_duration", 30),
            "clip_max_duration": args.get("clip_max_duration", 60),
            "layout_preference": args.get("layout_preference", "auto"),
            "custom_prompt": args.get("custom_prompt"),
            "brand_kit_id": args.get("brand_kit_id"),
        })

    if name == "openclip_get_batch_status":
        bid = args.get("batch_id")
        return _http_json("GET", f"/api/batch/{bid}" if bid else "/api/batch")

    if name == "openclip_list_styles":
        return _http_json("GET", "/api/caption-styles")

    if name == "openclip_list_brand_kits":
        return _http_json("GET", "/api/brand-kits")

    if name == "openclip_relayout_clip":
        return _http_json("POST", f"/api/clips/{args['clip_id']}/layout", {
            "layout_mode": args["layout_mode"],
        })

    if name == "openclip_discover_trending":
        import urllib.parse
        qs = urllib.parse.urlencode({
            "niche": args.get("niche", "podcasts"),
            "query": args.get("query", ""),
            "min_minutes": args.get("min_minutes", 3),
            "limit": args.get("limit", 10),
        })
        return _http_json("GET", f"/api/discover/trending?{qs}")

    if name == "openclip_publish_youtube":
        return _http_json("POST", f"/api/clips/{args['clip_id']}/publish/youtube", {
            "title": args.get("title"),
            "description": args.get("description"),
            "privacy_status": args.get("privacy_status", "private"),
            "publish_at": args.get("publish_at"),
        })

    if name == "openclip_render_clip":
        clip_id = args["clip_id"]
        clip = _http_json("GET", f"/api/clips/{clip_id}")
        edit_state = dict(clip.get("edit_state") or {})
        overrides = dict(edit_state.get("caption_overrides") or {})

        if "caption_style" in args and args["caption_style"]:
            style_key = args["caption_style"]
            if style_key == "none":
                edit_state["caption_enabled"] = False
            else:
                edit_state["caption_enabled"] = True
                edit_state["caption_style"] = style_key
                styles = _http_json("GET", "/api/caption-styles")
                match = next((s for s in styles if s.get("key") == style_key), None)
                if match and match.get("config"):
                    overrides.update(match["config"])
        if "animation" in args and args["animation"]:
            overrides["animation"] = args["animation"]
        edit_state["caption_overrides"] = overrides

        if args.get("trim_start") is not None:
            edit_state["trim_start"] = float(args["trim_start"])
        if args.get("trim_end") is not None:
            edit_state["trim_end"] = float(args["trim_end"])

        if args.get("auto_emojis"):
            em_res = _http_json("POST", f"/api/clips/{clip_id}/auto-emojis", {"edit_state": edit_state})
            if em_res.get("caption_blocks"):
                edit_state["caption_blocks"] = em_res["caption_blocks"]

        return _http_json("POST", f"/api/clips/{clip_id}/render", {
            "edit_state": edit_state,
            "title": args.get("title"),
        })

    raise ValueError(f"Unknown tool: {name}")


def handle_rpc(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process one JSON-RPC 2.0 MCP message and return a response dict (or None for notifications)."""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "notifications/initialized" or req_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            result = _execute_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_rpc(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
