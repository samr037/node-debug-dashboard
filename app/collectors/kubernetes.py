import glob
import json
import os
from datetime import datetime, timezone

from app.collectors.base import read_file, run_command, ttl_cache
from app.config import HOST_ROOT
from app.models.kubernetes import (
    CertificateInfo,
    ClusterNode,
    K8sApiEndpoint,
    K8sComponentStatus,
    K8sNodeAddress,
    K8sNodeCondition,
    K8sNodeInfo,
    K8sNodeResources,
    KubernetesOverview,
)


def _parse_resources(data: dict) -> K8sNodeResources:
    """Extract resource fields from a capacity/allocatable dict."""
    return K8sNodeResources(
        cpu=data.get("cpu", ""),
        memory=data.get("memory", ""),
        ephemeral_storage=data.get("ephemeral-storage", ""),
        pods=data.get("pods", ""),
        gpu_nvidia=data.get("nvidia.com/gpu", ""),
    )


@ttl_cache()
async def collect_k8s_node_info() -> K8sNodeInfo:
    """Query the Kubernetes API for this node's info using the service account."""
    token = await read_file("/var/run/secrets/kubernetes.io/serviceaccount/token")
    node_name = os.environ.get("KUBERNETES_NODE_NAME", "")

    if not token or not node_name:
        return K8sNodeInfo()

    token = token.strip()
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    url = f"https://kubernetes.default.svc/api/v1/nodes/{node_name}"

    stdout, _, rc = await run_command(
        [
            "curl",
            "-s",
            "--cacert",
            ca_path,
            "-H",
            f"Authorization: Bearer {token}",
            url,
        ]
    )

    if rc != 0 or not stdout.strip():
        return K8sNodeInfo()

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return K8sNodeInfo()

    metadata = data.get("metadata", {})
    status = data.get("status", {})
    node_info = status.get("nodeInfo", {})

    labels = metadata.get("labels", {})
    annotations = metadata.get("annotations", {})

    conditions = [
        K8sNodeCondition(
            type=c.get("type", ""),
            status=c.get("status", ""),
            reason=c.get("reason", ""),
            message=c.get("message", ""),
            last_transition=c.get("lastTransitionTime", ""),
        )
        for c in status.get("conditions", [])
    ]

    addresses = [
        K8sNodeAddress(
            type=a.get("type", ""),
            address=a.get("address", ""),
        )
        for a in status.get("addresses", [])
    ]

    capacity = _parse_resources(status.get("capacity", {}))
    allocatable = _parse_resources(status.get("allocatable", {}))

    return K8sNodeInfo(
        labels=labels,
        annotations_count=len(annotations),
        conditions=conditions,
        addresses=addresses,
        capacity=capacity,
        allocatable=allocatable,
        kubelet_version=node_info.get("kubeletVersion", ""),
        container_runtime=node_info.get("containerRuntimeVersion", ""),
        os_image=node_info.get("osImage", ""),
        architecture=node_info.get("architecture", ""),
    )


@ttl_cache()
async def collect_k8s_certificates() -> list[CertificateInfo]:
    """Scan Kubernetes PKI directories for certificates and parse their details."""
    pki_dirs = [
        f"{HOST_ROOT}/etc/kubernetes/pki/",
        f"{HOST_ROOT}/etc/kubernetes/pki/etcd/",
    ]

    cert_files: list[str] = []
    for pki_dir in pki_dirs:
        cert_files.extend(glob.glob(f"{pki_dir}*.crt"))

    certs: list[CertificateInfo] = []
    for cert_path in sorted(cert_files):
        stdout, _, rc = await run_command(
            [
                "openssl",
                "x509",
                "-in",
                cert_path,
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
        serial_number = ""
        sha256_fingerprint = ""

        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("subject="):
                subject = line[len("subject=") :].strip()
            elif line.startswith("issuer="):
                issuer = line[len("issuer=") :].strip()
            elif line.startswith("notBefore="):
                not_before = line[len("notBefore=") :].strip()
            elif line.startswith("notAfter="):
                not_after = line[len("notAfter=") :].strip()
            elif "Fingerprint=" in line:
                sha256_fingerprint = line.split("=", 1)[1].strip()
            elif line.startswith("serial="):
                serial_number = line[len("serial=") :].strip()

        days_until_expiry = None
        expiry_severity = "ok"
        if not_after:
            try:
                expiry_dt = datetime.strptime(
                    not_after, "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                days_until_expiry = (expiry_dt - now).days
                if days_until_expiry < 30:
                    expiry_severity = "critical"
                elif days_until_expiry < 90:
                    expiry_severity = "warning"
            except ValueError:
                pass

        certs.append(
            CertificateInfo(
                file_path=cert_path,
                subject=subject,
                issuer=issuer,
                not_before=not_before,
                not_after=not_after,
                serial_number=serial_number,
                sha256_fingerprint=sha256_fingerprint,
                days_until_expiry=days_until_expiry,
                expiry_severity=expiry_severity,
            )
        )

    return certs


@ttl_cache()
async def collect_k8s_components() -> list[K8sComponentStatus]:
    """Probe health endpoints for core Kubernetes components."""
    components = {
        "kubelet": "http://localhost:10248/healthz",
        "kube-apiserver": "https://localhost:6443/healthz",
        "kube-scheduler": "https://localhost:10259/healthz",
        "kube-controller-manager": "https://localhost:10257/healthz",
        "etcd": "https://localhost:2379/health",
        "kube-proxy": "http://localhost:10249/healthz",
    }

    results: list[K8sComponentStatus] = []
    for name, url in components.items():
        stdout, _, rc = await run_command(["curl", "-sk", "--max-time", "2", url])

        if rc != 0 or not stdout.strip():
            health_status = "Unknown"
            running = False
        elif "ok" in stdout.lower() or '"health":"true"' in stdout.lower():
            health_status = "Healthy"
            running = True
        else:
            health_status = "Unhealthy"
            running = False

        results.append(
            K8sComponentStatus(
                name=name,
                running=running,
                health_status=health_status,
            )
        )

    return results


@ttl_cache()
async def collect_k8s_api_endpoint() -> K8sApiEndpoint:
    """Check the Kubernetes API endpoint health."""
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")

    if not host:
        return K8sApiEndpoint()

    token = (
        await read_file("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ).strip()
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    url = f"https://{host}:{port}/healthz"

    cmd = ["curl", "-s", "--max-time", "2", "--cacert", ca_path]
    if token:
        cmd.extend(["-H", f"Authorization: Bearer {token}"])
    else:
        cmd.append("-k")
    cmd.append(url)

    stdout, _, rc = await run_command(cmd)
    healthy = rc == 0 and "ok" in stdout.lower()

    return K8sApiEndpoint(
        url=f"https://{host}:{port}",
        healthy=healthy,
    )


@ttl_cache()
async def collect_cluster_nodes() -> list[ClusterNode]:
    """List all nodes in the cluster with name, IP, role, and readiness."""
    token = (
        await read_file("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ).strip()
    if not token:
        return []

    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    stdout, _, rc = await run_command(
        [
            "curl",
            "-s",
            "--cacert",
            ca_path,
            "-H",
            f"Authorization: Bearer {token}",
            "https://kubernetes.default.svc/api/v1/nodes",
        ]
    )
    if rc != 0 or not stdout.strip():
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    current_node = os.environ.get("KUBERNETES_NODE_NAME", "")
    nodes: list[ClusterNode] = []

    for item in data.get("items", []):
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        labels = metadata.get("labels", {})
        name = metadata.get("name", "")

        # Get InternalIP
        ip = ""
        for addr in status.get("addresses", []):
            if addr.get("type") == "InternalIP":
                ip = addr.get("address", "")
                break

        # Determine role
        role = (
            "control-plane"
            if "node-role.kubernetes.io/control-plane" in labels
            else "worker"
        )

        # Check readiness
        ready = False
        for cond in status.get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                ready = True
                break

        nodes.append(
            ClusterNode(
                name=name,
                ip=ip,
                role=role,
                ready=ready,
                current=(name == current_node),
            )
        )

    return sorted(nodes, key=lambda n: (n.role != "control-plane", n.name))


@ttl_cache()
async def collect_kubernetes() -> KubernetesOverview:
    """Aggregate all Kubernetes collectors into a single overview."""
    return KubernetesOverview(
        node_info=await collect_k8s_node_info(),
        certificates=await collect_k8s_certificates(),
        api_endpoint=await collect_k8s_api_endpoint(),
        components=await collect_k8s_components(),
        cluster_nodes=await collect_cluster_nodes(),
    )
