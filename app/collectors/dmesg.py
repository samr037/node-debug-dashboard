"""Kernel ring buffer scanning for genuine hardware faults.

The matching here is deliberately narrow. A subsystem name is not evidence
of a fault: drivers announce themselves at boot, and matching on "edac" or
"thermal" turns those banners into permanent critical alerts. Every rule
matches an error *event*, and carries explicit exclusions for the
announcements that look similar.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.collectors.base import read_file, run_command, ttl_cache
from app.config import DMESG_WINDOW_HOURS, HOST_PROC
from app.models.warnings import NodeWarning

# Leading "[ 1234.5678]" seconds-since-boot stamp from plain dmesg.
_MONOTONIC_RE = re.compile(r"^\[\s*(\d+\.\d+)\]\s*(.*)$")
# Leading ISO stamp from `dmesg --time-format iso`.
_ISO_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[,.]\d+[+-]\d{2}:?\d{2})\s*(.*)$"
)


@dataclass(frozen=True)
class Rule:
    source: str
    severity: str
    label: str
    patterns: tuple[str, ...]
    # Tested first; a line hitting one of these is never treated as a fault.
    excludes: tuple[str, ...] = field(default=())

    def matches(self, message: str) -> bool:
        if any(re.search(p, message, re.IGNORECASE) for p in self.excludes):
            return False
        return any(re.search(p, message, re.IGNORECASE) for p in self.patterns)


RULES: tuple[Rule, ...] = (
    Rule(
        source="memory",
        severity="critical",
        label="Machine check exceptions",
        patterns=(
            r"\bmce:.*hardware error",
            r"machine check events logged",
            r"CPU\d+: Machine Check",
            r"\bMCE\b.*\b(uncorrected|fatal)\b",
        ),
        # "mce: CPU supports N MCE banks" is the driver reporting capability.
        excludes=(
            r"mce:\s*CPU\d*\s*supports",
            r"mce:\s*Machine check.*(enabled|initialized)",
            r"no mce",
        ),
    ),
    Rule(
        source="memory",
        severity="critical",
        label="Uncorrectable ECC errors",
        patterns=(r"\b\d+\s+UE\b", r"uncorrected error", r"uncorrectable error"),
        excludes=(r"\b0\s+UE\b",),
    ),
    Rule(
        source="memory",
        severity="warning",
        label="Correctable ECC errors",
        # EDAC reports these as "EDAC MC0: 1 CE memory read error on ...".
        # The count is the signal; the driver banner carries no count at all.
        patterns=(r"EDAC\s+MC\d+:\s*\d+\s+CE\b", r"\bcorrected error\b"),
        excludes=(
            r"EDAC\s+MC\d+:\s*0\s+CE\b",
            r"EDAC MC:\s*Ver",
            r"no ecc support",
            r"EDAC.*giving out device",
        ),
    ),
    Rule(
        source="cpu",
        severity="critical",
        label="CPU lockups or RCU stalls",
        patterns=(
            r"soft lockup",
            r"hard LOCKUP",
            r"rcu.*detected stall",
            r"rcu.*self-detected stall",
        ),
    ),
    Rule(
        source="cpu",
        severity="warning",
        label="CPU thermal throttling",
        # Only an over-threshold event counts. Registering a thermal governor,
        # enabling thermal monitoring, or a NIC's "Interrupt Throttling Rate"
        # are all normal boot chatter.
        patterns=(
            r"temperature above threshold",
            r"package temperature above",
            r"clock throttled",
            r"thermal throttl",
        ),
        excludes=(
            r"thermal monitoring enabled",
            r"registered thermal governor",
            r"registered as thermal_zone",
            r"interrupt throttling rate",
            r"temperature\s*/\s*speed normal",
        ),
    ),
    Rule(
        source="disk",
        severity="critical",
        label="Disk I/O errors",
        # "I/O error" on its own is specific enough and catches every form
        # the block, filesystem and journal layers emit:
        #   I/O error, dev sda, sector 8715240 op 0x1:(WRITE)
        #   Buffer I/O error on dev sda, logical block 0, lost sync page write
        #   JBD2: I/O error when updating journal superblock for sda-8.
        #   EXT4-fs (sda): I/O error while writing superblock
        # Matching "ata.*error" or "scsi.*error" instead, as this used to,
        # hits driver probe banners.
        patterns=(
            r"\bI/O error\b",
            r"critical (medium|target) error",
            r"failed command:",
            r"exception Emask",
            r"SCSI error.*return code",
            r"rejecting I/O to offline device",
        ),
    ),
)


def parse_line(raw: str, boot_time: datetime | None) -> tuple[datetime | None, str]:
    """Split a dmesg line into (timestamp, message).

    Returns a null timestamp when the line carries no parseable stamp, in
    which case the caller keeps the line rather than silently dropping it.
    """
    iso = _ISO_RE.match(raw)
    if iso:
        try:
            return datetime.fromisoformat(iso.group(1).replace(",", ".")), iso.group(2)
        except ValueError:
            return None, iso.group(2)

    mono = _MONOTONIC_RE.match(raw)
    if mono:
        if boot_time is None:
            return None, mono.group(2)
        return boot_time + timedelta(seconds=float(mono.group(1))), mono.group(2)

    return None, raw


def scan(
    lines: list[str],
    boot_time: datetime | None = None,
    cutoff: datetime | None = None,
    window_hours: int = DMESG_WINDOW_HOURS,
) -> list[NodeWarning]:
    """Apply the rules to dmesg output and summarise what matched."""
    hits: dict[Rule, list[str]] = {}

    for raw in lines:
        if not raw.strip():
            continue
        stamp, message = parse_line(raw, boot_time)

        # Boot-time events age out like anything else. Without this a single
        # event kept firing an alert until the next reboot.
        if cutoff is not None and stamp is not None and stamp < cutoff:
            continue

        for rule in RULES:
            if rule.matches(message):
                hits.setdefault(rule, []).append(message.strip())
                break

    warnings: list[NodeWarning] = []
    for rule in RULES:
        messages = hits.get(rule)
        if not messages:
            continue
        # Show what actually matched — a bare count is not triageable.
        sample = "; ".join(m[:120] for m in messages[-2:])
        warnings.append(
            NodeWarning(
                severity=rule.severity,
                source=rule.source,
                message=(
                    f"{rule.label}: {len(messages)} in the last "
                    f"{window_hours}h — {sample}"
                ),
            )
        )
    return warnings


async def _boot_time() -> datetime | None:
    """Wall-clock time the kernel started, for converting monotonic stamps."""
    uptime_raw = await read_file(f"{HOST_PROC}/uptime")
    if not uptime_raw:
        return None
    try:
        return datetime.now(timezone.utc) - timedelta(
            seconds=float(uptime_raw.split()[0])
        )
    except (ValueError, IndexError):
        return None


@ttl_cache()
async def collect_dmesg_warnings() -> list[NodeWarning]:
    stdout, _, rc = await run_command(["dmesg", "--time-format", "iso"])
    if rc != 0:
        stdout, _, rc = await run_command(["dmesg"])
        if rc != 0:
            return []

    return scan(
        stdout.splitlines(),
        boot_time=await _boot_time(),
        cutoff=datetime.now(timezone.utc) - timedelta(hours=DMESG_WINDOW_HOURS),
    )
