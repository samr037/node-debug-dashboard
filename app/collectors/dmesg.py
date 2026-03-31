import re

from app.collectors.base import run_command, ttl_cache
from app.models.warnings import Warning


@ttl_cache()
async def collect_dmesg_warnings() -> list[Warning]:
    stdout, _, rc = await run_command(["dmesg", "--time-format", "iso"])
    if rc != 0:
        stdout, _, rc = await run_command(["dmesg"])
        if rc != 0:
            return []

    warnings: list[Warning] = []

    # MCE / Machine Check Exceptions
    mce_lines = [
        line
        for line in stdout.splitlines()
        if re.search(
            r"mce|machine check|uncorrected.*error|hardware error", line, re.IGNORECASE
        )
        and "no mce" not in line.lower()
    ]
    if mce_lines:
        warnings.append(
            Warning(
                severity="critical",
                source="memory",
                message=f"MCE errors detected ({len(mce_lines)} occurrences)",
            )
        )

    # EDAC
    edac_lines = [
        line
        for line in stdout.splitlines()
        if re.search(
            r"edac|ecc error|memory.*error|corrected error",
            line,
            re.IGNORECASE,
        )
    ]
    if edac_lines:
        warnings.append(
            Warning(
                severity="critical",
                source="memory",
                message=f"EDAC/ECC memory errors ({len(edac_lines)} occurrences)",
            )
        )

    # CPU errors / throttling
    cpu_lines = [
        line
        for line in stdout.splitlines()
        if re.search(
            r"cpu.*error|thermal|throttl|msr.*error|microcode.*error|soft lockup|hard lockup|rcu_sched",
            line,
            re.IGNORECASE,
        )
    ]
    if cpu_lines:
        # Distinguish thermal from errors
        thermal = any(
            re.search(r"thermal|throttl", line, re.IGNORECASE) for line in cpu_lines
        )
        lockup = any(
            re.search(r"lockup|rcu_sched", line, re.IGNORECASE) for line in cpu_lines
        )
        if lockup:
            warnings.append(
                Warning(
                    severity="critical",
                    source="cpu",
                    message=f"CPU lockup/stall detected ({len(cpu_lines)} occurrences)",
                )
            )
        elif thermal:
            warnings.append(
                Warning(
                    severity="warning",
                    source="cpu",
                    message=f"CPU thermal throttling detected ({len(cpu_lines)} occurrences)",
                )
            )

    # Disk I/O errors
    io_lines = [
        line
        for line in stdout.splitlines()
        if re.search(
            r"i/o error|ata.*error|blk_update_request|end_request.*error|buffer i/o|scsi.*error|medium error|sense key",
            line,
            re.IGNORECASE,
        )
    ]
    if io_lines:
        warnings.append(
            Warning(
                severity="critical",
                source="disk",
                message=f"Disk I/O errors detected ({len(io_lines)} occurrences)",
            )
        )

    return warnings
