"""Regression tests for the ndiag-* shell scripts.

Each test runs a script under bash with the system tools it calls replaced by
small stubs on PATH (and fake /proc, cgroup and diskstats files), so nothing
touches real disks, memory or the network.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


@pytest.fixture
def env(tmp_path):
    """Fake bin dir + call log; returns a helper to add stubs and run scripts."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.log"
    calls.touch()

    class Env:
        def __init__(self):
            self.path = tmp_path
            self.calls = calls

        def stub(self, name, body):
            f = bindir / name
            f.write_text(f'#!/bin/bash\necho "{name} $*" >> "{calls}"\n' + body + "\n")
            f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        def run(self, script, *args, extra_env=None, stdin=subprocess.DEVNULL):
            e = {
                "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": str(tmp_path),
                "LC_ALL": "C",
            }
            e.update(extra_env or {})
            return subprocess.run(
                [BASH, str(SCRIPTS / script), *args],
                env=e,
                stdin=stdin,
                capture_output=True,
                text=True,
                timeout=60,
            )

        def log(self):
            return calls.read_text()

    return Env()


# ── ndiag-memtest ──────────────────────────────────────────────────────────


@pytest.fixture
def mem_env(env):
    """32 GB available on the host, pod cgroup limited to 1Gi with 350MiB used."""
    proc = env.path / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text("MemTotal: 67108864 kB\nMemAvailable: 33554432 kB\n")
    cg = env.path / "cgroup"
    cg.mkdir()
    (cg / "memory.max").write_text(f"{1024 * 1024 * 1024}\n")
    (cg / "memory.current").write_text(f"{350 * 1024 * 1024}\n")
    env.memenv = {"HOST_PROC": str(proc), "CGROUP_DIR": str(cg)}
    env.stub("memtester", 'echo "  Stuck Address       : ok"; exit 0')
    env.stub("stress-ng", "exit 0")
    return env


SAFE_MB = (1024 - 350) - 128  # cgroup headroom minus 128MB margin


def test_memtest_full_sized_from_cgroup_not_host(mem_env):
    r = mem_env.run("ndiag-memtest", "--yes", "full", extra_env=mem_env.memenv)
    assert r.returncode == 0, r.stderr
    assert f"memtester {SAFE_MB}M 1" in mem_env.log()


def test_memtest_full_refuses_without_confirmation(mem_env):
    r = mem_env.run("ndiag-memtest", "full", extra_env=mem_env.memenv)
    assert r.returncode != 0
    assert "memtester" not in mem_env.log()


def test_memtest_quick_refuses_size_above_pod_limit(mem_env):
    r = mem_env.run("ndiag-memtest", "quick", "1024", extra_env=mem_env.memenv)
    assert r.returncode != 0
    assert "Refusing" in r.stderr
    assert "memtester" not in mem_env.log()


def test_memtest_stress_default_shrinks_to_fit(mem_env):
    r = mem_env.run("ndiag-memtest", "-y", "stress", extra_env=mem_env.memenv)
    assert r.returncode == 0, r.stderr
    assert f"--vm-bytes {SAFE_MB}M" in mem_env.log()


def test_memtest_unlimited_cgroup_uses_host_headroom(mem_env):
    cg = Path(mem_env.memenv["CGROUP_DIR"])
    (cg / "memory.max").write_text("max\n")
    r = mem_env.run("ndiag-memtest", "--yes", "full", extra_env=mem_env.memenv)
    assert r.returncode == 0, r.stderr
    assert f"memtester {32768 - 1024}M 1" in mem_env.log()


def test_memtest_oom_killed_is_not_reported_as_passed(mem_env):
    mem_env.stub("memtester", 'echo "  Stuck Address       : ok"; exit 137')
    r = mem_env.run("ndiag-memtest", "quick", extra_env=mem_env.memenv)
    assert r.returncode != 0
    assert "All tests passed" not in r.stdout
    assert "did NOT complete" in r.stdout


def test_memtest_failures_reported(mem_env):
    mem_env.stub("memtester", 'echo "  Random Value        : FAILURE"; exit 4')
    r = mem_env.run("ndiag-memtest", "quick", extra_env=mem_env.memenv)
    assert "MEMORY ERRORS DETECTED: 1" in r.stdout


# ── ndiag-fsck ─────────────────────────────────────────────────────────────


def _block_device():
    for cand in ("/dev/disk0", "/dev/sda", "/dev/vda", "/dev/nvme0n1", "/dev/loop0"):
        try:
            if stat.S_ISBLK(os.stat(cand).st_mode):
                return cand
        except OSError:
            pass
    return None


@pytest.fixture
def fsck_env(env):
    proc = env.path / "proc"
    (proc / "1").mkdir(parents=True)
    (proc / "1" / "mountinfo").write_text("22 1 0:21 / / rw - overlay overlay rw\n")
    env.fsckenv = {"HOST_PROC": str(proc)}
    # blkid: TYPE per device; lsblk knows nothing (no udev on Talos)
    env.stub(
        "blkid",
        """
if [ "$1" = "-o" ] && [ "$2" = "device" ]; then
    printf '/dev/sda\\n/dev/sda1\\n/dev/mapper/vg-lv\\n/dev/sdb1\\n'; exit 0
fi
dev="${@: -1}"
case "$dev" in
    /dev/sda) exit 2 ;;
    /dev/sda1) echo vfat ;;
    /dev/mapper/vg-lv) echo ext4 ;;
    *) echo ext4 ;;
esac""",
    )
    env.stub(
        "lsblk",
        """
case "$*" in
    *MAJ:MIN*) echo " 8:17" ;;
    *) echo "" ;;
esac""",
    )
    env.stub("e2fsck", 'echo "clean"')
    env.stub("tune2fs", 'echo "Filesystem state:         clean"')
    env.stub("python3", "exit 0")  # O_EXCL open succeeds
    env.stub("readlink", 'echo "${@: -1}"')
    return env


def test_fsck_check_uses_blkid_fstype(fsck_env):
    r = fsck_env.run("ndiag-fsck", "check", "/dev/sdb1", extra_env=fsck_env.fsckenv)
    assert r.returncode == 0, r.stderr
    assert "e2fsck -n -f /dev/sdb1" in fsck_env.log()
    assert "No fsck available" not in r.stdout


def test_fsck_status_regex_and_mapper_path(fsck_env):
    r = fsck_env.run("ndiag-fsck", "status", extra_env=fsck_env.fsckenv)
    assert r.returncode == 0, r.stderr
    assert "gpt" not in r.stdout
    assert "msdos" not in r.stdout
    # tune2fs on the real /dev/mapper path, not /dev/vg-lv
    assert "tune2fs -l /dev/mapper/vg-lv" in fsck_env.log()
    assert "/dev/vg-lv" not in fsck_env.log().replace("/dev/mapper/vg-lv", "")


@pytest.mark.skipif(_block_device() is None, reason="no block device to point at")
def test_fsck_repair_refuses_when_mounted_on_host(fsck_env):
    dev = _block_device()
    mi = Path(fsck_env.fsckenv["HOST_PROC"]) / "1" / "mountinfo"
    mi.write_text(mi.read_text() + "80 22 8:17 / /var/mnt/pvc rw - ext4 /dev/xyz rw\n")
    r = fsck_env.run("ndiag-fsck", "--yes", "repair", dev, extra_env=fsck_env.fsckenv)
    assert r.returncode != 0
    assert "mounted at /var/mnt/pvc" in r.stdout
    assert "e2fsck" not in fsck_env.log()


@pytest.mark.skipif(_block_device() is None, reason="no block device to point at")
def test_fsck_repair_matches_btrfs_by_source(fsck_env):
    dev = _block_device()
    mi = Path(fsck_env.fsckenv["HOST_PROC"]) / "1" / "mountinfo"
    mi.write_text(mi.read_text() + f"81 22 0:55 / /var/lib/data rw - btrfs {dev} rw\n")
    r = fsck_env.run("ndiag-fsck", "--yes", "repair", dev, extra_env=fsck_env.fsckenv)
    assert r.returncode != 0
    assert "mounted at /var/lib/data" in r.stdout


@pytest.mark.skipif(_block_device() is None, reason="no block device to point at")
def test_fsck_repair_needs_confirmation(fsck_env):
    dev = _block_device()
    r = fsck_env.run("ndiag-fsck", "repair", dev, extra_env=fsck_env.fsckenv)
    assert r.returncode != 0
    assert "e2fsck" not in fsck_env.log()
    r = fsck_env.run("ndiag-fsck", "--yes", "repair", dev, extra_env=fsck_env.fsckenv)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"e2fsck -y -f {dev}" in fsck_env.log()


@pytest.mark.skipif(_block_device() is None, reason="no block device to point at")
def test_fsck_repair_refuses_busy_device(fsck_env):
    fsck_env.stub("python3", "exit 1")  # O_EXCL open -> EBUSY
    r = fsck_env.run(
        "ndiag-fsck", "--yes", "repair", _block_device(), extra_env=fsck_env.fsckenv
    )
    assert r.returncode != 0
    assert "busy" in r.stdout
    assert "e2fsck" not in fsck_env.log()


def test_fsck_smart_test_health_bits_do_not_reissue(env):
    env.stub(
        "smartctl",
        """
case "$*" in
    -t*) echo "Testing has begun."; echo "Test will complete after Fri Oct 17 12:00:00 2026"; exit 64 ;;
    *) echo "SMART Self-test log"; exit 0 ;;
esac""",
    )
    r = env.run("ndiag-fsck", "smart-test", "/dev/sda")
    assert r.returncode == 0, r.stderr
    assert "Test started" in r.stdout
    assert "ETA: Fri Oct 17 12:00:00 2026" in r.stdout
    assert "-d sat" not in env.log()


def test_fsck_smart_test_failure_not_reported_as_started(env):
    env.stub(
        "smartctl",
        """
case "$*" in
    -t*) echo "Unknown USB bridge"; exit 1 ;;
    *) exit 0 ;;
esac""",
    )
    r = env.run("ndiag-fsck", "smart-test", "/dev/sdz")
    assert r.returncode != 0
    assert "Test started" not in r.stdout
    assert "NOT started" in r.stdout
    assert "-d sat" in env.log()  # open failure (bit 1) still retries as SAT


# ── ndiag-disk ─────────────────────────────────────────────────────────────


def test_disk_io_columns(env):
    proc = env.path / "proc"
    proc.mkdir()
    # major minor name reads rmerged rsect rms writes wmerged wsect ...
    (proc / "diskstats").write_text("8 0 sda 100 0 2000 0 500 0 8000 0 0 0 0\n")
    (proc / "diskstats2").write_text("8 0 sda 100 0 2000 0 5500 0 48000 0 0 0 0\n")
    env.stub("lsblk", 'echo "sda disk"')
    env.stub("sleep", f'cp "{proc}/diskstats2" "{proc}/diskstats"')
    env.stub("iostat", "exit 1")
    r = env.run("ndiag-disk", "io", extra_env={"HOST_PROC": str(proc)})
    assert r.returncode == 0, r.stderr
    row = next(line for line in r.stdout.splitlines() if line.strip().startswith("sda"))
    # reads/s, writes/s, read KB/s, write KB/s
    assert row.split() == ["sda", "0", "5000", "0", "20000"]


def _source_funcs(names):
    text = (SCRIPTS / "ndiag-disk").read_text()
    lines = text.splitlines()
    head = [line for line in lines if line.startswith("C_")]
    out = []
    for n in names:
        start = next(i for i, line in enumerate(lines) if line.startswith(f"{n}()"))
        end = next(i for i in range(start, len(lines)) if lines[i] == "}")
        out += lines[start : end + 1]
    return "\n".join(head + out)


def _run_bash(env, code):
    return subprocess.run(
        [BASH, "-c", "set -euo pipefail\n" + code],
        env={"PATH": f"{env.path / 'bin'}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_disk_smart_attr_shows_zero_for_inverted(env):
    code = (
        _source_funcs(["_smart_attr"])
        + """
_smart_attr "Available Spare" "0" "%" "20" "10" "1"
_smart_attr "Reallocated Sectors" "0" "" "0" "10"
"""
    )
    r = _run_bash(env, code)
    assert r.returncode == 0, r.stderr
    assert "Available Spare" in r.stdout
    assert "\033[31m" in r.stdout  # red
    assert "Reallocated" not in r.stdout  # zero counters still hidden


def test_disk_lifetime_uses_normalized_value(env):
    env.stub(
        "smartctl",
        """cat <<'EOF'
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
202 Percent_Lifetime_Remain 0x0030   097   097   001    Old_age   Offline      -       3
EOF""",
    )
    code = (
        _source_funcs(["_smart_attr", "_parse_ata_smart"])
        + '\n_parse_ata_smart /dev/sda ""\n'
    )
    r = _run_bash(env, code)
    assert r.returncode == 0, r.stderr
    line = next(ln for ln in r.stdout.splitlines() if "Lifetime Remaining" in ln)
    assert "97%" in line
    assert "\033[31m" not in line


# ── ndiag-mem ──────────────────────────────────────────────────────────────


@pytest.fixture
def proc_mem(env):
    proc = env.path / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text(
        "MemTotal: 1000000 kB\nMemFree: 100000 kB\nMemAvailable: 550000 kB\n"
        "SwapTotal: 100 kB\nSwapFree: 50 kB\n"
    )
    (proc / "swaps").write_text(
        "Filename Type Size Used Priority\n/swap file 100 50 -2\n"
    )
    return proc


def test_mem_oom_counts_cgroup_kills(env, proc_mem):
    env.stub(
        "dmesg",
        """cat <<'EOF'
[100.0] java invoked oom-killer: gfp_mask=0xcc0(GFP_KERNEL), order=0, oom_score_adj=0
[100.1] oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=x,mems_allowed=0,oom_memcg=/kubepods/pod1
[100.2] Memory cgroup out of memory: Killed process 1234 (java) total-vm:100kB
EOF""",
    )
    r = env.run("ndiag-mem", "oom", extra_env={"HOST_PROC": str(proc_mem)})
    assert r.returncode == 0, r.stderr
    assert "Found 1 OOM events" in r.stdout
    assert "integer expression" not in r.stderr


def test_mem_oom_none(env, proc_mem):
    env.stub("dmesg", 'echo "[1.0] nothing to see"')
    r = env.run("ndiag-mem", "oom", extra_env={"HOST_PROC": str(proc_mem)})
    assert r.returncode == 0, r.stderr
    assert "No OOM kills detected" in r.stdout
    assert r.stderr == ""


def test_mem_usage_gauge_single_percent(env, proc_mem):
    r = env.run("ndiag-mem", "usage", extra_env={"HOST_PROC": str(proc_mem)})
    assert r.returncode == 0, r.stderr
    assert "%%" not in r.stdout
    assert "45%" in r.stdout


def test_mem_swap_scans_all_pids_and_survives_zero_last(env, proc_mem):
    for pid in range(1, 150):
        d = proc_mem / str(pid)
        d.mkdir()
        (d / "status").write_text(f"Name:\tproc{pid}\nVmSwap:\t0 kB\n")
    # a heavy swapper well past the first 100 entries (sorted as text)
    (proc_mem / "99" / "status").write_text("Name:\tbigswap\nVmSwap:\t51200 kB\n")
    # last listed PID has no VmSwap line at all (kernel thread)
    (proc_mem / "999").mkdir()
    (proc_mem / "999" / "status").write_text("Name:\tkthreadd\n")
    r = env.run("ndiag-mem", "swap", extra_env={"HOST_PROC": str(proc_mem)})
    assert r.returncode == 0, r.stderr
    assert "bigswap" in r.stdout


# ── SIGPIPE under pipefail (ndiag-cpu / ndiag-mem / ndiag-net) ─────────────

BIG = 'yes "root 1 0.0 0.0 1 1 ? S 00:00 0:00 /usr/bin/kube-apiserver --some-very-long-flag" | head -200000'


def test_cpu_raw_top_large_ps_output(env):
    env.stub("ps", BIG)
    r = env.run("ndiag-cpu", "--raw", "top")
    assert r.returncode == 0, r.stderr


def test_cpu_top_large_ps_output(env):
    env.stub("ps", BIG)
    r = env.run("ndiag-cpu", "top", extra_env={"HOST_PROC": str(env.path)})
    assert r.returncode == 0, r.stderr


def test_mem_top_large_ps_output(env):
    env.stub("ps", BIG)
    r = env.run("ndiag-mem", "top")
    assert r.returncode == 0, r.stderr


def test_net_conns_large_ss_output(env):
    env.stub(
        "ss",
        """
case "$*" in
    -tnp) yes "ESTAB 0 0 10.0.0.1:443 10.0.0.2:5555 users:((\\"x\\",pid=1,fd=3))" | head -300000 ;;
    *) echo header ;;
esac""",
    )
    r = env.run("ndiag-net", "conns")
    assert r.returncode == 0, r.stderr


# ── ndiag-net ──────────────────────────────────────────────────────────────


def test_net_listen_keeps_udp_and_drops_headers(env):
    env.stub(
        "ss",
        """cat <<'EOF'
tcp LISTEN 0 4096 169.254.20.10:53 0.0.0.0:* users:(("node-cache",pid=10,fd=3))
udp UNCONN 0 0 169.254.20.10:53 0.0.0.0:* users:(("node-cache",pid=10,fd=4))
tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))
EOF""",
    )
    r = env.run("ndiag-net", "listen")
    assert r.returncode == 0, r.stderr
    assert "-tulnpH" in env.log()
    rows = [ln.split() for ln in r.stdout.splitlines() if "169.254.20.10" in ln]
    assert sorted(row[0] for row in rows) == ["TCP", "UDP"]
    assert "State" not in r.stdout
    assert "Local" not in r.stdout


def test_net_dns_uses_search_and_reverse(env):
    env.stub("date", "echo 1000000000")
    env.stub("dig", "echo 1.2.3.4")
    r = env.run("ndiag-net", "dns")
    assert r.returncode == 0, r.stderr
    log = env.log()
    assert "dig +short +search kubernetes.default.svc" in log
    assert "dig +short -x 1.1.1.1" in log
    assert "✗" not in r.stdout


# ── ndiag-part ─────────────────────────────────────────────────────────────


def test_part_table_continues_past_unpartitioned_disk(env):
    env.stub(
        "lsblk",
        """
case "$*" in
    "-dno NAME,TYPE,SIZE") printf 'sdb disk 100G\\nsdc disk 50G\\n' ;;
    "-dno MODEL /dev/sdb") echo "WDC RAW" ;;
    "-dno MODEL /dev/sdc") echo "Samsung SSD" ;;
    *) echo "100G" ;;
esac""",
    )
    env.stub("blkid", "exit 2")
    env.stub(
        "fdisk",
        """
echo "Disk $2: 100 GiB"
[ "$2" = "/dev/sdc" ] && echo "/dev/sdc1 2048 1000 998 50G Linux"
exit 0""",
    )
    r = env.run("ndiag-part", "table")
    assert r.returncode == 0, r.stderr
    assert "(no partitions)" in r.stdout
    assert "/dev/sdc1" in r.stdout


def test_part_fs_columns_with_empty_fields(env):
    env.stub(
        "lsblk",
        """cat <<'EOF'
NAME="sda" SIZE="100G" TYPE="disk" FSTYPE="" LABEL="" MOUNTPOINT=""
NAME="sda2" SIZE="20G" TYPE="part" FSTYPE="ext4" LABEL="" MOUNTPOINT="/var"
NAME="sda3" SIZE="5G" TYPE="part" FSTYPE="xfs" LABEL="my data" MOUNTPOINT=""
EOF""",
    )
    env.stub("blkid", "exit 0")
    r = env.run("ndiag-part", "fs")
    assert r.returncode == 0, r.stderr
    line = next(ln for ln in r.stdout.splitlines() if "sda2" in ln)
    # MOUNT is the last column, LABEL (20 wide) left blank before it
    assert line.rstrip().endswith("/var\033[0m")
    assert "ext4" in line
    line3 = next(ln for ln in r.stdout.splitlines() if "sda3" in ln)
    assert "my data" in line3


# ── ndiag-test ─────────────────────────────────────────────────────────────


def test_ndiag_test_alias_checks_can_fail(env):
    # Every diagnostic script "passes"; crictl is missing entirely
    for name in (
        "ndiag-cpu",
        "ndiag-mem",
        "ndiag-net",
        "ndiag-disk",
        "ndiag-part",
        "ndiag-fsck",
        "ndiag-memtest",
        "ndiag-efi",
        "kdiag-node",
        "kdiag-pods",
        "kdiag-etcd",
        "kdiag-certs",
        "kdiag-services",
        "kdiag-events",
        "ss",
        "ip",
        "lsblk",
    ):
        env.stub(name, "exit 0")
    r = env.run("ndiag-test")
    assert "crictl ps (exit:127)" in r.stdout
    assert r.returncode != 0


def test_net_ifaces_returns_0_when_last_iface_has_no_speed(env):
    # cmd_ifaces reads /host/sys/class/net directly: point it at a fake tree
    net = env.path / "net"
    for iface in ("eth0", "wg0"):  # wg0 sorts last and has no link speed
        st = net / iface / "statistics"
        st.mkdir(parents=True)
        for c in ("rx_errors", "rx_dropped", "tx_errors", "tx_dropped"):
            (st / c).write_text("0\n")
    env.stub(
        "ethtool",
        '[ "$1" = eth0 ] && printf "\\tSpeed: 1000Mb/s\\n\\tDuplex: Full\\n"; exit 0',
    )
    env.stub("ip", "exit 0")
    src = (SCRIPTS / "ndiag-net").read_text().replace("/host/sys/class/net", str(net))
    lib = src[: src.index("# Parse --raw")] + "cmd_ifaces\necho REACHED\n"
    script = env.path / "ifaces.sh"
    script.write_text(lib)
    r = subprocess.run(
        [BASH, str(script)],
        env={"PATH": f"{env.path / 'bin'}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "REACHED" in r.stdout
    assert "1000Mb/s" in r.stdout


# ── Review follow-ups ──────────────────────────────────────────────────────


def test_memtest_host_cgroupns_resolves_own_cgroup(mem_env):
    """Host cgroup namespace: root has no memory.max, our path comes from
    /proc/self/cgroup. Without it the 1Gi limit would be ignored again."""
    cg = Path(mem_env.memenv["CGROUP_DIR"])
    sub = cg / "kubepods" / "pod1" / "ctr"
    sub.mkdir(parents=True)
    (cg / "memory.max").rename(sub / "memory.max")
    (cg / "memory.current").rename(sub / "memory.current")
    selfcg = mem_env.path / "self_cgroup"
    selfcg.write_text("0::/kubepods/pod1/ctr\n")
    r = mem_env.run(
        "ndiag-memtest",
        "--yes",
        "full",
        extra_env={**mem_env.memenv, "SELF_CGROUP": str(selfcg)},
    )
    assert r.returncode == 0, r.stderr
    assert f"memtester {SAFE_MB}M 1" in mem_env.log()


def test_memtest_stress_refuses_when_no_headroom(mem_env):
    cg = Path(mem_env.memenv["CGROUP_DIR"])
    (cg / "memory.current").write_text(f"{1000 * 1024 * 1024}\n")
    r = mem_env.run("ndiag-memtest", "--yes", "stress", extra_env=mem_env.memenv)
    assert r.returncode != 0
    assert "Refusing" in r.stderr
    assert "stress-ng" not in mem_env.log()


def test_fsck_usage_lists_partitions_from_blkid(fsck_env):
    r = fsck_env.run("ndiag-fsck", "check", extra_env=fsck_env.fsckenv)
    assert r.returncode == 0, r.stderr
    assert "/dev/mapper/vg-lv" in r.stdout
    assert "ext4" in r.stdout
