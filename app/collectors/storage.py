import json
import os

from app.collectors.base import run_command, ttl_cache
from app.config import HOST_PROC
from app.models.storage import (
    DiskInfo,
    DiskUsage,
    Partition,
    SmartAttribute,
    SmartHealth,
    StorageOverview,
)


@ttl_cache(seconds=300)
async def collect_storage() -> StorageOverview:
    disks = await collect_disks()
    smart = await collect_all_smart()
    usage = await collect_disk_usage()
    return StorageOverview(disks=disks, smart=smart, usage=usage)


@ttl_cache(seconds=300)
async def collect_disks() -> list[DiskInfo]:
    stdout, _, rc = await run_command(
        [
            "lsblk",
            "-J",
            "-o",
            "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,ROTA,TRAN",
        ]
    )
    if rc != 0:
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    disks: list[DiskInfo] = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        partitions = []
        for child in dev.get("children", []):
            partitions.append(
                Partition(
                    name=child.get("name", ""),
                    size=child.get("size", ""),
                    type=child.get("type", "part"),
                    mountpoint=child.get("mountpoint"),
                    fstype=child.get("fstype"),
                )
            )
        disks.append(
            DiskInfo(
                name=dev.get("name", ""),
                model=(dev.get("model") or "").strip() or None,
                serial=(dev.get("serial") or "").strip() or None,
                size=dev.get("size", ""),
                type="disk",
                transport=dev.get("tran"),
                rotational=dev.get("rota", False),
                partitions=partitions,
            )
        )
    return disks


async def collect_smart_for_device(device: str) -> SmartHealth | None:
    dev_path = device if device.startswith("/dev/") else f"/dev/{device}"
    if not os.path.exists(dev_path):
        return None

    stdout, _, rc = await run_command(
        ["smartctl", "-a", dev_path, "--json"], timeout=15
    )
    if not stdout.strip():
        return None

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None

    return parse_smart(data, dev_path)


def parse_smart(data: dict, dev_path: str) -> SmartHealth:
    """Build SmartHealth from `smartctl -a --json` output."""
    # Virtual and network-backed disks lie about SMART in two different ways,
    # and the old `.get("passed", True)` default turned both into a green
    # PASSED with no model, no serial and 0 C:
    #
    #   QEMU disks     smart_status: null, smart_support.available: false
    #   Longhorn LUNs  smart_status.passed: true, but support.enabled: false
    #
    # So a health verdict only counts when SMART is actually enabled on the
    # drive, not merely advertised.
    smart_status = data.get("smart_status")
    health_passed = None
    if isinstance(smart_status, dict) and isinstance(smart_status.get("passed"), bool):
        health_passed = smart_status["passed"]

    smart_support = data.get("smart_support")
    if isinstance(smart_support, dict):
        enabled = smart_support.get("enabled", smart_support.get("available"))
        if enabled is False:
            health_passed = None

    smart_available = health_passed is not None

    temp = None
    temp_data = data.get("temperature", {})
    if isinstance(temp_data, dict):
        temp = temp_data.get("current")

    power_on = None
    poh = data.get("power_on_time", {})
    if isinstance(poh, dict):
        power_on = poh.get("hours")

    # Parse attributes
    wear = None
    realloc = None
    attributes: list[SmartAttribute] = []

    # ATA attributes
    for attr in data.get("ata_smart_attributes", {}).get("table", []):
        sa = SmartAttribute(
            id=attr.get("id", 0),
            name=attr.get("name", ""),
            value=attr.get("value", 0),
            worst=attr.get("worst", 0),
            threshold=attr.get("thresh", 0),
            raw_value=str(attr.get("raw", {}).get("string", "")),
        )
        attributes.append(sa)
        if sa.id == 5:  # Reallocated sectors
            try:
                realloc = int(sa.raw_value.split()[0])
            except (ValueError, IndexError):
                pass
        elif sa.id == 177:  # Wear leveling (SSD)
            wear = sa.value

    # NVMe: percentage used
    nvme_health = data.get("nvme_smart_health_information_log", {})
    if isinstance(nvme_health, dict):
        pct_used = nvme_health.get("percentage_used")
        if pct_used is not None:
            wear = 100 - pct_used
        if temp is None:
            temp = nvme_health.get("temperature")
        if power_on is None:
            power_on = nvme_health.get("power_on_hours")

    return SmartHealth(
        device=dev_path,
        model=data.get("model_name")
        or data.get("model_family")
        or data.get("scsi_model_name"),
        serial=data.get("serial_number"),
        smart_available=smart_available,
        health_passed=health_passed,
        temperature_celsius=temp,
        power_on_hours=power_on,
        wear_leveling_percent=wear,
        reallocated_sectors=realloc,
        attributes=attributes,
    )


# Transports that are network- or hypervisor-attached rather than a physical
# drive in this machine. Longhorn presents one iSCSI LUN per volume, which
# swamped the storage view with entries that have no hardware behind them.
_REMOTE_TRANSPORTS = {"iscsi", "fc", "fcoe", "nbd", "rbd"}


def smart_candidates(disks: list[DiskInfo]) -> list[str]:
    """Device paths worth asking SMART about.

    Driven off lsblk rather than a /dev glob. The old patterns were
    "/dev/sd?" and "/dev/nvme?", which missed virtio disks (/dev/vd*), eMMC
    and SD cards (/dev/mmcblk*), and any node with more than ten NVMe
    namespaces, while happily including every attached iSCSI LUN.
    """
    return [
        f"/dev/{d.name}"
        for d in disks
        if (d.transport or "").lower() not in _REMOTE_TRANSPORTS
    ]


@ttl_cache(seconds=300)
async def collect_all_smart() -> list[SmartHealth]:
    results: list[SmartHealth] = []
    for dev in smart_candidates(await collect_disks()):
        smart = await collect_smart_for_device(dev)
        if smart:
            results.append(smart)
    return results


@ttl_cache(seconds=300)
async def collect_disk_usage() -> list[DiskUsage]:
    stdout, _, rc = await run_command(
        [
            "nsenter",
            f"--mount={HOST_PROC}/1/ns/mnt",
            "--",
            "df",
            "-hPT",
            "--exclude-type=tmpfs",
            "--exclude-type=devtmpfs",
            "--exclude-type=squashfs",
        ]
    )
    if rc != 0:
        # Fallback to container view
        stdout, _, rc = await run_command(
            [
                "df",
                "-hPT",
                "--exclude-type=tmpfs",
                "--exclude-type=devtmpfs",
                "--exclude-type=squashfs",
            ]
        )
        if rc != 0:
            return []

    usages: list[DiskUsage] = []
    overlay_seen = False
    for line in stdout.splitlines()[1:]:  # Skip header
        parts = line.split()
        if len(parts) < 7:
            continue
        fs, fs_type, size, used, avail, pct, mount = (
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            parts[5],
            parts[6],
        )
        if fs_type == "overlay":
            if overlay_seen:
                continue
            overlay_seen = True

        pct_num = int(pct.replace("%", "")) if pct.replace("%", "").isdigit() else 0
        severity = "ok"
        if pct_num >= 95:
            severity = "critical"
        elif pct_num >= 85:
            severity = "warning"

        usages.append(
            DiskUsage(
                filesystem=fs,
                mount=mount,
                fs_type=fs_type,
                size=size,
                used=used,
                available=avail,
                used_percent=pct_num,
                severity=severity,
            )
        )
    return usages
