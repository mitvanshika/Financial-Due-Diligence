"""Tests for the team room's access code (src/dd/server.py, `dd serve` in
src/dd/cli.py).

One shared code, ROOM_ACCESS_CODE, guards the WebSocket and every HTTP
endpoint that returns room data. The pipeline and actions are swapped for the
same fast stand-ins test_room.py uses, so nothing here loads models or calls
Groq.

Run from the project root:   pytest
"""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from typer.testing import CliRunner

import dd.server as server_mod
from dd.server import check_startup, create_app

from test_room import _next, fakes  # noqa: F401 - `fakes` is a fixture

CODE = "s3cret-Room-Code-7f2b9c"
WRONG = "not-the-code-1234"


def _closed_with(ws) -> WebSocketDisconnect:
    """The server's next move must be closing the socket - no state first.
    It also tries to join: if the server wrongly let the socket in, that brings
    a state message and the test fails, instead of waiting forever."""
    ws.send_json({"type": "join", "name": "probe"})
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    return exc.value


def _auth(ws, code: str) -> None:
    ws.send_json({"type": "auth", "code": code})


# --- the WebSocket ---------------------------------------------------------------


def test_the_right_code_lets_you_join(fakes):
    with TestClient(create_app(access_code=CODE)) as client, client.websocket_connect("/ws") as ws:
        _auth(ws, CODE)
        ws.send_json({"type": "join", "name": "Ana"})
        assert _next(ws, "state")["state"]["members"][0]["name"] == "Ana"


def test_a_wrong_code_is_closed_with_4401(fakes):
    with TestClient(create_app(access_code=CODE)) as client:
        with client.websocket_connect("/ws") as ws:
            _auth(ws, WRONG)
            ws.send_json({"type": "join", "name": "Mallory"})
            assert _closed_with(ws).code == 4401
        assert client.app.state.room.members == {}


@pytest.mark.parametrize("first", [
    {"type": "join", "name": "Mallory"},
    {"type": "ask", "text": "What was net income?"},
    {"type": "auth"},
    {"type": "auth", "code": ""},
    {"type": "auth", "code": 12345},
])
def test_anything_but_a_valid_auth_first_is_closed_and_has_no_effect(fakes, first):
    with TestClient(create_app(access_code=CODE)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json(first)
            ws.send_json({"type": "ask", "text": "a second try?"})
            assert _closed_with(ws).code == 4401
        room = client.app.state.room
        assert room.members == {} and room.queue == [] and room.history == []


def test_no_state_reaches_a_socket_that_has_not_authenticated(fakes, monkeypatch):
    """While someone who IS in the room joins and asks, a socket that never
    sent its code receives nothing - only, after the time limit, a close."""
    monkeypatch.setattr(server_mod, "AUTH_TIMEOUT", 0.5)
    with TestClient(create_app(access_code=CODE)) as client:
        with client.websocket_connect("/ws") as lurker, client.websocket_connect("/ws") as ana:
            _auth(ana, CODE)
            ana.send_json({"type": "join", "name": "Ana"})
            _next(ana, "state")
            ana.send_json({"type": "ask", "text": "What was net income?"})
            _next(ana, "state")
            time.sleep(0.7)  # past the limit: the server must have closed it by now
            assert _closed_with(lurker).code == 4401


def test_too_many_wrong_codes_from_one_ip_are_closed_with_4429(fakes):
    with TestClient(create_app(access_code=CODE)) as client:
        for _ in range(server_mod.MAX_FAILURES):
            with client.websocket_connect("/ws") as ws:
                _auth(ws, WRONG)
                assert _closed_with(ws).code == 4401
        with client.websocket_connect("/ws") as ws:
            _auth(ws, CODE)  # even the right code, once locked out
            assert _closed_with(ws).code == 4429
        # HTTP shares the same count.
        assert client.get("/api/health", headers={"Authorization": f"Bearer {CODE}"}).status_code == 429


def test_the_lockout_ends_when_the_window_passes(fakes, monkeypatch):
    monkeypatch.setattr(server_mod, "FAILURE_WINDOW", 0.3)
    with TestClient(create_app(access_code=CODE)) as client:
        for _ in range(server_mod.MAX_FAILURES):
            with client.websocket_connect("/ws") as ws:
                _auth(ws, WRONG)
                _closed_with(ws)
        time.sleep(0.4)
        with client.websocket_connect("/ws") as ws:
            _auth(ws, CODE)
            ws.send_json({"type": "join", "name": "Ana"})
            _next(ws, "state")


def test_only_wrong_codes_count_toward_the_limit(fakes):
    """Successes and connections that sent no code at all (a first visit,
    before the page knows a code is needed) never lock anyone out."""
    with TestClient(create_app(access_code=CODE)) as client:
        for _ in range(server_mod.MAX_FAILURES + 2):
            with client.websocket_connect("/ws") as ws:
                _auth(ws, CODE)
                ws.send_json({"type": "join", "name": "Ana"})
                _next(ws, "state")
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "join", "name": "Ana"})
                assert _closed_with(ws).code == 4401
            assert client.get("/api/health").status_code == 401  # no header: not a guess
        with client.websocket_connect("/ws") as ws:
            _auth(ws, CODE)
            ws.send_json({"type": "join", "name": "Ana"})
            _next(ws, "state")


def test_with_auth_off_the_old_flow_works_and_an_auth_message_is_harmless(fakes):
    """No code configured (only allowed on loopback): everything works as before.
    A browser that still has a code saved sends it first; that changes nothing."""
    with TestClient(create_app()) as client, client.websocket_connect("/ws") as ws:
        _auth(ws, "left over from another session")
        ws.send_json({"type": "join", "name": "Ana"})
        assert _next(ws, "state")["state"]["members"][0]["name"] == "Ana"
        _auth(ws, "again")  # ignored once in, too
        ws.send_json({"type": "ask", "text": "still fine?"})
        _next(ws, "state")


# --- HTTP --------------------------------------------------------------------------


def test_health_needs_the_code_when_auth_is_on(fakes):
    with TestClient(create_app(access_code=CODE)) as client:
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/health", headers={"Authorization": f"Bearer {WRONG}"}).status_code == 401
        assert client.get("/api/health", headers={"Authorization": CODE}).status_code == 401  # no scheme
        ok = client.get("/api/health", headers={"Authorization": f"Bearer {CODE}"})
        assert ok.status_code == 200 and ok.json()["members"] == 0


def test_health_stays_open_when_auth_is_off(fakes):
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200


# --- refusing to start ----------------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5"])
def test_a_reachable_address_without_a_code_refuses_to_start(host):
    with pytest.raises(ValueError, match="ROOM_ACCESS_CODE"):
        check_startup(host, "")
    with pytest.raises(ValueError):
        check_startup(host, "   ")  # blank is not a code
    assert check_startup(host, CODE) is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_without_a_code_starts_with_a_warning(host):
    warning = check_startup(host, "")
    assert warning and "OFF" in warning
    assert check_startup(host, CODE) is None


@pytest.fixture
def serve_cli(monkeypatch):
    """Runs `dd serve` without starting a real server; returns (invoke, calls)."""
    import uvicorn

    from dd import cli
    from dd.config import get_secrets

    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append({"app": app, **kw}))

    def invoke(host: str, code: str):
        monkeypatch.setenv("ROOM_ACCESS_CODE", code)  # beats anything in .env
        get_secrets.cache_clear()
        return CliRunner().invoke(cli.app, ["serve", "--host", host])

    yield invoke, calls
    get_secrets.cache_clear()


def test_dd_serve_refuses_to_start_on_0000_without_a_code(serve_cli):
    invoke, calls = serve_cli
    result = invoke("0.0.0.0", "")
    assert result.exit_code != 0
    assert "ROOM_ACCESS_CODE" in result.output
    assert calls == [], "the server must not start"


def test_dd_serve_starts_on_0000_with_a_code_and_on_loopback_without(serve_cli):
    invoke, calls = serve_cli
    assert invoke("0.0.0.0", CODE).exit_code == 0
    result = invoke("127.0.0.1", "")
    assert result.exit_code == 0 and "OFF" in result.output
    assert [c["host"] for c in calls] == ["0.0.0.0", "127.0.0.1"]


# --- the code never leaks ---------------------------------------------------------


def test_the_code_never_appears_in_a_close_reason_error_or_log_line(fakes, serve_cli, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []

    with TestClient(create_app(access_code=CODE)) as client:
        with client.websocket_connect("/ws") as ws:  # a wrong code
            _auth(ws, WRONG)
            seen.append(_closed_with(ws).reason or "")
        with client.websocket_connect("/ws") as ws:  # a missing code
            ws.send_json({"type": "join", "name": "x"})
            seen.append(_closed_with(ws).reason or "")
        with client.websocket_connect("/ws") as ws:  # in, then every kind of error
            _auth(ws, CODE)
            for bad in ({"type": "join", "name": "Ana"}, {"type": "join", "name": "again"},
                        {"type": "nope"}, {"type": "vote", "action_id": "x", "approve": "no"}):
                ws.send_json(bad)
                seen.append(str(ws.receive_json()))
        for headers in ({}, {"Authorization": f"Bearer {WRONG}"}, {"Authorization": f"Bearer {CODE}"}):
            r = client.get("/api/health", headers=headers)
            seen.append(r.text + str(dict(r.headers)))
        for _ in range(server_mod.MAX_FAILURES):
            with client.websocket_connect("/ws") as ws:
                _auth(ws, WRONG)
                seen.append(_closed_with(ws).reason or "")
        r = client.get("/api/health", headers={"Authorization": f"Bearer {CODE}"})
        assert r.status_code == 429
        seen.append(r.text + str(dict(r.headers)))

    invoke, _ = serve_cli
    seen.append(invoke("0.0.0.0", CODE).output)
    seen.append(invoke("127.0.0.1", CODE).output)

    seen.extend(rec.getMessage() for rec in caplog.records)
    out = capsys.readouterr()
    seen.extend([out.out, out.err])
    for text in seen:
        assert CODE not in text
        assert WRONG not in text, "a guessed code is not logged either"
