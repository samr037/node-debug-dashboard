"""Regression tests for the Helm chart, rendered with `helm template`.

Skipped when helm is not on PATH. Nothing here talks to a cluster.
"""

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parent.parent / "charts" / "node-debug-dashboard"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm not installed"
)


def render(*args: str, namespace: str = "ns", check: bool = True):
    proc = subprocess.run(
        ["helm", "template", "r", str(CHART), "-n", namespace, *args],
        capture_output=True,
        text=True,
    )
    if check:
        assert proc.returncode == 0, proc.stderr
    return proc


def docs(*args: str, **kw) -> list[dict]:
    return [d for d in yaml.safe_load_all(render(*args, **kw).stdout) if d]


def kind(objs: list[dict], k: str) -> list[dict]:
    return [o for o in objs if o["kind"] == k]


def container(objs: list[dict]) -> dict:
    return kind(objs, "DaemonSet")[0]["spec"]["template"]["spec"]["containers"][0]


def env(objs: list[dict]) -> dict:
    return {e["name"]: e for e in container(objs).get("env", [])}


# --- auth.existingSecret alone must turn auth on --------------------------------


def test_existing_secret_alone_wires_both_keys_required():
    e = env(docs("--set", "auth.existingSecret=dash-auth"))
    for name, key in (("AUTH_TOKEN", "token"), ("AUTH_PASSWORD", "password")):
        ref = e[name]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "dash-auth" and ref["key"] == key
        assert not ref.get("optional")  # a typo must fail closed
    assert "AUTH_USERNAME" in e


def test_existing_secret_switches_still_select_keys():
    e = env(docs("--set", "auth.existingSecret=dash-auth", "--set", "auth.password=on"))
    assert "AUTH_PASSWORD" in e and "AUTH_TOKEN" not in e


def test_existing_secret_blank_key_skipped():
    e = env(docs("--set", "auth.existingSecret=dash-auth", "--set", "auth.tokenKey="))
    assert "AUTH_PASSWORD" in e and "AUTH_TOKEN" not in e


def test_no_auth_renders_no_auth_env():
    assert not any(k.startswith("AUTH_") for k in env(docs()))


# --- credentials never inline in the pod spec ----------------------------------


def test_credentials_go_through_chart_secret():
    objs = docs(
        "--set",
        "auth.token=tok",
        "--set",
        "auth.password=pw",
        "--set",
        "ssh.enabled=true",
        "--set",
        "ssh.passwordAuth=true",
        "--set",
        "ssh.password=s3cret",
    )
    ds = yaml.safe_dump(kind(objs, "DaemonSet")[0])
    for secret in ("tok", "pw", "s3cret"):
        assert f"value: {secret}" not in ds
    e = env(objs)
    for name in ("AUTH_TOKEN", "AUTH_PASSWORD", "SSH_PASSWORD"):
        assert "secretKeyRef" in e[name]["valueFrom"]
    (secret,) = kind(objs, "Secret")
    assert set(secret["data"]) == {"auth-token", "auth-password", "ssh-password"}
    annotations = kind(objs, "DaemonSet")[0]["spec"]["template"]["metadata"][
        "annotations"
    ]
    assert "checksum/credentials" in annotations


def test_ssh_existing_secret():
    e = env(
        docs(
            "--set",
            "ssh.enabled=true",
            "--set",
            "ssh.passwordAuth=true",
            "--set",
            "ssh.existingSecret=ssh-pw",
        )
    )
    assert e["SSH_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": "ssh-pw",
        "key": "password",
    }


def test_no_secret_without_credentials():
    assert not kind(docs(), "Secret")


# --- 0 / false via --set must not disable a credential -------------------------


@pytest.mark.parametrize("value", ["0", "false"])
def test_falsy_typed_credentials_kept(value):
    objs = docs(
        "--set",
        f"auth.password={value}",
        "--set",
        "ssh.enabled=true",
        "--set",
        "ssh.passwordAuth=true",
        "--set",
        f"ssh.password={value}",
    )
    e = env(objs)
    assert "AUTH_PASSWORD" in e and "SSH_PASSWORD" in e
    (secret,) = kind(objs, "Secret")
    assert base64.b64decode(secret["data"]["auth-password"]).decode() == value


# --- RBAC ----------------------------------------------------------------------


def test_clusterrole_has_no_node_proxy_and_has_metrics():
    (role,) = kind(docs(), "ClusterRole")
    resources = {
        (g, r)
        for rule in role["rules"]
        for g in rule["apiGroups"]
        for r in rule["resources"]
    }
    assert not any(r.startswith("nodes/") for _, r in resources)
    assert ("", "namespaces") not in resources
    assert ("metrics.k8s.io", "nodes") in resources


def test_cluster_scoped_names_include_namespace():
    a = {o["kind"]: o["metadata"]["name"] for o in docs(namespace="a")}
    b = {o["kind"]: o["metadata"]["name"] for o in docs(namespace="b")}
    for k in ("ClusterRole", "ClusterRoleBinding"):
        assert a[k] != b[k]
    (binding,) = kind(docs(namespace="a"), "ClusterRoleBinding")
    assert binding["roleRef"]["name"] == a["ClusterRole"]


def test_rbac_refuses_default_service_account():
    proc = render("--set", "serviceAccount.create=false", check=False)
    assert proc.returncode != 0 and "serviceAccount.name" in proc.stderr
    render("--set", "serviceAccount.create=false", "--set", "serviceAccount.name=dbg")


# --- NetworkPolicy -------------------------------------------------------------


def test_networkpolicy_without_sources_fails():
    proc = render("--set", "networkPolicy.enabled=true", check=False)
    assert proc.returncode != 0 and "allowedCIDRs" in proc.stderr


def test_networkpolicy_namespace_rule_includes_ssh():
    (np,) = kind(
        docs(
            "--set",
            "networkPolicy.enabled=true",
            "--set",
            "networkPolicy.allowedNamespaces={ops}",
            "--set",
            "networkPolicy.allowedCIDRs={10.0.0.0/8}",
            "--set",
            "ssh.enabled=true",
        ),
        "NetworkPolicy",
    )
    for rule in np["spec"]["ingress"]:
        assert {p["port"] for p in rule["ports"]} == {80, 2022}


# --- probes and hostPort -------------------------------------------------------


def test_probes_on_health():
    c = container(docs())
    for probe in ("livenessProbe", "readinessProbe"):
        assert c[probe]["httpGet"] == {"path": "/api/health", "port": "http"}


def test_host_port_only_without_host_network():
    assert all(
        "hostPort" not in p
        for p in container(docs("--set", "ssh.enabled=true"))["ports"]
    )
    ports = container(docs("--set", "hostNetwork=false", "--set", "ssh.enabled=true"))[
        "ports"
    ]
    assert {p["name"]: p["hostPort"] for p in ports} == {"http": 80, "ssh": 2022}


# --- NOTES ---------------------------------------------------------------------


# helm 3 needs a cluster even for `install --dry-run=client`, so render
# NOTES.txt through a wrapper template in a copy of the chart instead.
_NOTES_WRAPPER = """apiVersion: v1
kind: ConfigMap
metadata:
  name: notes-under-test
data:
  notes: |
{{ include "node-debug-dashboard/templates/NOTES.txt" . | indent 4 }}
"""


def _notes(*args: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        chart = Path(tmp) / CHART.name
        shutil.copytree(CHART, chart)
        (chart / "templates" / "zz-notes.yaml").write_text(_NOTES_WRAPPER)
        proc = subprocess.run(
            ["helm", "template", "r", str(chart), "-n", "ns"]
            + ["--show-only", "templates/zz-notes.yaml", *args],
            capture_output=True,
            text=True,
        )
    assert proc.returncode == 0, proc.stderr
    return yaml.safe_load(proc.stdout)["data"]["notes"]


def test_notes_no_default_passwords_and_auth_state():
    out = _notes("--set", "ssh.enabled=true", "--set", "ssh.passwordAuth=true")
    assert "debug:debug" not in out and "root:root" not in out
    assert "no ssh.password" in out
    assert "authentication is OFF" in out
    assert "Authentication: on" in _notes("--set", "auth.existingSecret=x")


def test_notes_warns_networkpolicy_hostnetwork():
    out = _notes(
        "--set",
        "networkPolicy.enabled=true",
        "--set",
        "networkPolicy.allowedCIDRs={10.0.0.0/8}",
    )
    assert "no effect with hostNetwork" in out
