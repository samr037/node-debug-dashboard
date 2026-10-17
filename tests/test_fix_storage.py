"""Regression tests for the storage collector audit fixes (2026-10-17)."""

import json

import pytest

from app.collectors import storage
from app.collectors.storage import (
    collect_all_smart,
    collect_disk_usage,
    collect_smart_for_device,
    parse_mountinfo,
    parse_smart,
    smart_candidates,
)
from app.models.storage import DiskInfo, SmartHealth


class _SmartHealthNext(SmartHealth):
    """SmartHealth with the fields the collector now fills in.

    The model lives outside this collector; until it grows these fields,
    pydantic drops them silently. Tests swap this in to check what the
    collector produces.
    """

    error: str | None = None
    standby: bool = False
    pending_sectors: int | None = None
    offline_uncorrectable: int | None = None
    smart_issues: list[str] = []


@pytest.fixture
def next_model(monkeypatch):
    monkeypatch.setattr(storage, "SmartHealth", _SmartHealthNext)


@pytest.fixture
def device_exists(monkeypatch):
    monkeypatch.setattr(storage.os.path, "exists", lambda p: True)


def _fake_run(result):
    calls = []

    async def run(cmd, timeout=None):
        calls.append(cmd)
        return result(cmd) if callable(result) else result

    run.calls = calls
    return run


def _disk(name: str, transport: str | None = None) -> DiskInfo:
    return DiskInfo(name=name, size="1G", type="disk", transport=transport)


# --- SMART: devices must not vanish ----------------------------------------


async def test_smartctl_timeout_keeps_device_as_not_reported(
    monkeypatch, device_exists, next_model
):
    monkeypatch.setattr(
        storage, "run_command", _fake_run(("", "Command timed out: smartctl", -1))
    )
    smart = await collect_smart_for_device("sdb")

    assert smart is not None
    assert smart.device == "/dev/sdb"
    assert smart.health_passed is None
    assert smart.smart_available is False
    assert "timed out" in smart.error


async def test_smartctl_garbage_output_keeps_device(
    monkeypatch, device_exists, next_model
):
    monkeypatch.setattr(storage, "run_command", _fake_run(("not json", "", 1)))
    smart = await collect_smart_for_device("/dev/sda")
    assert smart is not None and smart.error


async def test_smartctl_is_told_not_to_wake_sleeping_drives(
    monkeypatch, device_exists, next_model
):
    out = {
        "smartctl": {
            "exit_status": 2,
            "messages": [
                {
                    "string": "Device is in STANDBY mode, exit(2)",
                    "severity": "information",
                }
            ],
        }
    }
    run = _fake_run((json.dumps(out), "", 2))
    monkeypatch.setattr(storage, "run_command", run)
    smart = await collect_smart_for_device("/dev/sdd")

    cmd = run.calls[0]
    assert cmd[cmd.index("-n") + 1] == "standby"
    assert smart.standby is True
    assert smart.health_passed is None
    assert smart.error is None  # asleep is not a collection failure


async def test_one_malformed_device_does_not_hide_the_others(
    monkeypatch, clear_caches, device_exists, next_model
):
    async def disks():
        return [_disk("sda", "sata"), _disk("sdb", "sata")]

    outputs = {
        "/dev/sda": {
            "smart_status": {"passed": False},
            "smart_support": {"enabled": True},
        },
        "/dev/sdb": {"temperature": {"current": "n/a"}, "model_name": ["odd"]},
    }
    monkeypatch.setattr(storage, "collect_disks", disks)
    monkeypatch.setattr(
        storage,
        "run_command",
        _fake_run(lambda cmd: (json.dumps(outputs[cmd[-2]]), "", 0)),
    )
    reports = {r.device: r for r in await collect_all_smart()}

    assert reports["/dev/sda"].health_passed is False
    assert reports["/dev/sdb"].health_passed is None
    assert reports["/dev/sdb"].error


async def test_unexpected_exception_in_one_device_is_contained(
    monkeypatch, clear_caches, next_model
):
    async def disks():
        return [_disk("sda"), _disk("sdb")]

    async def per_device(dev):
        if dev == "/dev/sdb":
            raise PermissionError("EPERM")
        return _SmartHealthNext(device=dev, smart_available=True, health_passed=True)

    monkeypatch.setattr(storage, "collect_disks", disks)
    monkeypatch.setattr(storage, "collect_smart_for_device", per_device)
    reports = {r.device: r for r in await collect_all_smart()}

    assert reports["/dev/sda"].health_passed is True
    assert "EPERM" in reports["/dev/sdb"].error


def test_odd_shapes_do_not_raise():
    smart = parse_smart(
        {
            "temperature": {"current": "n/a"},
            "power_on_time": "x",
            "ata_smart_attributes": {"table": [{"id": 5, "raw": 7}, "junk"]},
        },
        "/dev/sda",
    )
    assert smart.temperature_celsius is None
    assert smart.reallocated_sectors is None


# --- SMART: exit-status bitmask ---------------------------------------------


def test_self_test_failure_bit_surfaces_despite_passed(next_model):
    data = {
        "smartctl": {"exit_status": 128 | 64},
        "smart_status": {"passed": True},
        "smart_support": {"available": True, "enabled": True},
    }
    smart = parse_smart(data, "/dev/sda")
    assert smart.health_passed is True
    assert "Self-test log contains errors" in smart.smart_issues
    assert "Device error log contains errors" in smart.smart_issues


def test_disk_failing_bit_marks_health_failed():
    smart = parse_smart({"smartctl": {"exit_status": 8}}, "/dev/sda")
    assert smart.health_passed is False


def test_rc_is_used_when_json_has_no_exit_status(next_model):
    smart = parse_smart({"smart_status": {"passed": True}}, "/dev/sda", rc=32)
    assert smart.smart_issues == ["Attribute was at or below threshold in the past"]


def test_query_failure_bits_are_not_issues(fixture_text, next_model):
    """Longhorn LUNs exit 4 (a SMART command failed); that is no finding."""
    smart = parse_smart(
        json.loads(fixture_text("smartctl-no-support.json")), "/dev/sdc"
    )
    assert smart.smart_issues == []
    assert smart.health_passed is None


# --- SMART: pending / uncorrectable sectors ---------------------------------


def _attr(id_, value=100, raw="0"):
    return {"id": id_, "name": f"a{id_}", "value": value, "worst": value,
            "thresh": 0, "raw": {"string": raw}}  # fmt: skip


def test_pending_and_uncorrectable_sectors_are_extracted(next_model):
    data = {
        "smart_status": {"passed": True},
        "ata_smart_attributes": {
            "table": [_attr(5, raw="0"), _attr(197, raw="48"), _attr(198, raw="3")]
        },
    }
    smart = parse_smart(data, "/dev/sdb")
    assert smart.reallocated_sectors == 0
    assert smart.pending_sectors == 48
    assert smart.offline_uncorrectable == 3


def test_scsi_grown_defects_count_as_reallocated():
    smart = parse_smart({"scsi_grown_defect_list": 12}, "/dev/sdx")
    assert smart.reallocated_sectors == 12


# --- SMART: wear ------------------------------------------------------------


def test_pny_fixture_wear_from_ssd_life_left(fixture_text):
    smart = parse_smart(json.loads(fixture_text("smartctl-ata.json")), "/dev/sda")
    assert smart.wear_leveling_percent == 100  # attribute 231 SSD_Life_Left


@pytest.mark.parametrize("attr_id", [177, 231, 233, 202, 169])
def test_vendor_wear_attributes(attr_id):
    data = {"ata_smart_attributes": {"table": [_attr(attr_id, value=7)]}}
    assert parse_smart(data, "/dev/sda").wear_leveling_percent == 7


def test_wear_from_device_statistics():
    data = {
        "ata_device_statistics": {
            "pages": [
                {
                    "number": 7,
                    "table": [
                        {"name": "Percentage Used Endurance Indicator", "value": 85}
                    ],
                }
            ]
        }
    }
    assert parse_smart(data, "/dev/sda").wear_leveling_percent == 15


def test_nvme_percentage_used_past_100_clamps_to_zero():
    data = {"nvme_smart_health_information_log": {"percentage_used": 120}}
    assert parse_smart(data, "/dev/nvme0").wear_leveling_percent == 0


# --- SMART candidates -------------------------------------------------------


def test_rbd_nbd_zram_dm_are_not_smart_candidates():
    disks = [_disk(n) for n in ("rbd0", "nbd0", "zram0", "drbd1", "dm-0", "sda")]
    assert smart_candidates(disks) == ["/dev/sda"]


def test_nvme_over_fabrics_is_not_a_candidate(monkeypatch, tmp_path):
    for name, transport in (("nvme0n1", "pcie"), ("nvme1n1", "tcp")):
        d = tmp_path / "block" / name / "device"
        d.mkdir(parents=True)
        (d / "transport").write_text(transport + "\n")
    monkeypatch.setattr(storage, "HOST_SYS", str(tmp_path))

    disks = [
        _disk("nvme0n1", "nvme"),
        _disk("nvme1n1", "nvme"),
        _disk("nvme2n1", "nvme"),
    ]
    assert smart_candidates(disks) == ["/dev/nvme0n1", "/dev/nvme2n1"]


# --- Disk usage -------------------------------------------------------------

MOUNTINFO = "\n".join(
    [
        "20 1 0:20 / / ro,relatime - squashfs /dev/loop0 ro",
        "21 20 0:21 / /proc rw - proc proc rw",
        "22 20 8:1 / /var rw,relatime - xfs /dev/sda1 rw",
        "23 22 8:1 /etc/cni /etc/cni rw,relatime - xfs /dev/sda1 rw",
        "24 22 8:17 / /var/mnt/my\\040data rw - ext4 /dev/sdb1 rw",
        "25 22 0:40 / /var/mnt/nfs rw - nfs4 srv:/export rw",
        "26 20 11:0 / /media/cidata ro - iso9660 /dev/sr0 ro",
        "27 22 0:50 / /run/containerd/a/rootfs rw - overlay overlay rw",
        "28 22 0:51 / /run/containerd/b/rootfs rw - overlay overlay rw",
        "29 20 0:30 / /sys/fs/cgroup rw - cgroup2 cgroup2 rw",
    ]
)

P = "/host-proc/1/root"


def test_parse_mountinfo_filters_and_dedups():
    mounts = parse_mountinfo(MOUNTINFO)
    assert mounts == [
        ("/dev/sda1", "xfs", "/var"),
        ("/dev/sdb1", "ext4", "/var/mnt/my data"),
        ("srv:/export", "nfs4", "/var/mnt/nfs"),
        ("overlay", "overlay", "/run/containerd/a/rootfs"),
    ]


@pytest.fixture
def host_mounts(monkeypatch):
    async def read(path):
        return MOUNTINFO if path == "/host-proc/1/mountinfo" else ""

    monkeypatch.setattr(storage, "HOST_PROC", "/host-proc")
    monkeypatch.setattr(storage, "read_file", read)


async def test_df_runs_in_container_on_host_paths_and_survives_rc1(
    monkeypatch, clear_caches, host_mounts
):
    """One stale NFS mount makes df exit 1; the other lines still count."""
    stdout = "\n".join(
        [
            "File                                Size  Used Avail Use%",
            f"{P}/var                            100G   97G    3G  97%",
            f"{P}/var/mnt/my data                100G   50G   50G  50%",
            f"{P}/run/containerd/a/rootfs        100G   97G    3G  97%",
        ]
    )
    run = _fake_run((stdout, "df: /host-proc/1/root/var/mnt/nfs: Stale file handle", 1))
    monkeypatch.setattr(storage, "run_command", run)

    usages = {u.mount: u for u in await collect_disk_usage()}

    cmd = run.calls[0]
    assert cmd[0] == "df" and "nsenter" not in cmd
    assert f"{P}/var/mnt/my data" in cmd
    assert set(usages) == {"/var", "/var/mnt/my data", "/run/containerd/a/rootfs"}
    assert usages["/var"].severity == "critical"
    assert usages["/var"].filesystem == "/dev/sda1"
    assert usages["/var/mnt/my data"].used_percent == 50
    assert usages["/var/mnt/my data"].fs_type == "ext4"
    assert not any(u.fs_type == "iso9660" for u in usages.values())


async def test_df_falls_back_to_container_mount_table(monkeypatch, clear_caches):
    async def read(path):
        if path == "/proc/self/mountinfo":
            return "30 1 0:60 / / rw - overlay overlay rw"
        return ""

    monkeypatch.setattr(storage, "read_file", read)
    monkeypatch.setattr(
        storage,
        "run_command",
        _fake_run(("File Size Used Avail Use%\n/  20G 18G 2G 90%\n", "", 0)),
    )
    usages = await collect_disk_usage()
    assert [(u.mount, u.severity) for u in usages] == [("/", "warning")]


async def test_df_zero_size_rows_are_dropped(monkeypatch, clear_caches, host_mounts):
    stdout = f"File Size Used Avail Use%\n{P}/var 0 0 0 -\n"
    monkeypatch.setattr(storage, "run_command", _fake_run((stdout, "", 0)))
    assert await collect_disk_usage() == []


# --- Review follow-ups --------------------------------------------------------


async def test_plain_text_standby_notice_is_not_an_error(
    monkeypatch, device_exists, next_model
):
    """A smartctl that prints the notice as text must not raise a false alarm."""
    run = _fake_run(("Device is in STANDBY mode, exit(2)\n", "", 2))
    monkeypatch.setattr(storage, "run_command", run)
    smart = await collect_smart_for_device("/dev/sdd")
    assert smart.standby is True
    assert smart.error is None


def test_device_open_failure_is_an_error_not_a_quiet_gap(next_model):
    data = {
        "smartctl": {
            "exit_status": 2,
            "messages": [
                {
                    "string": "Read Device Identity failed: Input/output error",
                    "severity": "error",
                }
            ],
        }
    }
    smart = parse_smart(data, "/dev/sdb")
    assert smart.health_passed is None
    assert smart.error == "Read Device Identity failed: Input/output error"


def test_command_line_bit_alone_is_not_an_error(next_model):
    """virtio/eMMC disks: 'Unable to detect device type', exit 1. No alarm."""
    assert parse_smart({"smartctl": {"exit_status": 1}}, "/dev/vda").error is None


async def test_df_denied_on_host_paths_falls_back_to_container_view(
    monkeypatch, clear_caches
):
    async def read(path):
        if path == "/host-proc/1/mountinfo":
            return MOUNTINFO
        return "30 1 0:60 / / rw - overlay overlay rw"

    def df(cmd):
        if any(a.startswith(P) for a in cmd):
            return "File Size Used Avail Use%\n", "df: Permission denied", 1
        return "File Size Used Avail Use%\n/  20G 10G 10G 50%\n", "", 0

    monkeypatch.setattr(storage, "HOST_PROC", "/host-proc")
    monkeypatch.setattr(storage, "read_file", read)
    monkeypatch.setattr(storage, "run_command", _fake_run(df))
    usages = await collect_disk_usage()
    assert [(u.mount, u.used_percent) for u in usages] == [("/", 50)]
