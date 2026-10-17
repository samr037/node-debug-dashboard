"""Regressions for the collectors-misc audit fixes (2026-10-17)."""

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.collectors import base, containers, network, processes, talos
from app.collectors.base import run_command, ttl_cache
from app.collectors.dmesg import parse_line, scan

# ── run_command ──


async def _wait_dead(pid: int, deadline: float = 3.0) -> bool:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


async def _read_pid(path, deadline: float = 3.0) -> int:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        await asyncio.sleep(0.02)
    raise AssertionError("child never wrote its pid")


async def test_timeout_kills_grandchildren(tmp_path):
    """A forked grandchild used to survive and keep the pipes open."""
    pidfile = tmp_path / "pid"
    out, err, rc = await run_command(
        ["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], timeout=0.5
    )
    assert rc == -1 and "timed out" in err
    assert await _wait_dead(await _read_pid(pidfile))


async def test_cancellation_kills_the_child(tmp_path):
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(
        run_command(["sh", "-c", f"echo $$ > {pidfile}; exec sleep 30"], timeout=10)
    )
    pid = await _read_pid(pidfile)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _wait_dead(pid)


async def test_missing_command_is_reported_not_raised():
    _, err, rc = await run_command(["/nonexistent/definitely-not-here"])
    assert rc == -1 and "not found" in err


# ── ttl_cache ──


async def test_queued_waiters_share_a_failure():
    """N callers of a failing collector must not run it N times in series."""
    calls = 0

    @ttl_cache(seconds=60)
    async def collect():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        raise RuntimeError("boom")

    results = await asyncio.gather(
        *(collect() for _ in range(10)), return_exceptions=True
    )
    assert calls == 1
    assert all(isinstance(r, RuntimeError) for r in results)

    # A later caller is not served the stale failure.
    with pytest.raises(RuntimeError):
        await collect()
    assert calls == 2


async def test_degraded_results_are_retried_soon():
    calls = 0

    @ttl_cache(seconds=600, cache_if=lambda r: r is not None, retry_after=0)
    async def collect():
        nonlocal calls
        calls += 1
        return None if calls == 1 else "ok"

    assert await collect() is None
    assert await collect() == "ok"  # not pinned for 600s
    assert await collect() == "ok"
    assert calls == 2


# ── talos ──


def _fake_sys(tmp_path):
    """sysfs with nvme0n1 (Talos partitions) and sda (a data disk)."""
    sys_root = tmp_path / "sys"
    devices = sys_root / "devices/pci0000:00"
    block = sys_root / "class/block"
    block.mkdir(parents=True)
    for disk, parts in (("nvme0n1", ["EFI", "META", "STATE"]), ("sda", ["data"])):
        (devices / "block" / disk).mkdir(parents=True)
        (devices / "block" / disk / "uevent").write_text(
            f"DEVNAME={disk}\nDEVTYPE=disk\n"
        )
        os.symlink(devices / "block" / disk, block / disk)
        for i, label in enumerate(parts, 1):
            pname = f"{disk}p{i}" if disk.startswith("nvme") else f"{disk}{i}"
            pdir = devices / "block" / disk / pname
            pdir.mkdir()
            (pdir / "uevent").write_text(
                f"DEVNAME={pname}\nPARTN={i}\nPARTNAME={label}\n"
            )
            os.symlink(pdir, block / pname)
    net = sys_root / "class/net"
    for name in (
        "lo",
        "lowpan0",
        "eth0",
        "lxc1234abcd",
        "cilium_host",
        "cilium_vxlan",
        "veth1",
        "tunl0",
    ):
        (net / name).mkdir(parents=True)
    return sys_root


def test_install_disk_from_talos_partition_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(talos, "HOST_SYS", str(_fake_sys(tmp_path)))
    assert talos._install_disk_from_sysfs() == "/dev/nvme0n1"


def test_cmdline_root_is_exact_key():
    assert talos._root_from_cmdline("console=ttyS0 nfsroot=10.0.0.1:/srv") == ""
    assert talos._root_from_cmdline("nfsroot=x root=/dev/sda2 ro") == "/dev/sda2"
    assert talos._root_from_cmdline("talos.platform=metal") == ""


@pytest.fixture
def talos_env(tmp_path, monkeypatch, clear_caches):
    from app.collectors import kubernetes

    monkeypatch.setattr(talos, "HOST_SYS", str(_fake_sys(tmp_path)))
    monkeypatch.setattr(talos, "HOST_PROC", str(tmp_path / "proc"))
    monkeypatch.setenv("KUBERNETES_NODE_NAME", "node-1")
    commands: list[list[str]] = []

    async def fake_run(cmd, timeout=10):
        commands.append(cmd)
        if cmd[:3] == ["ip", "-j", "addr"]:
            return (
                '[{"ifname":"eth0","addr_info":[{"local":"10.0.0.5","prefixlen":24}]},'
                '{"ifname":"lxc1234abcd","addr_info":[]},{"ifname":"weird"}]',
                "",
                0,
            )
        return "", "", 1

    node = {"value": None}

    async def fake_get(path, params=None):
        return node["value"]

    monkeypatch.setattr(talos, "run_command", fake_run)
    monkeypatch.setattr(kubernetes, "_k8s_get", fake_get)
    return commands, node


async def test_interfaces_skip_cni_devices_with_one_ip_call(talos_env):
    commands, node = talos_env
    node["value"] = {"metadata": {"labels": {}}}
    mc = await talos.collect_talos_machine_config()
    names = [i.name for i in mc.network_interfaces]
    assert names == ["eth0", "lowpan0"]
    assert mc.network_interfaces[0].addresses == ["10.0.0.5/24"]
    assert sum(1 for c in commands if c[0] == "ip") == 1
    assert mc.install_disk == "/dev/nvme0n1"


async def test_api_failure_is_reported_and_not_pinned(talos_env, monkeypatch):
    _, node = talos_env
    mc = await talos.collect_talos_machine_config()
    assert mc.config_available is False
    assert mc.machine_type == ""
    assert mc.error

    # The API comes back; a degraded result must not stick for 300s.
    node["value"] = {
        "metadata": {"labels": {"node-role.kubernetes.io/control-plane": ""}}
    }
    real_monotonic = time.monotonic
    monkeypatch.setattr(base.time, "monotonic", lambda: real_monotonic() + 30)
    mc = await talos.collect_talos_machine_config()
    assert mc.config_available is True
    assert mc.machine_type == "controlplane"


async def test_talos_certs_scan_system_secrets(tmp_path, monkeypatch, clear_caches):
    root = tmp_path / "host"
    for rel in (
        "system/secrets/kubernetes/kube-apiserver/apiserver.crt",
        "system/secrets/kubernetes/kube-apiserver/apiserver.key",
        "system/secrets/kubernetes/kube-scheduler/ca.crt",
        "var/lib/kubelet/pki/kubelet-client-2026.pem",
        # A rotated-out client cert the kubelet left behind.
        "var/lib/kubelet/pki/kubelet-client-2025.pem",
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    os.symlink(
        root / "var/lib/kubelet/pki/kubelet-client-2026.pem",
        root / "var/lib/kubelet/pki/kubelet-client-current.pem",
    )
    expiry = (datetime.now(timezone.utc) + timedelta(days=10)).strftime(
        "%b %d %H:%M:%S %Y GMT"
    )

    async def fake_run(cmd, timeout=10):
        return f"subject=CN=x\nnotAfter={expiry}\n", "", 0

    monkeypatch.setattr(talos, "HOST_ROOT", str(root))
    monkeypatch.setattr(talos, "run_command", fake_run)
    certs = await talos.collect_talos_certificates()
    names = sorted(c.name for c in certs)
    assert names == [
        "kube-apiserver/apiserver.crt",
        "kube-scheduler/ca.crt",
        "kubelet-client-current.pem",
    ]
    assert all(c.expiry_severity == "critical" for c in certs)


# ── dmesg ──


def test_pcie_aer_corrected_is_not_memory():
    line = "pcieport 0000:00:1c.5: AER: Corrected error received: 0000:03:00.0"
    (w,) = scan([line], cutoff=None)
    assert w.source == "pcie" and w.severity == "warning"


def test_pcie_aer_newer_kernel_wording():
    """Linux 6.x says "Correctable"/"Uncorrectable ... message received"."""
    (w,) = scan(
        [
            "pcieport 0000:00:1c.5: AER: Correctable error message received from 0000:03:00.0"
        ],
        cutoff=None,
    )
    assert w.source == "pcie" and w.severity == "warning"
    (w,) = scan(
        [
            "nvme 0000:03:00.0: PCIe Bus Error: severity=Correctable, type=Physical Layer"
        ],
        cutoff=None,
    )
    assert w.source == "pcie" and w.severity == "warning"
    (w,) = scan(
        [
            "pcieport 0000:00:1c.5: AER: Uncorrectable (Non-Fatal) error message"
            " received from 0000:03:00.0"
        ],
        cutoff=None,
    )
    assert w.source == "pcie" and w.severity == "critical"


def test_pcie_aer_uncorrected_is_critical():
    line = "pcieport 0000:00:1c.5: AER: Uncorrected (Non-Fatal) error received: 0000:03:00.0"
    (w,) = scan([line], cutoff=None)
    assert w.source == "pcie" and w.severity == "critical"


def test_corrected_mce_is_a_warning_not_critical():
    for line in (
        "mce: [Hardware Error]: Machine check events logged",
        "mce: [Hardware Error]: CPU 1: Machine Check: 0 Bank 7: cc00008000010090",
    ):
        (w,) = scan([line], cutoff=None)
        assert w.severity == "warning", line


def test_fatal_mce_is_critical():
    for line in (
        "mce: [Hardware Error]: CPU 0: Machine Check Exception: 5 Bank 4: b200000000070f0f",
        "Kernel panic - not syncing: Fatal machine check",
        "mce: [Hardware Error]: Machine check: Processor context corrupt",
    ):
        (w,) = scan([line], cutoff=None)
        assert w.severity == "critical", line


def test_mce_capability_banner_still_ignored():
    assert scan(["mce: CPU supports 7 MCE banks"], cutoff=None) == []


def test_virtual_cdrom_and_loop_io_errors_ignored():
    lines = [
        "I/O error, dev sr0, sector 0 op 0x0:(READ) flags 0x0",
        "Buffer I/O error on dev sr0, logical block 0, async page read",
        "blk_update_request: I/O error, dev loop3, sector 0",
        "I/O error, dev fd0, sector 0",
    ]
    assert scan(lines, cutoff=None) == []
    (w,) = scan(["I/O error, dev sda, sector 1"], cutoff=None)
    assert w.severity == "critical"


def test_whole_second_iso_stamp_ages_out():
    stamp, msg = parse_line("2026-09-17T06:24:36+00:00 I/O error, dev sda", None)
    assert stamp == datetime(2026, 9, 17, 6, 24, 36, tzinfo=timezone.utc)
    assert msg == "I/O error, dev sda"
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    line = "2026-09-17T06:24:36+00:00 blk_update_request: I/O error, dev sda"
    assert scan([line], cutoff=now - timedelta(hours=24)) == []


# ── processes ──


def _fake_proc(tmp_path, n_mem: int):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("1000.00 500.00\n")
    (proc / "meminfo").write_text("MemTotal:       1000000 kB\n")
    clk = processes._CLK_TCK

    def add(pid, comm, rss_kb, utime, cmdline):
        d = proc / str(pid)
        d.mkdir()
        fields = f"S 1 1 1 0 -1 0 0 0 0 0 {utime} 0 0 0 20 0 1 0 0"
        (d / "stat").write_text(f"{pid} ({comm}) {fields}\n")
        (d / "status").write_text(
            f"Name:\t{comm}\nUid:\t0\t0\t0\t0\nVmRSS:\t{rss_kb} kB\n"
        )
        (d / "cmdline").write_text(cmdline.replace(" ", "\x00"))

    for i in range(n_mem):
        add(1000 + i, "app", 1000 + i, 0, "app --serve")
    # A kernel thread at ~90% CPU with no RSS at all.
    add(7, "ksoftirqd/3", 0, int(900 * clk), "")
    add(
        8,
        "curl",
        10,
        0,
        "curl -H Authorization: Bearer abc.def --token=s3cr3t https://x",
    )
    return proc


def test_cpu_hog_with_no_rss_survives_the_cut(tmp_path, monkeypatch):
    proc = _fake_proc(tmp_path, n_mem=250)
    monkeypatch.setattr(processes, "HOST_PROC", str(proc))
    monkeypatch.setattr(processes, "HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(processes, "_prev_samples", {})
    overview = processes._collect_processes_sync()
    assert overview.total_count == 252
    hog = [p for p in overview.processes if p.pid == 7]
    assert hog and hog[0].cpu_percent == 90.0
    assert hog[0].command == "[ksoftirqd/3]"


def test_process_command_lines_are_redacted(tmp_path, monkeypatch):
    proc = _fake_proc(tmp_path, n_mem=0)
    monkeypatch.setattr(processes, "HOST_PROC", str(proc))
    monkeypatch.setattr(processes, "HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(processes, "_prev_samples", {})
    (curl,) = [p for p in processes._collect_processes_sync().processes if p.pid == 8]
    assert "abc.def" not in curl.command and "s3cr3t" not in curl.command
    assert "Authorization: Bearer ***" in curl.command
    assert "--token=***" in curl.command


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "kubelet --token abc --token-file /etc/t",
            "kubelet --token *** --token-file /etc/t",
        ),
        ("app --password=hunter2", "app --password=***"),
        (
            "psql postgres://u:pw@db/x?password=y",
            "psql postgres://u:***@db/x?password=***",
        ),
        ("talosctl --nodes 10.0.0.1", "talosctl --nodes 10.0.0.1"),
        # No scheme: the value itself is masked, not the next argument.
        (
            "curl -H Authorization: sk-abc123 -X POST",
            "curl -H Authorization: *** -X POST",
        ),
        (
            "curl https://h/cb?access_token=abc&x=1 client_secret=zz",
            "curl https://h/cb?access_token=***&x=1 client_secret=***",
        ),
    ],
)
def test_redact_command(raw, expected):
    assert processes._redact_command(raw) == expected


# ── containers ──


async def test_exited_containers_have_no_uptime(monkeypatch, clear_caches):
    created = int((time.time() - 3 * 86400) * 1e9)
    ctrs = [
        {
            "id": "a",
            "metadata": {"name": "web"},
            "podSandboxId": "p",
            "state": "CONTAINER_RUNNING",
            "createdAt": str(created),
        },
        {
            "id": "b",
            "metadata": {"name": "job"},
            "podSandboxId": "p",
            "state": "CONTAINER_EXITED",
            "createdAt": str(created),
        },
    ]

    async def fake_containers():
        return ctrs

    async def fake_pods():
        return {"p": {"name": "pod", "namespace": "default"}}

    async def fake_stats():
        return {}

    monkeypatch.setattr(containers, "_get_containers", fake_containers)
    monkeypatch.setattr(containers, "_get_pods", fake_pods)
    monkeypatch.setattr(containers, "_get_stats", fake_stats)
    by_name = {
        c.container_name: c for c in await containers.collect_workload_containers()
    }
    assert by_name["web"].uptime.startswith("3d")
    assert by_name["job"].uptime == ""


# ── network ──


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("default via 192.168.1.1 dev eth0 proto dhcp metric 100\n", "192.168.1.1"),
        ("default dev wg0 scope link\n", "dev wg0"),
        (
            "default proto static metric 1024 \n"
            "\tnexthop via 10.0.0.1 dev eth0 weight 1 \n"
            "\tnexthop via 10.0.1.1 dev eth1 weight 1 \n",
            "10.0.0.1, 10.0.1.1",
        ),
        ("", ""),
    ],
)
def test_default_gateway_parse(route, expected):
    assert network._parse_default_gateway(route) == expected


def test_bare_nameserver_line_is_skipped():
    resolv = "nameserver\nnameserver 10.0.0.2\n# nameserver 9.9.9.9\nsearch x\n"
    assert network._parse_nameservers(resolv) == ["10.0.0.2"]


async def test_dig_queries_the_listed_server(monkeypatch):
    seen = []

    async def fake_run(cmd, timeout=10):
        seen.append(cmd)
        return "142.250.0.1\n", "", 0

    monkeypatch.setattr(network, "run_command", fake_run)
    assert await network._dig("@10.0.0.2") == "142.250.0.1"
    assert "@10.0.0.2" in seen[0]
