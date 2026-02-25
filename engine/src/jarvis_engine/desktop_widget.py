from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import hashlib
import hmac
import json
import logging
import math
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DPAPI helpers – Windows Data Protection API via ctypes
# Encrypts/decrypts data tied to the current Windows user account.
# ---------------------------------------------------------------------------

_DPAPI_AVAILABLE = sys.platform == "win32"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* via Windows DPAPI, return base64-encoded ciphertext.

    Raises ``OSError`` if the DPAPI call fails.  On non-Windows platforms the
    function raises ``RuntimeError``.
    """
    if not _DPAPI_AVAILABLE:
        raise RuntimeError("DPAPI is only available on Windows")
    data = plaintext.encode("utf-8")
    input_blob = _DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
    output_blob = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(input_blob),
        None,   # description (optional)
        None,   # optional entropy
        None,   # reserved
        None,   # prompt struct
        0,      # flags
        ctypes.byref(output_blob),
    ):
        raise OSError("CryptProtectData failed")
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)  # type: ignore[attr-defined]
    return base64.b64encode(encrypted).decode("ascii")


def _dpapi_decrypt(b64_cipher: str) -> str:
    """Decrypt a base64-encoded DPAPI ciphertext, return plaintext string.

    Raises ``OSError`` if the DPAPI call fails.  On non-Windows platforms the
    function raises ``RuntimeError``.
    """
    if not _DPAPI_AVAILABLE:
        raise RuntimeError("DPAPI is only available on Windows")
    encrypted = base64.b64decode(b64_cipher)
    input_blob = _DATA_BLOB(len(encrypted), ctypes.create_string_buffer(encrypted, len(encrypted)))
    output_blob = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(input_blob),
        None,   # description out
        None,   # optional entropy
        None,   # reserved
        None,   # prompt struct
        0,      # flags
        ctypes.byref(output_blob),
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        decrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)  # type: ignore[attr-defined]
    return decrypted.decode("utf-8")

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

    # --- Resolve master password (DPAPI-protected or plaintext legacy) ---
    master_password = ""
    needs_migration = False
    if "master_password_protected" in raw:
        b64 = str(raw["master_password_protected"])
        try:
            master_password = _dpapi_decrypt(b64)
        except Exception:
            logger.warning("Failed to decrypt master_password_protected via DPAPI; password unavailable")
    elif "master_password" in raw:
        master_password = str(raw["master_password"])
        if master_password:
            needs_migration = True

    cfg = WidgetConfig(
        base_url=str(raw.get("base_url", "http://127.0.0.1:8787")).strip() or "http://127.0.0.1:8787",
        token=str(raw.get("token", "")).strip() or mobile.get("token", ""),
        signing_key=str(raw.get("signing_key", "")).strip() or mobile.get("signing_key", ""),
        device_id=str(raw.get("device_id", "galaxy_s25_primary")).strip() or "galaxy_s25_primary",
        master_password=master_password,
    )

    # Migrate plaintext master_password -> DPAPI-protected on load
    if needs_migration:
        try:
            _save_widget_cfg(root, cfg)
            logger.info("Migrated plaintext master_password to DPAPI-protected storage")
        except Exception:
            logger.warning("Failed to migrate plaintext master_password to DPAPI; will retry on next save")

    return cfg


def _save_widget_cfg(root: Path, cfg: WidgetConfig) -> None:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    payload: dict[str, Any] = {
        "base_url": cfg.base_url,
        "token": cfg.token,
        "signing_key": cfg.signing_key,
        "device_id": cfg.device_id,
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Encrypt master password via DPAPI; fall back to plaintext only on non-Windows
    if cfg.master_password:
        try:
            payload["master_password_protected"] = _dpapi_encrypt(cfg.master_password)
        except Exception:
            logger.warning("DPAPI encryption unavailable; storing master_password in plaintext")
            payload["master_password"] = cfg.master_password
    # Never write the plaintext key when DPAPI succeeds (no "master_password" key at all)

    _atomic_write_json(_widget_cfg_path(root), payload)


def _signed_headers(token: str, signing_key: str, body: bytes, device_id: str) -> dict[str, str]:
    ts = str(int(time.time()))
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
    import ipaddress
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme == "https":
        return True
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    # Allow HTTP for private/LAN IPs (trusted local network)
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private:
            return True
    except ValueError:
        pass
    return False


def _make_ssl_context_for_self_signed() -> ssl.SSLContext:
    """Create an SSL context that accepts self-signed certificates.

    This is safe for LAN communication with the Jarvis mobile API server
    where the self-signed cert is generated locally.  Hostname verification
    and CA trust are disabled because the cert is not issued by a public CA.

    SECURITY NOTE: This context should ONLY be used for connections to
    the local Jarvis server (private/loopback IPs).  ``_is_safe_widget_base_url``
    gates all callers to ensure this.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get_ssl_context(url: str) -> ssl.SSLContext | None:
    """Return an SSL context if the URL is HTTPS, else None."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return _make_ssl_context_for_self_signed()
    return None


def _http_json(cfg: WidgetConfig, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _is_safe_widget_base_url(cfg.base_url):
        raise RuntimeError("Widget base_url must use HTTPS for non-localhost hosts.")
    body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = _signed_headers(cfg.token, cfg.signing_key, body, cfg.device_id)
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url=f"{cfg.base_url.rstrip('/')}{path}", method=method, data=(None if payload is None else body), headers=headers)
    ssl_ctx = _get_ssl_context(cfg.base_url)
    try:
        with urlopen(req, timeout=35, context=ssl_ctx) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP request failed: HTTP {exc.code} {exc.reason}") from exc
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
    ssl_ctx = _get_ssl_context(base_url)
    with urlopen(req, timeout=35, context=ssl_ctx) as resp:
        raw = resp.read().decode("utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid bootstrap JSON: {exc}") from exc
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
    """Transcribe speech from microphone using faster-whisper (Whisper AI model).

    Falls back to Windows System.Speech if faster-whisper or sounddevice
    are not installed.
    """
    try:
        from jarvis_engine.stt import listen_and_transcribe
        result = listen_and_transcribe(
            max_duration_seconds=float(max(3, timeout_s)),
            language="en",
        )
        return result.text.strip()
    except RuntimeError:
        # faster-whisper or sounddevice not available -- fall back to System.Speech
        pass
    except Exception as exc:
        logger.warning("Whisper STT failed, falling back to System.Speech: %s", exc)
    # Fallback: Windows System.Speech via PowerShell
    return _voice_dictate_system_speech(timeout_s)


def _voice_dictate_system_speech(timeout_s: int = 8) -> str:
    """Legacy Windows System.Speech dictation (lower quality fallback)."""
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
            proc.wait(timeout=5)
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


# ---------------------------------------------------------------------------
# Windows toast notifications via PowerShell BalloonTip (no external deps)
# ---------------------------------------------------------------------------

_TOAST_ICON_TYPES = {"Info", "Warning", "Error"}
_TOAST_MAX_TITLE = 64
_TOAST_MAX_MESSAGE = 256
_TOAST_COOLDOWN_SECONDS = 120  # Max 1 toast per 2 minutes

# Module-level throttle state (thread-safe via GIL for simple reads/writes)
_last_toast_time: float = 0.0
_toast_lock = threading.Lock()


def _show_toast(title: str, message: str, icon: str = "Info") -> None:
    """Show a Windows balloon-tip notification via PowerShell.

    Fire-and-forget: launches a detached PowerShell process and returns
    immediately.  Errors are logged but never raised.

    Args:
        title: Notification title (truncated to 64 chars).
        message: Notification body (truncated to 256 chars).
        icon: One of "Info", "Warning", "Error".
    """
    global _last_toast_time

    if icon not in _TOAST_ICON_TYPES:
        icon = "Info"
    title = (title or "Jarvis")[:_TOAST_MAX_TITLE]
    message = (message or "")[:_TOAST_MAX_MESSAGE]

    # Throttle: max 1 toast per cooldown period
    with _toast_lock:
        now = time.time()
        if now - _last_toast_time < _TOAST_COOLDOWN_SECONDS:
            logger.debug("Toast throttled (cooldown active)")
            return
        _last_toast_time = now

    # Escape single quotes for PowerShell string literals
    safe_title = title.replace("'", "''")
    safe_message = message.replace("'", "''")

    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        f"$n.ShowBalloonTip(5000, '{safe_title}', '{safe_message}', "
        f"[System.Windows.Forms.ToolTipIcon]::{icon}); "
        "Start-Sleep 6; $n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_win_hidden_subprocess_kwargs(),
        )
    except Exception:
        logger.debug("Failed to launch toast notification", exc_info=True)


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
        self._hotword_active = threading.Event()  # Guards against multiple hotword loops
        self._orb_after_id: str | None = None
        self._launcher_after_id: str | None = None
        self._prev_svc_running: dict[str, bool] = {}  # Track service state for crash detection

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
        """Handle window close: minimize to launcher orb (tray-app pattern).
        Use the Exit button or Ctrl+Shift+Q for full shutdown."""
        self._hide_panel()

    def _shutdown(self) -> None:
        self.stop_event.set()
        # Cancel pending animation callbacks to prevent post-destroy TclError
        if self._orb_after_id is not None:
            try:
                self.after_cancel(self._orb_after_id)
            except Exception:
                pass
        if self._launcher_after_id is not None:
            try:
                self.after_cancel(self._launcher_after_id)
            except Exception:
                pass
        # Wait briefly for background threads to finish
        for t in threading.enumerate():
            if t.daemon and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=1.0)
        if self.launcher_win is not None:
            try:
                self.launcher_win.destroy()
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass

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
        self.intel_var = tk.StringVar(value="")
        self.intel_label = tk.Label(status_row, textvariable=self.intel_var, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9, "bold"))
        self.intel_label.pack(side=tk.RIGHT, padx=(0, 8))
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
        self.notify_var = tk.BooleanVar(value=True)
        self._check(flags, "Allow PC Actions", self.execute_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Auto-Approve", self.priv_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Speak", self.speak_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Auto Send", self.auto_send_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Wake Word", self.hotword_var, cmd=self._hotword_changed).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Notifications", self.notify_var).pack(side=tk.LEFT)

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
        self._btn(fetch, "Self-Heal", self._diagnose_repair_async, "#1f5f88").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._btn(fetch, "View Activity", self._view_activity_async, "#4a3570").pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Running Services section
        svc_frame = tk.LabelFrame(body, text="Running Services", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        svc_frame.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._svc_labels: dict[str, tuple[tk.Label, tk.Label]] = {}
        for svc_name, display_name in [("daemon", "Daemon"), ("mobile_api", "Mobile API"), ("widget", "Widget")]:
            row_f = tk.Frame(svc_frame, bg=self.PANEL)
            row_f.pack(fill=tk.X, padx=6, pady=2)
            dot = tk.Label(row_f, text="\u25CB", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 10))
            dot.pack(side=tk.LEFT)
            tk.Label(row_f, text=display_name, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 0))
            uptime_lbl = tk.Label(row_f, text="--", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9))
            uptime_lbl.pack(side=tk.RIGHT)
            self._svc_labels[svc_name] = (dot, uptime_lbl)
        self._refresh_services()

        # Brain Growth section
        growth_frame = tk.LabelFrame(body, text="Brain Growth", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        growth_frame.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._growth_labels: dict[str, tk.Label] = {}
        for key, display in [
            ("facts", "Facts"),
            ("kg", "KG Size"),
            ("memory", "Memory"),
            ("score", "Self-Test"),
            ("trend", "Trend"),
        ]:
            row_f = tk.Frame(growth_frame, bg=self.PANEL)
            row_f.pack(fill=tk.X, padx=6, pady=1)
            tk.Label(row_f, text=display, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9), width=10, anchor="w").pack(side=tk.LEFT)
            val = tk.Label(row_f, text="--", bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 9, "bold"), anchor="w")
            val.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)
            self._growth_labels[key] = val

        output_header = tk.Frame(body, bg=self.PANEL)
        output_header.pack(fill=tk.X, padx=10, pady=(10, 0))
        tk.Label(output_header, text="Conversation", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        tk.Button(
            output_header,
            text="Clear",
            bg="#1a2742",
            fg=self.MUTED,
            activebackground="#2a3752",
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            command=self._clear_history,
            cursor="hand2",
        ).pack(side=tk.RIGHT)
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
            state=tk.DISABLED,
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self._configure_chat_tags()

    def _configure_chat_tags(self) -> None:
        """Set up tag-based visual styles for the chat-style conversation display."""
        self.output.tag_configure(
            "user",
            background="#0c2d5e",
            foreground="#b8d4ff",
            font=("Consolas", 10, "bold"),
            lmargin1=40,
            lmargin2=40,
            rmargin=8,
            spacing1=4,
            spacing3=4,
        )
        self.output.tag_configure(
            "jarvis",
            background="#0d1e1e",
            foreground="#a8e6cf",
            font=("Consolas", 10),
            lmargin1=8,
            lmargin2=8,
            rmargin=40,
            spacing1=4,
            spacing3=4,
        )
        self.output.tag_configure(
            "system",
            foreground="#5a7a9e",
            font=("Consolas", 9),
            lmargin1=8,
            lmargin2=8,
            spacing1=2,
            spacing3=2,
        )
        self.output.tag_configure(
            "error",
            background="#1a0a0a",
            foreground="#f87171",
            font=("Consolas", 10),
            lmargin1=8,
            lmargin2=8,
            spacing1=4,
            spacing3=4,
        )
        self.output.tag_configure(
            "separator",
            foreground="#1e3250",
            font=("Consolas", 6),
            justify="center",
            spacing1=2,
            spacing3=2,
        )
        self.output.tag_configure(
            "timestamp",
            foreground="#3a5a7e",
            font=("Consolas", 8),
            justify="center",
            spacing1=6,
            spacing3=2,
        )

    def _clear_history(self) -> None:
        """Clear all text from the conversation display."""
        self.output.config(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.config(state=tk.DISABLED)

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

    def _log(self, message: str, role: str = "system") -> None:
        stamp = time.strftime("%H:%M:%S")
        self.output.config(state=tk.NORMAL)

        # Build the display line based on role
        if role == "user":
            prefix = "You"
            display = f"{prefix}: {message}\n"
        elif role == "jarvis":
            prefix = "Jarvis"
            display = f"{prefix}: {message}\n"
        elif role == "error":
            display = f"[{stamp}] ERROR: {message}\n"
        else:
            # system (default)
            display = f"[{stamp}] {message}\n"

        # Insert separator + timestamp before new exchanges (user messages)
        if role == "user":
            sep_line = "\u2500" * 48 + "\n"
            self.output.insert(tk.END, sep_line, "separator")
            self.output.insert(tk.END, f"  {stamp}  \n", "timestamp")

        # Insert the message with the appropriate tag
        tag = role if role in ("user", "jarvis", "system", "error") else "system"
        self.output.insert(tk.END, display, tag)

        self.output.see(tk.END)

        # Limit output widget to 500 lines to prevent unbounded memory growth
        line_count = int(self.output.index("end-1c").split(".")[0])
        if line_count > 500:
            self.output.delete("1.0", f"{line_count - 500}.0")

        self.output.config(state=tk.DISABLED)

    def _log_async(self, message: str, role: str = "system") -> None:
        if self.stop_event.is_set():
            return
        try:
            self.after(0, self._log, message, role)
        except Exception:
            pass  # Widget destroyed

    def _notify_toast(self, title: str, message: str, icon: str = "Info") -> None:
        """Send a toast notification if the Notifications toggle is enabled."""
        try:
            if self.notify_var.get():
                _show_toast(title, message, icon)
        except Exception:
            pass  # Widget may be destroyed

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
            self._log("Bootstrap failed: missing Base URL.", role="error")
            return
        if not cfg.master_password.strip():
            self._log("Bootstrap failed: enter Master password first.", role="error")
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
                self._log_async(f"Bootstrap complete. trusted_device={trusted}", role="jarvis")
            except HTTPError as exc:
                self._log_async(f"bootstrap failed: {_http_error_details(exc)}", role="error")
            except (URLError, RuntimeError, TimeoutError) as exc:
                self._log_async(f"bootstrap failed: {exc}", role="error")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"bootstrap failed: {exc}", role="error")

        self._thread(worker)

    def _diagnose_repair_async(self) -> None:
        cfg = self._current_cfg()  # Read tkinter vars on main thread

        def worker() -> None:
            try:
                self._log_async("Running sync checks...", role="system")
                sync_data = _http_json(cfg, "/sync/status", method="GET")
                sync_ok = bool(sync_data.get("ok", False))
                self._log_async(f"sync ok={sync_ok}", role="jarvis")
                last_sync = sync_data.get("last_sync_utc", "")
                if last_sync:
                    self._log_async(f"last sync: {last_sync}", role="jarvis")

                self._log_async("Running self-heal...", role="system")
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
                self._log_async(f"self-heal ok={heal_ok} exit={heal_exit}", role="jarvis")
                heal_lines = heal_data.get("stdout_tail", [])
                if isinstance(heal_lines, list) and heal_lines:
                    self._log_async(" | ".join(str(x) for x in heal_lines[-4:]), role="jarvis")
                if sync_ok and heal_ok:
                    self._log_async("Self-Heal completed.", role="jarvis")
                elif not heal_ok:
                    self._notify_toast("Jarvis Self-Heal", f"Self-heal finished with issues (exit={heal_exit})", "Warning")
            except HTTPError as exc:
                self._log_async(f"diagnose failed: {_http_error_details(exc)}", role="error")
                self._notify_toast("Jarvis Self-Heal", "Self-heal failed to complete", "Error")
            except (URLError, RuntimeError, TimeoutError) as exc:
                self._log_async(f"diagnose failed: {exc}", role="error")
                self._notify_toast("Jarvis Self-Heal", "Self-heal failed to complete", "Error")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"diagnose failed: {exc}", role="error")

        self._thread(worker)

    def _thread(self, fn) -> None:  # type: ignore[no-untyped-def]
        threading.Thread(target=fn, daemon=True).start()

    def _send_command_async(self) -> None:
        text = self.command_text.get("1.0", tk.END).strip()
        if not text:
            self._log("No command text.")
            return
        # Log the user's command with the "user" role
        self._log(text, role="user")
        # Read all tkinter vars on the main thread before spawning background thread
        cfg = self._current_cfg()
        execute = bool(self.execute_var.get())
        approve_privileged = bool(self.priv_var.get())
        speak = bool(self.speak_var.get())

        def worker() -> None:
            try:
                payload = {
                    "text": text,
                    "execute": execute,
                    "approve_privileged": approve_privileged,
                    "speak": speak,
                    "master_password": cfg.master_password,
                }
                data = _http_json(cfg, "/command", method="POST", payload=payload)
                intent = str(data.get("intent", "unknown"))
                ok = bool(data.get("ok", False))
                self._log_async(f"[{intent}] ok={ok}", role="jarvis")
                lines = data.get("stdout_tail", [])
                if isinstance(lines, list) and lines:
                    self._log_async(" | ".join(str(x) for x in lines[-6:]), role="jarvis")
            except HTTPError as exc:
                self._log_async(f"command failed: {_http_error_details(exc)}", role="error")
            except (URLError, RuntimeError, TimeoutError) as exc:
                self._log_async(f"command failed: {exc}", role="error")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"command failed: {exc}", role="error")

        self._thread(worker)

    def _quick_phrase(self, text: str) -> None:
        self._set_command_text(text)
        self._send_command_async()

    def _refresh_services(self) -> None:
        """Update service status dots directly from process_manager (no HTTP)."""
        try:
            from jarvis_engine.process_manager import list_services
            root = _repo_root()
            services = list_services(root)
            for svc in services:
                name = svc["service"]
                if name not in self._svc_labels:
                    continue
                dot, uptime_lbl = self._svc_labels[name]
                if svc["running"]:
                    dot.config(text="\u2022", fg="#22c55e")
                    s = svc.get("uptime_seconds", 0)
                    if s < 60:
                        uptime_lbl.config(text=f"{s}s")
                    elif s < 3600:
                        uptime_lbl.config(text=f"{s // 60}m")
                    else:
                        uptime_lbl.config(text=f"{s // 3600}h {(s % 3600) // 60}m")
                else:
                    dot.config(text="\u25CB", fg=self.MUTED)
                    uptime_lbl.config(text="stopped")
                    # Notify if service was previously running (crash detected)
                    if self._prev_svc_running.get(name, False):
                        self._notify_toast("Jarvis Service Down", f"{name} has stopped", "Warning")
                self._prev_svc_running[name] = svc["running"]
        except Exception:
            logger.debug("Failed to refresh service status: list_services unavailable or errored")
        # Re-schedule every 10 seconds
        self.after(10000, self._refresh_services)

    def _refresh_settings_async(self) -> None:
        cfg = self._current_cfg()  # Read tkinter vars on main thread

        def worker() -> None:
            try:
                data = _http_json(cfg, "/settings", method="GET")
                settings = data.get("settings", {})
                self._log_async(json.dumps(settings, ensure_ascii=True)[:600], role="jarvis")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"settings failed: {exc}", role="error")

        self._thread(worker)

    def _refresh_dashboard_async(self) -> None:
        cfg = self._current_cfg()  # Read tkinter vars on main thread

        def worker() -> None:
            try:
                data = _http_json(cfg, "/dashboard", method="GET")
                dash = data.get("dashboard", {})
                jar = dash.get("jarvis", {}) if isinstance(dash, dict) else {}
                mem = dash.get("memory_regression", {}) if isinstance(dash, dict) else {}
                self._log_async(
                    f"score={jar.get('score_pct', 0.0)} delta={jar.get('delta_vs_prev_pct', 0.0)} "
                    f"memory={mem.get('status', 'unknown')}",
                    role="jarvis",
                )
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"dashboard failed: {exc}", role="error")

        self._thread(worker)

    def _view_activity_async(self) -> None:
        cfg = self._current_cfg()  # Read tkinter vars on main thread

        # Category -> color map for output log
        _CAT_COLORS = {
            "error": "#ef4444",
            "security": "#ef4444",
            "llm_routing": "#3b82f6",
            "fact_extracted": "#22c55e",
            "proactive_trigger": "#eab308",
            "daemon_cycle": "#8ea4c5",
            "harvest": "#14b8a6",
            "voice": "#a78bfa",
        }

        def worker() -> None:
            try:
                data = _http_json(cfg, "/activity?limit=20", method="GET")
                events = data.get("events", [])
                stats = data.get("stats", {})
                if stats:
                    parts = [f"{k}:{v}" for k, v in stats.items()]
                    self._log_async(f"Activity (24h): {', '.join(parts)}", role="jarvis")
                if not events:
                    self._log_async("No recent activity events.", role="system")
                    return
                for evt in reversed(events):
                    ts_raw = str(evt.get("timestamp", ""))
                    ts_short = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
                    cat = str(evt.get("category", ""))
                    summary = str(evt.get("summary", ""))
                    role = "error" if cat == "error" else "jarvis"
                    self._log_async(f"[{ts_short}] [{cat.upper()}] {summary}", role=role)
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"activity failed: {exc}", role="error")

        self._thread(worker)

    def _dictate_async(self) -> None:
        auto_send = bool(self.auto_send_var.get())

        def worker() -> None:
            try:
                text = _voice_dictate_once(timeout_s=8)
                if not text:
                    self._log_async("No speech recognized.", role="system")
                    return
                self._set_command_text_async(text)
                self._log_async(f"dictated: {text}", role="system")
                if auto_send:
                    self.after(0, self._send_command_async)
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"dictation failed: {exc}", role="error")

        self._thread(worker)

    def _hotword_changed(self) -> None:
        if self.hotword_var.get():
            if self._hotword_active.is_set():
                self._log("Wake Word loop already running.")
                return
            self._log("Wake Word enabled. Say 'Jarvis' to trigger dictation.")
            self._thread(self._hotword_loop)
        else:
            self._log("Wake Word disabled.")

    def _hotword_loop(self) -> None:
        if self._hotword_active.is_set():
            return  # Another loop is already running
        self._hotword_active.set()
        try:
            self._hotword_loop_inner()
        finally:
            self._hotword_active.clear()

    def _hotword_loop_inner(self) -> None:
        def _read_hotword_var() -> bool:
            """Read hotword BooleanVar on main thread."""
            result: list[bool] = [False]
            ready = threading.Event()

            def _read() -> None:
                try:
                    result[0] = bool(self.hotword_var.get())
                except Exception:
                    result[0] = False
                ready.set()

            try:
                self.after(0, _read)
            except Exception:
                return False
            ready.wait(timeout=2.0)
            return result[0]

        while _read_hotword_var() and (not self.stop_event.is_set()):
            try:
                heard = _detect_hotword_once(keyword="jarvis", timeout_s=2)
                if heard and not self.stop_event.is_set():
                    try:
                        self.after(0, self._show_panel)
                        self._log_async("Wake word detected.")
                        self.after(0, self._dictate_async)
                    except Exception:
                        return  # Widget destroyed
            except Exception as exc:
                logger.warning("Hotword detection error: %s", exc)
            for _ in range(6):
                if self.stop_event.is_set() or (not _read_hotword_var()):
                    return
                time.sleep(0.5)

    def _start_status_workers(self) -> None:
        self._thread(self._health_loop)

    def _health_loop(self) -> None:
        while not self.stop_event.is_set():
            # Schedule tkinter var read on main thread and wait for result
            cfg_holder: list[WidgetConfig | None] = [None]
            ready = threading.Event()

            def _read_cfg() -> None:
                cfg_holder[0] = self._current_cfg()
                ready.set()

            try:
                self.after(0, _read_cfg)
            except Exception:
                return  # Widget destroyed
            ready.wait(timeout=5.0)
            cfg = cfg_holder[0]
            if cfg is None:
                for _ in range(16):
                    if self.stop_event.is_set():
                        return
                    time.sleep(0.5)
                continue
            if not _is_safe_widget_base_url(cfg.base_url):
                try:
                    self.after(0, self._set_online, False)
                except Exception:
                    return  # Widget destroyed
                for _ in range(16):
                    if self.stop_event.is_set():
                        return
                    time.sleep(0.5)
                continue
            url = f"{cfg.base_url.rstrip('/')}/health"
            resp = None
            ok = False
            intel_data: dict[str, Any] | None = None
            for _attempt in range(2):
                try:
                    req = Request(url=url, method="GET")
                    resp = urlopen(req, timeout=5)
                    ok = resp.status == 200
                    if ok:
                        try:
                            body = resp.read().decode("utf-8")
                            health_payload = json.loads(body)
                            if isinstance(health_payload, dict) and "intelligence" in health_payload:
                                intel_data = health_payload["intelligence"]
                        except Exception:
                            pass  # Parsing intelligence is best-effort
                    resp.close()
                    resp = None
                    if ok:
                        break
                except Exception:
                    ok = False
                finally:
                    # Ensure the response is always closed to prevent leaks
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:
                            pass
                        resp = None
                if self.stop_event.is_set():
                    break
                time.sleep(0.2)
            # Fetch intelligence growth data (piggyback on health poll)
            growth_data: dict[str, Any] | None = None
            if ok and cfg.token and cfg.signing_key:
                try:
                    growth_data = _http_json(cfg, "/intelligence/growth", method="GET")
                except Exception:
                    pass  # Growth data is best-effort
            # Fetch proactive alerts and send toast notifications
            if ok and cfg.token and cfg.signing_key:
                try:
                    dash = _http_json(cfg, "/dashboard", method="GET")
                    alerts = dash.get("dashboard", {}).get("proactive_alerts", [])
                    if isinstance(alerts, list):
                        for alert in alerts:
                            msg = str(alert.get("message", "")) if isinstance(alert, dict) else str(alert)
                            if msg:
                                self._notify_toast("Jarvis Alert", msg, "Warning")
                                break  # One toast per poll cycle (throttle handles the rest)
                except Exception:
                    pass  # Proactive alerts are best-effort
            if not self.stop_event.is_set():
                try:
                    self.after(0, self._set_online, ok, intel_data, growth_data)
                except Exception:
                    return  # Widget destroyed
            for _ in range(16):
                if self.stop_event.is_set():
                    return
                time.sleep(0.5)

    def _set_online(self, value: bool, intel_data: dict[str, Any] | None = None, growth_data: dict[str, Any] | None = None) -> None:
        """Update online state and refresh status — always call on main thread."""
        self.online = value
        self._update_intelligence_label(intel_data)
        self._update_growth_labels(growth_data)
        self._refresh_status_view()

    def _update_growth_labels(self, growth_data: dict[str, Any] | None) -> None:
        """Update Brain Growth labels from /intelligence/growth metrics."""
        if growth_data is None or not isinstance(growth_data, dict):
            for lbl in self._growth_labels.values():
                lbl.config(text="--", fg=self.MUTED)
            return
        try:
            m = growth_data.get("metrics", growth_data)
            facts_total = int(m.get("facts_total", 0))
            facts_7d = int(m.get("facts_last_7d", 0))
            kg_nodes = int(m.get("kg_nodes", 0))
            kg_edges = int(m.get("kg_edges", 0))
            mem_records = int(m.get("memory_records", 0))
            score = float(m.get("last_self_test_score", 0.0))
            trend = str(m.get("growth_trend", "stable"))

            self._growth_labels["facts"].config(
                text=f"{facts_total} (+{facts_7d} 7d)", fg=self.TEXT)
            self._growth_labels["kg"].config(
                text=f"{kg_nodes} nodes / {kg_edges} edges", fg=self.TEXT)
            self._growth_labels["memory"].config(
                text=f"{mem_records} records", fg=self.TEXT)

            score_pct = round(score * 100)
            score_color = self.ACCENT if score_pct >= 70 else "#eab308" if score_pct >= 50 else self.WARN
            self._growth_labels["score"].config(
                text=f"{score_pct}%", fg=score_color)

            trend_symbol = "\u25B2" if trend == "increasing" else "\u25BC" if trend == "declining" else "\u25C6"
            trend_color = "#22c55e" if trend == "increasing" else self.WARN if trend == "declining" else "#eab308"
            self._growth_labels["trend"].config(
                text=f"{trend_symbol} {trend}", fg=trend_color)
        except (TypeError, ValueError, KeyError):
            for lbl in self._growth_labels.values():
                lbl.config(text="--", fg=self.MUTED)

    def _update_intelligence_label(self, intel_data: dict[str, Any] | None) -> None:
        """Update the intelligence score label from /health response data."""
        if intel_data is None:
            self.intel_var.set("")
            self.intel_label.config(fg=self.MUTED)
            return
        try:
            score = float(intel_data.get("score", 0.0))
            regression = bool(intel_data.get("regression", False))
            score_pct = round(score * 100)
            if regression:
                self.intel_var.set(f"Intel: {score_pct}% REGRESSION")
                self.intel_label.config(fg=self.WARN)
            elif score_pct >= 70:
                self.intel_var.set(f"Intel: {score_pct}%")
                self.intel_label.config(fg=self.ACCENT)
            elif score_pct >= 50:
                self.intel_var.set(f"Intel: {score_pct}%")
                self.intel_label.config(fg="#eab308")  # yellow/warn
            else:
                self.intel_var.set(f"Intel: {score_pct}%")
                self.intel_label.config(fg=self.WARN)
        except (TypeError, ValueError):
            self.intel_var.set("")
            self.intel_label.config(fg=self.MUTED)

    def _refresh_status_view(self) -> None:
        self.status_var.set("ONLINE" if self.online else "OFFLINE")

    def _animate_orb(self) -> None:
        if self.stop_event.is_set():
            return
        self._pulse_phase = (self._pulse_phase + 0.22) % (2 * math.pi * 100)
        pulse = 5.0 + (math.sin(self._pulse_phase) * 1.8)
        cx, cy = 13.0, 13.0
        x0 = cx - pulse
        y0 = cy - pulse
        x1 = cx + pulse
        y1 = cy + pulse
        color = self.ACCENT if self.online else self.WARN
        try:
            self.orb_canvas.coords(self.orb_id, x0, y0, x1, y1)
            self.orb_canvas.itemconfig(self.orb_id, fill=color)
            self._orb_after_id = self.after(120, self._animate_orb)
        except Exception:
            return

    def _animate_launcher(self) -> None:
        if self.stop_event.is_set():
            return
        try:
            if self.launcher_canvas is not None and self._launcher_outer_id is not None and self._launcher_inner_id is not None:
                size = self._launcher_size
                self._launcher_phase = (self._launcher_phase + 0.18) % (2 * math.pi * 100)
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
            self._launcher_after_id = self.after(70, self._animate_launcher)
        except Exception:
            return


def run_desktop_widget() -> None:
    app = JarvisDesktopWidget(_repo_root())
    app.mainloop()
