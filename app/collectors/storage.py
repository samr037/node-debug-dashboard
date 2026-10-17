import asyncio
import json
import logging
import os
import re

from app.collectors.base import read_file, run_command, ttl_cache
from app.config import HOST_PROC, HOST_SYS
from app.models.storage import (
    DiskInfo,
    DiskUsage,
    Partition,
    SmartAttribute,
    SmartHealth,
    StorageOverview,
)

logger = logging.getLogger(__name__)


@ttl_cache(seconds=300)
async def collect_storage() -> StorageOverview:
    disks, smart, usage = await asyncio.gather(
        collect_disks(), collect_all_smart(), collect_disk_usage()
    )
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

    # -n standby: a spun-down HDD is reported as asleep rather than woken up
    # by every cache refresh while a dashboard tab is open.
    cmd = ["smartctl", "-a", "-n", "standby", dev_path, "--json"]
    # A smartctl stuck in uninterruptible I/O survives the timeout's SIGKILL.
    # Launching another on every cache expiry only piles them up on the same
    # dead drive, so report the stuck one instead.
    if await asyncio.to_thread(_still_running, cmd):
        return SmartHealth(
            device=dev_path,
            error="A previous smartctl on this device is still running (device not responding)",
        )
    stdout, stderr, rc = await run_command(cmd, timeout=15)
    # A drive that hangs smartctl is precisely the one worth seeing, so it
    # stays in the list as "not reported", with the reason, instead of
    # silently dropping out of SMART and of the warnings.
    if not stdout.strip():
        if _STANDBY_RE.search(stderr):
            return SmartHealth(device=dev_path, standby=True)
        reason = stderr.strip() or f"smartctl exited {rc} with no output"
        return SmartHealth(device=dev_path, error=reason)

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # An older smartctl may print the standby notice as plain text.
        if _STANDBY_RE.search(stdout):
            return SmartHealth(device=dev_path, standby=True)
        return SmartHealth(device=dev_path, error="smartctl output is not valid JSON")

    try:
        return parse_smart(data, dev_path, rc)
    except Exception as e:  # an odd USB bridge or firmware, not worth a 500
        logger.warning("Unparseable smartctl output for %s: %s", dev_path, e)
        return SmartHealth(device=dev_path, error=f"Unparseable smartctl output: {e}")


def _still_running(cmd: list[str], proc_root: str = "/proc") -> bool:
    """Whether a process with exactly this argv is alive (any state but zombie)."""
    want = "\0".join(cmd)
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(f"{proc_root}/{pid}/cmdline", "rb") as f:
                argv = f.read().decode(errors="replace").rstrip("\0")
            if argv != want:
                continue
            with open(f"{proc_root}/{pid}/stat") as f:
                state = f.read().rsplit(")", 1)[-1].split()[:1]
            if state != ["Z"]:
                return True
        except OSError:
            continue
    return False


# smartctl's exit status is a bitmask (smartctl(8), EXIT STATUS). Bits 0-2
# say the query itself went wrong, bit 3 is the failed health verdict, and
# bits 4-7 are findings that smart_status.passed does not reflect: a drive
# whose last self-test hit a read failure still says "passed".
_SMARTCTL_ISSUES = {
    4: "Prefail attribute at or below threshold",
    5: "Attribute was at or below threshold in the past",
    6: "Device error log contains errors",
    7: "Self-test log contains errors",
}

# Normalised "life left" attributes, by vendor, in order of preference:
# Samsung, Phison/Kingston/PNY, Intel, Crucial/Micron, WD/SanDisk.
_WEAR_ATTRS = (177, 231, 233, 202, 169)

_STANDBY_RE = re.compile(r"\bis in (STANDBY|SLEEP)\b", re.IGNORECASE)


def _int(value) -> int | None:
    """smartctl fields are ints in practice; anything else counts as unknown."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _raw_count(raw: str) -> int | None:
    try:
        return int(raw.split()[0])
    except (ValueError, IndexError):
        return None


def parse_smart(data: dict, dev_path: str, rc: int | None = None) -> SmartHealth:
    """Build SmartHealth from `smartctl -a --json` output."""
    data = _dict(data)
    meta = _dict(data.get("smartctl"))
    exit_status = _int(meta.get("exit_status"))
    if exit_status is None:
        exit_status = rc if rc is not None and rc >= 0 else 0

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
    if exit_status & (1 << 3):  # "SMART status check returned DISK FAILING"
        health_passed = False

    smart_support = data.get("smart_support")
    if isinstance(smart_support, dict):
        enabled = smart_support.get("enabled", smart_support.get("available"))
        if enabled is False:
            health_passed = None

    smart_available = health_passed is not None
    issues = [msg for bit, msg in _SMARTCTL_ISSUES.items() if exit_status & (1 << bit)]

    # -n standby makes smartctl stop with "Device is in STANDBY mode,
    # exit(2)". That drive is asleep, not broken: say so, but it is not a
    # collection failure (`error` is reserved for those).
    messages = [
        m
        for m in meta.get("messages", []) or []
        if isinstance(m, dict) and m.get("string")
    ]
    standby = any(_STANDBY_RE.search(str(m["string"])) for m in messages)

    # Bit 1: the device could not be opened or did not identify itself. A
    # dying drive often fails this way, fast, instead of hanging smartctl;
    # it must surface like a timeout does, not as a quiet "not reported".
    error = None
    if exit_status & (1 << 1) and not standby:
        errors = [
            str(m["string"]).strip() for m in messages if m.get("severity") == "error"
        ]
        error = (
            errors[0]
            if errors
            else f"smartctl could not open the device (exit {exit_status})"
        )

    temp = _int(_dict(data.get("temperature")).get("current"))
    power_on = _int(_dict(data.get("power_on_time")).get("hours"))

    # Parse attributes
    realloc = None
    pending = None
    uncorrectable = None
    wear_by_id: dict[int, int] = {}
    attributes: list[SmartAttribute] = []

    # ATA attributes
    table = _dict(data.get("ata_smart_attributes")).get("table", [])
    for attr in table if isinstance(table, list) else []:
        if not isinstance(attr, dict):
            continue
        sa = SmartAttribute(
            id=_int(attr.get("id")) or 0,
            name=str(attr.get("name", "")),
            value=_int(attr.get("value")) or 0,
            worst=_int(attr.get("worst")) or 0,
            threshold=_int(attr.get("thresh")) or 0,
            raw_value=str(_dict(attr.get("raw")).get("string", "")),
        )
        attributes.append(sa)
        if sa.id == 5:  # Reallocated sectors
            realloc = _raw_count(sa.raw_value)
        elif sa.id == 197:  # Current pending sectors: unreadable, not yet remapped
            pending = _raw_count(sa.raw_value)
        elif sa.id == 198:  # Offline uncorrectable sectors
            uncorrectable = _raw_count(sa.raw_value)
        elif sa.id in _WEAR_ATTRS:  # SSD life left, vendor-specific id
            wear_by_id[sa.id] = sa.value

    wear = next((wear_by_id[i] for i in _WEAR_ATTRS if i in wear_by_id), None)
    if wear is None:
        # Vendor-neutral fallback: ATA device statistics, page 7.
        for page in _dict(data.get("ata_device_statistics")).get("pages", []) or []:
            for entry in _dict(page).get("table", []) or []:
                entry = _dict(entry)
                used = _int(entry.get("value"))
                if used is not None and (
                    entry.get("name") == "Percentage Used Endurance Indicator"
                ):
                    wear = max(0, 100 - used)

    # SCSI/SAS: the grown defect list is that world's reallocated sectors.
    if realloc is None:
        realloc = _int(data.get("scsi_grown_defect_list"))

    # NVMe: percentage used
    nvme_health = data.get("nvme_smart_health_information_log", {})
    if isinstance(nvme_health, dict):
        pct_used = _int(nvme_health.get("percentage_used"))
        if pct_used is not None:
            # The spec lets this run past 100 (up to 255) once the rated
            # endurance is exceeded; that is 0% left, not -20%.
            wear = max(0, 100 - pct_used)
        if temp is None:
            temp = _int(nvme_health.get("temperature"))
        if power_on is None:
            power_on = _int(nvme_health.get("power_on_hours"))

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
        pending_sectors=pending,
        offline_uncorrectable=uncorrectable,
        smart_issues=issues,
        standby=standby,
        error=error,
        attributes=attributes,
    )


# Transports that are network- or hypervisor-attached rather than a physical
# drive in this machine. Longhorn presents one iSCSI LUN per volume, which
# swamped the storage view with entries that have no hardware behind them.
_REMOTE_TRANSPORTS = {"iscsi", "fc", "fcoe", "nbd", "rbd"}

# Kernel block devices backed by the network or by RAM (Ceph krbd, NBD,
# zram, DRBD, device-mapper). lsblk lists them as type "disk" with no TRAN
# at all, so the transport check above never sees them.
_VIRTUAL_PREFIXES = ("rbd", "nbd", "zram", "drbd", "dm-", "loop")


def _nvme_over_fabrics(name: str) -> bool:
    """NVMe/TCP, RDMA and FC namespaces (Longhorn v2) also report TRAN nvme."""
    if not name.startswith("nvme"):
        return False
    try:
        with open(f"{HOST_SYS}/block/{name}/device/transport") as f:
            return f.read().strip() not in ("", "pcie")
    except OSError:
        return False


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
        and not d.name.startswith(_VIRTUAL_PREFIXES)
        and not _nvme_over_fabrics(d.name)
    ]


@ttl_cache(seconds=300)
async def collect_all_smart() -> list[SmartHealth]:
    # smartctl can block for seconds on a sick drive, and the timeout is 15s
    # each. Queried in series, a node with several disks could spend longer
    # in this one collector than the whole request budget.
    devices = smart_candidates(await collect_disks())
    results = await asyncio.gather(
        *(collect_smart_for_device(d) for d in devices), return_exceptions=True
    )
    # One device blowing up must not hide the others, a failing one included.
    reports: list[SmartHealth] = []
    for dev, r in zip(devices, results):
        if isinstance(r, BaseException):
            logger.warning("SMART collection failed for %s: %r", dev, r)
            r = SmartHealth(device=dev, error=f"SMART collection failed: {r}")
        if r:
            reports.append(r)
    return reports


# Not storage anyone can fill, or full by design: pseudo filesystems, RAM
# backed ones, and read-only images (squashfs, iso9660, udf, erofs) that
# always report 100% and would raise a permanent critical warning.
_DF_SKIP_TYPES = {
    "tmpfs", "devtmpfs", "ramfs", "squashfs", "iso9660", "udf", "erofs",
    "proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "debugfs",
    "tracefs", "securityfs", "pstore", "bpf", "configfs", "fusectl",
    "hugetlbfs", "nsfs", "autofs", "rpc_pipefs", "binfmt_misc", "efivarfs",
    "selinuxfs",
}  # fmt: skip


def _unescape_mountinfo(field: str) -> str:
    # The kernel writes space, tab, newline and backslash as \ooo.
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text: str) -> list[tuple[str, str, str]]:
    """(source, fs type, mount point) per filesystem, the way df lists them."""
    by_dev: dict[str, tuple[str, str, str]] = {}
    overlay_seen = False
    for line in text.splitlines():
        pre, sep, post = line.partition(" - ")
        fields, tail = pre.split(), post.split()
        if not sep or len(fields) < 5 or len(tail) < 2:
            continue
        dev, mount = fields[2], _unescape_mountinfo(fields[4])
        fs_type, source = tail[0], _unescape_mountinfo(tail[1])
        if fs_type in _DF_SKIP_TYPES:
            continue
        # Nothing can fill a read-only mount, and some always read 100%:
        # Talos' root is a read-only overlay of its image layers.
        if "ro" in fields[5].split(","):
            continue
        if fs_type == "overlay":
            # Container root filesystems: they only mirror the filesystem
            # underneath. Keep "/" for the container's own view.
            if overlay_seen or mount != "/":
                continue
            overlay_seen = True
        # Bind mounts (kubelet volumes, Talos' /etc and /var binds) repeat
        # one filesystem many times; like df, keep the shortest mount point.
        prev = by_dev.get(dev)
        if prev is None or len(mount) < len(prev[2]):
            by_dev[dev] = (source, fs_type, mount)
    return list(by_dev.values())


@ttl_cache(seconds=300)
async def collect_disk_usage() -> list[DiskUsage]:
    # Host view: the container's own df, pointed at paths under /proc/1/root,
    # which resolve in the host's mount namespace. `nsenter ... -- df` cannot
    # work on Talos (no df on the host), and the /host bind mount is a
    # snapshot from pod start that misses everything mounted since.
    usages = await _df(
        f"{HOST_PROC}/1/root",
        parse_mountinfo(await read_file(f"{HOST_PROC}/1/mountinfo")),
    )
    if not usages:
        # Fallback to container view: no host /proc, or /proc/1/root denied
        # (privileged: false), where df fails on every host path.
        usages = await _df("", parse_mountinfo(await read_file("/proc/self/mountinfo")))
    return usages


async def _df(prefix: str, mounts: list[tuple[str, str, str]]) -> list[DiskUsage]:
    if not mounts:
        return []

    by_path = {prefix + m[2]: m for m in mounts}
    stdout, _, _ = await run_command(
        ["df", "-h", "--output=file,size,used,avail,pcent", "--", *by_path]
    )
    # No rc check: df exits 1 when one mount fails statfs (stale NFS, a dead
    # FUSE daemon) yet still prints every other line, which are all valid.

    usages: list[DiskUsage] = []
    for line in stdout.splitlines():
        # The path comes first and may contain spaces; the numbers do not.
        parts = line.strip().rsplit(None, 4)
        if len(parts) != 5 or parts[0] not in by_path:
            continue  # header, or a line we did not ask for
        path, size, used, avail, pct = parts
        if size == "0":
            continue  # no blocks at all: a pseudo filesystem df would hide
        fs, fs_type, mount = by_path[path]

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
