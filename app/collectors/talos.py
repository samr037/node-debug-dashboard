import glob as g
import os
from datetime import datetime, timezone

import yaml

from app.collectors.base import read_file, run_command, ttl_cache
from app.config import HOST_ROOT
from app.models.talos import (
    TalosCertificateInfo,
    TalosMachineConfig,
    TalosNetworkInterface,
    TalosOverview,
    TalosVersionInfo,
)


@ttl_cache()
async def collect_talos_version() -> TalosVersionInfo:
    version = (await read_file(f"{HOST_ROOT}/etc/talos/version")).strip()
    schematic_id = (await read_file(f"{HOST_ROOT}/etc/talos/schematic")).strip()
    return TalosVersionInfo(version=version, schematic_id=schematic_id)


@ttl_cache()
async def collect_talos_machine_config() -> TalosMachineConfig:
    raw = await read_file(f"{HOST_ROOT}/system/state/config.yaml")
    if not raw:
        return TalosMachineConfig(
            config_available=False,
            error="Config not accessible (may be encrypted)",
        )

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return TalosMachineConfig(
            config_available=False,
            error=f"Failed to parse config: {exc}",
        )

    if not isinstance(data, dict):
        return TalosMachineConfig(
            config_available=False,
            error="Config is not a valid YAML mapping",
        )

    machine = data.get("machine", {}) or {}
    cluster = data.get("cluster", {}) or {}
    install = machine.get("install", {}) or {}
    network = machine.get("network", {}) or {}

    # Parse network interfaces
    interfaces: list[TalosNetworkInterface] = []
    for iface in network.get("interfaces", []) or []:
        if not isinstance(iface, dict):
            continue
        addresses: list[str] = []
        for addr in iface.get("addresses", []) or []:
            if isinstance(addr, str):
                addresses.append(addr)
        routes: list[str] = []
        for route in iface.get("routes", []) or []:
            if isinstance(route, dict):
                network_str = route.get("network", "")
                gateway = route.get("gateway", "")
                routes.append(
                    f"{network_str} via {gateway}" if gateway else network_str
                )
        interfaces.append(
            TalosNetworkInterface(
                name=iface.get(
                    "interface", iface.get("deviceSelector", {}).get("hardwareAddr", "")
                ),
                addresses=addresses,
                routes=routes,
                dhcp=bool(iface.get("dhcp", False)),
            )
        )

    # Parse extensions
    extensions: list[str] = []
    for ext in install.get("extensions", []) or []:
        if isinstance(ext, dict):
            image = ext.get("image", "")
            if image:
                extensions.append(image)
        elif isinstance(ext, str):
            extensions.append(ext)

    control_plane = cluster.get("controlPlane", {}) or {}

    return TalosMachineConfig(
        machine_type=str(machine.get("type", "")),
        install_disk=str(install.get("disk", "")),
        install_image=str(install.get("image", "")),
        cluster_name=str(cluster.get("clusterName", "")),
        cluster_endpoint=str(control_plane.get("endpoint", "")),
        network_interfaces=interfaces,
        extensions=extensions,
        config_available=True,
    )


@ttl_cache()
async def collect_talos_certificates() -> list[TalosCertificateInfo]:
    search_dirs = [
        f"{HOST_ROOT}/system/state/",
        f"{HOST_ROOT}/etc/talos/",
    ]

    cert_files: list[str] = []
    for search_dir in search_dirs:
        for ext in ("*.crt", "*.pem"):
            cert_files.extend(sorted(g.glob(os.path.join(search_dir, ext))))

    certs: list[TalosCertificateInfo] = []
    for cert_file in cert_files:
        stdout, _, rc = await run_command(
            [
                "openssl",
                "x509",
                "-in",
                cert_file,
                "-noout",
                "-subject",
                "-issuer",
                "-dates",
                "-fingerprint",
                "-serial",
                "-sha256",
            ]
        )
        if rc != 0:
            continue

        subject = ""
        issuer = ""
        not_before = ""
        not_after = ""
        fingerprint = ""
        serial = ""

        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("subject="):
                subject = line.split("=", 1)[1].strip()
            elif line.startswith("issuer="):
                issuer = line.split("=", 1)[1].strip()
            elif line.startswith("notBefore="):
                not_before = line.split("=", 1)[1].strip()
            elif line.startswith("notAfter="):
                not_after = line.split("=", 1)[1].strip()
            elif "Fingerprint=" in line:
                fingerprint = line.split("=", 1)[1].strip()
            elif line.startswith("serial="):
                serial = line.split("=", 1)[1].strip()

        # Compute days until expiry
        days_until_expiry = None
        expiry_severity = "ok"
        if not_after:
            try:
                # OpenSSL date format: "Mon DD HH:MM:SS YYYY GMT"
                expiry_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                delta = expiry_dt - datetime.now(timezone.utc)
                days_until_expiry = delta.days
                if days_until_expiry < 30:
                    expiry_severity = "critical"
                elif days_until_expiry < 90:
                    expiry_severity = "warning"
            except (ValueError, OverflowError):
                pass

        certs.append(
            TalosCertificateInfo(
                name=os.path.basename(cert_file),
                subject=subject,
                issuer=issuer,
                not_before=not_before,
                not_after=not_after,
                serial_number=serial,
                sha256_fingerprint=fingerprint,
                days_until_expiry=days_until_expiry,
                expiry_severity=expiry_severity,
            )
        )

    return certs


@ttl_cache()
async def collect_talos() -> TalosOverview:
    version = await collect_talos_version()
    machine_config = await collect_talos_machine_config()
    certificates = await collect_talos_certificates()
    return TalosOverview(
        version=version,
        machine_config=machine_config,
        certificates=certificates,
    )
