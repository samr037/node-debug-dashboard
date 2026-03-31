import glob as g
import json
import os
from datetime import datetime, timezone

from app.collectors.base import read_file, run_command, ttl_cache
from app.config import HOST_ROOT
from app.models.talos import (
    TalosCertificateInfo,
    TalosMachineConfig,
    TalosNetworkInterface,
    TalosOverview,
    TalosVersionInfo,
)


@ttl_cache(seconds=300)
async def collect_talos_version() -> TalosVersionInfo:
    # Read from os-release (always available on Talos)
    os_release = await read_file(f"{HOST_ROOT}/etc/os-release")
    version = ""
    for line in os_release.splitlines():
        if line.startswith("VERSION_ID="):
            version = line.split("=", 1)[1].strip().strip('"')
            break

    # Schematic from node annotation (via K8s API)
    schematic_id = ""
    token = (
        await read_file("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ).strip()
    node_name = os.environ.get("KUBERNETES_NODE_NAME", "")
    if token and node_name:
        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        stdout, _, rc = await run_command(
            [
                "curl",
                "-s",
                "--cacert",
                ca_path,
                "-H",
                f"Authorization: Bearer {token}",
                f"https://kubernetes.default.svc/api/v1/nodes/{node_name}",
            ]
        )
        if rc == 0 and stdout.strip():
            try:
                data = json.loads(stdout)
                annotations = data.get("metadata", {}).get("annotations", {})
                schematic_id = annotations.get("extensions.talos.dev/schematic", "")
            except (json.JSONDecodeError, KeyError):
                pass

    return TalosVersionInfo(version=version, schematic_id=schematic_id)


@ttl_cache(seconds=300)
async def collect_talos_machine_config() -> TalosMachineConfig:
    """Extract Talos machine info from host filesystem and K8s API.

    Talos encrypts its config at rest, so we read what's available from
    the host filesystem (os-release, network interfaces) and node labels.
    """
    # Determine machine type from K8s node labels
    machine_type = "worker"
    install_image = ""
    extensions: list[str] = []
    cluster_name = ""
    cluster_endpoint = ""

    token = (
        await read_file("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ).strip()
    node_name = os.environ.get("KUBERNETES_NODE_NAME", "")

    if token and node_name:
        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        stdout, _, rc = await run_command(
            [
                "curl",
                "-s",
                "--cacert",
                ca_path,
                "-H",
                f"Authorization: Bearer {token}",
                f"https://kubernetes.default.svc/api/v1/nodes/{node_name}",
            ]
        )
        if rc == 0 and stdout.strip():
            try:
                data = json.loads(stdout)
                labels = data.get("metadata", {}).get("labels", {})

                if "node-role.kubernetes.io/control-plane" in labels:
                    machine_type = "controlplane"

                # Extract extension versions from labels
                for key, val in labels.items():
                    if key.startswith("extensions.talos.dev/"):
                        ext_name = key.replace("extensions.talos.dev/", "")
                        extensions.append(f"{ext_name}:{val}")

            except (json.JSONDecodeError, KeyError):
                pass

    # Get install disk from lsblk (find the Talos boot disk)
    install_disk = ""
    stdout, _, rc = await run_command(
        [
            "nsenter",
            f"--mount={HOST_ROOT}/../host-proc/1/ns/mnt",
            "--",
            "cat",
            "/proc/cmdline",
        ]
    )
    if rc != 0:
        # Fallback: read from /host-proc directly
        stdout = await read_file("/host-proc/cmdline")
    for part in stdout.split():
        if part.startswith("talos.platform="):
            pass  # could extract platform
        if "root=" in part:
            install_disk = part.split("=", 1)[1] if "=" in part else ""

    # Get network interfaces from the host
    interfaces: list[TalosNetworkInterface] = []
    try:
        iface_names = os.listdir("/sys/class/net/")
    except OSError:
        iface_names = []

    skip = ("lo", "veth", "cali", "cni", "flannel", "docker", "br-")
    for name in sorted(iface_names):
        if name == "lo" or any(name.startswith(p) for p in skip):
            continue

        addresses: list[str] = []
        stdout, _, rc = await run_command(["ip", "-j", "addr", "show", name])
        if rc == 0 and stdout.strip():
            try:
                for iface in json.loads(stdout):
                    for a in iface.get("addr_info", []):
                        addresses.append(f"{a['local']}/{a['prefixlen']}")
            except (json.JSONDecodeError, KeyError):
                pass

        dhcp = False  # Can't determine from outside Talos config
        interfaces.append(
            TalosNetworkInterface(
                name=name,
                addresses=addresses,
                dhcp=dhcp,
            )
        )

    return TalosMachineConfig(
        machine_type=machine_type,
        install_disk=install_disk,
        install_image=install_image,
        cluster_name=cluster_name,
        cluster_endpoint=cluster_endpoint,
        network_interfaces=interfaces,
        extensions=extensions,
        config_available=True,
    )


@ttl_cache(seconds=300)
async def collect_talos_certificates() -> list[TalosCertificateInfo]:
    # Search for certs in K8s PKI (Talos manages these)
    search_patterns = [
        f"{HOST_ROOT}/etc/kubernetes/pki/*.crt",
        f"{HOST_ROOT}/etc/kubernetes/pki/etcd/*.crt",
    ]

    cert_files: list[str] = []
    for pattern in search_patterns:
        cert_files.extend(sorted(g.glob(pattern)))

    # Talos-specific: no certs in /system/state/ or /etc/talos/ on modern Talos
    # The K8s PKI certs ARE the Talos-managed certs

    certs: list[TalosCertificateInfo] = []
    # Avoid duplicating K8s collector certs — only add if we find Talos-specific ones
    for search_dir in [f"{HOST_ROOT}/system/state/", f"{HOST_ROOT}/etc/talos/"]:
        for ext in ("*.crt", "*.pem"):
            for cert_file in sorted(g.glob(os.path.join(search_dir, ext))):
                # Skip .key files
                if cert_file.endswith(".key"):
                    continue
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

                subject = issuer = not_before = not_after = fingerprint = serial = ""
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

                days_until_expiry = None
                expiry_severity = "ok"
                if not_after:
                    try:
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


@ttl_cache(seconds=300)
async def collect_talos() -> TalosOverview:
    version = await collect_talos_version()
    machine_config = await collect_talos_machine_config()
    certificates = await collect_talos_certificates()
    return TalosOverview(
        version=version,
        machine_config=machine_config,
        certificates=certificates,
    )
