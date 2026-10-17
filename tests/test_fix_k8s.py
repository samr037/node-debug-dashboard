"""Regression tests for the Kubernetes collector and the shared HTTP clients."""

import asyncio
import os
import ssl

import httpx
import pytest

from app import httpclient
from app.collectors import kubernetes as k8s


class _FakeClient:
    """Stands in for an httpx.AsyncClient and records what it was sent."""

    def __init__(self, responses: dict[str, httpx.Response] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, headers=None, **kwargs):
        self.calls.append((url, dict(headers or {})))
        response = httpx.Response(200, text="ok")
        for prefix, candidate in self.responses.items():
            if url.startswith(prefix):
                response = candidate
                break
        response.request = httpx.Request("GET", url)
        return response


async def _token():
    return "s3cret"


@pytest.fixture
def no_clients():
    """Start from an empty client pool and close whatever a test built."""
    httpclient._clients.clear()
    httpclient._stamps.clear()
    httpclient._retired.clear()
    yield
    asyncio.run(httpclient.close_clients())


# --- /healthz must be an exact, successful "ok" ---------------------------


def test_failing_healthz_is_not_healthy(monkeypatch, clear_caches):
    body = (
        "[+]ping ok\n[+]log ok\n[-]etcd failed: reason withheld\nhealthz check failed"
    )
    fake = _FakeClient({"https://10.96.0.1": httpx.Response(500, text=body)})
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    monkeypatch.setattr(k8s, "read_service_account_token", _token)
    monkeypatch.setattr(k8s, "kubernetes_client", lambda: fake)

    endpoint = asyncio.run(k8s.collect_k8s_api_endpoint())
    assert endpoint.healthy is False
    assert endpoint.url == "https://10.96.0.1:443"


def test_ok_healthz_is_healthy(monkeypatch, clear_caches):
    fake = _FakeClient()
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setattr(k8s, "read_service_account_token", _token)
    monkeypatch.setattr(k8s, "kubernetes_client", lambda: fake)

    assert asyncio.run(k8s.collect_k8s_api_endpoint()).healthy is True


def test_api_endpoint_drops_token_without_verified_client(monkeypatch, clear_caches):
    fake = _FakeClient()
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setattr(k8s, "read_service_account_token", _token)
    monkeypatch.setattr(k8s, "kubernetes_client", lambda: None)
    monkeypatch.setattr(k8s, "insecure_client", lambda: fake)

    asyncio.run(k8s.collect_k8s_api_endpoint())
    assert fake.calls and all("Authorization" not in h for _, h in fake.calls)


# --- etcd 3.5 has no dbSizeQuota ------------------------------------------


def test_etcd_35_without_quota_still_alerts():
    gib = 1024**3
    status = k8s._parse_etcd_status(
        {"version": "3.5.16", "dbSize": str(int(1.8 * gib)), "dbSizeInUse": "1048576"}
    )
    assert status.db_size_quota_mb == 2048.0
    assert status.quota_used_percent == 90.0
    assert status.defrag_severity == "critical"


# --- the token only goes to the API server --------------------------------


def test_component_probes_send_token_only_to_apiserver(monkeypatch, clear_caches):
    insecure, verified = _FakeClient(), _FakeClient()
    monkeypatch.setattr(k8s, "read_service_account_token", _token)
    monkeypatch.setattr(k8s, "insecure_client", lambda: insecure)
    monkeypatch.setattr(k8s, "local_apiserver_client", lambda: verified)
    monkeypatch.setattr(k8s, "etcd_client", lambda: None)

    components = asyncio.run(k8s.collect_k8s_components())
    # The token only travels over the CA-verified client, to the API server.
    assert all("Authorization" not in h for _, h in insecure.calls)
    assert verified.calls == [
        ("https://localhost:6443/healthz", {"Authorization": "Bearer s3cret"})
    ]
    status = {c.name: c.health_status for c in components}
    assert status["kube-apiserver"] == "Healthy"


def test_apiserver_probe_is_anonymous_without_ca(monkeypatch, clear_caches):
    insecure = _FakeClient()
    monkeypatch.setattr(k8s, "read_service_account_token", _token)
    monkeypatch.setattr(k8s, "insecure_client", lambda: insecure)
    monkeypatch.setattr(k8s, "local_apiserver_client", lambda: None)
    monkeypatch.setattr(k8s, "etcd_client", lambda: None)

    asyncio.run(k8s.collect_k8s_components())
    assert "https://localhost:6443/healthz" in {url for url, _ in insecure.calls}
    assert all("Authorization" not in h for _, h in insecure.calls)


def test_local_apiserver_client_verifies_ca_not_hostname(
    monkeypatch, tmp_path, no_clients
):
    monkeypatch.setattr(httpclient, "SA_CA_PATH", str(tmp_path / "missing.crt"))
    assert httpclient.local_apiserver_client() is None

    built = []
    real = ssl.create_default_context

    def fake_default_context(cafile=None):
        context = real()
        built.append((cafile, context))
        return context

    (tmp_path / "ca.crt").write_text("x")
    monkeypatch.setattr(httpclient, "SA_CA_PATH", str(tmp_path / "ca.crt"))
    monkeypatch.setattr(httpclient.ssl, "create_default_context", fake_default_context)
    assert httpclient.local_apiserver_client() is not None
    cafile, context = built[0]
    assert cafile == str(tmp_path / "ca.crt")
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is False


# --- no unverified fallback for the service account client ----------------


def test_kubernetes_client_without_ca_is_none(monkeypatch, tmp_path, no_clients):
    monkeypatch.setattr(httpclient, "SA_CA_PATH", str(tmp_path / "missing.crt"))
    assert httpclient.kubernetes_client() is None


def test_k8s_get_skips_call_without_ca(monkeypatch):
    monkeypatch.setattr(k8s, "read_service_account_token", _token)
    monkeypatch.setattr(k8s, "kubernetes_client", lambda: None)
    assert asyncio.run(k8s._k8s_get("/api/v1/nodes")) is None


# --- API URL from the injected service IP, not cluster DNS ----------------


def test_k8s_get_uses_service_ip(monkeypatch):
    fake = _FakeClient({"https://": httpx.Response(200, json={"items": []})})
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "6443")
    monkeypatch.setattr(k8s, "read_service_account_token", _token)
    monkeypatch.setattr(k8s, "kubernetes_client", lambda: fake)

    assert asyncio.run(k8s._k8s_get("/api/v1/nodes")) == {"items": []}
    assert fake.calls[0][0] == "https://10.96.0.1:6443/api/v1/nodes"


def test_api_base_brackets_ipv6_and_falls_back_to_dns(monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "fd00::1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    assert httpclient.kubernetes_api_base() == "https://[fd00::1]:443"
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    assert httpclient.kubernetes_api_base() == "https://kubernetes.default.svc"


# --- Talos keeps the Kubernetes PKI under /system/secrets/kubernetes ------


def test_talos_kubernetes_pki_is_scanned(monkeypatch, tmp_path, clear_caches):
    nested = tmp_path / "system/secrets/kubernetes/kube-apiserver"
    nested.mkdir(parents=True)
    (nested / "apiserver.crt").write_text("")
    seen: list[str] = []

    async def fake_run(cmd, *args, **kwargs):
        seen.append(cmd[cmd.index("-in") + 1])
        return "notAfter=Jan  1 00:00:00 2099 GMT\n", "", 0

    monkeypatch.setattr(k8s, "HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(k8s, "run_command", fake_run)

    certs = asyncio.run(k8s.collect_k8s_certificates())
    assert seen == [str(nested / "apiserver.crt")]
    assert certs[0].expiry_severity == "ok"


# --- etcd SSL context is built once and rebuilt on rotation ---------------


def test_etcd_context_cached_until_files_change(monkeypatch, tmp_path, no_clients):
    files = []
    for name in ("ca.crt", "admin.crt", "admin.key"):
        path = tmp_path / name
        path.write_text("x")
        files.append(str(path))
    monkeypatch.setattr(httpclient, "ETCD_CA", files[0])
    monkeypatch.setattr(httpclient, "ETCD_CERT", files[1])
    monkeypatch.setattr(httpclient, "ETCD_KEY", files[2])

    builds = []

    def fake_context():
        builds.append(1)
        return ssl.create_default_context()

    monkeypatch.setattr(httpclient, "_etcd_context", fake_context)

    first = httpclient.etcd_client()
    assert httpclient.etcd_client() is first
    assert len(builds) == 1

    stat = os.stat(files[1])
    os.utime(files[1], ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second = httpclient.etcd_client()
    assert second is not first
    assert len(builds) == 2
    assert first in httpclient._retired


def test_etcd_client_none_without_certs(monkeypatch, tmp_path, no_clients):
    monkeypatch.setattr(httpclient, "ETCD_CA", str(tmp_path / "nope.crt"))
    assert httpclient.etcd_client() is None
