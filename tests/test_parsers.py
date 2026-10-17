"""Baseline coverage for the text/JSON parsers behind the collectors.

Fixtures under tests/fixtures/ are real output captured from a Talos node,
so these tests pin the formats the parsers actually meet in production.

They drive the real collectors (run_command / the proc root are faked) so a
regression in the production parsing fails here, rather than re-implementing
the parsing inside the test.
"""

import json

from app.collectors import memory, processes, storage
from app.collectors.memory import _dimm_from_dict
from app.collectors.storage import parse_smart


def _fake_run_command(stdout: str, rc: int = 0):
    async def _run(cmd, timeout=None):
        return stdout, "", rc

    return _run


async def test_lsblk_fixture_parses_into_disks(monkeypatch, clear_caches, fixture_text):
    """collect_disks() keeps only type=disk devices, with their partitions."""
    monkeypatch.setattr(
        storage, "run_command", _fake_run_command(fixture_text("lsblk.json"))
    )
    disks = await storage.collect_disks()

    assert [d.name for d in disks] == ["sda", "sdb", "sdc"]
    sda = disks[0]
    assert sda.size
    assert len(sda.partitions) == 4
    assert all(p.name for p in sda.partitions)


async def test_lsblk_garbage_output_is_not_fatal(monkeypatch, clear_caches):
    monkeypatch.setattr(storage, "run_command", _fake_run_command("not json"))
    assert await storage.collect_disks() == []


def test_smartctl_ata_fixture_parses(fixture_text):
    """smartctl --json output turns into a passing SmartHealth with attributes."""
    health = parse_smart(json.loads(fixture_text("smartctl-ata.json")), "/dev/sda")
    assert health.device == "/dev/sda"
    assert health.smart_available is True
    assert health.health_passed is True
    assert health.attributes, "ATA fixture should carry a SMART attribute table"


async def test_dimm_parsing_from_dmidecode(monkeypatch, fixture_text):
    """_collect_dimms() turns dmidecode -t 17 blocks into DimmInfo in MB."""
    monkeypatch.setattr(
        memory, "run_command", _fake_run_command(fixture_text("dmidecode-t17.txt"))
    )
    dimms = await memory._collect_dimms()

    assert len(dimms) == 2
    for dimm in dimms:
        assert dimm.locator
        assert dimm.size_mb == 8192


async def test_dimm_parsing_skips_empty_slots_and_keeps_last_block(monkeypatch):
    """Empty slots are dropped; the trailing block (no next header) is kept."""
    out = (
        "Handle 0x0010, DMI type 17, 40 bytes\n"
        "Memory Device\n"
        "\tSize: No Module Installed\n"
        "\tLocator: DIMM_A1\n"
        "\n"
        "Handle 0x0011, DMI type 17, 40 bytes\n"
        "Memory Device\n"
        "\tSize: 16 GB\n"
        "\tLocator: DIMM_A2\n"
        "\n"
        "Handle 0x0012, DMI type 17, 40 bytes\n"
        "Memory Device\n"
        "\tSize: 512 MB\n"
        "\tLocator: DIMM_B1\n"
    )
    monkeypatch.setattr(memory, "run_command", _fake_run_command(out))
    dimms = await memory._collect_dimms()

    assert [(d.locator, d.size_mb) for d in dimms] == [
        ("DIMM_A2", 16384),
        ("DIMM_B1", 512),
    ]


async def test_dimm_parsing_dmidecode_failure(monkeypatch):
    monkeypatch.setattr(memory, "run_command", _fake_run_command("", rc=1))
    assert await memory._collect_dimms() == []


def test_dimm_size_units_normalise():
    """GB sizes convert to MB; MB sizes pass through."""
    assert _dimm_from_dict({"size": "8 GB", "locator": "DIMM0"}).size_mb == 8192
    assert _dimm_from_dict({"size": "512 MB", "locator": "DIMM1"}).size_mb == 512


def test_dimm_placeholder_serials_become_none():
    """dmidecode placeholders shouldn't surface as real serial numbers."""
    for placeholder in ("Unknown", "Not Specified", ""):
        dimm = _dimm_from_dict({"size": "8 GB", "serial number": placeholder})
        assert dimm.serial is None


def test_proc_stat_fixture_through_process_collector(
    monkeypatch, tmp_path, fixture_text
):
    """The real /proc/<pid>/stat fixture goes through _collect_processes_sync.

    The fixture's starttime (field 22) is 20 ticks and utime+stime is
    231992+208122. With uptime set 10 s after the start, the first-sighting
    CPU average only comes out right if utime, stime and
    starttime are read from the right fields.
    """
    clk = processes._CLK_TCK
    starttime, cpu_ticks = 20, 231992 + 208122
    uptime = starttime / clk + 10.0

    proc = tmp_path / "proc"
    pid_dir = proc / "1"
    pid_dir.mkdir(parents=True)
    (proc / "uptime").write_text(f"{uptime} 0.00\n")
    (proc / "meminfo").write_text("MemTotal:       16000000 kB\n")
    (pid_dir / "stat").write_text(fixture_text("proc-pid-stat.txt"))
    (pid_dir / "status").write_text(fixture_text("proc-pid-status.txt"))
    (pid_dir / "cmdline").write_text("")

    monkeypatch.setattr(processes, "HOST_PROC", str(proc))
    monkeypatch.setattr(processes, "HOST_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(processes, "_prev_samples", {})

    overview = processes._collect_processes_sync()

    assert overview.total_count == 1
    p = overview.processes[0]
    assert p.pid == 1
    assert p.ppid == 0
    assert p.state == "sleeping"
    assert p.command == "[init]"
    assert p.rss_kb == 298008
    assert p.vsz_kb == 1504688
    assert p.cpu_percent == round(cpu_ticks / clk / 10.0 * 100, 2)


def _fake_proc(tmp_path, monkeypatch, stats: dict[int, str], uptime: float):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text(f"{uptime} 0.00\n")
    (proc / "meminfo").write_text("MemTotal:       16000000 kB\n")
    for pid, stat in stats.items():
        pid_dir = proc / str(pid)
        pid_dir.mkdir()
        (pid_dir / "stat").write_text(stat)
        (pid_dir / "status").write_text("VmRSS:\t100 kB\nVmSize:\t200 kB\n")
        (pid_dir / "cmdline").write_text("")
    monkeypatch.setattr(processes, "HOST_PROC", str(proc))
    monkeypatch.setattr(processes, "HOST_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(processes, "_prev_samples", {})


def test_proc_stat_comm_with_spaces_and_parens(monkeypatch, tmp_path):
    """comm may hold spaces and ')'; every field after it keeps its index.

    All the fields around ppid/utime/stime/starttime carry distinct values,
    so reading any of them one slot off changes the result.
    """
    clk = processes._CLK_TCK
    # state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt
    # utime stime cutime cstime prio nice threads itreal starttime vsize rss
    stat = (
        "42 (my (odd) proc) R 7 8 9 0 -1 0 0 0 0 0 "
        f"300 200 11 12 20 0 1 0 {clk * 5} 5000 60\n"
    )
    _fake_proc(tmp_path, monkeypatch, {42: stat}, uptime=15.0)

    p = processes._collect_processes_sync().processes[0]
    assert p.pid == 42
    assert p.ppid == 7
    assert p.state == "running"
    assert p.command == "[my (odd) proc]"
    # Started at 5 s, uptime 15 s: 500 ticks over 10 s of life.
    assert p.cpu_percent == round(500 / clk / 10.0 * 100, 2)


def test_proc_stat_garbage_is_skipped(monkeypatch, tmp_path):
    """Truncated or malformed stat lines are dropped, not raised."""
    _fake_proc(
        tmp_path,
        monkeypatch,
        {
            1: "1 (init S 0 0",  # no closing paren
            2: "2 (kthreadd) S 0 0 0",  # too few fields
            3: "3 (x) S notanint 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
        },
        uptime=100.0,
    )
    assert processes._collect_processes_sync().processes == []
