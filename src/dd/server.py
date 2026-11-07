"""Layer 7: the web server - networking only.

Serves the team's web page and one WebSocket, /ws, that connects each
browser to the shared room. All the rules (queue, live progress, the vote on
actions) live in room.py; this file only turns WebSocket messages into Room
calls and Room errors into messages.

Messages a browser sends (JSON objects):
    {"type": "auth",    "code": "..."}                       - must come first
    {"type": "join",    "name": "Sam"}                       - then this
    {"type": "ask",     "text": "What risks ...?"}
    {"type": "remove",  "question_id": "..."}               - from the queue
    {"type": "propose", "kind": "checklist|flag|memo", "question_id": "..."}
    {"type": "vote",    "action_id": "...", "approve": true|false}

Messages the server sends:
    {"type": "state", "you": "<your member id>", "state": {...}}  - after every change
    {"type": "error", "message": "..."}                           - only to the sender

Access code: with ROOM_ACCESS_CODE set, a socket that does not send the right
code first is closed with 4401 (4429 after too many wrong codes from its IP),
and HTTP data endpoints need `Authorization: Bearer <code>`. Without a code,
auth is off - `dd serve` only allows that on loopback (check_startup).

Start it with `dd serve`.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT
from .room import Room

WEB_DIST = PROJECT_ROOT / "web" / "dist"

# A socket has this long to send its code before it is closed.
AUTH_TIMEOUT = 10.0
# More wrong codes than this from one IP within the window = locked out until
# the oldest one is a window old. Only wrong codes count: a success, or a
# connection that sent no code at all, never locks anyone out.
MAX_FAILURES = 5
FAILURE_WINDOW = 60.0
CLOSE_BAD_CODE = 4401
CLOSE_TOO_MANY = 4429


def check_startup(host: str, code: str) -> str | None:
    """Fail closed: a room reachable from other machines must have a code.
    Raises ValueError if it would start open on the network; returns a warning
    if auth is off on loopback; None if a code is set."""
    if code.strip():
        return None
    if host == "localhost" or _is_loopback(host):
        return f"ROOM_ACCESS_CODE is not set - auth is OFF. Fine only because {host} is this machine alone."
    raise ValueError(
        f"refusing to start on {host} without an access code: anyone who can reach this port "
        "could join, vote and spend the Groq quota. Set ROOM_ACCESS_CODE in .env, "
        "or use --host 127.0.0.1 for this machine only."
    )


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # any other host name: assume reachable


def _code_matches(given: Any, code: str) -> bool:
    # Bytes, because compare_digest refuses non-ASCII text.
    return isinstance(given, str) and hmac.compare_digest(given.encode("utf-8"), code.encode("utf-8"))


class FailedAttempts:
    """Wrong codes per client IP, in memory. Forgotten on restart."""

    def __init__(self) -> None:
        self._times: dict[str, deque[float]] = {}

    def blocked(self, ip: str) -> bool:
        times = self._times.get(ip)
        if times is None:
            return False
        while times and times[0] <= time.monotonic() - FAILURE_WINDOW:
            times.popleft()
        if not times:
            del self._times[ip]
            return False
        return len(times) >= MAX_FAILURES

    def record(self, ip: str) -> None:
        self._times.setdefault(ip, deque()).append(time.monotonic())


def _field(msg: dict[str, Any], key: str) -> str:
    value = msg.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"'{key}' must be text")
    return value


async def _handle(room: Room, member_id: str, msg: dict[str, Any]) -> None:
    kind = msg.get("type")
    if kind == "ask":
        await room.ask(member_id, _field(msg, "text"))
    elif kind == "remove":
        await room.remove(member_id, _field(msg, "question_id"))
    elif kind == "propose":
        await room.propose(member_id, _field(msg, "kind"), _field(msg, "question_id"))
    elif kind == "vote":
        approve = msg.get("approve")
        if not isinstance(approve, bool):
            # Only a real true/false counts: the text "false" must never
            # be read as a yes.
            raise ValueError("'approve' must be true or false")
        await room.vote(member_id, _field(msg, "action_id"), approve)
    elif kind == "join":
        raise ValueError("you have already joined")
    else:
        raise ValueError(f"unknown message type '{kind}'")


def _from_own_page(ws: WebSocket) -> bool:
    """A browser tags a WebSocket with the address of the page that opened it.
    Only our own page may join: otherwise any website open in a team member's
    browser could connect to a room on localhost, ask questions and vote.
    No Origin header = not a browser page (scripts, tests): allowed, because
    this check protects people's browsers, not the network."""
    origin = ws.headers.get("origin")
    return origin is None or urlsplit(origin).netloc == ws.headers.get("host", "")


def _client_ip(conn: WebSocket | Request) -> str:
    return conn.client.host if conn.client else "unknown"


async def _authenticate(ws: WebSocket, code: str, failures: FailedAttempts) -> bool:
    """The first message must be {"type": "auth", "code": <the code>}. Until
    then nothing is sent and nothing is handled. On failure the socket is
    closed - with no reason text, so nothing about the code goes back."""
    ip = _client_ip(ws)
    if failures.blocked(ip):
        await ws.close(code=CLOSE_TOO_MANY)
        return False
    try:
        msg = json.loads(await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT))
    except WebSocketDisconnect:
        return False
    except (asyncio.TimeoutError, ValueError):
        msg = None
    given = msg.get("code") if isinstance(msg, dict) and msg.get("type") == "auth" else None
    if _code_matches(given, code):
        return True
    if isinstance(given, str) and given:
        failures.record(ip)  # a real guess; a missing code is not one
    await ws.close(code=CLOSE_BAD_CODE)
    return False


def create_app(state_file: Path | None = None, access_code: str | None = None) -> FastAPI:
    """access_code None or blank = auth off. `dd serve` passes ROOM_ACCESS_CODE
    and decides, through check_startup, whether running without one is allowed."""
    room = Room(state_file=state_file)
    code = access_code if access_code and access_code.strip() else None
    failures = FailedAttempts()

    def require_code(request: Request) -> None:
        """For every HTTP endpoint that returns room or answer data."""
        if not code:
            return
        ip = _client_ip(request)
        if failures.blocked(ip):
            raise HTTPException(429, "too many wrong access codes - wait a minute")
        scheme, _, given = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not given:
            raise HTTPException(401, "access code required", headers={"WWW-Authenticate": "Bearer"})
        if not _code_matches(given, code):
            failures.record(ip)
            raise HTTPException(401, "wrong access code", headers={"WWW-Authenticate": "Bearer"})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await room.start()
        yield
        await room.stop()

    app = FastAPI(title="Financial due-diligence room", lifespan=lifespan)
    app.state.room = room

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        require_code(request)  # the member count is room data
        return {"status": room.status, "members": len(room.members)}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        if not _from_own_page(ws):
            await ws.close(code=1008)  # policy violation
            return
        await ws.accept()
        if code and not await _authenticate(ws, code, failures):
            return
        member_id: str | None = None
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        raise ValueError("a message must be a JSON object")
                    if msg.get("type") == "auth":
                        pass  # already through (or auth is off): nothing to do
                    elif member_id is None:
                        if msg.get("type") != "join":
                            raise ValueError("join with a name first")
                        member_id = await room.join(ws, _field(msg, "name"))
                    else:
                        await _handle(room, member_id, msg)
                except (ValueError, json.JSONDecodeError) as exc:
                    # A bad message is answered, never fatal: the sender
                    # stays connected and nobody else is affected.
                    await ws.send_json({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            if member_id is not None:
                await room.disconnect(member_id, ws)

    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/")
        async def no_frontend() -> PlainTextResponse:
            return PlainTextResponse(
                "The web app hasn't been built yet. From the project root run:\n"
                "  cd web\n  npm install\n  npm run build\n"
                "then restart `dd serve`."
            )

    return app
