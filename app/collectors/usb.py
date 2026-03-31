import re

from app.collectors.base import run_command, ttl_cache
from app.models.hardware import USBDevice


@ttl_cache()
async def collect_usb() -> list[USBDevice]:
    stdout, _, rc = await run_command(["lsusb"])
    if rc != 0:
        return []

    devices: list[USBDevice] = []
    for line in stdout.splitlines():
        # Bus 001 Device 003: ID 0bda:8153 Realtek Semiconductor Corp. ...
        m = re.match(r"Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+(\S+)\s+(.*)", line.strip())
        if m:
            devices.append(
                USBDevice(
                    bus=m.group(1),
                    device=m.group(2),
                    id=m.group(3),
                    name=m.group(4).strip(),
                )
            )
    return devices
