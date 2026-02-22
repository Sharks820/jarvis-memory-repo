from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class ConnectorDefinition:
    connector_id: str
    name: str
    setup_url: str
    required_permission: bool
    required_any_env: tuple[str, ...] = ()
    required_all_env: tuple[str, ...] = ()
    fallback_local_files: tuple[str, ...] = ()


@dataclass
class ConnectorStatus:
    connector_id: str
    name: str
    setup_url: str
    required_permission: bool
    permission_granted: bool
    configured: bool
    ready: bool
    missing_env: list[str]
    missing_files: list[str]
    message: str


CONNECTORS: tuple[ConnectorDefinition, ...] = (
    ConnectorDefinition(
        connector_id="calendar",
        name="Calendar (Google/ICS)",
        setup_url="https://calendar.google.com/calendar/u/0/r/settings/export",
        required_permission=True,
        required_any_env=("JARVIS_CALENDAR_JSON", "JARVIS_CALENDAR_ICS_FILE", "JARVIS_CALENDAR_ICS_URL"),
    ),
    ConnectorDefinition(
        connector_id="email",
        name="Email (IMAP or JSON feed)",
        setup_url="https://support.google.com/accounts/answer/185833",
        required_permission=True,
        required_any_env=("JARVIS_EMAIL_JSON",),
        required_all_env=("JARVIS_IMAP_HOST", "JARVIS_IMAP_USER", "JARVIS_IMAP_PASS"),
    ),
    ConnectorDefinition(
        connector_id="tasks",
        name="Tasks Source",
        setup_url="https://github.com/gsd-build/get-shit-done",
        required_permission=False,
        required_any_env=("JARVIS_TASKS_JSON",),
        fallback_local_files=(".planning/tasks.json",),
    ),
    ConnectorDefinition(
        connector_id="bills",
        name="Bills Source",
        setup_url="https://support.google.com/googlepay",
        required_permission=False,
        required_any_env=("JARVIS_BILLS_JSON",),
        fallback_local_files=(".planning/bills.json",),
    ),
    ConnectorDefinition(
        connector_id="subscriptions",
        name="Subscriptions Source",
        setup_url="https://play.google.com/store/account/subscriptions",
        required_permission=False,
        required_any_env=("JARVIS_SUBSCRIPTIONS_JSON",),
        fallback_local_files=(".planning/subscriptions.json",),
    ),
    ConnectorDefinition(
        connector_id="mobile_ingest",
        name="Samsung/Phone Ingest Bridge",
        setup_url="https://github.com/termux/termux-app",
        required_permission=True,
        required_all_env=("JARVIS_MOBILE_TOKEN", "JARVIS_MOBILE_SIGNING_KEY"),
    ),
)


def load_connector_permissions(repo_root: Path) -> dict[str, Any]:
    path = _permissions_path(repo_root)
    if not path.exists():
        return {"connectors": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"connectors": {}}
    if not isinstance(raw, dict):
        return {"connectors": {}}
    connectors = raw.get("connectors")
    if not isinstance(connectors, dict):
        return {"connectors": {}}
    return {"connectors": connectors}


def grant_connector_permission(repo_root: Path, connector_id: str, scopes: list[str]) -> dict[str, Any]:
    connector_id = connector_id.strip().lower()
    known = {c.connector_id for c in CONNECTORS}
    if connector_id not in known:
        raise ValueError(f"Unknown connector_id: {connector_id}")
    data = load_connector_permissions(repo_root)
    connectors = data.setdefault("connectors", {})
    connectors[connector_id] = {
        "granted": True,
        "scopes": [s.strip() for s in scopes if s.strip()],
        "granted_utc": datetime.now(UTC).isoformat(),
    }
    save_connector_permissions(repo_root, data)
    return connectors[connector_id]


def save_connector_permissions(repo_root: Path, payload: dict[str, Any]) -> None:
    path = _permissions_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def evaluate_connector_statuses(repo_root: Path) -> list[ConnectorStatus]:
    permissions = load_connector_permissions(repo_root).get("connectors", {})
    statuses: list[ConnectorStatus] = []
    for definition in CONNECTORS:
        permission_granted = True
        if definition.required_permission:
            p = permissions.get(definition.connector_id, {})
            permission_granted = bool(p.get("granted", False))

        any_env_ok = _any_env_set(definition.required_any_env)
        all_env_ok, missing_all = _all_env_set(definition.required_all_env)
        file_ok, missing_files = _any_file_exists(repo_root, definition.fallback_local_files)
        configured = any_env_ok or all_env_ok or file_ok
        ready = configured and permission_granted

        if ready:
            msg = "Connector ready."
        elif (not permission_granted) and definition.required_permission:
            msg = "Permission required before setup."
        else:
            msg = "Setup required."

        statuses.append(
            ConnectorStatus(
                connector_id=definition.connector_id,
                name=definition.name,
                setup_url=definition.setup_url,
                required_permission=definition.required_permission,
                permission_granted=permission_granted,
                configured=configured,
                ready=ready,
                missing_env=missing_all,
                missing_files=missing_files,
                message=msg,
            )
        )
    return statuses


def build_connector_prompts(statuses: list[ConnectorStatus]) -> list[dict[str, str]]:
    prompts: list[dict[str, str]] = []
    for status in statuses:
        if status.ready:
            continue
        if status.required_permission and not status.permission_granted:
            prompts.append(
                {
                    "connector_id": status.connector_id,
                    "title": f"Grant permission: {status.name}",
                    "next_step": f"python -m jarvis_engine.main connect-grant --id {status.connector_id}",
                    "setup_url": status.setup_url,
                    "reason": "Permission missing",
                    "option_voice": f"Jarvis, grant connector {status.connector_id}",
                    "option_tap_url": status.setup_url,
                }
            )
            continue
        prompts.append(
            {
                "connector_id": status.connector_id,
                "title": f"Complete setup: {status.name}",
                "next_step": f"Open setup URL then run ops-sync again",
                "setup_url": status.setup_url,
                "reason": "Configuration missing",
                "option_voice": f"Jarvis, connect {status.connector_id}",
                "option_tap_url": status.setup_url,
            }
        )
    return prompts


def serialize_statuses(statuses: list[ConnectorStatus]) -> list[dict[str, Any]]:
    return [asdict(s) for s in statuses]


def _permissions_path(repo_root: Path) -> Path:
    return repo_root / ".planning" / "security" / "connector_permissions.json"


def _any_env_set(keys: tuple[str, ...]) -> bool:
    if not keys:
        return False
    for key in keys:
        if os.getenv(key, "").strip():
            return True
    return False


def _all_env_set(keys: tuple[str, ...]) -> tuple[bool, list[str]]:
    if not keys:
        return False, []
    missing = [key for key in keys if not os.getenv(key, "").strip()]
    return len(missing) == 0, missing


def _any_file_exists(repo_root: Path, relative_files: tuple[str, ...]) -> tuple[bool, list[str]]:
    if not relative_files:
        return False, []
    missing = []
    for rel in relative_files:
        if (repo_root / rel).exists():
            return True, []
        missing.append(rel)
    return False, missing
