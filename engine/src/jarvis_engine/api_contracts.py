"""API contract definitions for mobile API endpoints.

Defines Python dataclasses matching every API response, ensuring the
desktop engine and Android app never drift apart.  Provides runtime
validation (for debug/test mode) and JSON-schema generation for CI
contract checks.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Type, get_type_hints, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response contracts (dataclasses)
# ---------------------------------------------------------------------------


@dataclass
class HealthResponse:
    """GET /health response."""
    ok: bool = True
    status: str = ""
    intelligence: Optional[Dict[str, Any]] = None


@dataclass
class IntelligenceStatus:
    """Nested intelligence data within HealthResponse."""
    score: float = 0.0
    regression: bool = False
    last_test: str = ""


@dataclass
class BootstrapSession:
    """Nested session credentials within BootstrapResponse."""
    base_url: str = ""
    token: str = ""
    signing_key: str = ""
    device_id: str = ""
    trusted_device: bool = False


@dataclass
class BootstrapResponse:
    """POST /bootstrap response."""
    ok: bool = False
    session: Optional[Dict[str, Any]] = None
    owner_guard: Optional[Dict[str, Any]] = None
    message: str = ""


@dataclass
class CommandResponse:
    """POST /command response."""
    ok: bool = False
    intent: str = ""
    stdout_tail: List[str] = field(default_factory=list)
    command_exit_code: int = 0
    status_code: str = ""
    reason: str = ""
    stderr_tail: List[str] = field(default_factory=list)
    error: str = ""


@dataclass
class SettingsResponse:
    """GET /settings and POST /settings response."""
    ok: bool = True
    settings: Optional[Dict[str, Any]] = None


@dataclass
class DashboardResponse:
    """GET /dashboard response."""
    ok: bool = True
    dashboard: Optional[Dict[str, Any]] = None


@dataclass
class SpamCandidateDto:
    """Individual spam candidate entry."""
    number: str = ""
    score: float = 0.0
    calls: int = 0
    missed_ratio: float = 0.0
    avg_duration_s: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class SpamCandidatesResponse:
    """GET /spam/candidates response."""
    ok: bool = False
    candidates: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ProactiveAlertDto:
    """Individual proactive alert entry."""
    id: str = ""
    type: str = ""
    title: str = ""
    body: str = ""
    group_key: str = ""


@dataclass
class ProactiveAlertsResponse:
    """Future dedicated proactive alerts endpoint response."""
    ok: bool = False
    alerts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ConflictCheckResponse:
    """Calendar conflict checking response."""
    ok: bool = False
    conflicts: List[str] = field(default_factory=list)


@dataclass
class CertFingerprintResponse:
    """GET /cert-fingerprint response."""
    ok: bool = True
    fingerprint: str = ""
    algorithm: str = "sha256"


@dataclass
class IngestResponse:
    """POST /ingest response."""
    ok: bool = True
    record_id: str = ""
    ts: str = ""
    source: str = ""
    kind: str = ""
    task_id: str = ""


@dataclass
class ProcessesResponse:
    """GET /processes response."""
    ok: bool = True
    services: List[Dict[str, Any]] = field(default_factory=list)
    control: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncPullResponse:
    """POST /sync/pull response."""
    ok: bool = True
    encrypted_payload: str = ""
    new_cursors: Dict[str, Any] = field(default_factory=dict)
    has_more: bool = False


@dataclass
class SyncPushResponse:
    """POST /sync/push response."""
    ok: bool = True
    applied: int = 0
    conflicts_resolved: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class SyncStatusResponse:
    """GET /sync/status response."""
    ok: bool = True
    sync_status: Optional[Dict[str, Any]] = None


@dataclass
class ErrorResponse:
    """Generic error response from any endpoint."""
    ok: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Contract registry: endpoint name -> contract dataclass
# ---------------------------------------------------------------------------

_CONTRACT_REGISTRY: Dict[str, Type[Any]] = {
    "GET /health": HealthResponse,
    "POST /bootstrap": BootstrapResponse,
    "POST /command": CommandResponse,
    "GET /settings": SettingsResponse,
    "POST /settings": SettingsResponse,
    "GET /dashboard": DashboardResponse,
    "GET /spam/candidates": SpamCandidatesResponse,
    "GET /cert-fingerprint": CertFingerprintResponse,
    "POST /ingest": IngestResponse,
    "GET /processes": ProcessesResponse,
    "POST /sync/pull": SyncPullResponse,
    "POST /sync/push": SyncPushResponse,
    "GET /sync/status": SyncStatusResponse,
}


# ---------------------------------------------------------------------------
# Type mapping for JSON schema generation
# ---------------------------------------------------------------------------

_PYTHON_TYPE_TO_JSON: Dict[type, str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
}


def _type_to_json_schema(tp: Any) -> Dict[str, Any]:
    """Convert a Python type annotation to a JSON schema fragment."""
    # Handle string type annotations (from __future__ annotations)
    if isinstance(tp, str):
        tp = _resolve_str_type(tp)

    origin = getattr(tp, "__origin__", None)

    # Direct primitive types
    if tp in _PYTHON_TYPE_TO_JSON:
        return {"type": _PYTHON_TYPE_TO_JSON[tp]}

    # NoneType
    if tp is type(None):  # noqa: E721
        return {"type": "null"}

    # Handle parameterized generics
    args = getattr(tp, "__args__", None)
    if origin is not None:
        # Optional[T] = Union[T, None]
        if origin is Union:
            if args:
                non_none = [a for a in args if a is not type(None)]
                if len(non_none) == 1 and len(args) == 2:
                    inner = _type_to_json_schema(non_none[0])
                    inner["nullable"] = True
                    return inner
            return {"type": "object"}

        # List[T]
        if origin is list:
            items = _type_to_json_schema(args[0]) if args else {}
            return {"type": "array", "items": items}

        # Dict[K, V]
        if origin is dict:
            return {"type": "object"}

    # Fallback for bare List/Dict without args
    if tp is list or tp is List:
        return {"type": "array"}
    if tp is dict or tp is Dict:
        return {"type": "object"}

    return {"type": "object"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_contract(endpoint_name: str, response_dict: Dict[str, Any]) -> List[str]:
    """Validate a response dict against the contract for *endpoint_name*.

    Returns a list of error strings.  An empty list means the response
    conforms to the contract.

    This is intended for debug/test mode only — not production.
    """
    contract_cls = _CONTRACT_REGISTRY.get(endpoint_name)
    if contract_cls is None:
        return [f"Unknown endpoint: {endpoint_name}"]

    errors: List[str] = []
    contract_fields = {f.name: f for f in fields(contract_cls)}

    # Check that all contract-required fields are present
    for fname, f in contract_fields.items():
        if fname not in response_dict:
            # Fields with defaults are optional in the response
            if f.default is not dataclasses.MISSING:
                continue  # has a default value, OK to be missing
            if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                continue  # has a default_factory, OK to be missing
            errors.append(f"Missing required field: {fname}")

    # Check type compatibility for present fields
    for fname, f in contract_fields.items():
        if fname not in response_dict:
            continue
        value = response_dict[fname]
        if value is None:
            # Allow None for Optional fields
            continue
        # Basic type check against the annotation
        expected_type = f.type
        if expected_type == "bool" and not isinstance(value, bool):
            errors.append(f"Field {fname}: expected bool, got {type(value).__name__}")
        elif expected_type == "str" and not isinstance(value, str):
            errors.append(f"Field {fname}: expected str, got {type(value).__name__}")
        elif expected_type == "int" and not isinstance(value, int):
            # Allow float for int fields (JSON has no int/float distinction)
            if not isinstance(value, (int, float)):
                errors.append(f"Field {fname}: expected int, got {type(value).__name__}")
        elif expected_type == "float" and not isinstance(value, (int, float)):
            errors.append(f"Field {fname}: expected float, got {type(value).__name__}")

    return errors


def get_contract_schema(endpoint_name: Optional[str] = None) -> Dict[str, Any]:
    """Return JSON schema(s) for API contracts.

    If *endpoint_name* is provided, returns the schema for that single
    endpoint.  Otherwise returns a dict mapping endpoint names to schemas.
    """
    if endpoint_name is not None:
        contract_cls = _CONTRACT_REGISTRY.get(endpoint_name)
        if contract_cls is None:
            return {"error": f"Unknown endpoint: {endpoint_name}"}
        return _dataclass_to_schema(contract_cls)

    return {name: _dataclass_to_schema(cls) for name, cls in _CONTRACT_REGISTRY.items()}


def _dataclass_to_schema(cls: Type[Any]) -> Dict[str, Any]:
    """Convert a dataclass to a JSON-schema-like dict."""
    properties: Dict[str, Any] = {}
    required: List[str] = []
    # Use get_type_hints to resolve string annotations from __future__ annotations
    try:
        resolved_hints = get_type_hints(cls)
    except Exception:
        resolved_hints = {}
    for f in fields(cls):
        resolved_type = resolved_hints.get(f.name, f.type)
        prop = _type_to_json_schema(resolved_type)
        properties[f.name] = prop
        # Fields without defaults are required
        has_default = f.default is not dataclasses.MISSING
        has_factory = f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        if not has_default and not has_factory:
            required.append(f.name)
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _resolve_str_type(type_str: str) -> Any:
    """Resolve a string type annotation to a Python type."""
    mapping = {
        "bool": bool,
        "int": int,
        "float": float,
        "str": str,
    }
    return mapping.get(type_str, str)


def get_android_expected_fields() -> Dict[str, List[str]]:
    """Return the fields Android's Kotlin data classes expect per endpoint.

    This is the ground truth from ApiModels.kt, maintained in sync with
    the Android codebase.  If a field here is missing from the server
    contract, the Android app will receive null/default and may break.
    """
    return {
        "GET /health": ["status"],
        "POST /bootstrap": ["ok", "session", "message"],
        "POST /bootstrap.session": [
            "base_url", "token", "signing_key", "device_id", "trusted_device",
        ],
        "POST /command": ["ok", "intent", "stdout_tail"],
        "GET /settings": ["settings"],
        "GET /settings.settings": ["runtime_control", "gaming_mode"],
        "GET /dashboard": ["dashboard"],
        "GET /dashboard.dashboard": [
            "jarvis", "ranking", "etas", "memory_regression",
        ],
        "GET /spam/candidates": ["ok", "candidates"],
        "GET /cert-fingerprint": ["ok", "fingerprint", "algorithm"],
    }


def check_android_compatibility() -> List[str]:
    """Check that all fields Android expects are present in the server contracts.

    Returns a list of incompatibility descriptions.  Empty means compatible.
    """
    android_fields = get_android_expected_fields()
    errors: List[str] = []

    for endpoint_key, expected in android_fields.items():
        # For nested objects like "POST /bootstrap.session", validate
        # against the appropriate nested dataclass
        if "." in endpoint_key:
            base_endpoint, nested_name = endpoint_key.rsplit(".", 1)
            contract_cls = _CONTRACT_REGISTRY.get(base_endpoint)
            if contract_cls is None:
                errors.append(f"No contract for base endpoint {base_endpoint}")
                continue
            # Find the nested field
            contract_field_names = {f.name for f in fields(contract_cls)}
            if nested_name not in contract_field_names:
                errors.append(
                    f"Android expects nested object '{nested_name}' in "
                    f"{base_endpoint} but contract has no such field"
                )
            continue

        contract_cls = _CONTRACT_REGISTRY.get(endpoint_key)
        if contract_cls is None:
            errors.append(f"No contract for endpoint {endpoint_key}")
            continue

        contract_field_names = {f.name for f in fields(contract_cls)}
        for ef in expected:
            if ef not in contract_field_names:
                errors.append(
                    f"Android expects field '{ef}' from {endpoint_key} "
                    f"but server contract does not include it"
                )

    return errors
