import re

from app.collectors.base import run_command, ttl_cache
from app.models.system import EFIBootEntry, EFIInfo


@ttl_cache()
async def collect_efi() -> EFIInfo:
    stdout, _, rc = await run_command(["efibootmgr", "-v"])
    if rc != 0:
        # Try without -v
        stdout, _, rc = await run_command(["efibootmgr"])
        if rc != 0:
            return EFIInfo()

    boot_current = None
    boot_order: list[str] = []
    timeout = None
    entries: list[EFIBootEntry] = []

    for line in stdout.splitlines():
        line = line.strip()

        if line.startswith("BootCurrent:"):
            boot_current = line.split(":", 1)[1].strip()
        elif line.startswith("BootOrder:"):
            boot_order = [x.strip() for x in line.split(":", 1)[1].strip().split(",")]
        elif line.startswith("Timeout:"):
            timeout = line.split(":", 1)[1].strip()
        elif re.match(r"^Boot[0-9A-Fa-f]{4}", line):
            m = re.match(
                r"^Boot([0-9A-Fa-f]{4})(\*?)\s+(.*?)(\t.*)?$",
                line,
            )
            if m:
                number = m.group(1)
                active = m.group(2) == "*"
                label = m.group(3).strip()
                path = m.group(4).strip() if m.group(4) else ""

                entries.append(
                    EFIBootEntry(
                        number=number,
                        label=label,
                        active=active,
                        path=path,
                    )
                )

    return EFIInfo(
        boot_current=boot_current,
        boot_order=boot_order,
        entries=entries,
        timeout=timeout,
    )
