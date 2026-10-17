import time

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
from app.config import COUNTER_WINDOW_HOURS
from app.models.warnings import NodeWarning
from app.routers.degrade import gather_sections

router = APIRouter(tags=["warnings"])

# smartctl exit-status findings (bits 4-7) that point at a drive actively
# failing, as opposed to a past threshold crossing or a logged error.
_CRITICAL_SMART_ISSUES = {
    "Self-test log contains errors",
    "Prefail attribute at or below threshold",
}

# Last value seen per lifetime counter, and when it last went up.
_counters: dict[tuple[str, str, str], tuple[int, float | None]] = {}


def _counter_severity(
    key: tuple[str, str, str], value: int, severity: str
) -> str | None:
    """Severity for a cumulative error counter, or None when it is zero.

    NIC and EDAC counters only reset on reboot, so "> 0" alone kept one old
    event critical for the node's whole uptime. Full severity is kept for a
    counter that has gone up within COUNTER_WINDOW_HOURS; one that has not
    moved since (or since the dashboard started) is reported as info.
    """
    now = time.monotonic()
    prev = _counters.get(key)
    last_increase = None
    if prev is not None:
        prev_value, last_increase = prev
        if value > prev_value or (value < prev_value and value > 0):
            # Up, or reset (driver reload) and already non-zero again.
            last_increase = now
    _counters[key] = (value, last_increase)
    if value <= 0:
        return None
    if last_increase is not None and now - last_increase < COUNTER_WINDOW_HOURS * 3600:
        return severity
    return "info"


@router.get("/warnings", response_model=list[NodeWarning])
async def get_warnings():
    """Aggregated warnings from all subsystems."""
    warnings: list[NodeWarning] = []

    # Every source is independent, so gather them rather than paying for
    # each one in turn — this endpoint touches ten collectors. A source that
    # fails contributes nothing but a warning saying so, rather than taking
    # every other subsystem's warnings down with it.
    r, failed = await gather_sections(
        dmesg=collect_dmesg_warnings(),
        smart=collect_all_smart(),
        sensors=collect_sensors(),
        memory=collect_memory(),
        nics=collect_nics(),
        disk_usage=collect_disk_usage(),
        k8s_certs=collect_k8s_certificates(),
        talos_certs=collect_talos_certificates(),
        components=collect_k8s_components(),
        k8s_node=collect_k8s_node_info(),
    )
    for name in failed:
        warnings.append(
            NodeWarning(
                severity="warning",
                source="dashboard",
                message=f"Could not collect {name}; its warnings are missing",
            )
        )
    dmesg_warnings = r["dmesg"] or []
    smart_reports = r["smart"] or []
    sensor_readings = r["sensors"] or []
    mem = r["memory"]
    nics = r["nics"] or []
    disk_usage = r["disk_usage"] or []
    k8s_certs = r["k8s_certs"] or []
    talos_certs = r["talos_certs"] or []
    components = r["components"] or []
    k8s_node = r["k8s_node"]

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
        # A drive SMART could not be read from (timeout, open/IDENTIFY
        # failure) is often the dying one; a sleeping drive is not.
        if smart.error and not smart.standby:
            warnings.append(
                NodeWarning(
                    severity="warning",
                    source="smart",
                    message=f"SMART not readable: {smart.error}",
                    device=smart.device,
                )
            )
        if smart.pending_sectors and smart.pending_sectors > 0:
            warnings.append(
                NodeWarning(
                    severity="warning",
                    source="smart",
                    message=f"Pending sectors: {smart.pending_sectors}",
                    device=smart.device,
                )
            )
        if smart.offline_uncorrectable and smart.offline_uncorrectable > 0:
            warnings.append(
                NodeWarning(
                    severity="critical",
                    source="smart",
                    message=f"Offline uncorrectable sectors: {smart.offline_uncorrectable}",
                    device=smart.device,
                )
            )
        for issue in smart.smart_issues:
            warnings.append(
                NodeWarning(
                    severity="critical"
                    if issue in _CRITICAL_SMART_ISSUES
                    else "warning",
                    source="smart",
                    message=issue,
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

    # Memory warnings (lifetime EDAC counters)
    if mem is not None:
        sev = _counter_severity(
            ("memory", "", "ecc_ue"), mem.ecc_uncorrectable_errors, "critical"
        )
        if sev:
            warnings.append(
                NodeWarning(
                    severity=sev,
                    source="memory",
                    message=f"ECC uncorrectable errors: {mem.ecc_uncorrectable_errors}",
                )
            )
        sev = _counter_severity(
            ("memory", "", "ecc_ce"), mem.ecc_correctable_errors, "warning"
        )
        if sev:
            warnings.append(
                NodeWarning(
                    severity=sev,
                    source="memory",
                    message=f"ECC correctable errors: {mem.ecc_correctable_errors}",
                )
            )

    # Network warnings (lifetime sysfs counters)
    for nic in nics:
        sev = _counter_severity(
            ("network", nic.name, "crc"), nic.rx_crc_errors, "critical"
        )
        if sev:
            warnings.append(
                NodeWarning(
                    severity=sev,
                    source="network",
                    message=f"CRC errors: {nic.rx_crc_errors}",
                    device=nic.name,
                )
            )
        sev = _counter_severity(
            ("network", nic.name, "carrier"), nic.tx_carrier_errors, "critical"
        )
        if sev:
            warnings.append(
                NodeWarning(
                    severity=sev,
                    source="network",
                    message=f"Carrier errors: {nic.tx_carrier_errors}",
                    device=nic.name,
                )
            )
    if "nics" not in failed:
        # Forget interfaces that are gone, so pod-network churn does not
        # grow the table for the life of the process.
        present = {nic.name for nic in nics}
        for key in [k for k in _counters if k[0] == "network"]:
            if key[1] not in present:
                del _counters[key]

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
    # Both collectors scan Talos' /system/secrets/kubernetes on purpose (each
    # tab lists it), but one expiring cert must raise one warning: the Talos
    # one, which names it by its relative path, unless that source failed.
    talos_owns_pki = "talos_certs" not in failed
    for cert in k8s_certs:
        if talos_owns_pki and "/system/secrets/kubernetes/" in cert.file_path:
            continue
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
    for cond in k8s_node.conditions if k8s_node is not None else []:
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
