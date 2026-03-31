from app.collectors.base import run_command, ttl_cache
from app.models.hardware import PCIDevice


@ttl_cache()
async def collect_pci() -> list[PCIDevice]:
    stdout, _, rc = await run_command(["lspci", "-vmm"])
    if rc != 0:
        return []

    devices: list[PCIDevice] = []
    current: dict[str, str] = {}

    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                devices.append(
                    PCIDevice(
                        slot=current.get("Slot", ""),
                        class_name=current.get("Class", ""),
                        vendor=current.get("Vendor", ""),
                        device=current.get("Device", ""),
                        subsystem_vendor=current.get("SVendor", ""),
                        subsystem_device=current.get("SDevice", ""),
                        rev=current.get("Rev", ""),
                        driver=current.get("Driver", ""),
                    )
                )
            current = {}
        elif ":\t" in stripped:
            key, val = stripped.split(":\t", 1)
            current[key.strip()] = val.strip()

    if current:
        devices.append(
            PCIDevice(
                slot=current.get("Slot", ""),
                class_name=current.get("Class", ""),
                vendor=current.get("Vendor", ""),
                device=current.get("Device", ""),
                subsystem_vendor=current.get("SVendor", ""),
                subsystem_device=current.get("SDevice", ""),
                rev=current.get("Rev", ""),
                driver=current.get("Driver", ""),
            )
        )

    return devices
