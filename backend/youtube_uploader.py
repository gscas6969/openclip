"""
youtube_uploader.py — Official YouTube Data API v3 OAuth & Shorts Publisher.

Stores OAuth client credentials and refresh/access tokens encrypted at rest
in SQLite (via settings.py Fernet encryption). Supports:
  - Local OAuth2 authorization flow (/api/youtube/auth-url -> /api/youtube/callback)
  - Connected channel status & automatic token refresh
  - Resumable MP4 Shorts upload with title, description (#Shorts), tags,
    privacy status (private / unlisted / public), and native YouTube scheduling (publishAt)
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import settings as settings_mod  # type: ignore

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

DEFAULT_REDIRECT_URI = os.getenv(
    "YOUTUBE_REDIRECT_URI",
    "http://localhost:8000/api/youtube/callback",
)

# In-memory state -> { code_verifier, redirect_uri } during OAuth dance
_PENDING_FLOWS: Dict[str, Dict[str, Any]] = {}


def _parse_client_config(raw_json: str) -> Dict[str, Any]:
    data = json.loads(raw_json)
    if not isinstance(data, dict) or ("web" not in data and "installed" not in data):
        raise ValueError(
            "Invalid Google OAuth client JSON: expected a top-level 'web' or 'installed' object "
            "(download the OAuth Client ID JSON from Google Cloud Console)."
        )
    return data


async def save_client_secret(raw_json: str) -> Dict[str, Any]:
    cfg = _parse_client_config(raw_json)
    kind = "web" if "web" in cfg else "installed"
    client_id = cfg[kind].get("client_id", "")
    await settings_mod.set_setting("youtube_client_secret", json.dumps(cfg))
    return {"saved": True, "client_type": kind, "client_id_preview": client_id[:16] + "…" if client_id else ""}


async def _load_credentials():
    """Load google.oauth2.credentials.Credentials from encrypted SQLite, refreshing if needed."""
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore

    raw_token = await settings_mod.get_setting("youtube_token")
    if not raw_token or raw_token == "<decryption failed>":
        return None
    try:
        info = json.loads(raw_token)
    except Exception:
        return None

    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        await asyncio.to_thread(creds.refresh, Request())
        await settings_mod.set_setting("youtube_token", creds.to_json())
    return creds


async def get_status() -> Dict[str, Any]:
    raw_secret = await settings_mod.get_setting("youtube_client_secret")
    has_secret = bool(raw_secret and raw_secret != "<decryption failed>")

    status: Dict[str, Any] = {
        "has_client_secret": has_secret,
        "connected": False,
        "channel_title": None,
        "channel_id": None,
        "channel_thumbnail": None,
        "redirect_uri": DEFAULT_REDIRECT_URI,
        "error": None,
    }

    if not has_secret:
        return status

    try:
        creds = await _load_credentials()
    except Exception as exc:
        status["error"] = f"Token refresh failed: {exc}"
        return status

    if not creds or not creds.valid:
        return status

    def _fetch_channel() -> Dict[str, Any]:
        from googleapiclient.discovery import build  # type: ignore

        yt = build("youtube", "v3", credentials=creds)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items") or []
        if not items:
            return {}
        ch = items[0]
        snip = ch.get("snippet") or {}
        thumbs = snip.get("thumbnails") or {}
        thumb_url = (thumbs.get("default") or thumbs.get("medium") or {}).get("url")
        return {
            "channel_id": ch.get("id"),
            "channel_title": snip.get("title"),
            "channel_thumbnail": thumb_url,
        }

    try:
        ch_info = await asyncio.to_thread(_fetch_channel)
        status["connected"] = True
        status.update(ch_info)
    except Exception as exc:
        logger.warning("YouTube channel fetch failed: %s", exc)
        status["connected"] = True
        status["error"] = str(exc)

    return status


async def build_auth_url(redirect_uri: Optional[str] = None) -> Dict[str, str]:
    from google_auth_oauthlib.flow import Flow  # type: ignore

    raw_secret = await settings_mod.get_setting("youtube_client_secret")
    if not raw_secret or raw_secret == "<decryption failed>":
        raise ValueError("Upload your Google OAuth client_secret.json in Settings first.")

    cfg = _parse_client_config(raw_secret)
    redir = redirect_uri or DEFAULT_REDIRECT_URI
    flow = Flow.from_client_config(cfg, scopes=SCOPES, redirect_uri=redir)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _PENDING_FLOWS[state] = {
        "redirect_uri": redir,
        "code_verifier": getattr(flow, "code_verifier", None),
    }
    return {"auth_url": auth_url, "redirect_uri": redir}


async def handle_oauth_callback(code: str, state: str) -> Dict[str, Any]:
    from google_auth_oauthlib.flow import Flow  # type: ignore

    raw_secret = await settings_mod.get_setting("youtube_client_secret")
    if not raw_secret or raw_secret == "<decryption failed>":
        raise ValueError("Google OAuth client secret not found.")

    cfg = _parse_client_config(raw_secret)
    pending = _PENDING_FLOWS.pop(state, {})
    redir = pending.get("redirect_uri") or DEFAULT_REDIRECT_URI

    flow = Flow.from_client_config(cfg, scopes=SCOPES, state=state, redirect_uri=redir)
    if pending.get("code_verifier"):
        flow.code_verifier = pending["code_verifier"]

    # Allow http://localhost during local OAuth callback
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    await asyncio.to_thread(flow.fetch_token, code=code)
    creds = flow.credentials
    await settings_mod.set_setting("youtube_token", creds.to_json())
    return await get_status()


async def disconnect() -> None:
    await settings_mod.set_setting("youtube_token", "")


def _normalize_tags(tags: Optional[List[str]]) -> List[str]:
    if not tags:
        return ["Shorts"]
    cleaned: List[str] = []
    total_len = 0
    for raw in tags:
        t = str(raw).strip().lstrip("#")
        if not t or t.lower() in {c.lower() for c in cleaned}:
            continue
        if total_len + len(t) + 1 > 460:
            break
        cleaned.append(t)
        total_len += len(t) + 1
    if "shorts" not in {c.lower() for c in cleaned}:
        cleaned.append("Shorts")
    return cleaned


async def upload_short(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy_status: str = "private",
    publish_at: Optional[str] = None,
    category_id: str = "22",
    made_for_kids: bool = False,
) -> Dict[str, Any]:
    """
    Upload a vertical clip to YouTube Shorts using resumable MediaFileUpload.

    If ``publish_at`` (ISO-8601 UTC string, e.g. '2026-11-26T18:00:00Z') is set,
    YouTube requires ``privacyStatus`` to be 'private' and automatically publishes
    the Short at that timestamp.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found on disk: {video_path}")

    creds = await _load_credentials()
    if not creds or not creds.valid:
        raise RuntimeError("YouTube account is not connected. Connect your channel in Settings first.")

    safe_title = (title or "OpenClip Short").strip()[:100]
    safe_desc = (description or "").strip()
    if "#shorts" not in safe_title.lower() and "#shorts" not in safe_desc.lower():
        safe_desc = f"{safe_desc}\n\n#Shorts".strip()

    safe_privacy = privacy_status.lower().strip()
    if safe_privacy not in ("private", "unlisted", "public"):
        safe_privacy = "private"

    status_body: Dict[str, Any] = {
        "privacyStatus": "private" if publish_at else safe_privacy,
        "selfDeclaredMadeForKids": bool(made_for_kids),
    }
    if publish_at:
        status_body["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": safe_title,
            "description": safe_desc[:5000],
            "tags": _normalize_tags(tags),
            "categoryId": str(category_id or "22"),
        },
        "status": status_body,
    }

    def _do_upload() -> Dict[str, Any]:
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.http import MediaFileUpload  # type: ignore

        yt = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=4 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = req.next_chunk()
        return response

    resp = await asyncio.to_thread(_do_upload)
    video_id = resp.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube upload returned no video ID: {resp}")

    final_status = "scheduled" if publish_at else status_body["privacyStatus"]
    return {
        "video_id": video_id,
        "url": f"https://youtube.com/shorts/{video_id}",
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        "status": final_status,
        "publish_at": publish_at,
    }
