"""Tests for log_buffer and GET /admin/logs endpoint."""
import time
import logging

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import admin_router as ar
from log_buffer import _buffer, InMemoryLogHandler, query_logs


# ─── Helpers ──────────────────────────────────────────────────

def _add_entry(msg: str, level: str = "INFO", age_seconds: float = 0.0) -> None:
    """Insert a synthetic log entry directly into the buffer."""
    _buffer.append({
        "ts":         time.time() - age_seconds,
        "level":      level,
        "logger":     "test",
        "msg":        msg,
        "request_id": "-",
    })


def _make_token(roles: list[str] | None = None) -> str:
    """Create an unsigned JWT (works in DEV mode where signature is skipped)."""
    payload = {"sub": "test_user", "name": "Tester", "roles": roles or []}
    return jwt.encode(payload, key="test-secret", algorithm="HS256")


# ─── Fixtures ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_buffer():
    """Ensure the ring-buffer is empty before and after each test."""
    _buffer.clear()
    yield
    _buffer.clear()


@pytest.fixture
def client():
    """Minimal FastAPI test app containing only the admin router.

    This avoids starting the Redis lifespan from main.py while still
    exercising the real admin router logic including the admin guard.
    The guard calls main.decode_token() which runs in DEV mode
    (PUBLIC_KEY=None) during tests, so signature verification is skipped.
    """
    app = FastAPI()
    app.include_router(ar.router)
    return TestClient(app, raise_server_exceptions=True)


# ─── Unit tests: query_logs ───────────────────────────────────

def test_query_logs_time_filter():
    """Only entries within the requested time window are returned."""
    _add_entry("recent", age_seconds=30)          # 30 s ago → inside 5-min window
    _add_entry("old",    age_seconds=400)          # ~7 min ago → outside

    logs, total = query_logs(minutes=5.0)

    assert total == 1
    assert logs[0]["msg"] == "recent"


def test_query_logs_level_filter():
    """Entries below min_level are excluded."""
    _add_entry("debug msg",   level="DEBUG")
    _add_entry("info msg",    level="INFO")
    _add_entry("warning msg", level="WARNING")
    _add_entry("error msg",   level="ERROR")

    logs, total = query_logs(minutes=5.0, min_level="WARNING")

    assert total == 2
    levels = {e["level"] for e in logs}
    assert levels == {"WARNING", "ERROR"}


def test_query_logs_pagination():
    """limit and offset slice the filtered result set correctly."""
    for i in range(10):
        _add_entry(f"msg {i}")

    logs, total = query_logs(minutes=5.0, limit=3, offset=4)

    assert total == 10
    assert len(logs) == 3
    assert logs[0]["msg"] == "msg 4"
    assert logs[2]["msg"] == "msg 6"


def test_in_memory_handler_captures_log_records():
    """InMemoryLogHandler appends formatted records to the shared buffer."""
    handler = InMemoryLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("test_capture")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        log.info("hello from handler test")
        assert any(e["msg"] == "hello from handler test" for e in _buffer)
    finally:
        log.removeHandler(handler)


# ─── Integration tests: GET /admin/logs ───────────────────────

def test_admin_logs_no_token_returns_401(client):
    """Endpoint rejects requests without an Authorization header."""
    resp = client.get("/admin/logs")
    assert resp.status_code == 401


def test_admin_logs_non_admin_role_returns_403(client):
    """Endpoint rejects JWTs that lack the 'admin' role."""
    token = _make_token(roles=["user"])
    resp = client.get("/admin/logs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "Admin role required" in resp.json()["detail"]


def test_admin_logs_returns_correct_structure(client):
    """Admin token receives a well-formed response with matching log data."""
    _add_entry("structured log entry")
    token = _make_token(roles=["admin"])

    resp = client.get(
        "/admin/logs",
        params={"minutes": 5, "limit": 100, "offset": 0},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total"] >= 1
    assert data["returned"] == len(data["logs"])
    assert "query" in data
    assert any(e["msg"] == "structured log entry" for e in data["logs"])


def test_admin_logs_invalid_level_returns_400(client):
    """Unknown log level query parameter is rejected with HTTP 400."""
    token = _make_token(roles=["admin"])
    resp = client.get(
        "/admin/logs",
        params={"level": "VERBOSE"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "VERBOSE" in resp.json()["detail"]
