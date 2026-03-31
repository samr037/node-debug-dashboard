import glob
import re

from app.collectors.base import read_file, run_command, ttl_cache
from app.models.hardware import DimmInfo, MemoryInfo


@ttl_cache()
async def collect_memory() -> MemoryInfo:
    # /proc/meminfo
    meminfo = await read_file("/proc/meminfo")
    total_kb = available_kb = 0
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            total_kb = int(re.search(r"\d+", line).group())  # type: ignore[union-attr]
        elif line.startswith("MemAvailable:"):
            available_kb = int(re.search(r"\d+", line).group())  # type: ignore[union-attr]

    total_gb = round(total_kb / 1048576, 1)
    available_gb = round(available_kb / 1048576, 1)
    used_percent = round((1 - available_kb / total_kb) * 100, 1) if total_kb else 0

    # DIMMs via dmidecode
    dimms = await _collect_dimms()

    # EDAC counters
    ce_total, ue_total = await _collect_edac()

    return MemoryInfo(
        total_gb=total_gb,
        available_gb=available_gb,
        used_percent=used_percent,
        dimms=dimms,
        ecc_correctable_errors=ce_total,
        ecc_uncorrectable_errors=ue_total,
    )


async def _collect_dimms() -> list[DimmInfo]:
    stdout, _, rc = await run_command(["dmidecode", "-t", "17"])
    if rc != 0:
        return []

    dimms: list[DimmInfo] = []
    current: dict[str, str] = {}

    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped == "Memory Device":
            if current and current.get("size", "").lower() not in (
                "",
                "no module installed",
                "unknown",
            ):
                dimms.append(_dimm_from_dict(current))
            current = {}
        elif ":" in stripped:
            key, val = stripped.split(":", 1)
            current[key.strip().lower()] = val.strip()

    # Last device
    if current and current.get("size", "").lower() not in (
        "",
        "no module installed",
        "unknown",
    ):
        dimms.append(_dimm_from_dict(current))

    return dimms


def _dimm_from_dict(d: dict[str, str]) -> DimmInfo:
    size_str = d.get("size", "")
    size_mb = None
    m = re.match(r"(\d+)\s*(MB|GB)", size_str, re.IGNORECASE)
    if m:
        size_mb = int(m.group(1))
        if m.group(2).upper() == "GB":
            size_mb *= 1024

    speed_str = d.get("configured memory speed", d.get("speed", ""))
    speed_mhz = None
    sm = re.match(r"(\d+)", speed_str)
    if sm:
        speed_mhz = int(sm.group(1))

    serial = d.get("serial number", "")
    if serial.lower() in ("unknown", "not specified", ""):
        serial = ""

    return DimmInfo(
        locator=d.get("locator", "Unknown"),
        size_mb=size_mb,
        type=d.get("type", None),
        speed_mhz=speed_mhz,
        manufacturer=d.get("manufacturer", None),
        serial=serial or None,
        part_number=d.get("part number", "").strip() or None,
    )


async def _collect_edac() -> tuple[int, int]:
    ce_total = 0
    ue_total = 0
    for ce_file in glob.glob("/sys/devices/system/edac/mc/mc*/ce_count"):
        val = (await read_file(ce_file)).strip()
        if val.isdigit():
            ce_total += int(val)
    for ue_file in glob.glob("/sys/devices/system/edac/mc/mc*/ue_count"):
        val = (await read_file(ue_file)).strip()
        if val.isdigit():
            ue_total += int(val)
    return ce_total, ue_total
