import asyncio

from fastapi import APIRouter

from app.collectors.dmesg import collect_dmesg_warnings
from app.collectors.kubernetes import (
    collect_k8s_certificates,
    collect_k8s_components,
    collect_k8s_node_info,
)
from app.collectors.memory import collect_memory
from app.collectors.network import collect_nics
from app.collectors.sensors import collect_sensors
from app.collectors.storage import collect_all_smart, collect_disk_usage
from app.collectors.talos import collect_talos_certificates
from app.models.warnings import NodeWarning

router = APIRouter(tags=["warnings"])


@router.get("/warnings", response_model=list[NodeWarning])
async def get_warnings():
    """Aggregated warnings from all subsystems."""
    warnings: list[NodeWarning] = []

    # Every source is independent, so gather them rather than paying for
    # each one in turn — this endpoint touches nine collectors.
    (
        dmesg_warnings,
        smart_reports,
        sensor_readings,
        mem,
        nics,
        disk_usage,
        k8s_certs,
        talos_certs,
        components,
        k8s_node,
    ) = await asyncio.gather(
        collect_dmesg_warnings(),
        collect_all_smart(),
        collect_sensors(),
        collect_memory(),
        collect_nics(),
        collect_disk_usage(),
        collect_k8s_certificates(),
        collect_talos_certificates(),
        collect_k8s_components(),
        collect_k8s_node_info(),
    )

    # Dmesg warnings (MCE, EDAC, CPU, I/O)
    warnings.extend(dmesg_warnings)

    # SMART warnings
    for smart in smart_reports:
        # Only an explicit False is a failure. None means the drive never
        # reported, which is not the same thing and must not page anyone.
        if smart.health_passed is False:
            warnings.append(
                NodeWarning(
                    severity="critical",
                    source="smart",
                    message="SMART health FAILED",
                    device=smart.device,
                )
            )
        if smart.reallocated_sectors and smart.reallocated_sectors > 0:
            warnings.append(
                NodeWarning(
                    severity="warning",
                    source="smart",
                    message=f"Reallocated sectors: {smart.reallocated_sectors}",
                    device=smart.device,
                )
            )
        if smart.wear_leveling_percent is not None and smart.wear_leveling_percent < 20:
            warnings.append(
                NodeWarning(
                    severity="warning",
                    source="smart",
                    message=f"SSD wear level at {smart.wear_leveling_percent}%",
                    device=smart.device,
                )
            )
        if smart.temperature_celsius and smart.temperature_celsius > 60:
            sev = "critical" if smart.temperature_celsius > 70 else "warning"
            warnings.append(
                NodeWarning(
                    severity=sev,
                    source="smart",
                    message=f"Disk temperature: {smart.temperature_celsius}°C",
                    device=smart.device,
                )
            )

    # Sensor warnings (temperatures)
    for sensor in sensor_readings:
        if sensor.is_alarm:
            sev = (
                "critical"
                if sensor.critical and sensor.value >= sensor.critical
                else "warning"
            )
            warnings.append(
                NodeWarning(
                    severity=sev,
                    source="temperature",
                    message=f"{sensor.label}: {sensor.value}{sensor.unit} (crit={sensor.critical}, warn={sensor.warning})",
                )
            )

    # Memory warnings
    if mem.ecc_uncorrectable_errors > 0:
        warnings.append(
            NodeWarning(
                severity="critical",
                source="memory",
                message=f"ECC uncorrectable errors: {mem.ecc_uncorrectable_errors}",
            )
        )
    if mem.ecc_correctable_errors > 0:
        warnings.append(
            NodeWarning(
                severity="warning",
                source="memory",
                message=f"ECC correctable errors: {mem.ecc_correctable_errors}",
            )
        )

    # Network warnings
    for nic in nics:
        if nic.rx_crc_errors > 0:
            warnings.append(
                NodeWarning(
                    severity="critical",
                    source="network",
                    message=f"CRC errors: {nic.rx_crc_errors}",
                    device=nic.name,
                )
            )
        if nic.tx_carrier_errors > 0:
            warnings.append(
                NodeWarning(
                    severity="critical",
                    source="network",
                    message=f"Carrier errors: {nic.tx_carrier_errors}",
                    device=nic.name,
                )
            )

    # Disk usage warnings
    for usage in disk_usage:
        if usage.severity != "ok":
            warnings.append(
                NodeWarning(
                    severity=usage.severity,
                    source="disk",
                    message=f"{usage.mount}: {usage.used_percent}% used ({usage.used}/{usage.size})",
                    device=usage.filesystem,
                )
            )

    # Certificate expiry warnings (K8s)
    for cert in k8s_certs:
        if cert.expiry_severity != "ok":
            warnings.append(
                NodeWarning(
                    severity=cert.expiry_severity,
                    source="certificate",
                    message=f"K8s cert expires in {cert.days_until_expiry}d: {cert.file_path.split('/')[-1]}",
                )
            )

    # Certificate expiry warnings (Talos)
    for cert in talos_certs:
        if cert.expiry_severity != "ok":
            warnings.append(
                NodeWarning(
                    severity=cert.expiry_severity,
                    source="certificate",
                    message=f"Talos cert expires in {cert.days_until_expiry}d: {cert.name}",
                )
            )

    # etcd storage warnings (control-plane nodes only)
    for component in components:
        etcd = component.etcd_status
        if etcd is None or etcd.defrag_severity == "ok":
            continue
        warnings.append(
            NodeWarning(
                severity=etcd.defrag_severity,
                source="etcd",
                message=(
                    f"etcd at {etcd.quota_used_percent}% of its "
                    f"{etcd.db_size_quota_mb:.0f}MB quota with only "
                    f"{etcd.db_size_in_use_mb:.0f}MB in use — "
                    f"{etcd.defrag_reclaimable_mb:.0f}MB reclaimable by defrag"
                ),
            )
        )

    # K8s node condition warnings
    for cond in k8s_node.conditions:
        if cond.type == "Ready" and cond.status != "True":
            warnings.append(
                NodeWarning(
                    severity="critical",
                    source="kubernetes",
                    message=f"Node not Ready: {cond.message}",
                )
            )
        elif (
            cond.type in ("MemoryPressure", "DiskPressure", "PIDPressure")
            and cond.status == "True"
        ):
            warnings.append(
                NodeWarning(
                    severity="warning",
                    source="kubernetes",
                    message=f"{cond.type}: {cond.message}",
                )
            )

    return warnings
