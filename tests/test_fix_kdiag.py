"""Regression tests for the kdiag-* shell tools.

Each test copies scripts/ into a temp dir, points the shared library at a fake
service account / etcd cert set, and puts a fake `curl` first in PATH. The fake
answers by URL substring from a routes file, or fails with a curl exit code,
and records its argv and stdin so the tests can check where the token went.
Nothing touches a real cluster.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
BASH = os.environ.get("KDIAG_BASH") or shutil.which("bash")
TOKEN = "eyJhbGciOiJSUzI1NiJ9.SECRET-TEST-TOKEN"

pytestmark = pytest.mark.skipif(
    not BASH or not shutil.which("jq"), reason="needs bash and jq"
)

FAKE_CURL = r"""#!/usr/bin/env bash
# Fake curl: log argv, capture the header fed on stdin, answer by URL substring
printf '%s\n' "$*" >> "$FAKE_DIR/argv.log"
url=""
for a in "$@"; do
    case "$a" in
        @-) cat >> "$FAKE_DIR/stdin.log" ;;
        https://*|http://*) url="$a" ;;
    esac
done
while IFS=$'\t' read -r pattern file; do
    [ -n "$pattern" ] || continue
    case "$url" in *"$pattern"*) cat "$file"; exit 0 ;; esac
done < "$FAKE_DIR/routes.tsv"
exit "${FAKE_CURL_EXIT:-7}"
"""


@pytest.fixture
def kdiag(tmp_path):
    """Return run(script, *args, routes={substr: obj|str}) -> CompletedProcess."""
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, scripts)
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text(TOKEN)
    (sa / "ca.crt").write_text("fake-ca")
    etcd = tmp_path / "etcd"
    etcd.mkdir()
    for name in ("ca.crt", "admin.crt", "admin.key"):
        (etcd / name).write_text("x")
    (tmp_path / "proc").mkdir()
    # Overrides appended to the copied library: same code, test paths
    with open(scripts / "_kdiag-lib.sh", "a") as lib:
        lib.write(
            f"""
K8S_SA_DIR="{sa}"
K8S_CA="{sa}/ca.crt"
K8S_TOKEN_FILE="{sa}/token"
NODE_NAME="node1"
HOST_PROC="{tmp_path}/proc"
is_control_plane() {{ return 0; }}
_resolve_etcd_certs() {{
    ETCD_CA="{etcd}/ca.crt"; ETCD_CERT="{etcd}/admin.crt"; ETCD_KEY="{etcd}/admin.key"
}}
"""
        )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "routes.tsv").write_text("")

    def run(script, *args, routes=None, curl_exit=7):
        lines = []
        for i, (pattern, body) in enumerate((routes or {}).items()):
            f = fake / f"resp{i}"
            f.write_text(body if isinstance(body, str) else json.dumps(body))
            lines.append(f"{pattern}\t{f}")
        (fake / "routes.tsv").write_text("\n".join(lines) + "\n")
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "FAKE_DIR": str(fake),
            "FAKE_CURL_EXIT": str(curl_exit),
            "LC_ALL": "C",
        }
        return subprocess.run(
            [BASH, str(scripts / script), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    run.argv_log = lambda: (
        (fake / "argv.log").read_text() if (fake / "argv.log").exists() else ""
    )
    run.stdin_log = lambda: (
        (fake / "stdin.log").read_text() if (fake / "stdin.log").exists() else ""
    )
    return run


def _node(labels=None):
    return {
        "metadata": {
            "name": "node1",
            "labels": labels
            or {
                "kubernetes.io/os": "linux",
                "kubernetes.io/arch": "amd64",
                "kubernetes.io/hostname": "node1",
                "beta.kubernetes.io/os": "linux",
                "beta.kubernetes.io/arch": "amd64",
            },
            "annotations": {"talos.dev/owned-labels": "[]"},
        },
        "status": {
            "capacity": {"cpu": "4", "memory": "16318412Ki", "pods": "110"},
            "allocatable": {"cpu": "3950m", "memory": "15716012Ki", "pods": "110"},
            "conditions": [{"type": "Ready", "status": "True", "message": "ok"}],
        },
    }


def _pod(name, ns="apps", memory="128Mi", cpu="100m", waiting=None):
    status = {
        "phase": "Running",
        "containerStatuses": [{"name": "c", "restartCount": 0, "state": {}}],
    }
    if waiting:
        status["containerStatuses"][0].update(
            restartCount=12, state={"waiting": {"reason": waiting}}
        )
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "containers": [
                {
                    "name": "c",
                    "image": "busybox:1",
                    "resources": {"requests": {"cpu": cpu, "memory": memory}},
                }
            ]
        },
        "status": status,
    }


# ── set -e on API / etcd failures ──


def test_etcd_health_unreachable_reports_unhealthy(kdiag):
    r = kdiag("kdiag-etcd", "health", curl_exit=7)
    assert r.returncode == 0, r.stderr
    assert "etcd is unhealthy" in r.stdout
    assert "etcd unreachable" in r.stderr


def test_node_all_api_unreachable_runs_every_section(kdiag):
    r = kdiag("kdiag-node", "all", curl_exit=28)
    assert r.returncode == 0, r.stderr
    assert "K8s API unreachable" in r.stderr
    assert "Kubelet Health" in r.stdout


def test_pods_and_events_api_unreachable_exit_cleanly(kdiag):
    for script, sub in (("kdiag-pods", "all"), ("kdiag-events", "warnings")):
        r = kdiag(script, sub, curl_exit=7)
        assert r.returncode == 0, (script, r.stderr)
        assert "K8s API unreachable" in r.stderr


# ── kdiag-pods sick returning 1 ──


def test_pods_all_continues_past_sick_pod(kdiag):
    pods = {"items": [_pod("ok"), _pod("crashy", waiting="CrashLoopBackOff")]}
    r = kdiag("kdiag-pods", "all", routes={"/api/v1/pods": pods})
    assert r.returncode == 0, r.stderr
    assert "CrashLoopBackOff" in r.stdout
    assert "Pod Resource Usage" in r.stdout
    assert "Container Images" in r.stdout


def test_pods_resources_total_matches_rows_with_ki(kdiag):
    pods = {"items": [_pod("a", memory="262144Ki"), _pod("b", memory="512M")]}
    r = kdiag("kdiag-pods", "resources", routes={"/api/v1/pods": pods})
    assert r.returncode == 0, r.stderr
    # 256 Mi + 488 Mi (512M = 512e6 bytes)
    assert "256 Mi" in r.stdout and "488 Mi" in r.stdout
    assert "744 Mi memory" in r.stdout


# ── kdiag-node resources / taints ──


def test_node_resources_si_memory_units(kdiag):
    pods = {
        "items": [
            _pod("a", memory="500M"),
            _pod("b", memory="1G"),
            _pod("c", memory="128k", cpu="0.5"),
        ]
    }
    r = kdiag(
        "kdiag-node",
        "all",
        routes={"/api/v1/pods": pods, "/api/v1/nodes/node1": _node()},
    )
    assert r.returncode == 0, r.stderr
    assert "700m / 3950m" in r.stdout
    assert "1.4 GB / 15.0 GB" in r.stdout
    assert "Taints & Labels" in r.stdout and "Kubelet Health" in r.stdout


def test_node_taints_with_only_default_labels(kdiag):
    r = kdiag("kdiag-node", "taints", routes={"/api/v1/nodes/node1": _node()})
    assert r.returncode == 0, r.stderr
    assert "Annotations (non-default)" in r.stdout


def test_node_kubelet_does_not_match_itself(kdiag):
    if subprocess.run(["pgrep", "-x", "kubelet"], capture_output=True).returncode == 0:
        pytest.skip("a real kubelet runs on this host")
    r = kdiag("kdiag-node", "kubelet")
    assert r.returncode == 0, r.stderr
    assert "Kubelet process not found" in r.stdout
    assert "Kubelet running" not in r.stdout


# ── kdiag-etcd quota ──


def test_etcd_size_uses_default_2gib_quota(kdiag):
    status = {
        "dbSize": "2040109465",
        "dbSizeInUse": "2000000000",
        "raftIndex": "1",
        "raftTerm": "2",
    }
    r = kdiag("kdiag-etcd", "size", routes={"/v3/maintenance/status": status})
    assert r.returncode == 0, r.stderr
    assert "of 2.0 GB quota" in r.stdout
    assert "NOSPACE" in r.stdout


def test_etcd_size_uses_reported_quota(kdiag):
    status = {
        "dbSize": "2040109465",
        "dbSizeInUse": "2000000000",
        "dbSizeQuota": "8589934592",
    }
    r = kdiag("kdiag-etcd", "size", routes={"/v3/maintenance/status": status})
    assert r.returncode == 0, r.stderr
    assert "of 8.0 GB quota" in r.stdout
    assert "NOSPACE" not in r.stdout


# ── kdiag-services endpoints ──


def test_services_endpoints_only_system_namespaces(kdiag):
    pods = {"items": [_pod("dns", ns="kube-system"), _pod("x", ns="default")]}
    eps = {
        "items": [
            {
                "metadata": {"name": "kube-dns"},
                "subsets": [
                    {"addresses": [{"ip": "10.0.0.1"}], "ports": [{"port": 53}]}
                ],
            }
        ]
    }
    r = kdiag(
        "kdiag-services", "endpoints", routes={"/api/v1/pods": pods, "/endpoints": eps}
    )
    assert r.returncode == 0, r.stderr
    assert "kube-dns" in r.stdout


def test_services_endpoints_keeps_default_prefixed_namespace(kdiag):
    pods = {"items": [_pod("a", ns="default-apps")]}
    eps = {
        "items": [
            {
                "metadata": {"name": "web"},
                "subsets": [{"addresses": [{"ip": "1.1.1.1"}]}],
            }
        ]
    }
    r = kdiag(
        "kdiag-services", "endpoints", routes={"/api/v1/pods": pods, "/endpoints": eps}
    )
    assert r.returncode == 0, r.stderr
    assert "default-apps:" in r.stdout


# ── token out of argv, TLS verified ──


def test_token_never_on_curl_command_line(kdiag):
    watch_line = json.dumps(
        {
            "type": "ADDED",
            "object": {
                "type": "Warning",
                "reason": "BackOff",
                "involvedObject": {"kind": "Pod", "name": "p"},
            },
        }
    )
    routes = {
        "watch=true": watch_line + "\n",
        "/api/v1/events?limit=1": {"metadata": {"resourceVersion": "42"}},
        "/api/v1/nodes/node1": _node(),
        "/api/v1": {"kind": "APIResourceList"},
    }
    outputs = {}
    for script, sub in (
        ("kdiag-node", "status"),
        ("kdiag-events", "watch"),
        ("kdiag-services", "connectivity"),
    ):
        r = kdiag(script, sub, routes=routes)
        assert r.returncode == 0, (script, r.stderr)
        outputs[script] = r.stdout
    argv = kdiag.argv_log()
    assert "watch=true" in argv and "BackOff" in outputs["kdiag-events"]
    assert TOKEN not in argv
    assert f"Authorization: Bearer {TOKEN}" in kdiag.stdin_log()
    api_calls = [line for line in argv.splitlines() if "kubernetes.default.svc" in line]
    assert api_calls
    for call in api_calls:
        assert "--cacert" in call and " -k" not in f" {call}" and "-sk" not in call


# ── non-list answers (403 Status) must not abort the report ──

FORBIDDEN = {"kind": "Status", "status": "Failure", "reason": "Forbidden", "code": 403}


def test_status_object_does_not_abort_reports(kdiag):
    routes = {"/api/v1/pods": FORBIDDEN, "/api/v1/events": FORBIDDEN}
    for script, sub, last_section in (
        ("kdiag-pods", "all", "Container Images"),
        ("kdiag-events", "all", "All Recent Events"),
    ):
        r = kdiag(script, sub, routes=routes)
        assert r.returncode == 0, (script, r.stderr)
        assert last_section in r.stdout, script
