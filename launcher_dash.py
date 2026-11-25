"""
launcher_dash.py — Lightweight native desktop Control Dashboard for OpenClip.

Built with Python's built-in tkinter (no Electron, no extra dependencies).
Provides one-click Start / Stop / Restart controls, live port health status,
browser shortcuts, and real-time Backend + Frontend log streaming.

Usage:
    Double-click "OpenClip Dashboard" shortcut (OpenClip Dashboard.lnk)
    or run:
    powershell -ExecutionPolicy Bypass -File .\dashboard.ps1
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from typing import Optional
import tkinter as tk
from tkinter import messagebox, ttk

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")

BACKEND_PORT = 8000
FRONTEND_PORT = 5000
APP_URL = f"http://localhost:{FRONTEND_PORT}"
DOCS_URL = f"http://localhost:{BACKEND_PORT}/docs"

# Theme palette (dark OpenClip Studio aesthetic)
BG = "#0b0e14"
SURFACE = "#131924"
SURFACE_ALT = "#18202f"
BORDER = "#243044"
TEXT = "#f1f5f9"
TEXT_MUTED = "#94a3b8"
PRIMARY = "#4f46e5"
PRIMARY_HOVER = "#6366f1"
GREEN = "#10b981"
GREEN_DARK = "#059669"
AMBER = "#f59e0b"
RED = "#ef4444"
RED_DARK = "#dc2626"
LOG_BG = "#070a0f"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_LOG_LINES = 1500


def _enable_hidpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_http_ready(url: str, timeout: float = 0.8) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False


def _find_pids_on_port(port: int) -> list[int]:
    """Find listening PID(s) bound to ``port`` (Windows netstat / POSIX lsof)."""
    pids: set[int] = set()
    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=5,
            )
            needle = f":{port}"
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(needle):
                    try:
                        pid = int(parts[4])
                        if pid > 0:
                            pids.add(pid)
                    except ValueError:
                        pass
        except Exception:
            pass
    else:
        try:
            res = subprocess.run(
                ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in res.stdout.splitlines():
                if line.strip().isdigit():
                    pids.add(int(line.strip()))
        except Exception:
            pass
    return sorted(pids)


def _kill_pid_tree(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=10,
        )
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def _resolve_backend_python() -> str:
    venv_py = os.path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe")
    if os.path.isfile(venv_py):
        return venv_py
    unix_py = os.path.join(BACKEND_DIR, ".venv", "bin", "python")
    if os.path.isfile(unix_py):
        return unix_py
    return sys.executable


class OpenClipDashboard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("OpenClip Control Dashboard")
        self.root.geometry("820x600")
        self.root.minsize(680, 480)
        self.root.configure(bg=BG)

        # Try setting OpenClip favicon on Windows
        ico_path = os.path.join(FRONTEND_DIR, "app", "favicon.ico")
        if os.path.isfile(ico_path):
            try:
                self.root.iconbitmap(ico_path)
            except Exception:
                pass

        self.backend_proc: Optional[subprocess.Popen] = None
        self.frontend_proc: Optional[subprocess.Popen] = None
        self.backend_starting = False
        self.frontend_starting = False

        self.backend_up = False
        self.frontend_up = False

        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.log_history: list[tuple[str, str]] = []
        self.log_filter = tk.StringVar(value="all")
        self._stop_polling = threading.Event()
        self._busy = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._emit_log("SYSTEM", f"Workspace: {ROOT_DIR}")
        self._start_status_poller()
        self._drain_logs()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _make_btn(
        self,
        parent: tk.Widget,
        text: str,
        command,
        bg: str = SURFACE_ALT,
        fg: str = TEXT,
        hover_bg: Optional[str] = None,
        padx: int = 14,
        pady: int = 8,
        font=("Segoe UI Semibold", 10),
    ) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=hover_bg or bg,
            activeforeground=fg,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            cursor="hand2",
            padx=padx,
            pady=pady,
            font=font,
        )
        if hover_bg:
            btn.bind("<Enter>", lambda _e: btn.configure(bg=hover_bg) if str(btn["state"]) != "disabled" else None)
            btn.bind("<Leave>", lambda _e: btn.configure(bg=bg) if str(btn["state"]) != "disabled" else None)
        return btn

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=20, pady=18)
        outer.pack(fill=tk.BOTH, expand=True)

        # ---- Header ----
        header = tk.Frame(outer, bg=BG)
        header.pack(fill=tk.X, pady=(0, 14))

        title_col = tk.Frame(header, bg=BG)
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(
            title_col,
            text="OpenClip Studio",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Bold", 17),
        ).pack(anchor="w")

        tk.Label(
            title_col,
            text="Local Server Control Dashboard",
            bg=BG,
            fg=TEXT_MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(1, 0))

        self.overall_pill = tk.Label(
            header,
            text="● CHECKING…",
            bg=SURFACE,
            fg=AMBER,
            font=("Segoe UI Semibold", 9),
            padx=12,
            pady=6,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.overall_pill.pack(side=tk.RIGHT, anchor="center")

        # ---- Primary Action Bar ----
        actions = tk.Frame(outer, bg=SURFACE, padx=14, pady=12, highlightthickness=1, highlightbackground=BORDER)
        actions.pack(fill=tk.X, pady=(0, 14))

        self.btn_start = self._make_btn(
            actions, "▶  Start All", self.start_all,
            bg=GREEN, hover_bg=GREEN_DARK, fg="#ffffff", padx=18, pady=9,
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_stop = self._make_btn(
            actions, "■  Stop All", self.stop_all,
            bg=RED, hover_bg=RED_DARK, fg="#ffffff", padx=18, pady=9,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_restart = self._make_btn(
            actions, "↻  Restart", self.restart_all,
            bg=SURFACE_ALT, hover_bg="#253047", fg=TEXT, padx=14, pady=9,
        )
        self.btn_restart.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_docs = self._make_btn(
            actions, "API Docs", lambda: webbrowser.open(DOCS_URL),
            bg=SURFACE_ALT, hover_bg="#253047", fg=TEXT_MUTED, padx=12, pady=9,
        )
        self.btn_docs.pack(side=tk.RIGHT)

        self.btn_open = self._make_btn(
            actions, "↗  Open App", lambda: webbrowser.open(APP_URL),
            bg=PRIMARY, hover_bg=PRIMARY_HOVER, fg="#ffffff", padx=16, pady=9,
        )
        self.btn_open.pack(side=tk.RIGHT, padx=(0, 8))

        # ---- Service Status Cards ----
        cards = tk.Frame(outer, bg=BG)
        cards.pack(fill=tk.X, pady=(0, 14))
        cards.columnconfigure(0, weight=1, uniform="cards")
        cards.columnconfigure(1, weight=1, uniform="cards")

        self.backend_dot, self.backend_status_lbl, self.btn_be_toggle = self._build_service_card(
            cards, col=0, pad=(0, 7),
            title="Backend API",
            subtitle=f"FastAPI · http://localhost:{BACKEND_PORT}",
            toggle_cmd=self.toggle_backend,
        )
        self.frontend_dot, self.frontend_status_lbl, self.btn_fe_toggle = self._build_service_card(
            cards, col=1, pad=(7, 0),
            title="Frontend Web App",
            subtitle=f"Next.js · http://localhost:{FRONTEND_PORT}",
            toggle_cmd=self.toggle_frontend,
        )

        # ---- Logs Header & Filter ----
        log_bar = tk.Frame(outer, bg=BG)
        log_bar.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            log_bar,
            text="LIVE LOGS",
            bg=BG,
            fg=TEXT_MUTED,
            font=("Segoe UI Bold", 9),
        ).pack(side=tk.LEFT)

        clear_btn = self._make_btn(
            log_bar, "Clear", self.clear_logs,
            bg=SURFACE, hover_bg=SURFACE_ALT, fg=TEXT_MUTED, padx=10, pady=3, font=("Segoe UI", 8),
        )
        clear_btn.pack(side=tk.RIGHT)

        for key, label in (("frontend", "Frontend"), ("backend", "Backend"), ("all", "All")):
            rb = tk.Radiobutton(
                log_bar,
                text=label,
                variable=self.log_filter,
                value=key,
                command=self._rerender_logs,
                indicatoron=False,
                bg=SURFACE,
                fg=TEXT_MUTED,
                selectcolor=PRIMARY,
                activebackground=SURFACE_ALT,
                activeforeground=TEXT,
                relief=tk.FLAT,
                bd=0,
                padx=10,
                pady=3,
                cursor="hand2",
                font=("Segoe UI Semibold", 8),
            )
            rb.pack(side=tk.RIGHT, padx=(0, 6))

        # ---- Log Console ----
        log_frame = tk.Frame(outer, bg=LOG_BG, highlightthickness=1, highlightbackground=BORDER)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            bg=LOG_BG,
            fg="#cbd5e1",
            insertbackground=TEXT,
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=8,
            wrap=tk.WORD,
            font=("Consolas", 9),
            state=tk.DISABLED,
        )
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_text.tag_configure("BACKEND", foreground="#38bdf8")
        self.log_text.tag_configure("FRONTEND", foreground="#a78bfa")
        self.log_text.tag_configure("SYSTEM", foreground="#34d399")
        self.log_text.tag_configure("ERROR", foreground="#f87171")
        self.log_text.tag_configure("TIME", foreground="#64748b")

    def _build_service_card(
        self,
        parent: tk.Frame,
        col: int,
        pad: tuple[int, int],
        title: str,
        subtitle: str,
        toggle_cmd,
    ) -> tuple[tk.Label, tk.Label, tk.Button]:
        card = tk.Frame(
            parent, bg=SURFACE, padx=14, pady=12,
            highlightthickness=1, highlightbackground=BORDER,
        )
        card.grid(row=0, column=col, sticky="nsew", padx=pad)

        top = tk.Frame(card, bg=SURFACE)
        top.pack(fill=tk.X)

        dot = tk.Label(top, text="●", bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 11))
        dot.pack(side=tk.LEFT, padx=(0, 6))

        tk.Label(top, text=title, bg=SURFACE, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(side=tk.LEFT)

        toggle_btn = self._make_btn(
            top, "Start", toggle_cmd,
            bg=SURFACE_ALT, hover_bg="#253047", fg=TEXT, padx=10, pady=4, font=("Segoe UI Semibold", 8),
        )
        toggle_btn.pack(side=tk.RIGHT)

        tk.Label(card, text=subtitle, bg=SURFACE, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 2))

        status_lbl = tk.Label(card, text="Checking…", bg=SURFACE, fg=AMBER, font=("Segoe UI Semibold", 9))
        status_lbl.pack(anchor="w")

        return dot, status_lbl, toggle_btn

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _emit_log(self, source: str, line: str) -> None:
        clean = line.rstrip("\r\n")
        if not clean:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put((source, f"[{ts}] [{source}] {clean}"))

    def _stream_pipe(self, proc: subprocess.Popen, source: str) -> None:
        assert proc.stdout is not None
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self._emit_log(source, line)
        except Exception:
            pass
        finally:
            code = proc.poll()
            if code is not None:
                self._emit_log("SYSTEM", f"{source.title()} exited (code {code}).")

    def _drain_logs(self) -> None:
        batch: list[tuple[str, str]] = []
        try:
            while len(batch) < 80:
                batch.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass

        if batch:
            self.log_history.extend(batch)
            if len(self.log_history) > MAX_LOG_LINES:
                self.log_history = self.log_history[-MAX_LOG_LINES:]
            flt = self.log_filter.get()
            self.log_text.configure(state=tk.NORMAL)
            for source, text in batch:
                if flt != "all" and source.lower() != flt and source != "SYSTEM":
                    continue
                self._append_formatted_line(source, text)
            # Trim widget lines if needed
            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > MAX_LOG_LINES:
                self.log_text.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        self.root.after(120, self._drain_logs)

    def _append_formatted_line(self, source: str, text: str) -> None:
        # Format: "[HH:MM:SS] [SOURCE] rest"
        tag = source if source in ("BACKEND", "FRONTEND", "SYSTEM", "ERROR") else "SYSTEM"
        if "error" in text.lower() or "traceback" in text.lower():
            body_tag = "ERROR"
        else:
            body_tag = None

        parts = text.split("] ", 2)
        if len(parts) == 3:
            self.log_text.insert(tk.END, parts[0] + "] ", "TIME")
            self.log_text.insert(tk.END, parts[1] + "] ", tag)
            if body_tag:
                self.log_text.insert(tk.END, parts[2] + "\n", body_tag)
            else:
                self.log_text.insert(tk.END, parts[2] + "\n")
        else:
            self.log_text.insert(tk.END, text + "\n", tag)

    def _rerender_logs(self) -> None:
        flt = self.log_filter.get()
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        for source, text in self.log_history:
            if flt != "all" and source.lower() != flt and source != "SYSTEM":
                continue
            self._append_formatted_line(source, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def clear_logs(self) -> None:
        self.log_history.clear()
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Live Status Polling
    # ------------------------------------------------------------------

    def _start_status_poller(self) -> None:
        def _loop() -> None:
            while not self._stop_polling.is_set():
                be_open = _is_port_open(BACKEND_PORT)
                fe_open = _is_port_open(FRONTEND_PORT)
                be_ready = be_open and _is_http_ready(f"http://127.0.0.1:{BACKEND_PORT}/api/projects")
                fe_ready = fe_open

                if be_ready:
                    self.backend_starting = False
                elif self.backend_proc and self.backend_proc.poll() is not None:
                    self.backend_starting = False

                if fe_ready:
                    self.frontend_starting = False
                elif self.frontend_proc and self.frontend_proc.poll() is not None:
                    self.frontend_starting = False

                self.backend_up = be_ready
                self.frontend_up = fe_ready

                try:
                    self.root.after(0, self._update_status_ui, be_ready, be_open, fe_ready)
                except tk.TclError:
                    break
                self._stop_polling.wait(1.5)

        threading.Thread(target=_loop, daemon=True).start()

    def _update_status_ui(self, be_ready: bool, be_open: bool, fe_ready: bool) -> None:
        # Backend card
        if be_ready:
            owned = self.backend_proc is not None and self.backend_proc.poll() is None
            self.backend_dot.configure(fg=GREEN)
            self.backend_status_lbl.configure(
                text="Running" if owned else "Running (external process)", fg=GREEN
            )
            self.btn_be_toggle.configure(text="Stop")
        elif self.backend_starting or be_open:
            self.backend_dot.configure(fg=AMBER)
            self.backend_status_lbl.configure(text="Starting…", fg=AMBER)
            self.btn_be_toggle.configure(text="Stop")
        else:
            self.backend_dot.configure(fg=RED)
            self.backend_status_lbl.configure(text="Stopped", fg=TEXT_MUTED)
            self.btn_be_toggle.configure(text="Start")

        # Frontend card
        if fe_ready:
            owned = self.frontend_proc is not None and self.frontend_proc.poll() is None
            self.frontend_dot.configure(fg=GREEN)
            self.frontend_status_lbl.configure(
                text="Running" if owned else "Running (external process)", fg=GREEN
            )
            self.btn_fe_toggle.configure(text="Stop")
        elif self.frontend_starting:
            self.frontend_dot.configure(fg=AMBER)
            self.frontend_status_lbl.configure(text="Starting…", fg=AMBER)
            self.btn_fe_toggle.configure(text="Stop")
        else:
            self.frontend_dot.configure(fg=RED)
            self.frontend_status_lbl.configure(text="Stopped", fg=TEXT_MUTED)
            self.btn_fe_toggle.configure(text="Start")

        # Overall pill
        if be_ready and fe_ready:
            self.overall_pill.configure(text="● ALL SERVICES ONLINE", fg=GREEN)
        elif self.backend_starting or self.frontend_starting or be_ready or fe_ready:
            self.overall_pill.configure(text="● PARTIAL / STARTING", fg=AMBER)
        else:
            self.overall_pill.configure(text="● STOPPED", fg=RED)

    # ------------------------------------------------------------------
    # Process Controls
    # ------------------------------------------------------------------

    def _start_backend_sync(self) -> None:
        if _is_port_open(BACKEND_PORT):
            self._emit_log("SYSTEM", f"Backend is already listening on port {BACKEND_PORT}.")
            return
        py = _resolve_backend_python()
        if not os.path.isfile(py):
            self._emit_log("ERROR", f"Python not found at {py}. Run setup.ps1 first.")
            return

        os.makedirs(os.path.join(BACKEND_DIR, "bin"), exist_ok=True)
        cmd = [
            py, "-m", "uvicorn", "api:app",
            "--reload", "--host", "0.0.0.0", "--port", str(BACKEND_PORT),
            "--reload-exclude", os.path.join(BACKEND_DIR, ".venv"),
            "--reload-exclude", os.path.join(BACKEND_DIR, "bin"),
        ]
        self.backend_starting = True
        self._emit_log("SYSTEM", f"Starting Backend on port {BACKEND_PORT}…")
        try:
            self.backend_proc = subprocess.Popen(
                cmd,
                cwd=BACKEND_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
            threading.Thread(target=self._stream_pipe, args=(self.backend_proc, "BACKEND"), daemon=True).start()
        except Exception as exc:
            self.backend_starting = False
            self._emit_log("ERROR", f"Failed to start backend: {exc}")

    def _start_frontend_sync(self) -> None:
        if _is_port_open(FRONTEND_PORT):
            self._emit_log("SYSTEM", f"Frontend is already listening on port {FRONTEND_PORT}.")
            return
        if not os.path.isdir(os.path.join(FRONTEND_DIR, "node_modules")):
            self._emit_log("ERROR", "frontend/node_modules missing. Run setup.ps1 first.")
            return

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        self.frontend_starting = True
        self._emit_log("SYSTEM", f"Starting Frontend on port {FRONTEND_PORT}…")
        try:
            self.frontend_proc = subprocess.Popen(
                [npm_cmd, "run", "dev"],
                cwd=FRONTEND_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
            threading.Thread(target=self._stream_pipe, args=(self.frontend_proc, "FRONTEND"), daemon=True).start()
        except Exception as exc:
            self.frontend_starting = False
            self._emit_log("ERROR", f"Failed to start frontend: {exc}")

    def _stop_backend_sync(self) -> None:
        self._emit_log("SYSTEM", "Stopping Backend…")
        self.backend_starting = False
        if self.backend_proc is not None and self.backend_proc.poll() is None:
            _kill_pid_tree(self.backend_proc.pid)
        self.backend_proc = None
        for pid in _find_pids_on_port(BACKEND_PORT):
            _kill_pid_tree(pid)
        self._emit_log("SYSTEM", "Backend stopped.")

    def _stop_frontend_sync(self) -> None:
        self._emit_log("SYSTEM", "Stopping Frontend…")
        self.frontend_starting = False
        if self.frontend_proc is not None and self.frontend_proc.poll() is None:
            _kill_pid_tree(self.frontend_proc.pid)
        self.frontend_proc = None
        for pid in _find_pids_on_port(FRONTEND_PORT):
            _kill_pid_tree(pid)
        self._emit_log("SYSTEM", "Frontend stopped.")

    def _run_bg(self, fn) -> None:
        if self._busy:
            return
        self._busy = True

        def _wrap() -> None:
            try:
                fn()
            finally:
                self._busy = False

        threading.Thread(target=_wrap, daemon=True).start()

    def start_all(self) -> None:
        def _go() -> None:
            self._start_backend_sync()
            self._start_frontend_sync()
        self._run_bg(_go)

    def stop_all(self) -> None:
        def _go() -> None:
            self._stop_frontend_sync()
            self._stop_backend_sync()
        self._run_bg(_go)

    def restart_all(self) -> None:
        def _go() -> None:
            self._emit_log("SYSTEM", "Restarting all services…")
            self._stop_frontend_sync()
            self._stop_backend_sync()
            time.sleep(0.8)
            self._start_backend_sync()
            self._start_frontend_sync()
        self._run_bg(_go)

    def toggle_backend(self) -> None:
        if self.backend_up or self.backend_starting or _is_port_open(BACKEND_PORT):
            self._run_bg(self._stop_backend_sync)
        else:
            self._run_bg(self._start_backend_sync)

    def toggle_frontend(self) -> None:
        if self.frontend_up or self.frontend_starting or _is_port_open(FRONTEND_PORT):
            self._run_bg(self._stop_frontend_sync)
        else:
            self._run_bg(self._start_frontend_sync)

    def _on_close(self) -> None:
        owned_running = (
            (self.backend_proc is not None and self.backend_proc.poll() is None)
            or (self.frontend_proc is not None and self.frontend_proc.poll() is None)
        )
        if owned_running:
            ans = messagebox.askyesnocancel(
                "Close OpenClip Dashboard",
                "OpenClip servers started by this dashboard are still running.\n\n"
                "Yes — Stop servers and exit\n"
                "No — Keep servers running in background\n"
                "Cancel — Stay open",
            )
            if ans is None:
                return
            if ans is True:
                self._stop_frontend_sync()
                self._stop_backend_sync()

        self._stop_polling.set()
        self.root.destroy()


def main() -> None:
    _enable_hidpi()
    root = tk.Tk()
    OpenClipDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
