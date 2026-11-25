# OpenClip Studio

> Free, open-source, local-first AI video clipping & multi-track studio engine.
> Paste a YouTube link (including ended livestreams) or upload a local video → auto-detect highlights → smart 9:16 reframe → fine-tune in the Studio Editor → export or schedule to YouTube Shorts.

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Original Repo](https://img.shields.io/badge/original%20project-aionixos%2Fopenclip-blueviolet)](https://github.com/aionixos/openclip)

> **Credits & Attribution:** OpenClip was originally created by **[AIONIX](https://github.com/aionixos)** in the original repository **[aionixos/openclip](https://github.com/aionixos/openclip)**. This fork builds upon AIONIX's open-source foundation (MIT License) with Studio editing, livestream highlight detection, clean sentence-aligned clip endings, and a native desktop control dashboard. If you appreciate the project, please also star the **[original OpenClip repo](https://github.com/aionixos/openclip)**!

![OpenClip Dashboard Preview](frontend/public/screenshot.png)

---

## Key Features

- **100% Local-First** — No subscriptions, no watermarks, no cloud uploads. Videos, clips, and SQLite metadata stay on your machine.
- **YouTube, Ended Livestreams & Local Uploads** — Supports standard YouTube URLs (`watch?v=`, `youtu.be/`, `/shorts/`, `/live/` VODs) as well as local `MP4`/`MOV`/`MKV`/`WEBM` uploads.
- **Livestream & Audience Highlight Detection** — Combines YouTube **"Most Replayed" heatmaps**, **Live Chat replay spike & humor keyword detection** (`LMAO`, `HAHA`, `CLIP IT`, etc.), and **FFmpeg audio hype/laughter peaks** alongside the transcript so the AI spots genuinely funny and high-energy moments even in multi-hour VODs.
- **Clean, Natural Clip Endings** — Pause-preserving JSON3 word timings, decimal-second LLM prompting, transcript sentence/pause boundary snapping, real-audio waveform end refinement, and subtle micro audio fade-outs prevent abrupt mid-sentence cutoffs.
- **Multi-Provider AI** — Works with **OpenAI Codex CLI** (using your existing ChatGPT/Codex subscription), **Claude Code CLI** (using your existing Claude subscription), **OpenAI**, **Anthropic Claude**, **Google Gemini**, or local **Ollama**, plus custom focus prompts and one-click presets (*Funny & Chaotic*, *Stream Highlights*, *Hot Takes*, *Actionable Tips*).
- **Smart Multi-Layout 9:16 Reframing** — Auto-detects or lets you switch between *Smart Face Track*, *Podcast Split-Screen* (with multi-camera close-up switching), *Podcast Active Speaker*, *Tutorial (Screen + Face)*, and *Original + Blur Fill*.
- **Non-Destructive Studio Clip Editor** — Trim, cut silences/fillers, edit word-level transcripts, pick from **24 kinetic ASS caption presets** (8 animation modes), auto-inject contextual emojis, resync captions with Whisper, apply Brand Kits/logos, and export individual MP4s or a project ZIP.
- **Creator Workflow Suite** — Batch URL Queue, Trending Video Discover + AI Scout, Local Quick Tools (trim, compress, convert, rotate/resize), YouTube Shorts OAuth auto-scheduler, Storage Cleanup manager, and a built-in **MCP server** (`backend/mcp_server.py`).
- **Native Desktop Control Dashboard (No Electron)** — Lightweight Python `tkinter` launcher ([launcher_dash.py](launcher_dash.py) / [dashboard.ps1](dashboard.ps1)) with one-click **Start / Stop / Restart**, live status indicators, browser shortcuts, and real-time Backend + Frontend logs.

---

## Tech Stack

| Layer               | Tool                                                          |
| ------------------- | ------------------------------------------------------------- |
| YouTube Download    | `yt-dlp` (CLI)                                                |
| Video & Audio       | `FFmpeg` / `ffprobe` (direct CLI subprocess + `filter_complex`) |
| Transcripts         | YouTube JSON3 captions → Transcript API → Gemini Live → Whisper |
| Highlight Signals   | YouTube Heatmap + Live Chat Replay + FFmpeg 4 kHz RMS peaks   |
| Face & Lip Tracking | MediaPipe Tasks API (BlazeFace + FaceLandmarker) + OpenCV     |
| Caption Engine      | Kinetic ASS renderer (24 presets, 8 animation modes)          |
| Backend             | Python 3.11+ (FastAPI, Uvicorn, aiosqlite)                    |
| Frontend            | Next.js 16 (React 19, TypeScript, Tailwind CSS)               |
| Desktop Launcher    | Python `tkinter` (native, zero extra dependencies)            |
| Local Storage       | SQLite (`data/openclip.db`) + Fernet-encrypted API keys       |

---

## Quick Start

### Prerequisites

- **Python 3.11 or 3.12** *(3.12 recommended for MediaPipe & Whisper wheel compatibility)*
- **Node.js 18+**
- **FFmpeg** installed and on `PATH` (or installed via `winget install Gyan.FFmpeg` on Windows)

### Windows (PowerShell)

1. **Clone & run setup:**
   ```powershell
   git clone https://github.com/gscas6969/openclip.git
   cd openclip
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```
2. **Start OpenClip** using either the **Desktop Control Dashboard** or the **CLI script**:
   - **Desktop Control Dashboard (recommended, GUI with Start/Stop/Logs):**
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\dashboard.ps1
     ```
     *(You can also run `launcher_dash.py` via a Windows shortcut pointing to `backend\.venv\Scripts\pythonw.exe` with `"launcher_dash.py"` as the argument.)*
   - **Terminal launcher (`Ctrl + C` stops both servers):**
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\start.ps1
     ```

### macOS / Linux

```bash
git clone https://github.com/gscas6969/openclip.git
cd openclip
bash setup.sh
bash start.sh
```

### Local URLs

- **Frontend App:** http://localhost:5000
- **Backend API:** http://localhost:8000
- **Interactive API Docs:** http://localhost:8000/docs

---

## Project Structure

```text
openclip/
├── launcher_dash.py           ← Native tkinter Control Dashboard (Start/Stop/Logs)
├── dashboard.ps1              ← PowerShell launcher for the Control Dashboard
├── setup.ps1 / setup.sh       ← Dependency & environment setup scripts
├── start.ps1 / start.sh       ← Terminal launcher for Backend + Frontend
├── backend/
│   ├── api.py                 ← FastAPI routes, pipeline orchestration & WebSockets
│   ├── database.py            ← SQLite schema & async CRUD
│   ├── downloader.py          ← yt-dlp video downloader
│   ├── transcriber.py         ← JSON3 captions → Transcript API → Gemini Live → Whisper
│   ├── gemini_transcriber.py  ← Speech-to-text via Gemini Transcribe Live
│   ├── highlight_signals.py   ← Most Replayed heatmap, live chat spikes & audio hype
│   ├── llm.py                 ← Multi-provider Map-Reduce AI clip finder
│   ├── clip_boundaries.py     ← Sentence/pause boundary snapping & audio end refinement
│   ├── reframer.py            ← Multi-layout 9:16 reframing & active speaker tracking
│   ├── layout_detector.py     ← Smart screen & face layout detector
│   ├── speaker_detector.py    ← Lip-movement & audio RMS speaker tracker
│   ├── captioner.py           ← 24 kinetic ASS caption styles & block builder
│   ├── editor_engine.py       ← Non-destructive render compiler, waveform & silence cut
│   ├── batch_queue.py         ← Multi-URL sequential batch processor
│   ├── discover.py            ← Trending video discovery & AI Scout
│   ├── quick_tools.py         ← Local FFmpeg trim, compress, convert & transform tools
│   ├── youtube_uploader.py    ← YouTube Shorts OAuth & native scheduling
│   ├── storage_manager.py     ← Disk usage stats & cleanup utility
│   ├── mcp_server.py          ← Local MCP server for AI clients
│   └── settings.py            ← Encrypted local settings storage
├── frontend/
│   ├── app/
│   │   ├── page.tsx                 ← Dashboard
│   │   ├── create/page.tsx          ← Create project (YouTube / VOD / Upload + presets)
│   │   ├── project/[id]/page.tsx    ← Project clips, relayout & YouTube scheduling
│   │   ├── project/[id]/edit/[clipId]/page.tsx ← Multi-track Studio Clip Editor
│   │   ├── batch/page.tsx           ← Batch URL queue
│   │   ├── discover/page.tsx        ← Trending video discovery + AI Scout
│   │   ├── tools/page.tsx           ← Local Quick Tools
│   │   └── settings/page.tsx        ← LLM, Whisper, Brand Kits, YouTube & Storage
│   └── components/                  ← UI & Studio Editor components
├── data/                            ← SQLite DB + local encryption key (gitignored)
└── tmp/                             ← Downloaded videos & rendered clips (gitignored)
```

---

## Core API Endpoints

| Method | Path                                   | Description                                 |
| ------ | -------------------------------------- | ------------------------------------------- |
| GET    | `/api/projects`                        | List all projects                           |
| POST   | `/api/projects`                        | Create project from YouTube / livestream URL|
| POST   | `/api/projects/upload`                 | Upload local video & start pipeline         |
| GET    | `/api/projects/{id}`                   | Get project details & clips                 |
| POST   | `/api/projects/{id}/retry`             | Resume project from last checkpoint         |
| DELETE | `/api/projects/{id}`                   | Delete project & disk files                 |
| POST   | `/api/clips/{id}/render`               | Render clip from Studio Editor `edit_state` |
| POST   | `/api/clips/{id}/layout`               | Re-reframe clip with a different 9:16 layout|
| POST   | `/api/clips/{id}/resync-whisper`       | Word-level Whisper resync for a clip        |
| POST   | `/api/clips/{id}/publish/youtube`      | Upload/schedule clip to YouTube Shorts      |
| POST   | `/api/batch`                           | Enqueue multiple YouTube URLs               |
| WS     | `/ws/progress/{id}`                    | Real-time pipeline progress stream          |

---

## FAQ

**Where is my data stored?**
Everything stays locally on your machine:
- Videos & rendered clips: `tmp/{project_id}/`
- Database & encrypted keys: `data/openclip.db` + `data/.secret_key`

Only external requests made are `yt-dlp` (to YouTube) and your chosen LLM provider API (or zero external LLM calls if using local **Ollama**).

**Does it work on ended livestreams?**
Yes — paste a `youtube.com/watch?v=...` or `youtube.com/live/...` URL from an ended stream (or upload the VOD). OpenClip automatically scans YouTube's Most Replayed heatmap, live chat reaction spikes, and audio hype/laughter peaks alongside the transcript, and you can click the **Funny & Chaotic** or **Stream Highlights** preset on the Create page.

**How do I run tests?**
```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest -v test_clip_boundaries.py test_highlight_signals.py
```

---

## Credits & License

- **Original Creator:** **[AIONIX](https://github.com/aionixos)** — original repository at **[github.com/aionixos/openclip](https://github.com/aionixos/openclip)**
- **License:** MIT License — see [LICENSE](LICENSE) for full terms.
