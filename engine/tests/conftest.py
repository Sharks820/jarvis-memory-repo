from __future__ import annotations
# ruff: noqa: E402

import hashlib
import hmac
import math
import os
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Skip expensive embedding model warmup in tests — prevents xdist workers
# from all loading the ~1.2GB nomic-bert model simultaneously.
os.environ.setdefault("JARVIS_SKIP_EMBED_WARMUP", "1")
os.environ.setdefault("JARVIS_SKIP_OLLAMA", "1")


def pytest_unconfigure(config):
    """Force-exit after pytest prints results to avoid hanging on daemon threads.

    Some modules (sentence-transformers, activity_feed) spawn non-daemon
    threads that prevent clean shutdown.  os._exit() bypasses thread join
    and atexit handlers to exit immediately.

    Uses pytest_unconfigure (runs AFTER terminal summary) instead of
    pytest_sessionfinish (runs BEFORE) so test failures are always visible.

    Skips force-exit for xdist workers (they need normal shutdown).
    """
    # Don't force-exit in xdist worker processes
    if hasattr(config, "workerinput"):
        return
    sys.stdout.flush()
    sys.stderr.flush()
    # Brief pause ensures terminal summary is fully written before force-exit
    time.sleep(0.1)
    os._exit(getattr(config, "_exitstatus", 0))


@pytest.fixture(autouse=True)
def _isolate_activity_feed():
    """Redirect the ActivityFeed singleton to an in-memory DB for every test.

    Without this, tests that call log_activity() or get_activity_feed()
    pollute the production activity_feed.db because the singleton resolves
    repo_root() to the real project directory.
    """
    try:
        import jarvis_engine.memory.activity_feed as _af
        _af._reset_feed()
        # Pre-seed the singleton with an in-memory feed so any test that
        # calls log_activity() / get_activity_feed() writes to RAM, not disk.
        _af._feed_holder["instance"] = _af.ActivityFeed(db_path=":memory:")
    except (ImportError, OSError, AttributeError):
        pass
    yield
    try:
        from jarvis_engine.memory.activity_feed import _reset_feed
        _reset_feed()
    except (ImportError, OSError, AttributeError):
        pass


def make_test_db(
    *,
    check_same_thread: bool = True,
    row_factory: bool = True,
) -> sqlite3.Connection:
    """Create a properly configured in-memory SQLite connection for tests.

    Uses :func:`jarvis_engine._db_pragmas.configure_sqlite` so that test DBs
    match production PRAGMA settings (WAL, busy_timeout).

    Parameters
    ----------
    check_same_thread:
        Passed to ``sqlite3.connect``.  Set *False* when the connection is
        shared across threads (e.g. handler tests).
    row_factory:
        When *True* (default), sets ``conn.row_factory = sqlite3.Row`` to
        match the production ``connect_db`` behaviour.
    """
    from jarvis_engine._db_pragmas import configure_sqlite

    conn = sqlite3.connect(":memory:", check_same_thread=check_same_thread)
    if row_factory:
        conn.row_factory = sqlite3.Row
    configure_sqlite(conn)
    return conn


from jarvis_engine.memory.basic_ingest import IngestionPipeline
from jarvis_engine.memory.store import MemoryStore
from jarvis_engine.mobile_routes.server import MobileIngestHandler, MobileIngestServer


class MockEmbeddingService:
    """Deterministic embedding service for testing (shared across test modules)."""

    def __init__(self, dim: int = 768) -> None:
        self._dim = dim

    def embed(self, text: str, prefix: str = "search_document") -> list[float]:
        seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16) / 1e8
        return [math.sin(seed + i * 0.1) for i in range(self._dim)]

    def embed_query(self, query: str) -> list[float]:
        return self.embed(query, prefix="search_query")


@dataclass
class TestServer:
    root: Path
    auth_token: str
    signing_key: str
    host: str
    port: int
    server: MobileIngestServer
    thread: threading.Thread

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def signed_headers(
    raw_body: bytes,
    auth_token: str,
    signing_key: str,
    *,
    timestamp: float | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    ts = int(time.time()) if timestamp is None else int(timestamp)
    nonce_value = uuid.uuid4().hex if nonce is None else nonce
    signing_material = f"{ts}\n{nonce_value}\n".encode("utf-8") + raw_body
    sig = hmac.new(signing_key.encode("utf-8"), signing_material, hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer {auth_token}",
        "X-Jarvis-Signature": sig,
        "X-Jarvis-Timestamp": str(ts),
        "X-Jarvis-Nonce": nonce_value,
        "Content-Type": "application/json",
    }


def http_request(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    req = Request(url=url, method=method, data=body, headers=headers or {})
    try:
        with urlopen(req, timeout=8) as resp:
            return resp.getcode(), resp.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def _make_bus_mock(result_obj):
    """Create a mock bus that returns *result_obj* from dispatch().

    Shared across test_main*.py modules.
    """
    from unittest.mock import MagicMock

    from jarvis_engine.command_bus import CommandBus

    bus = MagicMock(spec=CommandBus)
    bus.dispatch.return_value = result_obj
    return bus


@pytest.fixture()
def mock_bus(monkeypatch):
    """Fixture that creates a mock bus and patches _get_bus to return it.

    Shared across test_main*.py modules.
    """
    from jarvis_engine.cli import ops as cli_ops_mod
    from jarvis_engine.cli import knowledge as cli_knowledge_mod
    from jarvis_engine import _cli_helpers as cli_helpers_mod
    from jarvis_engine.cli import system as cli_system_mod
    from jarvis_engine.cli import security as cli_security_mod
    from jarvis_engine.cli import tasks as cli_tasks_mod
    from jarvis_engine.cli import voice as cli_voice_mod
    def _factory(result_obj):
        bus = _make_bus_mock(result_obj)
        def _get_bus_fn():
            return bus

        monkeypatch.setattr(cli_ops_mod, "_get_bus", _get_bus_fn)
        monkeypatch.setattr(cli_knowledge_mod, "_get_bus", _get_bus_fn)
        monkeypatch.setattr(cli_helpers_mod, "_get_bus", _get_bus_fn)
        monkeypatch.setattr(cli_system_mod, "_get_bus", _get_bus_fn)
        monkeypatch.setattr(cli_security_mod, "_get_bus", _get_bus_fn)
        monkeypatch.setattr(cli_tasks_mod, "_get_bus", _get_bus_fn)
        monkeypatch.setattr(cli_voice_mod, "_get_bus", _get_bus_fn)
        return bus

    return _factory


@pytest.fixture()
def mobile_server(tmp_path: Path) -> TestServer:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    auth_token = "test-auth-token"
    signing_key = "test-signing-key"
    store = MemoryStore(root)
    pipeline = IngestionPipeline(store)
    server = MobileIngestServer(
        ("127.0.0.1", 0),
        MobileIngestHandler,
        auth_token=auth_token,
        signing_key=signing_key,
        pipeline=pipeline,
        repo_root=root,
    )
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield TestServer(
            root=root,
            auth_token=auth_token,
            signing_key=signing_key,
            host=host,
            port=port,
            server=server,
            thread=thread,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

