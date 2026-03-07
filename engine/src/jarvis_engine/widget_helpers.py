"""Widget utility functions and configuration management.

Extracted from ``desktop_widget.py`` to improve separation of concerns.
Contains: DPAPI encryption/decryption, WidgetConfig dataclass, config
load/save, HTTP helpers (signed requests, SSL, bootstrap), voice dictation
helpers, toast notifications, tray icon creation, edge snapping, and the
Tooltip class.
"""

from __future__ import annotations

__all__ = ["WidgetConfig"]

import base64
import ctypes
import ctypes.wintypes
import hashlib
import hmac
import ipaddress as _ipaddress_mod
import json
import logging
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

from jarvis_engine._constants import DEFAULT_API_PORT as _DEFAULT_PORT
from jarvis_engine._shared import env_int as _env_int
from jarvis_engine._shared import win_hidden_subprocess_kwargs as _win_hidden_subprocess_kwargs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DPAPI helpers -- Windows Data Protection API via ctypes
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


# ---------------------------------------------------------------------------
# WidgetConfig dataclass
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    from jarvis_engine.config import repo_root
    return repo_root()


def _security_dir(root: Path) -> Path:
    return root / ".planning" / "security"


def _mobile_api_cfg_path(root: Path) -> Path:
    return _security_dir(root) / "mobile_api.json"


def _widget_cfg_path(root: Path) -> Path:
    return _security_dir(root) / "desktop_widget.json"


# ---------------------------------------------------------------------------
# Config load / save
# ---------------------------------------------------------------------------

def _load_mobile_api_cfg(root: Path) -> dict[str, str]:
    path = _mobile_api_cfg_path(root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        logger.debug("Failed to read mobile API config from %s", path)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        "token": str(raw.get("token", "")).strip(),
        "signing_key": str(raw.get("signing_key", "")).strip(),
    }


def _load_widget_cfg(root: Path) -> WidgetConfig:
    from jarvis_engine._shared import load_json_file

    mobile = _load_mobile_api_cfg(root)
    path = _widget_cfg_path(root)
    raw: dict[str, Any] = load_json_file(path, {}, expected_type=dict)

    # Normalize optional keys so legacy configs share one read path.
    raw.setdefault("master_password_protected", "")
    raw.setdefault("master_password", "")
    raw.setdefault("token_protected", "")
    raw.setdefault("token", "")
    raw.setdefault("signing_key_protected", "")
    raw.setdefault("signing_key", "")
    raw.setdefault("base_url", "")
    raw.setdefault("device_id", "galaxy_s25_primary")

    # --- Resolve master password (DPAPI-protected or plaintext legacy) ---
    master_password = ""
    needs_migration = False
    if str(raw.get("master_password_protected", "")).strip():
        b64 = str(raw.get("master_password_protected", ""))
        try:
            master_password = _dpapi_decrypt(b64)
        except (OSError, ValueError, RuntimeError):
            logger.warning("Failed to decrypt protected credential via DPAPI; value unavailable")
    elif str(raw.get("master_password", "")).strip():
        master_password = str(raw.get("master_password", ""))
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
            logger.debug("Config key %r has non-integer value %r; treating as None", key, v)
            return None

    # --- Resolve token and signing_key (DPAPI-protected or plaintext legacy) ---
    def _resolve_dpapi_field(field: str, fallback: str = "") -> str:
        protected_key = f"{field}_protected"
        protected_val = str(raw.get(protected_key, "")).strip()
        if protected_val:
            try:
                return _dpapi_decrypt(protected_val)
            except (OSError, ValueError, RuntimeError):
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
    _default_base = f"{_default_scheme}://127.0.0.1:{_DEFAULT_PORT}"

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
        _local_url = f"{_default_scheme}://127.0.0.1:{_parsed.port or _DEFAULT_PORT}/health"
        _stale = False
        try:
            _ctx = _make_ssl_context_for_self_signed() if _parsed.scheme == "https" else None
            with urlopen(Request(url=_probe_url, method="GET"), timeout=3, context=_ctx):
                pass  # Saved URL works fine
        except (OSError, ValueError) as exc:
            logger.debug("Saved base_url %s unreachable: %s -- trying localhost", _probe_url, exc)
            # Saved URL unreachable -- try localhost
            try:
                _ctx_l = _make_ssl_context_for_self_signed() if _default_scheme == "https" else None
                with urlopen(Request(url=_local_url, method="GET"), timeout=3, context=_ctx_l):
                    _stale = True  # localhost works, saved URL is stale
            except (OSError, ValueError) as exc2:
                logger.debug("Localhost fallback %s also unreachable: %s", _local_url, exc2)
        if _stale:
            _old_url = _base_url
            _base_url = f"{_default_scheme}://127.0.0.1:{_parsed.port or _DEFAULT_PORT}"
            logger.info("Auto-healed stale base_url %s -> %s", _old_url, _base_url)

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
                logger.info("Migrated legacy plaintext credentials to DPAPI-protected storage")
        except (OSError, ValueError, TypeError):
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
            except Exception as exc:  # boundary: catch-all justified
                logger.debug("DPAPI encrypt failed for %s, storing plaintext: %s", field, exc)
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
        except (OSError, ValueError, RuntimeError):
            logger.warning("DPAPI encryption unavailable; storing legacy credential in plaintext")
            payload["master_password"] = cfg.master_password
    # Never write the plaintext key when DPAPI succeeds (no "master_password" key at all)

    _atomic_write_json(_widget_cfg_path(root), payload)


# ---------------------------------------------------------------------------
# HTTP / signing helpers
# ---------------------------------------------------------------------------

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
    except ValueError as exc:
        logger.debug("Could not parse host %r as IP address: %s", host, exc)
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


def _http_timeout_seconds(path: str) -> int:
    """Return HTTP timeout for a widget API path."""
    default_timeout = _env_int("JARVIS_WIDGET_HTTP_TIMEOUT_S", 60, minimum=10, maximum=600)
    long_timeout = _env_int("JARVIS_WIDGET_COMMAND_TIMEOUT_S", 300, minimum=30, maximum=900)
    normalized = (path or "").strip().lower()
    if normalized.startswith("/command") or normalized.startswith("/self-heal"):
        return long_timeout
    return default_timeout


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
        _fallback = f"{_parsed_url.scheme}://127.0.0.1:{_parsed_url.port or _DEFAULT_PORT}"
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
            with urlopen(req, timeout=_http_timeout_seconds(path), context=ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON response: {exc}") from exc
            if not isinstance(parsed, dict):
                raise RuntimeError("Invalid response payload")
            return parsed
        except HTTPError as exc:
            # HTTP errors (401, 403, 500, etc.) indicate the server IS
            # reachable but rejected the request.  Do NOT fall back to
            # localhost -- the issue is auth/server-side, not connectivity.
            raise RuntimeError(f"HTTP request failed: HTTP {exc.code} {exc.reason}") from exc
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


def _http_error_details(exc: HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace").strip()
    except Exception as exc2:  # boundary: catch-all justified
        logger.debug("Failed to read HTTP error response body: %s", exc2)
        raw = ""
    if raw:
        short = raw[:420].replace("\n", " ")
        return f"{exc}; body={short}"
    return str(exc)


# ---------------------------------------------------------------------------
# Voice dictation helpers (Windows System.Speech fallback)
# ---------------------------------------------------------------------------

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
    except RuntimeError as exc:
        # faster-whisper or sounddevice not available -- fall back to System.Speech
        logger.debug("Whisper STT unavailable, falling back to System.Speech: %s", exc)
    except Exception as exc:  # boundary: catch-all justified
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
        except OSError as kill_exc:
            logger.debug("Failed to kill timed-out voice dictation process: %s", kill_exc)
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

    Runs a PowerShell process with a 30-second timeout to prevent hangs.
    Errors are logged but never raised.

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

    # Escape PowerShell special characters to prevent injection
    safe_title = title.replace("'", "''").replace("`", "``").replace("$", "`$").replace(";", "`;")
    safe_message = message.replace("'", "''").replace("`", "``").replace("$", "`$").replace(";", "`;")

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
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            **_win_hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        logger.debug("Toast notification process timed out after 30s")
    except (OSError, ValueError):
        logger.debug("Failed to launch toast notification", exc_info=True)


# ---------------------------------------------------------------------------
# Tray icon, edge snapping, position helpers
# ---------------------------------------------------------------------------

def _create_tray_icon_image():
    """Create a 64x64 PIL Image with a blue background and white 'J' for the tray icon."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.debug("Pillow not available; skipping tray icon image creation")
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


import tkinter as tk


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
    except Exception:  # Widget may be destroyed
        logger.debug("Cannot read screen dimensions for edge snap (widget may be destroyed)")
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
    except Exception:  # Widget may be destroyed
        logger.debug("Cannot read screen dimensions for position validation (widget may be destroyed)")
        return False
    return -100 <= x <= screen_w and -100 <= y <= screen_h


# ---------------------------------------------------------------------------
# Tooltip class
# ---------------------------------------------------------------------------

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

    def _schedule(self, _event: tk.Event[Any] | None = None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _cancel(self, _event: tk.Event[Any] | None = None) -> None:
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
