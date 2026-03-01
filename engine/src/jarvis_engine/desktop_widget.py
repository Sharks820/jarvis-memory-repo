from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import hashlib
import hmac
import json
import logging
import math
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
    panel_x: int | None = None
    panel_y: int | None = None
    launcher_x: int | None = None
    launcher_y: int | None = None


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

    # --- Position fields (backward-compatible: absent = None) ---
    def _int_or_none(key: str) -> int | None:
        v = raw.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # --- Resolve token and signing_key (DPAPI-protected or plaintext legacy) ---
    def _resolve_dpapi_field(field: str, fallback: str = "") -> str:
        protected_key = f"{field}_protected"
        if protected_key in raw:
            try:
                return _dpapi_decrypt(str(raw[protected_key]))
            except Exception:
                logger.warning("Failed to decrypt %s via DPAPI", protected_key)
        val = str(raw.get(field, "")).strip()
        if val:
            needs_migration_fields.append(field)
        return val or fallback

    needs_migration_fields: list[str] = []
    resolved_token = _resolve_dpapi_field("token", mobile.get("token", ""))
    resolved_signing_key = _resolve_dpapi_field("signing_key", mobile.get("signing_key", ""))

    # Auto-detect scheme: use HTTPS when TLS certs exist (matches server auto-detection).
    _tls_available = (_security_dir(root) / "tls_cert.pem").exists() and (_security_dir(root) / "tls_key.pem").exists()
    _default_scheme = "https" if _tls_available else "http"
    _default_base = f"{_default_scheme}://127.0.0.1:8787"

    # Auto-upgrade saved http:// base_url to https:// when TLS certs exist.
    _saved_url = str(raw.get("base_url", "")).strip()
    if _saved_url and _tls_available and _saved_url.startswith("http://"):
        _saved_url = "https://" + _saved_url[len("http://"):]
    _base_url = _saved_url or _default_base

    # --- Auto-heal stale IPs on startup ---
    # If the saved URL points to a non-localhost address, probe it.
    # If unreachable but localhost works, silently switch and persist the fix.
    from urllib.parse import urlparse as _ul_parse
    _parsed = _ul_parse(_base_url)
    if _parsed.hostname not in ("127.0.0.1", "localhost", "::1", None):
        _probe_url = f"{_base_url.rstrip('/')}/health"
        _local_url = f"{_default_scheme}://127.0.0.1:{_parsed.port or 8787}/health"
        _stale = False
        try:
            _ctx = _make_ssl_context_for_self_signed() if _parsed.scheme == "https" else None
            with urlopen(Request(url=_probe_url, method="GET"), timeout=3, context=_ctx):
                pass  # Saved URL works fine
        except Exception:
            # Saved URL unreachable — try localhost
            try:
                _ctx_l = _make_ssl_context_for_self_signed() if _default_scheme == "https" else None
                with urlopen(Request(url=_local_url, method="GET"), timeout=3, context=_ctx_l):
                    _stale = True  # localhost works, saved URL is stale
            except Exception:
                pass  # Neither works — keep saved URL, user will see OFFLINE
        if _stale:
            _old_url = _base_url
            _base_url = f"{_default_scheme}://127.0.0.1:{_parsed.port or 8787}"
            logger.info("Auto-healed stale base_url %s → %s", _old_url, _base_url)

    cfg = WidgetConfig(
        base_url=_base_url,
        token=resolved_token,
        signing_key=resolved_signing_key,
        device_id=str(raw.get("device_id", "galaxy_s25_primary")).strip() or "galaxy_s25_primary",
        master_password=master_password,
        panel_x=_int_or_none("panel_x"),
        panel_y=_int_or_none("panel_y"),
        launcher_x=_int_or_none("launcher_x"),
        launcher_y=_int_or_none("launcher_y"),
    )

    # Migrate plaintext secrets (master_password, token, signing_key) -> DPAPI-protected
    # Also persist auto-healed base_url so stale IP is permanently fixed
    _url_healed = (_base_url != (_saved_url or _default_base))
    if needs_migration or needs_migration_fields or _url_healed:
        try:
            _save_widget_cfg(root, cfg)
            if _url_healed:
                logger.info("Persisted auto-healed base_url to config")
            if needs_migration or needs_migration_fields:
                logger.info("Migrated plaintext secrets to DPAPI-protected storage")
        except Exception:
            logger.warning("Failed to save config migration; will retry on next save")

    return cfg


def _save_widget_cfg(root: Path, cfg: WidgetConfig) -> None:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    payload: dict[str, Any] = {
        "base_url": cfg.base_url,
        "device_id": cfg.device_id,
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Encrypt token and signing_key via DPAPI; fall back to plaintext on non-Windows
    for field in ("token", "signing_key"):
        value = getattr(cfg, field, "")
        if value:
            try:
                payload[f"{field}_protected"] = _dpapi_encrypt(value)
            except Exception:
                payload[field] = value

    # Persist window positions if set
    if cfg.panel_x is not None:
        payload["panel_x"] = cfg.panel_x
    if cfg.panel_y is not None:
        payload["panel_y"] = cfg.panel_y
    if cfg.launcher_x is not None:
        payload["launcher_x"] = cfg.launcher_x
    if cfg.launcher_y is not None:
        payload["launcher_y"] = cfg.launcher_y

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


import ipaddress as _ipaddress_mod

# Pre-built network object for CGNAT/Tailscale range check (RFC 6598).
# Module-level to avoid re-parsing on every call to _is_safe_widget_base_url.
_CGNAT_NETWORK = _ipaddress_mod.ip_network("100.64.0.0/10")


def _is_safe_widget_base_url(url: str) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme == "https":
        return True
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    # Allow HTTP for private/LAN IPs and CGNAT/Tailscale (trusted local network)
    try:
        addr = _ipaddress_mod.ip_address(host)
        if addr.is_private:
            return True
        # Tailscale and carrier-grade NAT use 100.64.0.0/10 (RFC 6598 shared
        # address space).  Python's is_private excludes this range, but it is
        # not publicly routable and Tailscale treats it as a private mesh.
        if addr in _CGNAT_NETWORK:
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

    # Try configured URL first, then auto-fallback to localhost if it fails
    # (handles stale Tailscale/VPN IPs gracefully)
    from urllib.parse import urlparse
    _parsed_url = urlparse(cfg.base_url)
    _is_localhost = _parsed_url.hostname in ("127.0.0.1", "localhost", "::1")
    _urls_to_try = [cfg.base_url]
    if not _is_localhost:
        _fallback = f"{_parsed_url.scheme}://127.0.0.1:{_parsed_url.port or 8787}"
        _urls_to_try.append(_fallback)

    last_exc: Exception | None = None
    for base in _urls_to_try:
        # Generate FRESH signed headers for each URL attempt.  Each attempt
        # produces a unique nonce so that if the primary URL's server consumes
        # the nonce (even on an HTTP error or timeout), the fallback URL gets
        # its own valid nonce instead of being rejected as a replay.
        headers = _signed_headers(cfg.token, cfg.signing_key, body, cfg.device_id)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url=f"{base.rstrip('/')}{path}", method=method, data=(None if payload is None else body), headers=headers)
        ssl_ctx = _get_ssl_context(base)
        try:
            with urlopen(req, timeout=60, context=ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON response: {exc}") from exc
            if not isinstance(parsed, dict):
                raise RuntimeError("Invalid response payload")
            return parsed
        except HTTPError as exc:
            last_exc = RuntimeError(f"HTTP request failed: HTTP {exc.code} {exc.reason}")
        except (URLError, TimeoutError, OSError) as exc:
            last_exc = RuntimeError(f"HTTP request failed: {exc}")
            if base != _urls_to_try[-1]:
                logger.info("Primary URL %s unreachable, trying localhost fallback", base)
    raise last_exc or RuntimeError("HTTP request failed")


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


def _create_tray_icon_image():
    """Create a 64x64 PIL Image with a blue background and white 'J' for the tray icon."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    size = 64
    image = Image.new("RGBA", (size, size), (18, 163, 255, 255))  # ACCENT_2 blue
    draw = ImageDraw.Draw(image)
    # Draw circle background
    draw.ellipse([0, 0, size - 1, size - 1], fill=(18, 163, 255, 255))
    # Draw "J" text centered
    try:
        font = ImageFont.truetype("segoeui.ttf", 38)
    except (IOError, OSError):
        try:
            font = ImageFont.truetype("arial.ttf", 38)
        except (IOError, OSError):
            font = ImageFont.load_default()
    # Get text bounding box for centering
    bbox = draw.textbbox((0, 0), "J", font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) // 2 - bbox[0]
    y = (size - text_h) // 2 - bbox[1]
    draw.text((x, y), "J", fill=(255, 255, 255, 255), font=font)
    return image


def _snap_to_edge(
    x: int, y: int, w: int, h: int, tk_root: tk.Misc, snap_dist: int = 20
) -> tuple[int, int]:
    """Snap coordinates to screen edges if within *snap_dist* pixels.

    Works with any tkinter widget to query screen dimensions.
    Returns the (possibly adjusted) (x, y) tuple.
    """
    try:
        screen_w = tk_root.winfo_screenwidth()
        screen_h = tk_root.winfo_screenheight()
    except Exception:
        return x, y
    # Left edge
    if 0 <= x <= snap_dist:
        x = 0
    # Right edge
    right = x + w
    if screen_w - snap_dist <= right <= screen_w + snap_dist:
        x = screen_w - w
    # Top edge
    if 0 <= y <= snap_dist:
        y = 0
    # Bottom edge (leave ~40px for taskbar)
    taskbar_margin = 40
    bottom = y + h
    if screen_h - taskbar_margin - snap_dist <= bottom <= screen_h:
        y = screen_h - taskbar_margin - h
    return x, y


def _is_position_on_screen(x: int, y: int, tk_root: tk.Misc) -> bool:
    """Return True if (x, y) is within the visible screen area."""
    try:
        screen_w = tk_root.winfo_screenwidth()
        screen_h = tk_root.winfo_screenheight()
    except Exception:
        return False
    return -100 <= x <= screen_w and -100 <= y <= screen_h


class _Tooltip:
    """Hover tooltip for tkinter widgets."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 300) -> None:
        self._widget = widget
        self._text = text
        self._delay = delay
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._cancel, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _cancel(self, _event: Any = None) -> None:
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self) -> None:
        if self._tip:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        label = tk.Label(
            tw, text=self._text, justify=tk.LEFT,
            background="#1e293b", foreground="#e2e8f0",
            relief=tk.SOLID, borderwidth=1,
            font=("Segoe UI", 9), padx=6, pady=3,
        )
        label.pack()

    def _hide(self) -> None:
        if self._tip:
            self._tip.destroy()
            self._tip = None


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
        self._anim_t0: float = time.monotonic()
        self._launcher_size = 96
        self.launcher_win: tk.Toplevel | None = None
        self.launcher_canvas: tk.Canvas | None = None
        self._l_arc1: int | None = None
        self._l_arc2: int | None = None
        self._l_arc3: int | None = None
        self._l_arc4: int | None = None
        self._l_core: int | None = None
        self._l_glow: int | None = None
        self._l_particles: list[int] = []
        self._orb_sweep: int | None = None
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._launcher_dragged = False
        self._hotword_active = threading.Event()  # Guards against multiple hotword loops
        self._orb_after_id: str | None = None
        self._launcher_after_id: str | None = None
        self._prev_svc_running: dict[str, bool] = {}  # Track service state for crash detection
        self._widget_state: str = "idle"  # idle | listening | processing | error
        self._thinking_marker: str | None = None  # Text index of thinking indicator start
        self._thinking_after_id: str | None = None  # after() id for dot animation
        self._thinking_dots: int = 3
        self._thinking_start_time: float = 0.0  # When thinking started (time.time())
        self._processing_timeout_id: str | None = None  # Safety timeout for stuck processing
        self._welcome_shown: bool = False  # One-time welcome message flag
        self._error_clear_id: str | None = None  # after() id for auto-clearing error state
        self._position_save_id: str | None = None  # debounce timer for position save
        self._SNAP_DISTANCE = 20  # pixels from screen edge to trigger snap
        self._tray_icon: Any = None  # pystray.Icon instance (or None if unavailable)

        self.title("Jarvis Unlimited")
        # Restore saved panel position if available and on-screen
        _px, _py = self.cfg.panel_x, self.cfg.panel_y
        if _px is not None and _py is not None:
            self.geometry(f"470x840+{_px}+{_py}")
        else:
            self.geometry("470x840+40+60")
        self.minsize(420, 620)
        self.configure(bg=self.BG)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._build_launcher()
        self._build_tray_icon()
        self._bind_shortcuts()
        self._start_status_workers()
        self._animate_orb()
        self._animate_launcher()
        self._hide_panel()
        self._log("Jarvis Widget started. Checking connection...", role="system")
        self._log("Enter sends command, Shift+Enter inserts newline.", role="system")
        # Show initial connection status after first health check
        self._thread(self._startup_status_check)

    def _startup_status_check(self) -> None:
        """Quick connection check on startup so user sees status immediately."""
        time.sleep(1.5)  # Give health loop a moment to poll
        if self.stop_event.is_set():
            return
        if self.online:
            self._log_async("Connected to Jarvis services.", role="jarvis")
        else:
            self._log_async("OFFLINE - Cannot reach Jarvis services.", role="error")
            self._log_async("Start services with: jarvis-engine daemon", role="system")
            self._log_async("Then click Connect to authenticate.", role="system")

    def _on_close(self) -> None:
        """Handle window close: minimize to launcher orb (tray-app pattern).
        Use the Exit button or Ctrl+Shift+Q for full shutdown."""
        self._hide_panel()

    def _shutdown(self) -> None:
        self.stop_event.set()
        self._stop_tray_icon()
        # Cancel pending animation callbacks to prevent post-destroy TclError
        if self._orb_after_id is not None:
            try:
                self.after_cancel(self._orb_after_id)
            except Exception as exc:
                logger.debug("Failed to cancel orb animation callback: %s", exc)
        if self._launcher_after_id is not None:
            try:
                self.after_cancel(self._launcher_after_id)
            except Exception as exc:
                logger.debug("Failed to cancel launcher animation callback: %s", exc)
        # Wait briefly for background threads to finish
        for t in threading.enumerate():
            if t.daemon and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=1.0)
        if self.launcher_win is not None:
            try:
                self.launcher_win.destroy()
            except Exception as exc:
                logger.debug("Failed to destroy launcher window during shutdown: %s", exc)
        try:
            self.destroy()
        except Exception as exc:
            logger.debug("Failed to destroy main widget window during shutdown: %s", exc)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-space>", lambda _e: self._toggle_min())
        self.bind("<Escape>", lambda _e: self._toggle_min())
        self.bind("<Control-Return>", lambda _e: self._send_command_async())
        self.bind("<Control-Shift-Q>", lambda _e: self._shutdown())
        self.bind("<Configure>", self._on_panel_configure)

    def _toggle_min(self) -> None:
        if self.state() in {"withdrawn", "iconic"}:
            self._show_panel()
        else:
            self._hide_panel()

    def _show_panel(self) -> None:
        # Restore saved position if valid
        px, py = self.cfg.panel_x, self.cfg.panel_y
        if px is not None and py is not None and _is_position_on_screen(px, py, self):
            self.geometry(f"+{px}+{py}")
        self.deiconify()
        self.lift()
        self.focus_force()
        if self.launcher_win is not None:
            self.launcher_win.withdraw()

    def _hide_panel(self) -> None:
        self.withdraw()
        # Always show the launcher orb so the user has a visible click target.
        # The system tray icon (if available) is a supplementary access method,
        # not a replacement — it may be hidden in the Windows 11 overflow area.
        if self.launcher_win is not None:
            self.launcher_win.deiconify()
            self.launcher_win.lift()

    def _on_panel_configure(self, event) -> None:  # type: ignore[no-untyped-def]
        """Handle panel move/resize -- debounce position save and snap to edge."""
        # Only process events from the root window itself, not child widgets
        if event.widget is not self:
            return
        # Debounce: cancel previous timer, schedule a new save in 300ms
        if self._position_save_id is not None:
            try:
                self.after_cancel(self._position_save_id)
            except Exception:
                pass
        self._position_save_id = self.after(300, self._save_panel_position)

    def _save_panel_position(self) -> None:
        """Save the current panel position to config (with edge snap)."""
        try:
            x = self.winfo_x()
            y = self.winfo_y()
        except Exception:
            return
        x, y = _snap_to_edge(x, y, self.winfo_width(), self.winfo_height(), self)
        # Apply snapped position
        try:
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass
        self.cfg.panel_x = x
        self.cfg.panel_y = y
        try:
            _save_widget_cfg(self.root_path, self.cfg)
        except Exception:
            logger.debug("Failed to save widget position to config")

    def _save_launcher_position(self) -> None:
        """Save the current launcher orb position to config."""
        if self.launcher_win is None:
            return
        try:
            x = self.launcher_win.winfo_x()
            y = self.launcher_win.winfo_y()
        except Exception:
            return
        x, y = _snap_to_edge(x, y, self._launcher_size, self._launcher_size, self)
        try:
            self.launcher_win.geometry(f"+{x}+{y}")
        except Exception:
            pass
        self.cfg.launcher_x = x
        self.cfg.launcher_y = y
        try:
            _save_widget_cfg(self.root_path, self.cfg)
        except Exception:
            logger.debug("Failed to save launcher position to config")

    def _build_launcher(self) -> None:
        launcher = tk.Toplevel(self)
        launcher.overrideredirect(True)
        launcher.attributes("-topmost", True)
        launcher.configure(bg=self.LAUNCHER_TRANSPARENT)
        try:
            launcher.wm_attributes("-transparentcolor", self.LAUNCHER_TRANSPARENT)
        except Exception as exc:
            logger.debug("Failed to set launcher transparent color attribute: %s", exc)
        size = self._launcher_size
        # Restore saved launcher position or default to bottom-right
        lx, ly = self.cfg.launcher_x, self.cfg.launcher_y
        if lx is not None and ly is not None and _is_position_on_screen(lx, ly, launcher):
            x, y = lx, ly
        else:
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
        cx, cy = size / 2, size / 2
        # Outer glow halo (breathing) — thicker for visibility
        self._l_glow = canvas.create_oval(2, 2, size - 2, size - 2, outline="#0d3d36", width=3)
        # Arc 4: outermost decorative ring, thin, slow counter-rotate
        self._l_arc4 = canvas.create_arc(
            1, 1, size - 1, size - 1, start=0, extent=60,
            style=tk.ARC, outline="#2dd4bf", width=1,
        )
        # Rotating arc 1: outer ring, 240deg extent — thicker
        self._l_arc1 = canvas.create_arc(
            6, 6, size - 6, size - 6, start=0, extent=240,
            style=tk.ARC, outline="#2dd4bf", width=3,
        )
        # Rotating arc 2: mid ring, 160deg extent (counter-rotating)
        self._l_arc2 = canvas.create_arc(
            14, 14, size - 14, size - 14, start=120, extent=160,
            style=tk.ARC, outline="#0ea5e9", width=2,
        )
        # Rotating arc 3: inner fast ring, 90deg (processing indicator, hidden by default)
        self._l_arc3 = canvas.create_arc(
            21, 21, size - 21, size - 21, start=0, extent=90,
            style=tk.ARC, outline="#f59e0b", width=2, state=tk.HIDDEN,
        )
        # Core circle (breathing) with outline ring
        core_pad = 24
        self._l_core = canvas.create_oval(
            core_pad, core_pad, size - core_pad, size - core_pad,
            fill="#0f766e", outline="#2dd4bf", width=1,
        )
        # Orbiting particles (5 dots at different orbit radii for richer effect)
        self._l_particles = []
        for _ in range(5):
            pid = canvas.create_oval(0, 0, 5, 5, fill="#5eead4", outline="")
            self._l_particles.append(pid)
        # Center letter — larger, bolder
        canvas.create_text(cx, cy, text="J", fill="#ecfeff", font=("Segoe UI", 20, "bold"))

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
        if self._launcher_dragged:
            self._save_launcher_position()
        else:
            self._show_panel()

    # ------------------------------------------------------------------
    # System tray icon (pystray)
    # ------------------------------------------------------------------

    def _build_tray_icon(self) -> None:
        """Create a system tray icon using pystray. Runs in a daemon thread."""
        try:
            import pystray  # noqa: E402
        except ImportError:
            logger.info("pystray or Pillow not installed; system tray icon disabled")
            return

        # Create a 64x64 icon: blue circle with white "J"
        image = _create_tray_icon_image()

        menu = pystray.Menu(
            pystray.MenuItem("Show Widget", self._tray_show_widget, default=True),
            pystray.MenuItem("Voice Dictate", self._tray_voice_dictate),
            pystray.MenuItem("Ops Brief", self._tray_ops_brief),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )

        icon = pystray.Icon("jarvis", image, "Jarvis Unlimited", menu)
        self._tray_icon = icon
        # pystray.Icon.run() blocks, so run in a daemon thread
        tray_thread = threading.Thread(target=icon.run, daemon=True)
        tray_thread.start()

    def _tray_show_widget(self, icon=None, item=None) -> None:  # type: ignore[no-untyped-def]
        """Tray menu: Show Widget (also handles double-click)."""
        try:
            self.after(0, self._show_panel)
        except Exception:
            pass

    def _tray_voice_dictate(self, icon=None, item=None) -> None:  # type: ignore[no-untyped-def]
        """Tray menu: Voice Dictate."""
        try:
            self.after(0, self._show_panel)
            self.after(100, self._voice_dictate)
        except Exception:
            pass

    def _tray_ops_brief(self, icon=None, item=None) -> None:  # type: ignore[no-untyped-def]
        """Tray menu: Ops Brief."""
        try:
            self.after(0, self._show_panel)
            self.after(100, lambda: self._send_text("ops brief"))
        except Exception:
            pass

    def _tray_quit(self, icon=None, item=None) -> None:  # type: ignore[no-untyped-def]
        """Tray menu: Quit."""
        try:
            self.after(0, self._shutdown)
        except Exception:
            pass

    def _stop_tray_icon(self) -> None:
        """Stop the tray icon cleanly."""
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception as exc:
                logger.debug("Failed to stop tray icon: %s", exc)
            self._tray_icon = None

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
        tk.Button(
            top,
            text="?",
            bg="#1a3050",
            fg="#a5b4fc",
            activebackground="#2a4060",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 11, "bold"),
            command=self._show_help,
            cursor="hand2",
            width=2,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        status_row = tk.Frame(header, bg=self.PANEL)
        status_row.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.orb_canvas = tk.Canvas(status_row, width=30, height=30, bg=self.PANEL, highlightthickness=0)
        self.orb_canvas.pack(side=tk.LEFT)
        self._orb_sweep = self.orb_canvas.create_arc(
            2, 2, 28, 28, start=0, extent=90,
            style=tk.ARC, outline=self.ACCENT, width=1,
        )
        self.orb_id = self.orb_canvas.create_oval(9, 9, 21, 21, fill="#6366f1", outline="")
        self.status_var = tk.StringVar(value="CONNECTING...")
        self.status_label = tk.Label(status_row, textvariable=self.status_var, bg=self.PANEL, fg="#a5b4fc", font=("Segoe UI", 10, "bold"))
        self.status_label.pack(side=tk.LEFT, padx=(6, 0))
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
        self._entry(sec, "Master password", self.master_var, show="*")

        # Advanced fields (hidden by default)
        self._adv_visible = tk.BooleanVar(value=False)
        adv_toggle = tk.Frame(sec, bg=self.PANEL)
        adv_toggle.pack(fill=tk.X, padx=6, pady=(2, 0))
        self._adv_toggle_btn = tk.Button(
            adv_toggle,
            text="\u25B6 Advanced",
            bg=self.PANEL,
            fg=self.MUTED,
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            cursor="hand2",
            command=self._toggle_advanced,
        )
        self._adv_toggle_btn.pack(side=tk.LEFT)
        self._adv_frame = tk.Frame(sec, bg=self.PANEL)
        self._entry(self._adv_frame, "Bearer token", self.token_var)
        self._entry(self._adv_frame, "Signing key", self.key_var)
        self._entry(self._adv_frame, "Device ID", self.device_var)
        # Advanced frame hidden by default (not packed)

        self._sec_buttons = tk.Frame(sec, bg=self.PANEL)
        self._sec_buttons.pack(fill=tk.X, padx=6, pady=(4, 8))
        tk.Button(
            self._sec_buttons,
            text="Save",
            bg="#133d70",
            fg="#eaf3ff",
            relief=tk.FLAT,
            command=self._save_session,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(
            self._sec_buttons,
            text="Connect",
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
        self._voice_btn = self._btn(row, "Voice Dictate", self._dictate_async, self.ACCENT_2)
        self._voice_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._send_btn = self._btn(row, "Send", self._send_command_async, self.ACCENT)
        self._send_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        quick = tk.Frame(body, bg=self.PANEL)
        quick.pack(fill=tk.X, padx=10, pady=(8, 0))
        _pause_btn = self._btn(quick, "\u23F8 Pause", lambda: self._quick_phrase("Jarvis, pause daemon"), self.WARN)
        _pause_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _Tooltip(_pause_btn, "Pause the Jarvis daemon.\nStops background tasks, proactive alerts, and auto-learning.\nUse Resume to restart.")
        _resume_btn = self._btn(quick, "\u25B6 Resume", lambda: self._quick_phrase("Jarvis, resume daemon"), self.ACCENT)
        _resume_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _Tooltip(_resume_btn, "Resume the Jarvis daemon.\nRestarts background tasks, proactive alerts, and auto-learning.")
        _safe_btn = self._btn(quick, "\U0001F6E1 Safe Mode", lambda: self._quick_phrase("Jarvis, enable safe mode"), self.ACCENT_2)
        _safe_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _Tooltip(_safe_btn, "Enable Safe Mode.\nForces all queries through local Ollama (no cloud).\nUse for private/sensitive conversations.")

        fetch = tk.Frame(body, bg=self.PANEL)
        fetch.pack(fill=tk.X, padx=10, pady=(8, 0))
        _refresh_btn = self._btn(fetch, "\U0001F504 Refresh", self._refresh_dashboard_async, "#35517a")
        _refresh_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _Tooltip(_refresh_btn, "Refresh the dashboard.\nShows intelligence score, memory stats, and growth trends.")
        _diag_btn = self._btn(fetch, "\U0001F527 Diagnose", self._diagnose_repair_async, "#1f5f88")
        _diag_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _Tooltip(_diag_btn, "Run self-healing diagnostics.\nChecks DB integrity, repairs broken indexes,\nand creates a recovery snapshot.")
        _activity_btn = self._btn_lg(fetch, "\U0001F4CA Activity", self._view_activity_async, "#4a3570")
        _activity_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _Tooltip(_activity_btn, "View recent activity log.\nShows last 20 events: commands, learning,\nalerts, and system actions.")

        # Running Services section
        svc_frame = tk.LabelFrame(body, text="Running Services", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        svc_frame.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._svc_labels: dict[str, tuple[tk.Label, tk.Label]] = {}
        for svc_name, display_name in [("daemon", "Assistant"), ("mobile_api", "Mobile API"), ("widget", "Widget")]:
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
        tk.Label(output_header, text="\U0001F4AC  Conversation", bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        tk.Button(
            output_header,
            text="Clear",
            bg="#1a2742",
            fg=self.MUTED,
            activebackground="#2a3752",
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            command=self._clear_history,
            cursor="hand2",
            padx=6,
            pady=2,
        ).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(
            output_header,
            text="\u23F9 End Conversation",
            bg="#7a2f2f",
            fg="#fca5a5",
            activebackground="#a03030",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            command=self._end_conversation,
            cursor="hand2",
            padx=6,
            pady=2,
        ).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(
            output_header,
            text="\u2197 Pop Out",
            bg="#2a3f5f",
            fg=self.TEXT,
            activebackground="#3a5070",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            command=self._pop_out_conversation,
            cursor="hand2",
            padx=6,
            pady=2,
        ).pack(side=tk.RIGHT)
        self.output = tk.Text(
            body,
            height=16,
            wrap=tk.WORD,
            bg="#081127",
            fg="#d6e4ff",
            insertbackground="#d6e4ff",
            relief=tk.FLAT,
            highlightbackground="#3a5a8a",
            highlightthickness=2,
            font=("Consolas", 11),
            state=tk.DISABLED,
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self._configure_chat_tags()

        # Tooltips on key controls
        _Tooltip(self.command_text, "Type a command or question")
        _Tooltip(self._voice_btn, "Click or say 'Jarvis' to dictate")
        _Tooltip(self._send_btn, "Send command (Enter)")

    def _configure_chat_tags(self) -> None:
        """Set up tag-based visual styles for the chat-style conversation display."""
        self.output.tag_configure(
            "user",
            background="#0c2d5e",
            foreground="#b8d4ff",
            font=("Consolas", 11, "bold"),
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
            font=("Consolas", 11),
            lmargin1=8,
            lmargin2=8,
            rmargin=40,
            spacing1=4,
            spacing3=4,
        )
        self.output.tag_configure(
            "system",
            foreground="#7a9abe",
            font=("Consolas", 10),
            lmargin1=8,
            lmargin2=8,
            spacing1=2,
            spacing3=2,
        )
        self.output.tag_configure(
            "error",
            background="#2a0a0a",
            foreground="#ff6b6b",
            font=("Consolas", 11, "bold"),
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
        self.output.tag_configure(
            "thinking",
            foreground="#ff9f43",
            font=("Consolas", 11, "italic"),
            lmargin1=8,
            lmargin2=8,
            rmargin=40,
            spacing1=4,
            spacing3=4,
        )
        self.output.tag_configure(
            "learned",
            foreground="#34d399",
            font=("Consolas", 9, "italic"),
            lmargin1=8,
            lmargin2=8,
            spacing1=1,
            spacing3=1,
        )

    def _clear_history(self) -> None:
        """Clear all text from the conversation display."""
        self.output.config(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.config(state=tk.DISABLED)

    def _end_conversation(self) -> None:
        """End the conversation session, clear server-side history, and reset."""
        # Reset local UI state
        self._cancel_processing_timeout()
        self._hide_thinking()
        self._set_state("idle")
        self.command_text.config(state=tk.NORMAL)
        self._log("Conversation ended. Starting fresh.", role="system")
        # Clear server-side conversation history
        cfg = self._current_cfg()

        def worker() -> None:
            try:
                _http_json(cfg, "/conversation/clear", method="POST", payload={})
            except Exception:
                pass  # Best-effort clear

        self._thread(worker)

    def _pop_out_conversation(self) -> None:
        """Open conversation in a separate resizable window with command input."""
        if hasattr(self, "_popout_win") and self._popout_win is not None:
            try:
                self._popout_win.lift()
                self._popout_win.focus_force()
                return
            except tk.TclError:
                self._popout_win = None

        win = tk.Toplevel(self)
        win.title("Jarvis — Conversation")
        win.geometry("750x600")
        win.minsize(500, 400)
        win.configure(bg=self.BG)
        win.attributes("-topmost", True)
        self._popout_win = win

        # --- Conversation display (top, expandable) ---
        chat_frame = tk.Frame(win, bg=self.BG)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        popout_text = tk.Text(
            chat_frame,
            wrap=tk.WORD,
            bg="#081127",
            fg="#d6e4ff",
            insertbackground="#d6e4ff",
            relief=tk.FLAT,
            highlightbackground="#3a5a8a",
            highlightthickness=2,
            font=("Consolas", 12),
            state=tk.DISABLED,
        )
        scrollbar = tk.Scrollbar(chat_frame, command=popout_text.yview, bg="#0a1a3a",
                                 troughcolor="#0d1628", activebackground="#1e3250")
        popout_text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        popout_text.pack(fill=tk.BOTH, expand=True)

        # Configure the same chat tags on the pop-out widget
        for tag in ("user", "jarvis", "system", "error", "separator", "timestamp"):
            tag_opts = self.output.tag_configure(tag)
            if tag_opts:
                resolved = {k: v[-1] for k, v in tag_opts.items() if v and v[-1]}
                if resolved:
                    popout_text.tag_configure(tag, **resolved)

        # Copy content preserving tags — use tag_ranges() for O(n) bulk copy
        popout_text.config(state=tk.NORMAL)
        full_text = self.output.get("1.0", tk.END)
        if full_text.strip():
            # Insert all text first (fast bulk copy)
            popout_text.insert(tk.END, full_text)
            # Apply tags using tag_ranges (each range is a start/end pair)
            for tag in ("user", "jarvis", "system", "error", "separator",
                        "timestamp", "thinking"):
                try:
                    ranges = self.output.tag_ranges(tag)
                    for i in range(0, len(ranges), 2):
                        popout_text.tag_add(tag, str(ranges[i]), str(ranges[i + 1]))
                except tk.TclError:
                    pass
        popout_text.config(state=tk.DISABLED)
        self._popout_text = popout_text

        # --- Command input area (bottom, fixed) ---
        input_frame = tk.Frame(win, bg=self.PANEL, highlightbackground=self.EDGE, highlightthickness=1)
        input_frame.pack(fill=tk.X, padx=8, pady=8)

        # Accent bar at top of input area
        tk.Frame(input_frame, bg=self.ACCENT, height=2).pack(fill=tk.X)

        input_inner = tk.Frame(input_frame, bg=self.PANEL)
        input_inner.pack(fill=tk.X, padx=8, pady=8)

        popout_cmd = tk.Text(
            input_inner,
            height=3,
            wrap=tk.WORD,
            bg="#081127",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief=tk.FLAT,
            highlightbackground="#2a4368",
            highlightthickness=1,
            font=("Consolas", 12),
        )
        popout_cmd.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 8))

        btn_frame = tk.Frame(input_inner, bg=self.PANEL)
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y)

        send_btn = tk.Button(
            btn_frame,
            text="\u25B6 Send",
            bg=self.ACCENT,
            fg="#000000",
            activebackground="#0ea5a0",
            activeforeground="#000000",
            relief=tk.FLAT,
            font=("Segoe UI", 11, "bold"),
            cursor="hand2",
            padx=16,
            pady=6,
        )
        send_btn.pack(fill=tk.BOTH, expand=True)

        def _popout_send(event: Any = None) -> None:
            text = popout_cmd.get("1.0", tk.END).strip()
            if not text:
                return
            popout_cmd.delete("1.0", tk.END)
            # Mirror to main command box and send
            self._set_command_text(text)
            self._send_command_async()
            return "break"

        def _popout_key(event: Any) -> str | None:
            if event.keysym == "Return" and not event.state & 0x1:  # Enter without Shift
                _popout_send()
                return "break"
            return None

        popout_cmd.bind("<Key>", _popout_key)
        send_btn.config(command=_popout_send)
        popout_cmd.focus_set()

        def _on_close() -> None:
            self._popout_win = None
            self._popout_text = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _toggle_advanced(self) -> None:
        """Show/hide advanced session fields (token, signing key, device ID)."""
        if self._adv_visible.get():
            self._adv_frame.pack_forget()
            self._adv_visible.set(False)
            self._adv_toggle_btn.config(text="\u25B6 Advanced")
        else:
            self._adv_frame.pack(fill=tk.X, padx=0, pady=(0, 0), before=self._sec_buttons)
            self._adv_visible.set(True)
            self._adv_toggle_btn.config(text="\u25BC Advanced")

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

    def _btn_lg(self, parent: tk.Widget, text: str, command, color: str):  # type: ignore[no-untyped-def]
        """Larger variant of _btn for primary action buttons."""
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg="#eef8ff",
            activebackground=color,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=12,
            pady=8,
            font=("Segoe UI", 12, "bold"),
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

        # Mirror to pop-out conversation window if open
        popout = getattr(self, "_popout_text", None)
        if popout is not None:
            try:
                popout.config(state=tk.NORMAL)
                if role == "user":
                    sep_line = "\u2500" * 48 + "\n"
                    popout.insert(tk.END, sep_line, "separator")
                    popout.insert(tk.END, f"  {stamp}  \n", "timestamp")
                popout.insert(tk.END, display, tag)
                popout.see(tk.END)
                popout.config(state=tk.DISABLED)
            except tk.TclError:
                self._popout_text = None

    def _log_async(self, message: str, role: str = "system") -> None:
        if self.stop_event.is_set():
            return
        try:
            self.after(0, self._log, message, role)
        except Exception:
            pass  # Widget destroyed

    def _show_thinking(self) -> None:
        """Insert animated 'Jarvis is thinking...' indicator in chat."""
        import time as _time
        self._thinking_start_time = _time.time()
        self.output.config(state=tk.NORMAL)
        self._thinking_marker = self.output.index(tk.END)
        self.output.insert(tk.END, "\u23f3 Jarvis is thinking...  (0s)\n", "thinking")
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)
        popout = getattr(self, "_popout_text", None)
        if popout is not None:
            try:
                popout.config(state=tk.NORMAL)
                popout.insert(tk.END, "\u23f3 Jarvis is thinking...  (0s)\n", "thinking")
                popout.see(tk.END)
                popout.config(state=tk.DISABLED)
            except tk.TclError:
                pass
        self._animate_thinking()

    def _hide_thinking(self) -> None:
        """Remove the thinking indicator from chat."""
        if self._thinking_after_id is not None:
            try:
                self.after_cancel(self._thinking_after_id)
            except Exception:
                pass
            self._thinking_after_id = None
        if self._thinking_marker is not None:
            try:
                marker_end = f"{self._thinking_marker} lineend+1c"
                self.output.config(state=tk.NORMAL)
                self.output.delete(self._thinking_marker, marker_end)
                self.output.config(state=tk.DISABLED)
            except tk.TclError:
                pass
            popout = getattr(self, "_popout_text", None)
            if popout is not None:
                try:
                    popout.config(state=tk.NORMAL)
                    popout.delete("end-2l", tk.END)
                    popout.config(state=tk.DISABLED)
                except tk.TclError:
                    pass
            self._thinking_marker = None

    def _show_help(self) -> None:
        """Show help overlay with commands and tips."""
        help_win = tk.Toplevel(self)
        help_win.title("Jarvis Help")
        help_win.geometry("420x480")
        help_win.configure(bg="#0a1628")
        help_win.attributes("-topmost", True)
        help_win.resizable(False, False)

        tk.Label(
            help_win, text="Jarvis Help", bg="#0a1628", fg="#e2e8f0",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(16, 8))

        sections = [
            ("Talking to Jarvis", [
                "Type any question or command, press Enter or Send",
                "Voice Dictate or say 'Jarvis' (wake word) for voice input",
                "Say 'done' or click End Conversation when finished",
                "Jarvis keeps context across commands in a conversation",
            ]),
            ("Teaching Jarvis", [
                '"Remember that [fact]" -- saves to memory',
                '"What do you know about [topic]?" -- queries memory',
                '"Forget about [topic]" -- removes from knowledge base',
                "Jarvis auto-learns from every conversation",
            ]),
            ("Quick Commands", [
                '"Knowledge status" -- brain health report',
                '"System status" -- service health check',
                '"Mission status" -- active learning missions',
                '"Search the web for [topic]" -- web-augmented answers',
            ]),
            ("Control Buttons", [
                "Pause -- stops daemon (background tasks, alerts, learning)",
                "Resume -- restarts daemon after pause",
                "Safe Mode -- forces local Ollama (no cloud) for privacy",
                "Refresh -- shows intelligence score and memory stats",
                "Diagnose -- runs self-healing, repairs DB, creates snapshot",
                "Activity -- shows last 20 events (commands, learning, alerts)",
                "End Conversation -- clears context and starts fresh",
            ]),
            ("Keyboard Shortcuts", [
                "Enter -- Send command",
                "Ctrl+Enter -- Send command (alternative)",
                "Shift+Enter -- New line (don't send)",
                "Escape -- Close this help window",
            ]),
        ]

        text = tk.Text(
            help_win, wrap=tk.WORD, bg="#0a1628", fg="#cbd5e1",
            font=("Segoe UI", 10), relief=tk.FLAT, padx=16, pady=8,
            state=tk.DISABLED, highlightthickness=0,
        )
        text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        text.tag_configure("heading", foreground="#6ee7b7", font=("Segoe UI", 11, "bold"))
        text.tag_configure("item", foreground="#cbd5e1", font=("Segoe UI", 10))
        text.config(state=tk.NORMAL)
        for heading, items in sections:
            text.insert(tk.END, f"\n{heading}\n", "heading")
            for item in items:
                text.insert(tk.END, f"  {item}\n", "item")
        text.config(state=tk.DISABLED)

        help_win.bind("<Escape>", lambda _: help_win.destroy())
        help_win.focus_set()

    def _show_welcome(self) -> None:
        """Show one-time welcome message in chat."""
        if self._welcome_shown:
            return
        self._welcome_shown = True
        self._log(
            "Hi! I'm Jarvis. Ask me anything, teach me with "
            "'Remember that...', or click ? for help.",
            role="jarvis",
        )

    def _show_learned_indicator(self) -> None:
        """Show a brief 'Learned' indicator that fades after 2s."""
        self.output.config(state=tk.NORMAL)
        marker = self.output.index(tk.END)
        self.output.insert(tk.END, "  Learned\n", "learned")
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

        def _remove() -> None:
            try:
                self.output.config(state=tk.NORMAL)
                self.output.delete(marker, f"{marker}+1l")
                self.output.config(state=tk.DISABLED)
            except tk.TclError:
                pass

        self.after(2000, _remove)

    def _animate_thinking(self) -> None:
        """Update thinking indicator in-place with elapsed time."""
        import time as _time
        if self._thinking_marker is None:
            return
        self._thinking_dots = (self._thinking_dots % 3) + 1
        elapsed = int(_time.time() - self._thinking_start_time)
        dots = "." * self._thinking_dots
        # Build progress bar: fills over 30 seconds
        bar_len = 20
        filled = min(bar_len, int(elapsed * bar_len / 30))
        bar = "\u2593" * filled + "\u2591" * (bar_len - filled)
        label = f"\u23f3 Jarvis is thinking{dots}  ({elapsed}s)  [{bar}]\n"
        try:
            marker_end = f"{self._thinking_marker} lineend+1c"
            self.output.config(state=tk.NORMAL)
            self.output.delete(self._thinking_marker, marker_end)
            self.output.insert(self._thinking_marker, label, "thinking")
            self.output.see(self._thinking_marker)
            self.output.config(state=tk.DISABLED)
        except tk.TclError:
            return
        self._thinking_after_id = self.after(400, self._animate_thinking)

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
        _fallback = f"{'https' if (_security_dir(self.root_path) / 'tls_cert.pem').exists() else 'http'}://127.0.0.1:8787"
        return WidgetConfig(
            base_url=self.base_var.get().strip() or _fallback,
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
                self.after(0, self._show_welcome)
            except HTTPError as exc:
                self._log_async(f"Connect failed: {_http_error_details(exc)}", role="error")
            except URLError:
                self._log_async("Cannot reach Jarvis server.", role="error")
                self._log_async("Make sure the Mobile API is running and the Base URL is correct.", role="error")
            except (RuntimeError, TimeoutError) as exc:
                self._log_async(f"Connect failed: {exc}", role="error")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"Connect failed: {exc}", role="error")

        self._thread(worker)

    def _diagnose_repair_async(self) -> None:
        cfg = self._current_cfg()  # Read tkinter vars on main thread

        def worker() -> None:
            try:
                self._log_async("\u2500" * 40, role="system")
                self._log_async("\U0001F527 JARVIS DIAGNOSTICS", role="system")
                self._log_async("\u2500" * 40, role="system")

                # Step 1: Check connection
                self._log_async("[1/4] Checking API connection...", role="system")
                try:
                    health_data = _http_json(cfg, "/health", method="GET")
                    health_ok = bool(health_data.get("ok", False))
                    self._log_async(f"  API: {'ONLINE' if health_ok else 'DEGRADED'}", role="jarvis")
                except Exception:
                    self._log_async("  API: OFFLINE - cannot reach Jarvis services", role="error")
                    self._log_async("  Make sure Mobile API is running (jarvis-engine serve-mobile)", role="system")
                    return

                # Step 2: Check sync status
                self._log_async("[2/4] Checking sync status...", role="system")
                try:
                    sync_data = _http_json(cfg, "/sync/status", method="GET")
                    sync_ok = bool(sync_data.get("ok", False))
                    last_sync = sync_data.get("last_sync_utc", "unknown")
                    self._log_async(f"  Sync: {'OK' if sync_ok else 'Issues detected'}", role="jarvis")
                    self._log_async(f"  Last sync: {last_sync}", role="jarvis")
                except Exception as exc:
                    self._log_async(f"  Sync: unavailable ({exc})", role="error")

                # Step 3: Check intelligence
                self._log_async("[3/4] Testing intelligence pipeline...", role="system")
                try:
                    dash_data = _http_json(cfg, "/dashboard", method="GET")
                    score = dash_data.get("intelligence_score", "?")
                    mem_count = dash_data.get("memory_count", "?")
                    fact_count = dash_data.get("fact_count", "?")
                    self._log_async(f"  Intelligence score: {score}", role="jarvis")
                    self._log_async(f"  Memories: {mem_count}, Facts: {fact_count}", role="jarvis")
                except Exception as exc:
                    self._log_async(f"  Intelligence: unavailable ({exc})", role="error")

                # Step 4: Run self-heal
                self._log_async("[4/4] Running self-heal maintenance...", role="system")
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
                heal_lines = heal_data.get("stdout_tail", [])
                if heal_ok:
                    self._log_async("  Self-heal: completed successfully", role="jarvis")
                else:
                    self._log_async(f"  Self-heal: finished with issues (exit={heal_exit})", role="error")
                if isinstance(heal_lines, list) and heal_lines:
                    for line in heal_lines[-5:]:
                        s = str(line).strip()
                        if s:
                            self._log_async(f"  {s}", role="jarvis")

                self._log_async("\u2500" * 40, role="system")
                if heal_ok:
                    self._log_async("\u2705 All systems healthy.", role="jarvis")
                else:
                    self._log_async("\u26A0 Some issues detected. Check details above.", role="error")
                    self._notify_toast("Jarvis Self-Heal", f"Self-heal finished with issues (exit={heal_exit})", "Warning")
            except HTTPError as exc:
                self._log_async(f"Diagnose failed: {_http_error_details(exc)}", role="error")
                self._notify_toast("Jarvis", "Diagnose & Repair failed", "Error")
            except URLError:
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
                self._log_async("Start with: jarvis-engine daemon", role="system")
            except (RuntimeError, TimeoutError) as exc:
                self._log_async(f"Diagnose failed: {exc}", role="error")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"Diagnose failed: {exc}", role="error")

        self._thread(worker)

    def _thread(self, fn) -> None:  # type: ignore[no-untyped-def]
        threading.Thread(target=fn, daemon=True).start()

    def _cancel_processing_timeout(self) -> None:
        """Cancel the safety timeout for stuck processing state."""
        if self._processing_timeout_id is not None:
            try:
                self.after_cancel(self._processing_timeout_id)
            except Exception:
                pass
            self._processing_timeout_id = None

    def _processing_timed_out(self) -> None:
        """Safety net: force-reset stuck processing state after 120s."""
        self._processing_timeout_id = None
        if self._widget_state == "processing":
            self._hide_thinking()
            self._log("Command timed out (120s). Ready for new commands.", role="error")
            self.command_text.config(state=tk.NORMAL)
            self._set_state("idle")

    def _send_command_async(self) -> None:
        # Guard: warn if already processing but don't silently drop
        if getattr(self, "_widget_state", "idle") == "processing":
            self._log("Still processing previous command. Please wait...", role="system")
            return
        text = self.command_text.get("1.0", tk.END).strip()
        if not text:
            self._log("No command text.")
            return
        # Check for conversation-ending phrases
        lower = text.lower().strip()
        if lower in ("done", "end conversation", "bye", "goodbye", "that's all", "thats all", "end"):
            self.command_text.delete("1.0", tk.END)
            self._end_conversation()
            return
        # Clear command text immediately after reading
        self.command_text.delete("1.0", tk.END)
        # Log the user's command with the "user" role
        self._log(text, role="user")
        self._set_state("processing")
        self._show_thinking()
        self.command_text.config(state=tk.DISABLED)
        # Start safety timeout: force-reset after 120 seconds
        self._cancel_processing_timeout()
        self._processing_timeout_id = self.after(120_000, self._processing_timed_out)
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
                self.after(0, self._hide_thinking)
                self.after(0, self._cancel_processing_timeout)
                # Parse clean response from stdout_tail
                lines = data.get("stdout_tail", [])
                response_text = ""
                reason_text = ""
                error_text = str(data.get("error", ""))
                if isinstance(lines, list):
                    for line in lines:
                        s = str(line)
                        if s.startswith("response="):
                            response_text = s[len("response="):]
                        elif s.startswith("reason=") and not reason_text:
                            reason_text = s[len("reason="):]
                        elif s.startswith("error=") and not error_text:
                            error_text = s[len("error="):]
                intent = str(data.get("intent", "unknown"))
                ok = bool(data.get("ok", False))
                if response_text:
                    self._log_async(response_text, role="jarvis")
                elif not ok and (reason_text or error_text):
                    # Show a clear error message instead of cryptic [intent] ok=False
                    msg = reason_text or error_text
                    self._log_async(f"Error: {msg}", role="error")
                else:
                    self._log_async(f"[{intent}] ok={ok}", role="jarvis")
                    if isinstance(lines, list) and lines:
                        self._log_async(" | ".join(str(x) for x in lines[-6:]), role="jarvis")
                if ok and intent in ("memory_ingest", "memory_forget", "llm_conversation"):
                    self.after(0, self._show_learned_indicator)
                if not ok:
                    self._set_error_briefly_async()
                else:
                    self._set_state_async("idle")
                # Prompt for continuation
                self._log_async("Ready for next command. Say 'done' or click End Conversation when finished.", role="system")
            except HTTPError as exc:
                self.after(0, self._hide_thinking)
                self.after(0, self._cancel_processing_timeout)
                self._log_async(f"Command failed: {_http_error_details(exc)}", role="error")
                self._log_async("Ready for next command.", role="system")
                self._set_error_briefly_async()
            except URLError:
                self.after(0, self._hide_thinking)
                self.after(0, self._cancel_processing_timeout)
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
                self._set_error_briefly_async()
            except (RuntimeError, TimeoutError) as exc:
                self.after(0, self._hide_thinking)
                self.after(0, self._cancel_processing_timeout)
                self._log_async(f"Command failed: {exc}", role="error")
                self._log_async("Ready for next command.", role="system")
                self._set_error_briefly_async()
            except Exception as exc:  # noqa: BLE001
                self.after(0, self._hide_thinking)
                self.after(0, self._cancel_processing_timeout)
                self._log_async(f"Command failed: {exc}", role="error")
                self._log_async("Ready for next command.", role="system")
                self._set_error_briefly_async()
            finally:
                try:
                    self.after(0, lambda: self.command_text.config(state=tk.NORMAL))
                except Exception:
                    pass

        self._thread(worker)

    def _send_text(self, text: str) -> None:
        """Set the command text and send it -- convenience for programmatic use."""
        self._set_command_text(text)
        self._send_command_async()

    def _voice_dictate(self) -> None:
        """Convenience alias for tray menu / external callers."""
        self._dictate_async()

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
            except URLError:
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"Dashboard failed: {exc}", role="error")

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
            except URLError:
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"Could not load activity: {exc}", role="error")

        self._thread(worker)

    def _dictate_async(self) -> None:
        # Guard: skip if already listening or processing to prevent overlapping voice
        if getattr(self, "_widget_state", "idle") in ("listening", "processing"):
            return
        auto_send = bool(self.auto_send_var.get())
        self._set_state("listening")

        def worker() -> None:
            try:
                text = _voice_dictate_once(timeout_s=8)
                if not text:
                    self._log_async("No speech recognized.", role="system")
                    self._set_state_async("idle")
                    return
                self._set_state_async("processing")
                self._set_command_text_async(text)
                self._log_async(f"dictated: {text}", role="system")
                if auto_send:
                    self.after(0, self._send_command_async)
                else:
                    self._set_state_async("idle")
            except Exception as exc:  # noqa: BLE001
                self._log_async(f"dictation failed: {exc}", role="error")
                self._set_error_briefly_async()

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
            # Cooldown: 10s after wake word to avoid re-triggering during processing
            for _ in range(20):
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
            # Build list of URLs to try (configured + localhost fallback)
            from urllib.parse import urlparse as _urlparse
            _pu = _urlparse(cfg.base_url)
            _health_urls = [f"{cfg.base_url.rstrip('/')}/health"]
            if _pu.hostname not in ("127.0.0.1", "localhost", "::1"):
                _health_urls.append(f"{_pu.scheme}://127.0.0.1:{_pu.port or 8787}/health")

            resp = None
            ok = False
            intel_data: dict[str, Any] | None = None
            for url in _health_urls:
                ssl_ctx = _get_ssl_context(url)
                for _attempt in range(2):
                    try:
                        req = Request(url=url, method="GET")
                        resp = urlopen(req, timeout=5, context=ssl_ctx)
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
                        if resp is not None:
                            try:
                                resp.close()
                            except Exception as exc:
                                logger.debug("Failed to close health poll HTTP response: %s", exc)
                            resp = None
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.2)
                if ok or self.stop_event.is_set():
                    break
            # Fetch growth + alerts in ONE request via /widget-status
            growth_data: dict[str, Any] | None = None
            if ok and cfg.token and cfg.signing_key:
                try:
                    ws = _http_json(cfg, "/widget-status", method="GET")
                    growth_data = ws.get("growth") if isinstance(ws, dict) else None
                    alerts = ws.get("alerts", []) if isinstance(ws, dict) else []
                    if isinstance(alerts, list):
                        for alert in alerts:
                            msg = str(alert.get("message", "")) if isinstance(alert, dict) else str(alert)
                            if msg:
                                self._notify_toast("Jarvis Alert", msg, "Warning")
                                break  # One toast per poll cycle
                except Exception as exc:
                    logger.debug("Failed to fetch widget-status: %s", exc)
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

    def _set_state(self, state: str) -> None:
        """Update the visual state machine: idle | listening | processing | error."""
        if state not in ("idle", "listening", "processing", "error"):
            state = "idle"
        self._widget_state = state
        self._refresh_status_view()

    def _set_state_async(self, state: str) -> None:
        """Schedule state change on the main tkinter thread."""
        if self.stop_event.is_set():
            return
        try:
            self.after(0, self._set_state, state)
        except Exception:
            pass  # Widget destroyed

    def _set_error_briefly(self) -> None:
        """Set error state for 2 seconds, then revert to idle."""
        self._set_state("error")
        # Cancel any previous error-clear timer
        if self._error_clear_id is not None:
            try:
                self.after_cancel(self._error_clear_id)
            except Exception:
                pass
        self._error_clear_id = self.after(2000, self._set_state, "idle")

    def _set_error_briefly_async(self) -> None:
        """Schedule brief error state on the main tkinter thread."""
        if self.stop_event.is_set():
            return
        try:
            self.after(0, self._set_error_briefly)
        except Exception:
            pass

    def _refresh_status_view(self) -> None:
        state = self._widget_state
        if state == "listening":
            self.status_var.set("LISTENING...")
            self.status_label.config(fg=self.ACCENT_2)
        elif state == "processing":
            label = "ONLINE - Processing..." if self.online else "OFFLINE - Processing..."
            self.status_var.set(label)
            self.status_label.config(fg="#ff9f43")
        elif state == "error":
            self.status_var.set("ERROR")
            self.status_label.config(fg=self.WARN)
        elif self.online:
            self.status_var.set("ONLINE - Idle")
            self.status_label.config(fg=self.ACCENT)
        else:
            self.status_var.set("OFFLINE")
            self.status_label.config(fg="#a5b4fc")

    def _orb_color(self) -> str:
        """Return the current orb color based on widget state."""
        state = self._widget_state
        if state == "listening":
            return self.ACCENT_2  # blue
        if state == "processing":
            return "#ff9f43"  # orange
        if state == "error":
            return self.WARN  # red
        # idle: color depends on online status
        return self.ACCENT if self.online else "#6366f1"

    def _animate_orb(self) -> None:
        if self.stop_event.is_set():
            return
        t = time.monotonic() - self._anim_t0
        color = self._orb_color()
        # Smooth breathing pulse (time-based, ~30fps)
        breath = math.sin(t * 3.0)
        pulse = 6.0 + breath * 1.5
        cx, cy = 15.0, 15.0
        # Scanning sweep rotation
        sweep_angle = (t * 120) % 360
        try:
            self.orb_canvas.coords(self.orb_id, cx - pulse, cy - pulse, cx + pulse, cy + pulse)
            self.orb_canvas.itemconfig(self.orb_id, fill=color)
            if self._orb_sweep is not None:
                self.orb_canvas.itemconfig(self._orb_sweep, start=sweep_angle, outline=color)
            self._orb_after_id = self.after(33, self._animate_orb)
        except Exception:
            return

    def _animate_launcher(self) -> None:
        if self.stop_event.is_set():
            return
        try:
            if self.launcher_canvas is None:
                return
            t = time.monotonic() - self._anim_t0
            size = self._launcher_size
            cx, cy = size / 2, size / 2

            # State-dependent speed multiplier
            state = self._widget_state
            if state == "processing":
                speed = 2.5
            elif state == "listening":
                speed = 1.6
            elif state == "error":
                speed = 0.3
            else:
                speed = 1.0

            # Arc 4: outermost decorative ring, slow counter-rotate with breathing extent
            if self._l_arc4 is not None:
                a4 = (360 - (t * 30 * speed) % 360) % 360
                ext4 = 50 + 20 * math.sin(t * 1.2 * speed)
                self.launcher_canvas.itemconfig(self._l_arc4, start=a4, extent=ext4)
            # Rotate arc 1: outer, clockwise with breathing extent
            if self._l_arc1 is not None:
                a1 = (t * 90 * speed) % 360
                ext1 = 220 + 40 * math.sin(t * 0.8 * speed)
                self.launcher_canvas.itemconfig(self._l_arc1, start=a1, extent=ext1)
            # Rotate arc 2: mid, counter-clockwise with breathing extent
            if self._l_arc2 is not None:
                a2 = (360 - (t * 60 * speed) % 360) % 360
                ext2 = 140 + 30 * math.sin(t * 1.4 * speed + 1.0)
                self.launcher_canvas.itemconfig(self._l_arc2, start=a2, extent=ext2)
            # Arc 3: fast inner ring, only visible during processing
            if self._l_arc3 is not None:
                if state == "processing":
                    a3 = (t * 180 * speed) % 360
                    ext3 = 70 + 30 * math.sin(t * 3.0 * speed)
                    self.launcher_canvas.itemconfig(self._l_arc3, start=a3, extent=ext3, state=tk.NORMAL)
                elif state == "listening":
                    # Show subtle inner ring while listening too
                    a3 = (t * 80 * speed) % 360
                    self.launcher_canvas.itemconfig(self._l_arc3, start=a3, extent=60, state=tk.NORMAL)
                else:
                    self.launcher_canvas.itemconfig(self._l_arc3, state=tk.HIDDEN)

            # Core circle breathing
            if self._l_core is not None:
                breath = math.sin(t * 2.5 * speed)
                pad = 24 + breath * 2
                self.launcher_canvas.coords(self._l_core, pad, pad, size - pad, size - pad)

            # Orbiting particles at different radii, speeds, and pulsing sizes
            for i, pid in enumerate(self._l_particles):
                orbit_r = 38 - i * 4
                orbit_speed = (35 + i * 18) * speed
                phase_offset = i * (360.0 / len(self._l_particles))
                angle = math.radians((t * orbit_speed) % 360 + phase_offset)
                # Pulsing particle size
                p_size = 3 + 1.5 * math.sin(t * (3.0 + i * 0.5) * speed + i)
                px = cx + orbit_r * math.cos(angle) - p_size / 2
                py = cy + orbit_r * math.sin(angle) - p_size / 2
                self.launcher_canvas.coords(pid, px, py, px + p_size, py + p_size)

            # Glow halo breathing — more pronounced
            if self._l_glow is not None:
                glow_pulse = 0.5 + 0.5 * math.sin(t * 1.5 * speed)
                gpad = 1 + glow_pulse * 3
                self.launcher_canvas.coords(self._l_glow, gpad, gpad, size - gpad, size - gpad)

            # State-reactive color palette: (arc1, arc2, core, particles, glow, arc4)
            online = self.online
            if state == "listening":
                colors = ("#3b82f6", "#60a5fa", "#1e3a8a", "#93c5fd", "#1e3a5e", "#2563eb")
            elif state == "processing":
                colors = ("#f59e0b", "#fbbf24", "#78350f", "#fde68a", "#3d2800", "#d97706")
            elif state == "error":
                colors = ("#ef4444", "#f87171", "#7f1d1d", "#fca5a5", "#3d0d0d", "#dc2626")
            elif online:
                colors = ("#2dd4bf", "#0ea5e9", "#0f766e", "#5eead4", "#0d3d36", "#14b8a6")
            else:
                colors = ("#6366f1", "#818cf8", "#312e81", "#a5b4fc", "#1e1b4b", "#7c3aed")

            arc1_c, arc2_c, core_c, particle_c, glow_c, arc4_c = colors
            if self._l_arc1 is not None:
                self.launcher_canvas.itemconfig(self._l_arc1, outline=arc1_c)
            if self._l_arc2 is not None:
                self.launcher_canvas.itemconfig(self._l_arc2, outline=arc2_c)
            if self._l_arc4 is not None:
                self.launcher_canvas.itemconfig(self._l_arc4, outline=arc4_c)
            if self._l_core is not None:
                self.launcher_canvas.itemconfig(self._l_core, fill=core_c, outline=arc1_c)
            for pid in self._l_particles:
                self.launcher_canvas.itemconfig(pid, fill=particle_c)
            if self._l_glow is not None:
                self.launcher_canvas.itemconfig(self._l_glow, outline=glow_c)

            self._launcher_after_id = self.after(33, self._animate_launcher)
        except Exception:
            return


def run_desktop_widget() -> None:
    app = JarvisDesktopWidget(_repo_root())
    app.mainloop()
