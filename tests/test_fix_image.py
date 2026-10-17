"""Regression tests for the image: Dockerfile, entrypoint.sh and the SSH shell files.

The shell pieces run for real where the interpreter is available (zsh for the
files sourced by the login shell, bash >= 4 for entrypoint.sh) and are skipped
otherwise. The Dockerfile checks are static: building the image needs a
Docker daemon and network access.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ZSH = shutil.which("zsh")


def _modern_bash():
    """A bash new enough for entrypoint.sh (${v,,}, wait -n), or None (macOS ships 3.2)."""
    for candidate in ("bash", "/opt/homebrew/bin/bash", "/usr/local/bin/bash"):
        path = shutil.which(candidate)
        if not path:
            continue
        out = subprocess.run(
            [path, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True
        )
        if out.stdout.strip().isdigit() and int(out.stdout.strip()) >= 5:
            return path
    return None


BASH = _modern_bash()


def _extract_function(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, re.M | re.S)
    assert match, f"{name}() not found"
    return match.group(0)


# --- ssh/zshrc ---------------------------------------------------------------


@pytest.mark.skipif(not ZSH, reason="zsh not installed")
def test_k8s_curl_keeps_path_and_hides_token(tmp_path):
    """`local path` in zsh clobbered $PATH, so kn/kp/kpn silently printed nothing."""
    func = _extract_function((ROOT / "ssh/zshrc").read_text(), "_k8s_curl")
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("s3cr3t-token")
    func = func.replace("/var/run/secrets/kubernetes.io/serviceaccount", str(sa))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "curl"
    stub.write_text('#!/bin/sh\necho "ARGV: $*"\necho "STDIN: $(cat)"\n')
    stub.chmod(0o755)
    (tmp_path / "fn.zsh").write_text(func)

    out = subprocess.run(
        [
            ZSH,
            "-f",
            "-c",
            f'PATH="{bindir}:$PATH"; source fn.zsh; _k8s_curl /api/v1/nodes',
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "command not found" not in out.stderr
    argv = next(line for line in out.stdout.splitlines() if line.startswith("ARGV:"))
    assert "https://kubernetes.default.svc/api/v1/nodes" in argv
    # The SA token must never be on a command line (visible in ps / /api/processes)
    assert "s3cr3t-token" not in argv
    assert "STDIN: Authorization: Bearer s3cr3t-token" in out.stdout


# --- ssh/motd.sh -------------------------------------------------------------


def _run_motd(tmp_path, env_extra=None):
    host = tmp_path / "host"
    script = (ROOT / "ssh/motd.sh").read_text()
    script = script.replace("/host-proc/", f"{tmp_path}/host-proc/")
    script = script.replace("/host/", f"{host}/")
    (tmp_path / "motd.zsh").write_text(script)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path)}
    env.update(env_extra or {})
    # Sourced like zshrc does, so zsh's NOMATCH applies
    return subprocess.run(
        [ZSH, "-f", "-c", "source ./motd.zsh"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


def _role(stdout: str) -> str:
    line = next(line for line in stdout.splitlines() if "Role" in line)
    return re.sub(r"\x1b\[[0-9;]*m", "", line).split()[-1]


@pytest.mark.skipif(not ZSH, reason="zsh not installed")
def test_motd_no_glob_error_on_worker(tmp_path):
    (tmp_path / "host/etc/kubernetes/manifests").mkdir(parents=True)
    out = _run_motd(tmp_path)
    assert "no matches found" not in out.stderr
    assert _role(out.stdout) == "worker"


@pytest.mark.skipif(not ZSH, reason="zsh not installed")
def test_motd_detects_talos_control_plane(tmp_path):
    # Talos: etcd is a machined service, no static-pod manifest, but /var/lib/etcd exists
    (tmp_path / "host/etc/kubernetes/manifests").mkdir(parents=True)
    (tmp_path / "host/var/lib/etcd").mkdir(parents=True)
    out = _run_motd(tmp_path)
    assert _role(out.stdout) == "control-plane"


@pytest.mark.skipif(not ZSH, reason="zsh not installed")
def test_motd_detects_kubeadm_control_plane(tmp_path):
    manifests = tmp_path / "host/etc/kubernetes/manifests"
    manifests.mkdir(parents=True)
    (manifests / "etcd.yaml").write_text("")
    out = _run_motd(tmp_path)
    assert _role(out.stdout) == "control-plane"


@pytest.mark.skipif(not ZSH, reason="zsh not installed")
def test_motd_dashboard_url_includes_non_default_port(tmp_path):
    out = _run_motd(tmp_path, {"HTTP_PORT": "8080", "KUBERNETES_NODE_NAME": "node-1"})
    assert ":8080/docs" in out.stdout
    assert "(node-1)" in out.stdout
    out = _run_motd(tmp_path, {"HTTP_PORT": "80"})
    assert ":80/" not in out.stdout


# --- entrypoint.sh -----------------------------------------------------------


def _bash_eval(snippet: str) -> str:
    text = (ROOT / "entrypoint.sh").read_text()
    funcs = _extract_function(text, "is_true") + _extract_function(text, "strip")
    out = subprocess.run([BASH, "-c", funcs + snippet], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.mark.skipif(not BASH, reason="bash >= 5 not installed")
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", "ON"])
def test_entrypoint_is_true_accepts(value):
    assert _bash_eval(f'is_true "{value}" && echo y || echo n').strip() == "y"


@pytest.mark.skipif(not BASH, reason="bash >= 5 not installed")
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "enabled"])
def test_entrypoint_is_true_rejects(value):
    assert _bash_eval(f'is_true "{value}" && echo y || echo n').strip() == "n"


@pytest.mark.skipif(not BASH, reason="bash >= 5 not installed")
def test_entrypoint_strip():
    assert _bash_eval("printf '[%s]' \"$(strip $'  pa ss\\n')\"") == "[pa ss]"


def test_entrypoint_uses_one_boolean_rule():
    """The banner checked SSH_ENABLED = "true" only, while startup accepted 1/yes too."""
    text = (ROOT / "entrypoint.sh").read_text()
    assert not re.search(r'"\$SSH_(ENABLED|PASSWORD_AUTH)" = ', text)
    assert 'is_true "$SSH_ENABLED"' in text
    assert 'is_true "$SSH_PASSWORD_AUTH"' in text


def test_entrypoint_supervises_sshd():
    text = (ROOT / "entrypoint.sh").read_text()
    assert "sshd -t" in text
    assert "SSHD_PID=$!" in text
    assert re.search(r'kill -0 "\$SSHD_PID"', text)
    assert re.search(r'wait -n "\$UVICORN_PID" "\$SSHD_PID"', text)


def test_entrypoint_exports_env_to_ssh_sessions():
    text = (ROOT / "entrypoint.sh").read_text()
    assert "/etc/environment" in text
    for var in ("KUBERNETES_NODE_NAME", "HTTP_PORT", "LANG", "LC_ALL"):
        assert var in text


# --- Dockerfile / build inputs ----------------------------------------------


def test_dockerfile_drops_build_time_host_keys():
    text = (ROOT / "Dockerfile").read_text()
    apt = text.index("openssh-server")
    rm = text.index("rm -f /etc/ssh/ssh_host_*")
    assert rm > apt
    # ...in the same RUN as the install, so no layer keeps them
    assert "\nRUN" not in text[apt:rm]


def test_dockerfile_setup_does_not_swallow_failures():
    text = (ROOT / "Dockerfile").read_text()
    # `a && b || true && c` hides failures of everything before the `|| true`
    assert not re.search(r"\|\|\s*true\s*&&", text)
    setup = text[text.index("# SSH & user setup") :]
    assert "set -eux" in setup


def test_dockerfile_verifies_crictl_and_pip_hashes():
    text = (ROOT / "Dockerfile").read_text()
    assert "sha256sum -c" in text
    assert re.search(r"CRICTL_SHA256_AMD64=[0-9a-f]{64}", text)
    assert re.search(r"CRICTL_SHA256_ARM64=[0-9a-f]{64}", text)
    assert "--require-hashes -r requirements.txt" in text


def test_dockerfile_sets_session_locale():
    text = (ROOT / "Dockerfile").read_text()
    assert "/etc/default/locale" in text
    assert "/^AcceptEnv/d" in text


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
def test_requirements_are_locked_with_hashes(name):
    reqs = []
    current = None
    for line in (ROOT / name).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            current = [line]
            reqs.append(current)
        else:
            current.append(line)
    assert reqs
    for req in reqs:
        assert "==" in req[0], req[0]
        assert any("--hash=sha256:" in part for part in req), req[0]


def test_dockerignore_matches_nested_paths():
    patterns = (ROOT / ".dockerignore").read_text().split()
    for pattern in ("**/__pycache__", "**/*.pyc", "**/.DS_Store"):
        assert pattern in patterns


def _pins(name: str) -> dict:
    pins = {}
    for line in (ROOT / name).read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9._-]+)==([^ ;\\]+)", line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def test_dev_lock_matches_runtime_lock():
    """CI installs requirements-dev.txt: its runtime pins must be the image's."""
    runtime, dev = _pins("requirements.txt"), _pins("requirements-dev.txt")
    assert runtime
    assert {name: dev.get(name) for name in runtime} == runtime


def test_entrypoint_logs_effective_password_auth():
    """The log said password=true even when auth was refused for lack of a password."""
    text = (ROOT / "entrypoint.sh").read_text()
    assert "(password=${SSH_PASSWORD_AUTH})" not in text
