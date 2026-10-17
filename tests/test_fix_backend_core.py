"""Regression tests for the backend-core audit fixes (2026-10-17).

Collectors are replaced by stubs, so nothing here touches a real node.
"""

import base64
import importlib
import time

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

FULL_ID = "a" * 64


def reload_app(monkeypatch, **env):
    for key in (
        "AUTH_TOKEN",
        "AUTH_USERNAME",
        "AUTH_PASSWORD",
        "SSH_ENABLED",
        "SSH_PASSWORD_AUTH",
        "SSH_PASSWORD",
    ):
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


@pytest.fixture(autouse=True)
def restore_modules():
    yield
    import app.auth
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.auth)
    importlib.reload(app.main)


def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


# ── config ──


@pytest.mark.parametrize("value", ["True", "TRUE", "on", " yes "])
def test_ssh_enabled_accepts_any_case(monkeypatch, value):
    reload_app(monkeypatch, SSH_ENABLED=value)
    import app.config

    assert app.config.SSH_ENABLED is True


def test_ssh_password_auth_needs_a_password(monkeypatch):
    """The entrypoint refuses password auth without SSH_PASSWORD."""
    reload_app(monkeypatch, SSH_PASSWORD_AUTH="true")
    import app.config

    assert app.config.SSH_PASSWORD_AUTH is False

    reload_app(monkeypatch, SSH_PASSWORD_AUTH="true", SSH_PASSWORD="pw")
    assert app.config.SSH_PASSWORD_AUTH is True


async def test_secret_with_trailing_newline_still_matches(monkeypatch):
    app = reload_app(monkeypatch, AUTH_TOKEN="tok\n", AUTH_PASSWORD="pw\n")
    creds = base64.b64encode(b"debug:pw").decode()
    async with client_for(app) as c:
        h = {"Authorization": "Bearer tok"}
        assert (await c.get("/api/sections/unknown", headers=h)).status_code == 404
        h = {"Authorization": f"Basic {creds}"}
        assert (await c.get("/api/sections/unknown", headers=h)).status_code == 404


# ── auth ──


async def test_non_ascii_basic_credentials_are_401_not_500(monkeypatch):
    app = reload_app(monkeypatch, AUTH_PASSWORD="hunter2")
    creds = base64.b64encode("é:x".encode()).decode()
    async with client_for(app) as c:
        resp = await c.get(
            "/api/sections/unknown", headers={"Authorization": f"Basic {creds}"}
        )
        assert resp.status_code == 401


async def test_non_ascii_password_can_log_in(monkeypatch):
    app = reload_app(monkeypatch, AUTH_PASSWORD="pässwörd", AUTH_USERNAME="jösé")
    creds = base64.b64encode("jösé:pässwörd".encode()).decode()
    async with client_for(app) as c:
        resp = await c.get(
            "/api/sections/unknown", headers={"Authorization": f"Basic {creds}"}
        )
        assert resp.status_code == 404


async def test_non_ascii_bearer_bytes(monkeypatch):
    app = reload_app(monkeypatch, AUTH_TOKEN="tökén")
    async with client_for(app) as c:
        bad = {"Authorization": b"Bearer \xe9"}
        assert (await c.get("/api/sections/unknown", headers=bad)).status_code == 401
        good = {"Authorization": "Bearer tökén".encode()}
        assert (await c.get("/api/sections/unknown", headers=good)).status_code == 404


@pytest.mark.parametrize(
    "header", ["bearer s3cret", "BEARER s3cret", "Bearer\ts3cret", "Bearer  s3cret"]
)
async def test_bearer_scheme_is_case_insensitive(monkeypatch, header):
    app = reload_app(monkeypatch, AUTH_TOKEN="s3cret")
    async with client_for(app) as c:
        resp = await c.get("/api/sections/unknown", headers={"Authorization": header})
        assert resp.status_code == 404


async def test_basic_scheme_is_case_insensitive(monkeypatch):
    app = reload_app(monkeypatch, AUTH_PASSWORD="hunter2")
    creds = base64.b64encode(b"debug:hunter2").decode()
    async with client_for(app) as c:
        resp = await c.get(
            "/api/sections/unknown", headers={"Authorization": f"basic {creds}"}
        )
        assert resp.status_code == 404


# ── log WebSocket ──


@pytest.fixture
def log_ws(monkeypatch, tmp_path):
    """App with crictl stubbed to point at a fake pod log; records tail."""
    app = reload_app(monkeypatch)
    from app.routers import containers

    log = tmp_path / "var/log/pods/ns_pod_uid/ctr/0.log"
    log.parent.mkdir(parents=True)
    log.write_text("first line\n")

    async def fake_log_path(_cid):
        return str(log)

    spawned = []
    real_exec = containers.asyncio.create_subprocess_exec

    async def recording_exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        spawned.append((args, proc))
        return proc

    monkeypatch.setattr(containers, "HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(containers, "_get_container_log_path", fake_log_path)
    monkeypatch.setattr(containers.asyncio, "create_subprocess_exec", recording_exec)
    return TestClient(app), spawned


def test_ws_rejects_cross_origin(log_ws):
    client, spawned = log_ws
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/api/containers/{FULL_ID}/logs",
            headers={"origin": "http://evil.example"},
        ) as ws:
            ws.receive_text()
    assert exc.value.code == 1008
    assert spawned == []


def test_ws_rejects_short_id_prefix(log_ws):
    client, spawned = log_ws
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/containers/a/logs") as ws:
            ws.receive_text()
    assert exc.value.code == 1008
    assert spawned == []


def test_ws_same_origin_streams_and_kills_tail_on_disconnect(log_ws):
    client, spawned = log_ws
    with client.websocket_connect(
        f"/api/containers/{FULL_ID}/logs",
        headers={"origin": "http://testserver"},
    ) as ws:
        assert ws.receive_text() == "first line\n"
        ws.close(1000)
        # Checked before leaving the block: on exit TestClient cancels the
        # handler, which would kill tail even with the old send-only loop.
        # The container is quiet, so only reading the disconnect stops tail.
        (args, proc) = spawned[0]
        for _ in range(50):
            if proc.returncode is not None:
                break
            time.sleep(0.05)
        assert proc.returncode is not None
    # Follows the name, so kubelet's rename-based rotation is picked up.
    assert "-F" in args and "-f" not in args


# ── degraded aggregation ──


class Stub:
    def model_dump(self):
        return {"stub": True}


def _ok(value):
    async def f():
        return value

    return f


async def _boom():
    raise ValueError("invalid literal for int() with base 10: '-'")


def _stub_collectors(monkeypatch, module, failing):
    lists = {
        "collect_pci",
        "collect_usb",
        "collect_nics",
        "collect_sensors",
        "collect_gpus",
        "get_warnings",
    }
    for name in dir(module):
        if not (name.startswith("collect_") or name == "get_warnings"):
            continue
        if name in failing:
            monkeypatch.setattr(module, name, _boom)
        elif name == "collect_connectivity":
            monkeypatch.setattr(module, name, _ok({"dns": []}))
        else:
            monkeypatch.setattr(module, name, _ok([] if name in lists else Stub()))


async def test_overview_degrades_when_one_collector_raises(monkeypatch):
    app = reload_app(monkeypatch)
    from app.routers import overview

    _stub_collectors(monkeypatch, overview, {"collect_cpu"})
    async with client_for(app) as c:
        resp = await c.get("/api/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["hardware"]["cpu"] is None
    assert body["errors"] == ["cpu"]
    assert body["node"] == {"stub": True}
    assert body["hardware"]["memory"] == {"stub": True}


async def test_hardware_section_degrades(monkeypatch):
    app = reload_app(monkeypatch)
    from app.routers import sections

    _stub_collectors(monkeypatch, sections, {"collect_cpu", "collect_usb"})
    async with client_for(app) as c:
        resp = await c.get("/api/sections/hardware")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cpu"] is None
        assert body["usb_devices"] == []
        assert sorted(body["errors"]) == ["cpu", "usb"]

        # A single-collector section has nothing partial: 503, not a 500.
        _stub_collectors(monkeypatch, sections, {"collect_node_info"})
        assert (await c.get("/api/sections/node")).status_code == 503


async def test_api_hardware_degrades_device_lists(monkeypatch):
    app = reload_app(monkeypatch)
    from app.models.hardware import CPUInfo, MemoryInfo
    from app.routers import hardware

    monkeypatch.setattr(
        hardware,
        "collect_cpu",
        _ok(
            CPUInfo(
                model="x",
                sockets=1,
                cores_per_socket=1,
                threads=1,
                architecture="aarch64",
            )
        ),
    )
    monkeypatch.setattr(
        hardware,
        "collect_memory",
        _ok(MemoryInfo(total_gb=1, available_gb=1, used_percent=0)),
    )
    for name in ("collect_pci", "collect_nics", "collect_sensors", "collect_gpus"):
        monkeypatch.setattr(hardware, name, _ok([]))
    monkeypatch.setattr(hardware, "collect_usb", _boom)
    async with client_for(app) as c:
        resp = await c.get("/api/hardware")
        assert resp.status_code == 200
        assert resp.json()["usb_devices"] == []

        monkeypatch.setattr(hardware, "collect_cpu", _boom)
        assert (await c.get("/api/hardware")).status_code == 503


async def test_warnings_degrade_and_report_missing_source(monkeypatch):
    from app.routers import warnings

    for name in dir(warnings):
        if name.startswith("collect_"):
            monkeypatch.setattr(warnings, name, _ok([]))
    monkeypatch.setattr(warnings, "collect_memory", _ok(None))
    monkeypatch.setattr(warnings, "collect_k8s_node_info", _ok(None))
    monkeypatch.setattr(warnings, "collect_all_smart", _boom)

    result = await warnings.get_warnings()
    assert [(w.source, w.severity) for w in result] == [("dashboard", "warning")]
    assert "smart" in result[0].message


# ── lifetime error counters ──


def test_counter_only_critical_while_it_is_moving(monkeypatch):
    from app.routers import warnings

    monkeypatch.setattr(warnings, "_counters", {})
    now = [1000.0]
    monkeypatch.setattr(warnings.time, "monotonic", lambda: now[0])
    key = ("network", "eth0", "crc")

    assert warnings._counter_severity(key, 0, "critical") is None
    # Already non-zero when first seen: an old event, not proof of a live one.
    assert (
        warnings._counter_severity(("network", "eth1", "crc"), 3, "critical") == "info"
    )

    assert warnings._counter_severity(key, 3, "critical") == "critical"
    now[0] += 3600
    assert warnings._counter_severity(key, 3, "critical") == "critical"

    now[0] += warnings.COUNTER_WINDOW_HOURS * 3600
    assert warnings._counter_severity(key, 3, "critical") == "info"

    assert warnings._counter_severity(key, 4, "critical") == "critical"


def test_unauthenticated_start_logs_warning(monkeypatch, caplog):
    app = reload_app(monkeypatch)
    with caplog.at_level("WARNING"), TestClient(app):
        pass
    assert any("authentication is DISABLED" in r.message for r in caplog.records)

    app = reload_app(monkeypatch, AUTH_TOKEN="t")
    caplog.clear()
    with caplog.at_level("WARNING"), TestClient(app):
        pass
    assert not any("authentication" in r.message for r in caplog.records)


async def test_counters_forget_vanished_interfaces(monkeypatch):
    from app.models.hardware import NICInfo
    from app.routers import warnings

    monkeypatch.setattr(warnings, "_counters", {})
    for name in dir(warnings):
        if name.startswith("collect_"):
            monkeypatch.setattr(warnings, name, _ok([]))
    monkeypatch.setattr(warnings, "collect_memory", _ok(None))
    monkeypatch.setattr(warnings, "collect_k8s_node_info", _ok(None))

    monkeypatch.setattr(warnings, "collect_nics", _ok([NICInfo(name="eth9")]))
    await warnings.get_warnings()
    assert {k[1] for k in warnings._counters} == {"eth9"}

    monkeypatch.setattr(warnings, "collect_nics", _ok([NICInfo(name="eth0")]))
    await warnings.get_warnings()
    assert {k[1] for k in warnings._counters} == {"eth0"}

    # A failed NIC collector must not wipe what is known about live links.
    monkeypatch.setattr(warnings, "collect_nics", _boom)
    await warnings.get_warnings()
    assert {k[1] for k in warnings._counters} == {"eth0"}


@pytest.mark.parametrize(
    "headers",
    [{}, {"origin": "http://TestServer"}],
    ids=["no-origin", "same-origin-any-case"],
)
def test_ws_allows_same_origin_and_non_browser(log_ws, headers):
    client, spawned = log_ws
    with client.websocket_connect(
        f"/api/containers/{FULL_ID}/logs", headers=headers
    ) as ws:
        assert ws.receive_text() == "first line\n"
    assert len(spawned) == 1
