"""SMART health reporting and device discovery."""

import json

from app.collectors.storage import _REMOTE_TRANSPORTS, parse_smart, smart_candidates
from app.models.storage import DiskInfo


def _disk(name: str, transport: str | None = None) -> DiskInfo:
    return DiskInfo(name=name, size="1G", type="disk", transport=transport)


def test_iscsi_lun_claiming_passed_is_not_trusted(fixture_text):
    """Longhorn's iSCSI target answers `passed: true` without real SMART.

    It advertises smart_support.available but leaves enabled false, and
    smartctl exits 4. The old code took `passed` at face value and showed
    14 Longhorn volumes on talos-1-pve2 as healthy drives.
    """
    data = json.loads(fixture_text("smartctl-no-support.json"))
    assert data["smart_status"]["passed"] is True  # the drive does claim this
    assert data["smart_support"] == {"available": True, "enabled": False}

    smart = parse_smart(data, "/dev/sdc")
    assert smart.health_passed is None
    assert smart.smart_available is False


def test_qemu_disk_without_smart_is_not_a_pass(fixture_text):
    """QEMU virtual disks omit smart_status and report available: false."""
    data = json.loads(fixture_text("smartctl-qemu.json"))
    assert "smart_status" not in data
    assert data["smart_support"]["available"] is False

    smart = parse_smart(data, "/dev/sda")
    assert smart.health_passed is None
    assert smart.smart_available is False


def test_real_ata_drive_still_reports_passed(fixture_text):
    """The fix must not suppress genuine SMART results."""
    smart = parse_smart(json.loads(fixture_text("smartctl-ata.json")), "/dev/sda")

    assert smart.health_passed is True
    assert smart.smart_available is True
    assert smart.model == "PNY CS900 240GB SSD"
    assert smart.attributes


def test_explicit_failure_is_preserved():
    """A drive reporting failure must still surface as failed, not unknown."""
    smart = parse_smart({"smart_status": {"passed": False}}, "/dev/sda")
    assert smart.health_passed is False
    assert smart.smart_available is True


def test_smart_support_disabled_overrides_status():
    """SMART advertised but not enabled means the verdict is not real."""
    smart = parse_smart(
        {
            "smart_status": {"passed": True},
            "smart_support": {"available": True, "enabled": False},
        },
        "/dev/sdc",
    )
    assert smart.health_passed is None
    assert smart.smart_available is False


def test_nvme_without_smart_support_block_is_trusted(fixture_text):
    """smartctl omits smart_support for NVMe; don't treat that as disabled."""
    smart = parse_smart(
        {
            "smart_status": {"passed": True},
            "nvme_smart_health_information_log": {
                "percentage_used": 3,
                "temperature": 41,
                "power_on_hours": 9000,
            },
        },
        "/dev/nvme0",
    )
    assert smart.health_passed is True
    assert smart.smart_available is True
    assert smart.wear_leveling_percent == 97
    assert smart.temperature_celsius == 41


def test_iscsi_luns_are_not_smart_candidates(fixture_text):
    """Longhorn attaches one iSCSI LUN per volume; none are physical drives."""
    lsblk = json.loads(fixture_text("lsblk-longhorn.json"))
    disks = [
        _disk(d["name"], d.get("tran"))
        for d in lsblk["blockdevices"]
        if d.get("type") == "disk"
    ]
    candidates = smart_candidates(disks)

    assert candidates == ["/dev/sda", "/dev/sdb"]
    assert not any("sdc" in c for c in candidates)


def test_virtio_and_emmc_are_discovered():
    """The old /dev/sd? and /dev/nvme? globs missed these entirely."""
    disks = [_disk("vda"), _disk("mmcblk0"), _disk("nvme12n1")]
    assert smart_candidates(disks) == [
        "/dev/vda",
        "/dev/mmcblk0",
        "/dev/nvme12n1",
    ]


def test_transport_matching_is_case_insensitive():
    assert smart_candidates([_disk("sdc", "iSCSI")]) == []


def test_local_transports_are_kept():
    for transport in ("sata", "nvme", "usb", "ata", None):
        assert smart_candidates([_disk("sdx", transport)]) == ["/dev/sdx"]


def test_remote_transport_set_covers_longhorn():
    assert "iscsi" in _REMOTE_TRANSPORTS
