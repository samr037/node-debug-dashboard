from fastapi import APIRouter

from app.collectors.dmesg import collect_dmesg_warnings
from app.collectors.memory import collect_memory
from app.collectors.network import collect_nics
from app.collectors.sensors import collect_sensors
from app.collectors.storage import collect_all_smart, collect_disk_usage
from app.models.warnings import Warning

router = APIRouter(tags=["warnings"])


@router.get("/warnings", response_model=list[Warning])
async def get_warnings():
    """Aggregated warnings from all subsystems."""
    warnings: list[Warning] = []

    # Dmesg warnings (MCE, EDAC, CPU, I/O)
    warnings.extend(await collect_dmesg_warnings())

    # SMART warnings
    for smart in await collect_all_smart():
        if not smart.health_passed:
            warnings.append(
                Warning(
                    severity="critical",
                    source="smart",
                    message="SMART health FAILED",
                    device=smart.device,
                )
            )
        if smart.reallocated_sectors and smart.reallocated_sectors > 0:
            warnings.append(
                Warning(
                    severity="warning",
                    source="smart",
                    message=f"Reallocated sectors: {smart.reallocated_sectors}",
                    device=smart.device,
                )
            )
        if smart.wear_leveling_percent is not None and smart.wear_leveling_percent < 20:
            warnings.append(
                Warning(
                    severity="warning",
                    source="smart",
                    message=f"SSD wear level at {smart.wear_leveling_percent}%",
                    device=smart.device,
                )
            )
        if smart.temperature_celsius and smart.temperature_celsius > 60:
            sev = "critical" if smart.temperature_celsius > 70 else "warning"
            warnings.append(
                Warning(
                    severity=sev,
                    source="smart",
                    message=f"Disk temperature: {smart.temperature_celsius}°C",
                    device=smart.device,
                )
            )

    # Sensor warnings (temperatures)
    for sensor in await collect_sensors():
        if sensor.is_alarm:
            sev = (
                "critical"
                if sensor.critical and sensor.value >= sensor.critical
                else "warning"
            )
            warnings.append(
                Warning(
                    severity=sev,
                    source="temperature",
                    message=f"{sensor.label}: {sensor.value}{sensor.unit} (crit={sensor.critical}, warn={sensor.warning})",
                )
            )

    # Memory warnings
    mem = await collect_memory()
    if mem.ecc_uncorrectable_errors > 0:
        warnings.append(
            Warning(
                severity="critical",
                source="memory",
                message=f"ECC uncorrectable errors: {mem.ecc_uncorrectable_errors}",
            )
        )
    if mem.ecc_correctable_errors > 0:
        warnings.append(
            Warning(
                severity="warning",
                source="memory",
                message=f"ECC correctable errors: {mem.ecc_correctable_errors}",
            )
        )

    # Network warnings
    for nic in await collect_nics():
        if nic.rx_crc_errors > 0:
            warnings.append(
                Warning(
                    severity="critical",
                    source="network",
                    message=f"CRC errors: {nic.rx_crc_errors}",
                    device=nic.name,
                )
            )
        if nic.tx_carrier_errors > 0:
            warnings.append(
                Warning(
                    severity="critical",
                    source="network",
                    message=f"Carrier errors: {nic.tx_carrier_errors}",
                    device=nic.name,
                )
            )

    # Disk usage warnings
    for usage in await collect_disk_usage():
        if usage.severity != "ok":
            warnings.append(
                Warning(
                    severity=usage.severity,
                    source="disk",
                    message=f"{usage.mount}: {usage.used_percent}% used ({usage.used}/{usage.size})",
                    device=usage.filesystem,
                )
            )

    return warnings
