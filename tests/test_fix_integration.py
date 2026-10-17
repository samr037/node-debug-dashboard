"""Cross-group follow-ups: the pieces one group asked another to finish."""

import pytest

from app.collectors import kubernetes as k8s
from app.collectors import network, storage
from app.models.kubernetes import CertificateInfo, K8sNodeInfo
from app.models.storage import SmartHealth
from app.models.talos import TalosCertificateInfo


def _ok(value):
    async def f(*args, **kwargs):
        return value

    return f


@pytest.fixture
def quiet_warnings(monkeypatch):
    from app.routers import warnings

    for name in dir(warnings):
        if name.startswith("collect_"):
            monkeypatch.setattr(warnings, name, _ok([]))
    monkeypatch.setattr(warnings, "collect_memory", _ok(None))
    monkeypatch.setattr(warnings, "collect_k8s_node_info", _ok(None))
    return warnings


# --- SMART fields reach the model and the warnings ---------------------------


def test_smart_model_keeps_collector_fields():
    s = SmartHealth(
        device="/dev/sda",
        error="timeout",
        standby=False,
        pending_sectors=3,
        offline_uncorrectable=1,
        smart_issues=["Self-test log contains errors"],
    )
    dumped = s.model_dump()
    for key in (
        "error",
        "standby",
        "pending_sectors",
        "offline_uncorrectable",
        "smart_issues",
    ):
        assert key in dumped


async def test_smart_warnings(quiet_warnings, monkeypatch):
    reports = [
        SmartHealth(
            device="/dev/sda",
            pending_sectors=4,
            offline_uncorrectable=2,
            smart_issues=[
                "Self-test log contains errors",
                "Device error log contains errors",
            ],
        ),
        SmartHealth(device="/dev/sdb", error="Command timed out: smartctl"),
        SmartHealth(device="/dev/sdc", standby=True),
    ]
    monkeypatch.setattr(quiet_warnings, "collect_all_smart", _ok(reports))
    got = {
        (w.device, w.message): w.severity for w in await quiet_warnings.get_warnings()
    }
    assert got[("/dev/sda", "Pending sectors: 4")] == "warning"
    assert got[("/dev/sda", "Offline uncorrectable sectors: 2")] == "critical"
    assert got[("/dev/sda", "Self-test log contains errors")] == "critical"
    assert got[("/dev/sda", "Device error log contains errors")] == "warning"
    assert got[("/dev/sdb", "SMART not readable: Command timed out: smartctl")] == (
        "warning"
    )
    assert not any(dev == "/dev/sdc" for dev, _ in got)


async def test_talos_pki_cert_warns_once(quiet_warnings, monkeypatch):
    k8s_cert = CertificateInfo(
        file_path="/host/system/secrets/kubernetes/kube-apiserver/apiserver.crt",
        days_until_expiry=5,
        expiry_severity="critical",
    )
    talos_cert = TalosCertificateInfo(
        name="kube-apiserver/apiserver.crt",
        days_until_expiry=5,
        expiry_severity="critical",
    )
    monkeypatch.setattr(quiet_warnings, "collect_k8s_certificates", _ok([k8s_cert]))
    monkeypatch.setattr(quiet_warnings, "collect_talos_certificates", _ok([talos_cert]))
    certs = [
        w for w in await quiet_warnings.get_warnings() if w.source == "certificate"
    ]
    assert len(certs) == 1 and "Talos" in certs[0].message

    # If the Talos source fails, the Kubernetes one still reports it.
    async def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(quiet_warnings, "collect_talos_certificates", boom)
    certs = [
        w for w in await quiet_warnings.get_warnings() if w.source == "certificate"
    ]
    assert len(certs) == 1 and "K8s" in certs[0].message


# --- K8s API reachability never sends the token unverified --------------------


class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class _Client:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    async def get(self, url, **kw):
        self.calls.append((url, kw.get("headers")))
        return self.resp

    async def head(self, *a, **kw):
        return _Resp(200, "")


@pytest.fixture
def conn_env(monkeypatch, clear_caches):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "fd00::1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "")
    monkeypatch.setattr(network, "read_file", _ok(""))
    monkeypatch.setattr(network, "run_command", _ok(("", "", 1)))
    monkeypatch.setattr(network, "public_client", lambda: _Client(_Resp(200, "")))
    monkeypatch.setattr(network, "read_service_account_token", _ok("SECRET"))
    return monkeypatch


async def test_k8s_probe_uses_verified_client_with_token(conn_env):
    verified = _Client(_Resp(200, "ok"))
    insecure = _Client(_Resp(200, "ok"))
    conn_env.setattr(network, "kubernetes_client", lambda: verified)
    conn_env.setattr(network, "insecure_client", lambda: insecure)
    result = await network.collect_connectivity()
    assert result["kubernetes_api_ok"] is True
    assert verified.calls == [
        ("https://[fd00::1]:443/healthz", {"Authorization": "Bearer SECRET"})
    ]
    assert insecure.calls == []


async def test_k8s_probe_without_ca_is_anonymous(conn_env):
    insecure = _Client(_Resp(200, "ok"))
    conn_env.setattr(network, "kubernetes_client", lambda: None)
    conn_env.setattr(network, "insecure_client", lambda: insecure)
    await network.collect_connectivity()
    (url, headers) = insecure.calls[0]
    assert "SECRET" not in str(headers)


async def test_k8s_probe_requires_200(conn_env):
    conn_env.setattr(network, "kubernetes_client", lambda: _Client(_Resp(500, "ok")))
    assert (await network.collect_connectivity())["kubernetes_api_ok"] is False


# --- degraded Kubernetes fetches are not cached for the full TTL -------------


async def test_failed_node_info_is_retried(monkeypatch, clear_caches):
    monkeypatch.setenv("KUBERNETES_NODE_NAME", "n1")
    calls = []

    async def fake_get(path, params=None):
        calls.append(path)
        return None

    monkeypatch.setattr(k8s, "_k8s_get", fake_get)
    assert await k8s.collect_k8s_node_info() == K8sNodeInfo()
    import app.collectors.base as base

    real = base.time.monotonic
    monkeypatch.setattr(base.time, "monotonic", lambda: real() + 15)
    await k8s.collect_k8s_node_info()
    assert len(calls) == 2  # retried after retry_after, not after 60s


def test_etcd_quota_assumed_flag():
    assert k8s._parse_etcd_status({"dbSize": 1}).quota_assumed is True
    assert k8s._parse_etcd_status(
        {"dbSize": 1, "dbSizeQuota": 8 << 30}
    ).quota_assumed is (False)


# --- a smartctl stuck in D state is not relaunched ----------------------------


async def test_stuck_smartctl_not_relaunched(monkeypatch):
    monkeypatch.setattr(storage.os.path, "exists", lambda p: True)
    monkeypatch.setattr(storage, "_still_running", lambda cmd: True)

    async def run(*a, **kw):
        raise AssertionError("smartctl must not be launched again")

    monkeypatch.setattr(storage, "run_command", run)
    s = await storage.collect_smart_for_device("/dev/sda")
    assert s.error and "still running" in s.error


def test_still_running_matches_exact_argv(tmp_path):
    cmd = ["smartctl", "-a", "-n", "standby", "/dev/sda", "--json"]
    pid = tmp_path / "42"
    pid.mkdir()
    (pid / "cmdline").write_bytes(b"\0".join(c.encode() for c in cmd) + b"\0")
    (pid / "stat").write_text("42 (smartctl) D 1 1 1")
    assert storage._still_running(cmd, str(tmp_path))
    assert not storage._still_running(cmd[:4] + ["/dev/sdb", "--json"], str(tmp_path))
    (pid / "stat").write_text("42 (smartctl) Z 1 1 1")
    assert not storage._still_running(cmd, str(tmp_path))


# --- chart: SSH host keys, counter window, mount propagation -----------------

_helm = pytest.mark.skipif(
    __import__("shutil").which("helm") is None, reason="helm not installed"
)


def _render(*args):
    import subprocess
    from pathlib import Path

    import yaml

    chart = Path(__file__).parent.parent / "charts" / "node-debug-dashboard"
    out = subprocess.run(
        ["helm", "template", "t", str(chart), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    (ds,) = [d for d in yaml.safe_load_all(out) if d and d["kind"] == "DaemonSet"]
    return ds["spec"]["template"]["spec"]


@_helm
def test_chart_ssh_host_keys_and_counter_window():
    spec = _render(
        "--set", "ssh.enabled=true", "--set-string", "counterWindowHours=12"
    )  # fmt: skip
    (c,) = spec["containers"]
    env = {e["name"]: e.get("value") for e in c["env"]}
    assert env["COUNTER_WINDOW_HOURS"] == "12"
    assert env["SSH_HOST_KEY_DIR"] == "/var/lib/node-debug-dashboard/ssh"
    vols = {v["name"]: v for v in spec["volumes"]}
    assert vols["ssh-host-keys"]["hostPath"]["type"] == "DirectoryOrCreate"
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["host-root"]["mountPropagation"] == "HostToContainer"

    spec = _render()  # SSH off: no host-key volume, no counter override
    assert "ssh-host-keys" not in {v["name"] for v in spec["volumes"]}
    (c,) = spec["containers"]
    assert "COUNTER_WINDOW_HOURS" not in {e["name"] for e in c["env"]}
