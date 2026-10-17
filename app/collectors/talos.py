import asyncio
import glob as g
import json
import os
from datetime import datetime, timezone

from app.collectors.base import read_file, run_command, ttl_cache
from app.config import HOST_PROC, HOST_ROOT, HOST_SYS
from app.models.talos import (
    TalosCertificateInfo,
    TalosMachineConfig,
    TalosNetworkInterface,
    TalosOverview,
    TalosVersionInfo,
)


# Pod network devices and CNI plumbing: Cilium's lxc*/cilium_*, Calico's
# cali*, flannel, the IPVS dummy, tunnels. On a busy node these outnumber
# the real NICs fifty to one.
_SKIP_IFACE_PREFIXES = (
    "veth",
    "lxc",
    "cilium_",
    "cali",
    "cni",
    "flannel",
    "docker",
    "br-",
    "kube-ipvs",
    "tunl",
    "vxlan",
    "genev",
)

# GPT partition labels only Talos's install disk carries. EPHEMERAL can be
# moved to another disk, and EFI exists on any UEFI disk.
_TALOS_PARTLABELS = ("META", "STATE")


# A failed fetch (API server restarting mid-upgrade) is retried after a few
# seconds rather than making a control plane read as a worker for 5 minutes.
@ttl_cache(seconds=300, cache_if=lambda node: node is not None)
async def _node_object() -> dict | None:
    """This node's object from the API server.

    Both collectors below want a couple of fields off it, and each used to
    fetch it separately.
    """
    from app.collectors.kubernetes import _k8s_get

    node_name = os.environ.get("KUBERNETES_NODE_NAME", "")
    if not node_name:
        return None
    return await _k8s_get(f"/api/v1/nodes/{node_name}")


# Not cached itself: a file read plus the cached node object, and caching
# here would pin an empty schematic for the full TTL after an API blip.
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
    node = await _node_object()
    if node:
        annotations = node.get("metadata", {}).get("annotations", {})
        schematic_id = annotations.get("extensions.talos.dev/schematic", "")

    return TalosVersionInfo(version=version, schematic_id=schematic_id)


def _install_disk_from_sysfs() -> str:
    """The disk carrying Talos's own partitions, e.g. "/dev/nvme0n1".

    Partition uevents carry the GPT label, and a partition's sysfs path
    sits under its parent disk's.
    """
    block_dir = f"{HOST_SYS}/class/block"
    try:
        names = sorted(os.listdir(block_dir))
    except OSError:
        return ""
    for name in names:
        uevent = ""
        try:
            with open(f"{block_dir}/{name}/uevent", errors="replace") as f:
                uevent = f.read()
        except OSError:
            continue
        for line in uevent.splitlines():
            if line.startswith("PARTNAME=") and line[9:].strip() in _TALOS_PARTLABELS:
                parent = os.path.basename(
                    os.path.dirname(os.path.realpath(f"{block_dir}/{name}"))
                )
                if parent and parent != "block":
                    return f"/dev/{parent}"
    return ""


def _root_from_cmdline(cmdline: str) -> str:
    """root= off a kernel command line; Talos sets none, other distros do."""
    for part in cmdline.split():
        # startswith, not "in": nfsroot= and the like are not root=.
        if part.startswith("root="):
            return part.split("=", 1)[1]
    return ""


def _parse_ip_addr(stdout: str) -> dict[str, list[str]]:
    """`ip -j addr` output as {ifname: ["10.0.0.5/24", ...]}."""
    by_name: dict[str, list[str]] = {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return by_name
    if not isinstance(data, list):
        return by_name
    for iface in data:
        if not isinstance(iface, dict):
            continue
        addrs = by_name.setdefault(str(iface.get("ifname", "")), [])
        for a in iface.get("addr_info", []) or []:
            if isinstance(a, dict) and "local" in a and "prefixlen" in a:
                addrs.append(f"{a['local']}/{a['prefixlen']}")
    return by_name


@ttl_cache(seconds=300, cache_if=lambda mc: mc.config_available)
async def collect_talos_machine_config() -> TalosMachineConfig:
    """Extract Talos machine info from host filesystem and K8s API.

    Talos encrypts its config at rest, so we read what's available from
    the host filesystem (os-release, network interfaces) and node labels.
    """
    # Determine machine type from K8s node labels; unknown without them.
    machine_type = ""
    install_image = ""
    extensions: list[str] = []
    cluster_name = ""
    cluster_endpoint = ""

    node = await _node_object()
    error = ""
    if not node:
        error = "Node object unavailable from the Kubernetes API"
    else:
        labels = node.get("metadata", {}).get("labels", {})

        machine_type = "worker"
        if "node-role.kubernetes.io/control-plane" in labels:
            machine_type = "controlplane"

        # Extract extension versions from labels
        for key, val in labels.items():
            if key.startswith("extensions.talos.dev/"):
                ext_name = key.replace("extensions.talos.dev/", "")
                extensions.append(f"{ext_name}:{val}")

    install_disk = _install_disk_from_sysfs()
    if not install_disk:
        install_disk = _root_from_cmdline(await read_file(f"{HOST_PROC}/cmdline"))

    # Get network interfaces from the host
    interfaces: list[TalosNetworkInterface] = []
    try:
        iface_names = os.listdir(f"{HOST_SYS}/class/net/")
    except OSError:
        iface_names = []

    # One `ip` call for every interface, not one per interface.
    addresses: dict[str, list[str]] = {}
    stdout, _, rc = await run_command(["ip", "-j", "addr", "show"])
    if rc == 0 and stdout.strip():
        addresses = _parse_ip_addr(stdout)

    for name in sorted(iface_names):
        if name == "lo" or name.startswith(_SKIP_IFACE_PREFIXES):
            continue

        dhcp = False  # Can't determine from outside Talos config
        interfaces.append(
            TalosNetworkInterface(
                name=name,
                addresses=addresses.get(name, []),
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
        # Only what came off the node object; the rest is best effort.
        config_available=bool(node),
        error=error,
    )


@ttl_cache(seconds=300)
async def collect_talos_certificates() -> list[TalosCertificateInfo]:
    # Where Talos actually keeps the Kubernetes PKI: the apiserver,
    # controller-manager and scheduler material under /system/secrets, and
    # the kubelet's own serving/client certs. /etc/kubernetes/pki does not
    # exist on Talos, and the etcd certs are covered by the Kubernetes
    # collector.
    search_roots = [
        (f"{HOST_ROOT}/system/secrets/kubernetes", "**/*.crt"),
        (f"{HOST_ROOT}/var/lib/kubelet/pki", "*.crt"),
        # Only the live rotated cert: the kubelet leaves every dated
        # kubelet-client-<date>.pem behind, and the expired ones would
        # all read as critical.
        (f"{HOST_ROOT}/var/lib/kubelet/pki", "*-current.pem"),
    ]

    seen: set[str] = set()
    certs: list[TalosCertificateInfo] = []
    for root, pattern in search_roots:
        for cert_file in sorted(g.glob(os.path.join(root, pattern), recursive=True)):
            # Dedupe on the target, should a .crt and a link share one.
            real = os.path.realpath(cert_file)
            if real in seen or not os.path.isfile(real):
                continue
            seen.add(real)
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
                    # Relative, since every component dir has a ca.crt.
                    name=os.path.relpath(cert_file, root),
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


@ttl_cache(seconds=300, cache_if=lambda o: o.machine_config.config_available)
async def collect_talos() -> TalosOverview:
    version, machine_config, certificates = await asyncio.gather(
        collect_talos_version(),
        collect_talos_machine_config(),
        collect_talos_certificates(),
    )
    return TalosOverview(
        version=version,
        machine_config=machine_config,
        certificates=certificates,
    )
