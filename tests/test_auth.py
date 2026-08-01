"""Optional dashboard authentication."""

import base64
import importlib

import httpx
import pytest


def build_app(monkeypatch, **env):
    """Rebuild the app with the given auth environment."""
    for key in ("AUTH_TOKEN", "AUTH_USERNAME", "AUTH_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import app.auth
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.auth)
    importlib.reload(app.main)
    return app.main.app


def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture(autouse=True)
def restore_modules():
    yield
    import app.auth
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.auth)
    importlib.reload(app.main)


async def test_open_by_default(monkeypatch):
    """Unconfigured means unchanged — upgrading must not lock anyone out."""
    app = build_app(monkeypatch)
    async with client_for(app) as c:
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/api/sections/unknown")).status_code == 404


async def test_bearer_token_required_when_set(monkeypatch):
    app = build_app(monkeypatch, AUTH_TOKEN="s3cret")
    async with client_for(app) as c:
        assert (await c.get("/api/sections/unknown")).status_code == 401

        resp = await c.get(
            "/api/sections/unknown", headers={"Authorization": "Bearer s3cret"}
        )
        assert resp.status_code == 404  # reached the route


async def test_wrong_bearer_token_rejected(monkeypatch):
    app = build_app(monkeypatch, AUTH_TOKEN="s3cret")
    async with client_for(app) as c:
        resp = await c.get(
            "/api/sections/node", headers={"Authorization": "Bearer wrong"}
        )
        assert resp.status_code == 401


async def test_basic_auth_accepted(monkeypatch):
    app = build_app(monkeypatch, AUTH_PASSWORD="hunter2", AUTH_USERNAME="ops")
    creds = base64.b64encode(b"ops:hunter2").decode()
    async with client_for(app) as c:
        assert (await c.get("/api/sections/unknown")).status_code == 401
        resp = await c.get(
            "/api/sections/unknown", headers={"Authorization": f"Basic {creds}"}
        )
        assert resp.status_code == 404


async def test_basic_auth_challenges_the_browser(monkeypatch):
    """Without a challenge header a browser shows JSON instead of prompting."""
    app = build_app(monkeypatch, AUTH_PASSWORD="hunter2")
    async with client_for(app) as c:
        resp = await c.get("/api/sections/node")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"].startswith("Basic ")


async def test_wrong_basic_password_rejected(monkeypatch):
    app = build_app(monkeypatch, AUTH_PASSWORD="hunter2")
    creds = base64.b64encode(b"debug:wrong").decode()
    async with client_for(app) as c:
        resp = await c.get(
            "/api/sections/node", headers={"Authorization": f"Basic {creds}"}
        )
        assert resp.status_code == 401


async def test_malformed_basic_header_rejected(monkeypatch):
    app = build_app(monkeypatch, AUTH_PASSWORD="hunter2")
    async with client_for(app) as c:
        for header in ("Basic !!!notbase64", "Basic ", "Bearer hunter2", "garbage"):
            resp = await c.get("/api/sections/node", headers={"Authorization": header})
            assert resp.status_code == 401, header


async def test_health_stays_open_for_kubelet_probes(monkeypatch):
    """The kubelet cannot present credentials; probes must not start failing."""
    app = build_app(monkeypatch, AUTH_TOKEN="s3cret", AUTH_PASSWORD="hunter2")
    async with client_for(app) as c:
        assert (await c.get("/api/health")).status_code == 200


async def test_static_frontend_is_also_protected(monkeypatch):
    """Auth that only covers /api leaves the dashboard itself readable."""
    app = build_app(monkeypatch, AUTH_TOKEN="s3cret")
    async with client_for(app) as c:
        assert (await c.get("/")).status_code == 401


async def test_websocket_scope_is_covered(monkeypatch):
    """BaseHTTPMiddleware would have let the log stream through unchecked."""

    build_app(monkeypatch, AUTH_TOKEN="s3cret")
    importlib.reload(__import__("app.auth", fromlist=["auth"]))
    import app.auth as auth_mod

    sent: list[dict] = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("request reached the app without credentials")

    middleware = auth_mod.AuthMiddleware(downstream)
    await middleware(
        {"type": "websocket", "path": "/api/containers/abc/logs", "headers": []},
        receive,
        send,
    )
    assert sent == [{"type": "websocket.close", "code": 1008}]
