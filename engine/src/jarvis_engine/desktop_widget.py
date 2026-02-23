from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

import tkinter as tk


@dataclass
class WidgetConfig:
    base_url: str
    token: str
    signing_key: str
    device_id: str
    master_password: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _security_dir(root: Path) -> Path:
    return root / ".planning" / "security"


def _mobile_api_cfg_path(root: Path) -> Path:
    return _security_dir(root) / "mobile_api.json"


def _widget_cfg_path(root: Path) -> Path:
    return _security_dir(root) / "desktop_widget.json"


def _load_mobile_api_cfg(root: Path) -> dict[str, str]:
    path = _mobile_api_cfg_path(root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        "token": str(raw.get("token", "")).strip(),
        "signing_key": str(raw.get("signing_key", "")).strip(),
    }


def _load_widget_cfg(root: Path) -> WidgetConfig:
    mobile = _load_mobile_api_cfg(root)
    path = _widget_cfg_path(root)
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (json.JSONDecodeError, OSError):
            raw = {}

    return WidgetConfig(
        base_url=str(raw.get("base_url", "http://127.0.0.1:8787")).strip() or "http://127.0.0.1:8787",
        token=str(raw.get("token", "")).strip() or mobile.get("token", ""),
        signing_key=str(raw.get("signing_key", "")).strip() or mobile.get("signing_key", ""),
        device_id=str(raw.get("device_id", "galaxy_s25_primary")).strip() or "galaxy_s25_primary",
        master_password=str(raw.get("master_password", "")),
    )


def _save_widget_cfg(root: Path, cfg: WidgetConfig) -> None:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    payload = {
        "base_url": cfg.base_url,
        "token": cfg.token,
        "signing_key": cfg.signing_key,
        "device_id": cfg.device_id,
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _atomic_write_json(_widget_cfg_path(root), payload)


def _signed_headers(token: str, signing_key: str, body: bytes, device_id: str) -> dict[str, str]:
    ts = str(time.time())
    nonce = uuid.uuid4().hex
    signing_material = ts.encode("utf-8") + b"\n" + nonce.encode("utf-8") + b"\n" + body
    sig = hmac.new(signing_key.encode("utf-8"), signing_material, hashlib.sha256).hexdigest()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Jarvis-Timestamp": ts,
        "X-Jarvis-Nonce": nonce,
        "X-Jarvis-Signature": sig,
    }
    if device_id.strip():
        headers["X-Jarvis-Device-Id"] = device_id.strip()
    return headers


def _is_safe_widget_base_url(url: str) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme == "https":
        return True
    return host in {"127.0.0.1", "localhost", "::1"}


def _http_json(cfg: WidgetConfig, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _is_safe_widget_base_url(cfg.base_url):
        raise RuntimeError("Widget base_url must use HTTPS for non-localhost hosts.")
    body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = _signed_headers(cfg.token, cfg.signing_key, body, cfg.device_id)
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url=f"{cfg.base_url.rstrip('/')}{path}", method=method, data=(None if payload is None else body), headers=headers)
    try:
        with urlopen(req, timeout=35) as resp:
            raw = resp.read().decode("utf-8")
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"HTTP request failed: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Invalid response payload")
    return parsed


def _http_json_bootstrap(base_url: str, master_password: str, device_id: str) -> dict[str, Any]:
    if not base_url.strip():
        raise RuntimeError("Base URL is required for bootstrap.")
    if not _is_safe_widget_base_url(base_url):
        raise RuntimeError("Bootstrap URL must use HTTPS for non-localhost hosts.")
    if not master_password.strip():
        raise RuntimeError("Master password is required for bootstrap.")
    payload = {
        "master_password": master_password.strip(),
        "device_id": device_id.strip(),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url=f"{base_url.rstrip('/')}/bootstrap",
        method="POST",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=35) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Invalid bootstrap response payload")
    return parsed


from jarvis_engine._shared import win_hidden_subprocess_kwargs as _win_hidden_subprocess_kwargs


def _http_error_details(exc: HTTPError) -> str:
    try:
        with exc:
            raw = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        raw = ""
    if raw:
        short = raw[:420].replace("\n", " ")
        return f"{exc}; body={short}"
    return str(exc)


def _voice_dictate_once(timeout_s: int = 8) -> str:
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine; "
        "$r.SetInputToDefaultAudioDevice(); "
        "$r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar)); "
        f"$res = $r.Recognize([TimeSpan]::FromSeconds({int(max(2, timeout_s))})); "
        "if ($res) { $res.Text }"
    )
    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_win_hidden_subprocess_kwargs(),
        )
    except OSError as exc:
        raise RuntimeError(f"Voice dictation failed: {exc}") from exc
    try:
        stdout, stderr = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired as exc:
        try:
            proc.kill()
        except OSError:
            pass
        raise RuntimeError("Voice dictation timed out") from exc
    if proc.returncode != 0:
        raise RuntimeError((stderr or "").strip() or "Voice dictation failed")
    return (stdout or "").strip()


def _detect_hotword_once(keyword: str = "jarvis", timeout_s: int = 2) -> bool:
    keyword = keyword.strip().lower()[:40] or "jarvis"
    if not re.fullmatch(r"[a-z0-9 ]{1,40}", keyword):
        return False
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine; "
        "$r.SetInputToDefaultAudioDevice(); "
        "$choices = New-Object System.Speech.Recognition.Choices; "
        f"$choices.Add('{keyword}'); "
        "$grammar = New-Object System.Speech.Recognition.Grammar((New-Object System.Speech.Recognition.GrammarBuilder($choices))); "
        "$r.LoadGrammar($grammar); "
        f"$res = $r.Recognize([TimeSpan]::FromSeconds({int(max(1, timeout_s))})); "
        "if ($res) { $res.Text }"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        **_win_hidden_subprocess_kwargs(),
    )
    if proc.returncode != 0:
        return False
    return proc.stdout.strip().lower() == keyword


class JarvisDesktopWidget(tk.Tk):
    BG = "#070d1a"
    PANEL = "#0d1628"
    EDGE = "#1e3250"
    TEXT = "#dce8ff"
    MUTED = "#8ea4c5"
    ACCENT = "#12c9b1"
    ACCENT_2 = "#1aa3ff"
    WARN = "#d15a5a"
    LAUNCHER_TRANSPARENT = "#010203"

    def __init__(self, root_path: Path) -> None:
        super().__init__()
        self.root_path = root_path
        self.cfg = _load_widget_cfg(root_path)
        self.stop_event = threading.Event()
        self.online = False
        self._pulse_phase = 0.0
        self._launcher_phase = 0.0
        self._launcher_size = 84
        self.launcher_win: tk.Toplevel | None = None
        self.launcher_canvas: tk.Canvas | None = None
        self._launcher_outer_id: int | None = None
        self._launcher_inner_id: int | None = None
        self._launcher_ring_2_id: int | None = None
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._launcher_dragged = False

        self.title("Jarvis Unlimited")
        self.geometry("470x760+40+60")
        self.minsize(420, 620)
        self.configure(bg=self.BG)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._build_launcher()
        self._bind_shortcuts()
        self._start_status_workers()
        self._animate_orb()
        self._animate_launcher()
        self._hide_panel()
        self._log("Widget online. Enter sends command, Shift+Enter inserts newline.")

    def _on_close(self) -> None:
        self._hide_panel()

    def _shutdown(self) -> None:
        self.stop_event.set()
        if self.launcher_win is not None:
            try:
                self.launcher_win.destroy()
            except Exception:
                pass
        self.destroy()

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-space>", lambda _e: self._toggle_min())
        self.bind("<Escape>", lambda _e: self._toggle_min())
        self.bind("<Control-Return>", lambda _e: self._send_command_async())
        self.bind("<Control-Shift-Q>", lambda _e: self._shutdown())

    def _toggle_min(self) -> None:
        if self.state() in {"withdrawn", "iconic"}:
            self._show_panel()
        else:
            self._hide_panel()

    def _show_panel(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        if self.launcher_win is not None:
            self.launcher_win.withdraw()

    def _hide_panel(self) -> None:
        self.withdraw()
        if self.launcher_win is not None:
            self.launcher_win.deiconify()
            self.launcher_win.lift()

    def _build_launcher(self) -> None:
        launcher = tk.Toplevel(self)
        launcher.overrideredirect(True)
        launcher.attributes("-topmost", True)
        launcher.configure(bg=self.LAUNCHER_TRANSPARENT)
        try:
            launcher.wm_attributes("-transparentcolor", self.LAUNCHER_TRANSPARENT)
        except Exception:
            pass
        size = self._launcher_size
        screen_w = launcher.winfo_screenwidth()
        screen_h = launcher.winfo_screenheight()
        x = max(8, screen_w - size - 24)
        y = max(8, screen_h - size - 96)
        launcher.geometry(f"{size}x{size}+{x}+{y}")

        canvas = tk.Canvas(
            launcher,
            width=size,
            height=size,
            highlightthickness=0,
            bd=0,
            bg=self.LAUNCHER_TRANSPARENT,
            cursor="hand2",
        )
        canvas.pack(fill=tk.BOTH, expand=True)
        self._launcher_outer_id = canvas.create_oval(5, 5, size - 5, size - 5, outline="#2dd4bf", width=2)
        self._launcher_ring_2_id = canvas.create_oval(9, 9, size - 9, size - 9, outline="#0ea5e9", width=1)
        self._launcher_inner_id = canvas.create_oval(16, 16, size - 16, size - 16, fill="#0f766e", outline="")
        canvas.create_text(size / 2, size / 2, text="J", fill="#ecfeff", font=("Segoe UI", 16, "bold"))

        canvas.bind("<ButtonPress-1>", self._launcher_start_drag)
        canvas.bind("<B1-Motion>", self._launcher_drag)
        canvas.bind("<ButtonRelease-1>", self._launcher_release)
        canvas.bind("<Button-3>", lambda _e: self._shutdown())
        launcher.bind("<ButtonPress-1>", self._launcher_start_drag)
        launcher.bind("<B1-Motion>", self._launcher_drag)
        launcher.bind("<ButtonRelease-1>", self._launcher_release)
        launcher.bind("<Control-Shift-Q>", lambda _e: self._shutdown())
        self.launcher_win = launcher
        self.launcher_canvas = canvas

    def _launcher_start_drag(self, event):  # type: ignore[no-untyped-def]
        self._drag_offset_x = int(event.x)
        self._drag_offset_y = int(event.y)
        self._launcher_dragged = False

    def _launcher_drag(self, event):  # type: ignore[no-untyped-def]
        if self.launcher_win is None:
            return
        self._launcher_dragged = True
        x = int(self.launcher_win.winfo_x() + event.x - self._drag_offset_x)
        y = int(self.launcher_win.winfo_y() + event.y - self._drag_offset_y)
        self.launcher_win.geometry(f"+{x}+{y}")

    def _launcher_release(self, _event):  # type: ignore[no-untyped-def]
        if not self._launcher_dragged:
            self._show_panel()

    def _build_ui(self) -> None:
        shell = tk.Frame(self, bg=self.BG, bd=0)
        shell.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        header = tk.Frame(shell, bg=self.PANEL, highlightbackground=self.EDGE, highlightthickness=1)
        header.pack(fill=tk.X)

        top = tk.Frame(header, bg=self.PANEL)
        top.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(top, text="Jarvis Unlimited", bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        tk.Button(
            top,
            text="Minimize",
            bg="#10213a",
            fg=self.TEXT,
            activebackground="#173158",
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            command=self._hide_panel,
        ).pack(side=tk.RIGHT)
        tk.Button(
            top,
            text="Exit",
            bg="#2a1111",
            fg="#fecaca",
            activebackground="#4a1b1b",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            command=self._shutdown,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        status_row = tk.Frame(header, bg=self.PANEL)
        status_row.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.orb_canvas = tk.Canvas(status_row, width=26, height=26, bg=self.PANEL, highlightthickness=0)
        self.orb_canvas.pack(side=tk.LEFT)
        self.orb_id = self.orb_canvas.create_oval(8, 8, 18, 18, fill=self.WARN, outline="")
        self.status_var = tk.StringVar(value="OFFLINE")
        tk.Label(status_row, textvariable=self.status_var, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(status_row, text="Hotword: say 'Jarvis'", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9)).pack(side=tk.RIGHT)

        body = tk.Frame(shell, bg=self.PANEL, highlightbackground=self.EDGE, highlightthickness=1)
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        sec = tk.LabelFrame(body, text="Secure Session", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        sec.pack(fill=tk.X, padx=10, pady=(10, 8))

        self.base_var = tk.StringVar(value=self.cfg.base_url)
        self.token_var = tk.StringVar(value=self.cfg.token)
        self.key_var = tk.StringVar(value=self.cfg.signing_key)
        self.device_var = tk.StringVar(value=self.cfg.device_id)
        self.master_var = tk.StringVar(value=self.cfg.master_password)

        self._entry(sec, "Base URL", self.base_var)
        self._entry(sec, "Bearer token", self.token_var)
        self._entry(sec, "Signing key", self.key_var)
        self._entry(sec, "Device ID", self.device_var)
        self._entry(sec, "Master password", self.master_var, show="*")

        sec_buttons = tk.Frame(sec, bg=self.PANEL)
        sec_buttons.pack(fill=tk.X, padx=6, pady=(4, 8))
        tk.Button(
            sec_buttons,
            text="Save on Device",
            bg="#133d70",
            fg="#eaf3ff",
            relief=tk.FLAT,
            command=self._save_session,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(
            sec_buttons,
            text="Bootstrap",
            bg="#0f766e",
            fg="#ecfeff",
            relief=tk.FLAT,
            command=self._bootstrap_session_async,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        cmd_block = tk.Frame(body, bg=self.PANEL)
        cmd_block.pack(fill=tk.X, padx=10)
        tk.Label(cmd_block, text="Command", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.command_text = tk.Text(
            cmd_block,
            height=5,
            wrap=tk.WORD,
            bg="#081127",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief=tk.FLAT,
            highlightbackground="#2a4368",
            highlightthickness=1,
            font=("Consolas", 11),
        )
        self.command_text.pack(fill=tk.X, pady=(4, 4))
        self.command_text.bind("<Return>", self._on_command_enter)

        flags = tk.Frame(body, bg=self.PANEL)
        flags.pack(fill=tk.X, padx=10, pady=(2, 0))
        self.execute_var = tk.BooleanVar(value=False)
        self.priv_var = tk.BooleanVar(value=False)
        self.speak_var = tk.BooleanVar(value=False)
        self.auto_send_var = tk.BooleanVar(value=True)
        self.hotword_var = tk.BooleanVar(value=False)
        self._check(flags, "Execute", self.execute_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Privileged", self.priv_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Speak", self.speak_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Auto Send", self.auto_send_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Wake Word", self.hotword_var, cmd=self._hotword_changed).pack(side=tk.LEFT)

        row = tk.Frame(body, bg=self.PANEL)
        row.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._btn(row, "Voice Dictate", self._dictate_async, self.ACCENT_2).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._btn(row, "Send", self._send_command_async, self.ACCENT).pack(side=tk.LEFT, fill=tk.X, expand=True)

        quick = tk.Frame(body, bg=self.PANEL)
        quick.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._btn(quick, "Pause", lambda: self._quick_phrase("Jarvis, pause daemon"), self.WARN).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._btn(quick, "Resume", lambda: self._quick_phrase("Jarvis, resume daemon"), self.ACCENT).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._btn(quick, "Safe On", lambda: self._quick_phrase("Jarvis, enable safe mode"), self.ACCENT_2).pack(side=tk.LEFT, fill=tk.X, expand=True)

        fetch = tk.Frame(body, bg=self.PANEL)
        fetch.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._btn(fetch, "Refresh Settings", self._refresh_settings_async, "#35517a").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._btn(fetch, "Refresh Dashboard", self._refresh_dashboard_async, "#35517a").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._btn(fetch, "Diagnose + Repair", self._diagnose_repair_async, "#1f5f88").pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(body, text="Output", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
        self.output = tk.Text(
            body,
            height=12,
            wrap=tk.WORD,
            bg="#081127",
            fg="#d6e4ff",
            insertbackground="#d6e4ff",
            relief=tk.FLAT,
            highlightbackground="#2a4368",
            highlightthickness=1,
            font=("Consolas", 10),
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))

    def _entry(self, parent: tk.Widget, label: str, var: tk.StringVar, show: str | None = None) -> None:
        tk.Label(parent, text=label, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=6, pady=(4, 0))
        tk.Entry(
            parent,
            textvariable=var,
            show=show or "",
            bg="#081127",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief=tk.FLAT,
            highlightbackground="#2a4368",
            highlightthickness=1,
        ).pack(fill=tk.X, padx=6, pady=(2, 0))

    def _check(self, parent: tk.Widget, text: str, variable: tk.BooleanVar, cmd=None):  # type: ignore[no-untyped-def]
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=cmd,
            bg=self.PANEL,
            fg=self.MUTED,
            selectcolor="#1a2742",
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 9),
        )

    def _btn(self, parent: tk.Widget, text: str, command, color: str):  # type: ignore[no-untyped-def]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg="#eef8ff",
            activebackground=color,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=8,
            pady=6,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )

    def _on_command_enter(self, event):  # type: ignore[no-untyped-def]
        if event.state & 0x0001:  # Shift key pressed
            return None
        self._send_command_async()
        return "break"

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.output.insert("1.0", f"[{stamp}] {message}\n")
        self.output.see("1.0")

    def _log_async(self, message: str) -> None:
        self.after(0, self._log, message)

    def _set_command_text(self, value: str) -> None:
        self.command_text.delete("1.0", tk.END)
        self.command_text.insert("1.0", value)

    def _set_command_text_async(self, value: str) -> None:
        self.after(0, self._set_command_text, value)

    def _current_cfg(self) -> WidgetConfig:
        return WidgetConfig(
            base_url=self.base_var.get().strip() or "http://127.0.0.1:8787",
            token=self.token_var.get().strip(),
            signing_key=self.key_var.get().strip(),
            device_id=self.device_var.get().strip(),
            master_password=self.master_var.get(),
        )

    def _save_session(self) -> None:
        cfg = self._current_cfg()
        _save_widget_cfg(self.root_path, cfg)
        self._log("Session saved locally.")

    def _apply_session_update(self, session: dict[str, Any]) -> None:
        base_url = str(session.get("base_url", "")).strip()
        token = str(session.get("token", "")).strip()
        signing_key = str(session.get("signing_key", "")).strip()
        device_id = str(session.get("device_id", "")).strip()
        if base_url:
            self.base_var.set(base_url)
        if token:
            self.token_var.set(token)
        if signing_key:
            self.key_var.set(signing_key)
        if device_id:
            self.device_var.set(device_id)
        self._save_session()

    def _bootstrap_session_async(self) -> None:
        cfg = self._current_cfg()
        if not cfg.base_url.strip():
            self._log("Bootstrap failed: missing Base URL.")
            return
        if not cfg.master_password.strip():
            self._log("Bootstrap failed: enter Master password first.")
            return

        def worker() -> None:
            try:
                data = _http_json_bootstrap(cfg.base_url, cfg.master_password, cfg.device_id)
                ok = bool(data.get("ok", False))
                session = data.get("session", {})
                if (not ok) or (not isinstance(session, dict)):
                    raise RuntimeError(str(data.get("error", "Bootstrap returned no session data.")))
                self.after(0, self._apply_session_update, session)
                trusted = bool(session.get("trusted_device", False))
                self._log_async(f"Bootstrap complete. trusted_device={trusted}")
            except HTTPError as exc:
                self._log_async(f"bootstrap failed: {_http_error_details(exc)}")
            except (URLError, RuntimeError, TimeoutError) as exc:
                self._log_async(f"bootstrap failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"bootstrap failed: {exc}")

        self._thread(worker)

    def _diagnose_repair_async(self) -> None:
        def worker() -> None:
            cfg = self._current_cfg()
            try:
                self._log_async("Running sync checks...")
                sync_data = _http_json(cfg, "/sync/status", method="GET")
                sync_ok = bool(sync_data.get("ok", False))
                self._log_async(f"sync ok={sync_ok}")
                last_sync = sync_data.get("last_sync_utc", "")
                if last_sync:
                    self._log_async(f"last sync: {last_sync}")

                self._log_async("Running self-heal...")
                heal_data = _http_json(
                    cfg,
                    "/self-heal",
                    method="POST",
                    payload={
                        "keep_recent": 1800,
                        "force_maintenance": True,
                        "snapshot_note": "widget-diagnose",
                    },
                )
                heal_ok = bool(heal_data.get("ok", False))
                heal_exit = int(heal_data.get("command_exit_code", -1))
                self._log_async(f"self-heal ok={heal_ok} exit={heal_exit}")
                heal_lines = heal_data.get("stdout_tail", [])
                if isinstance(heal_lines, list) and heal_lines:
                    self._log_async(" | ".join(str(x) for x in heal_lines[-4:]))
                if sync_ok and heal_ok:
                    self._log_async("Diagnose + Repair completed.")
            except HTTPError as exc:
                self._log_async(f"diagnose failed: {_http_error_details(exc)}")
            except (URLError, RuntimeError, TimeoutError) as exc:
                self._log_async(f"diagnose failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"diagnose failed: {exc}")

        self._thread(worker)

    def _thread(self, fn) -> None:  # type: ignore[no-untyped-def]
        threading.Thread(target=fn, daemon=True).start()

    def _send_command_async(self) -> None:
        text = self.command_text.get("1.0", tk.END).strip()
        if not text:
            self._log("No command text.")
            return

        def worker() -> None:
            try:
                cfg = self._current_cfg()
                payload = {
                    "text": text,
                    "execute": bool(self.execute_var.get()),
                    "approve_privileged": bool(self.priv_var.get()),
                    "speak": bool(self.speak_var.get()),
                }
                data = _http_json(cfg, "/command", method="POST", payload=payload)
                intent = str(data.get("intent", "unknown"))
                ok = bool(data.get("ok", False))
                self._log_async(f"intent={intent} ok={ok}")
                lines = data.get("stdout_tail", [])
                if isinstance(lines, list) and lines:
                    self._log_async(" | ".join(str(x) for x in lines[-6:]))
            except HTTPError as exc:
                self._log_async(f"command failed: {_http_error_details(exc)}")
            except (URLError, RuntimeError, TimeoutError) as exc:
                self._log_async(f"command failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"command failed: {exc}")

        self._thread(worker)

    def _quick_phrase(self, text: str) -> None:
        self._set_command_text(text)
        self._send_command_async()

    def _refresh_settings_async(self) -> None:
        def worker() -> None:
            try:
                data = _http_json(self._current_cfg(), "/settings", method="GET")
                settings = data.get("settings", {})
                self._log_async(json.dumps(settings, ensure_ascii=True)[:600])
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"settings failed: {exc}")

        self._thread(worker)

    def _refresh_dashboard_async(self) -> None:
        def worker() -> None:
            try:
                data = _http_json(self._current_cfg(), "/dashboard", method="GET")
                dash = data.get("dashboard", {})
                jar = dash.get("jarvis", {}) if isinstance(dash, dict) else {}
                mem = dash.get("memory_regression", {}) if isinstance(dash, dict) else {}
                self._log_async(
                    f"score={jar.get('score_pct', 0.0)} delta={jar.get('delta_vs_prev_pct', 0.0)} "
                    f"memory={mem.get('status', 'unknown')}"
                )
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"dashboard failed: {exc}")

        self._thread(worker)

    def _dictate_async(self) -> None:
        auto_send = bool(self.auto_send_var.get())

        def worker() -> None:
            try:
                text = _voice_dictate_once(timeout_s=8)
                if not text:
                    self._log_async("No speech recognized.")
                    return
                self._set_command_text_async(text)
                self._log_async(f"dictated: {text}")
                if auto_send:
                    self.after(0, self._send_command_async)
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"dictation failed: {exc}")

        self._thread(worker)

    def _hotword_changed(self) -> None:
        if self.hotword_var.get():
            self._log("Wake Word enabled. Say 'Jarvis' to trigger dictation.")
            self._thread(self._hotword_loop)
        else:
            self._log("Wake Word disabled.")

    def _hotword_loop(self) -> None:
        while self.hotword_var.get() and (not self.stop_event.is_set()):
            try:
                heard = _detect_hotword_once(keyword="jarvis", timeout_s=2)
                if heard:
                    self.after(0, self._show_panel)
                    self._log_async("Wake word detected.")
                    self.after(0, self._dictate_async)
            except Exception as exc:
                logger.warning("Hotword detection error: %s", exc)
            for _ in range(6):
                if self.stop_event.is_set() or (not self.hotword_var.get()):
                    return
                time.sleep(0.5)

    def _start_status_workers(self) -> None:
        self._thread(self._health_loop)

    def _health_loop(self) -> None:
        while not self.stop_event.is_set():
            cfg = self._current_cfg()
            if not _is_safe_widget_base_url(cfg.base_url):
                self.online = False
                self.after(0, self._refresh_status_view)
                for _ in range(16):
                    if self.stop_event.is_set():
                        return
                    time.sleep(0.5)
                continue
            url = f"{cfg.base_url.rstrip('/')}/health"
            ok = False
            for _attempt in range(2):
                try:
                    req = Request(url=url, method="GET")
                    with urlopen(req, timeout=5) as resp:
                        ok = resp.status == 200
                    if ok:
                        break
                except Exception:
                    ok = False
                if self.stop_event.is_set():
                    break
                time.sleep(0.2)
            self.online = ok
            self.after(0, self._refresh_status_view)
            for _ in range(16):
                if self.stop_event.is_set():
                    return
                time.sleep(0.5)

    def _refresh_status_view(self) -> None:
        self.status_var.set("ONLINE" if self.online else "OFFLINE")

    def _animate_orb(self) -> None:
        self._pulse_phase += 0.22
        pulse = 5.0 + (math.sin(self._pulse_phase) * 1.8)
        cx, cy = 13.0, 13.0
        x0 = cx - pulse
        y0 = cy - pulse
        x1 = cx + pulse
        y1 = cy + pulse
        color = self.ACCENT if self.online else self.WARN
        self.orb_canvas.coords(self.orb_id, x0, y0, x1, y1)
        self.orb_canvas.itemconfig(self.orb_id, fill=color)
        self.after(120, self._animate_orb)

    def _animate_launcher(self) -> None:
        if self.launcher_canvas is not None and self._launcher_outer_id is not None and self._launcher_inner_id is not None:
            size = self._launcher_size
            self._launcher_phase += 0.18
            pulse = 1.0 + (math.sin(self._launcher_phase) * 1.2)
            outer_pad = 4.0 + pulse
            mid_pad = 8.0 + (pulse * 0.8)
            inner_pad = 15.0 + (pulse * 0.7)
            glow = "#2dd4bf" if self.online else "#93c5fd"
            ring = "#0ea5e9" if self.online else "#60a5fa"
            core = "#0f766e" if self.online else "#1e3a8a"
            self.launcher_canvas.coords(self._launcher_outer_id, outer_pad, outer_pad, size - outer_pad, size - outer_pad)
            if self._launcher_ring_2_id is not None:
                self.launcher_canvas.coords(self._launcher_ring_2_id, mid_pad, mid_pad, size - mid_pad, size - mid_pad)
            self.launcher_canvas.coords(self._launcher_inner_id, inner_pad, inner_pad, size - inner_pad, size - inner_pad)
            self.launcher_canvas.itemconfig(self._launcher_outer_id, outline=glow)
            if self._launcher_ring_2_id is not None:
                self.launcher_canvas.itemconfig(self._launcher_ring_2_id, outline=ring)
            self.launcher_canvas.itemconfig(self._launcher_inner_id, fill=core)
        self.after(70, self._animate_launcher)


def run_desktop_widget() -> None:
    app = JarvisDesktopWidget(_repo_root())
    app.mainloop()
