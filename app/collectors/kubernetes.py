import asyncio
import glob
import os
from datetime import datetime, timezone

import httpx

from app.collectors.base import run_command, ttl_cache
from app.config import HOST_ROOT, SSH_ENABLED, SSH_PASSWORD_AUTH, SSH_PORT
from app.httpclient import (
    bearer,
    etcd_client,
    insecure_client,
    kubernetes_client,
    read_service_account_token,
)
from app.models.kubernetes import (
    CertificateInfo,
    ClusterNode,
    EtcdStatus,
    K8sApiEndpoint,
    K8sComponentStatus,
    K8sNodeAddress,
    K8sNodeCondition,
    K8sNodeInfo,
    K8sNodeResources,
    KubernetesOverview,
    SSHInfo,
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


async def _k8s_get(path: str, params: dict | None = None) -> dict | None:
    """GET a path on the API server using the service account credentials."""
    token = await read_service_account_token()
    if not token:
        return None
    try:
        response = await kubernetes_client().get(
            f"https://kubernetes.default.svc{path}",
            headers=bearer(token),
            params=params,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


@ttl_cache(seconds=60)
async def collect_k8s_node_info() -> K8sNodeInfo:
    """Query the Kubernetes API for this node's info using the service account."""
    node_name = os.environ.get("KUBERNETES_NODE_NAME", "")
    if not node_name:
        return K8sNodeInfo()

    data = await _k8s_get(f"/api/v1/nodes/{node_name}")
    if data is None:
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


@ttl_cache(seconds=300)
async def collect_k8s_certificates() -> list[CertificateInfo]:
    """Scan Kubernetes PKI directories for certificates and parse their details."""
    pki_dirs = [
        f"{HOST_ROOT}/etc/kubernetes/pki/",
        f"{HOST_ROOT}/etc/kubernetes/pki/etcd/",
        f"{HOST_ROOT}/system/secrets/etcd/",
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


def _first_int(data: dict, *keys: str) -> int:
    """Read the first key that is present, tolerating etcd's naming.

    The gRPC gateway emits camelCase for message fields but snake_case
    inside the response header, and that has shifted between etcd releases,
    so accept either spelling. Numeric fields arrive as JSON strings because
    they are int64.
    """
    for key in keys:
        if key in data:
            try:
                return int(data[key])
            except (TypeError, ValueError):
                continue
    return 0


def _parse_etcd_status(data: dict) -> EtcdStatus:
    """Build EtcdStatus from a /v3/maintenance/status response."""
    header = data.get("header", {})

    db_size = _first_int(data, "dbSize", "db_size")
    db_in_use = _first_int(data, "dbSizeInUse", "db_size_in_use")
    quota = _first_int(data, "dbSizeQuota", "db_size_quota")

    mb = 1048576
    reclaimable = max(db_size - db_in_use, 0)
    quota_pct = round(db_size / quota * 100, 1) if quota else 0.0

    # etcd stops accepting writes at the quota, and only a defrag returns
    # the free-list to the filesystem, so flag it before it gets there.
    if quota_pct >= 80:
        defrag_severity = "critical"
    elif quota_pct >= 50 and reclaimable > db_in_use:
        defrag_severity = "warning"
    else:
        defrag_severity = "ok"

    member_id = str(_first_int(header, "member_id", "memberId") or "")
    leader_id = str(_first_int(data, "leader") or "")

    return EtcdStatus(
        healthy=True,
        db_size_mb=round(db_size / mb, 1),
        db_size_in_use_mb=round(db_in_use / mb, 1),
        db_size_quota_mb=round(quota / mb, 1),
        quota_used_percent=quota_pct,
        defrag_reclaimable_mb=round(reclaimable / mb, 1),
        defrag_severity=defrag_severity,
        version=str(data.get("version", "")),
        member_id=member_id,
        leader_id=leader_id,
        is_leader=bool(member_id) and member_id == leader_id,
        # raftIndex is top-level, not in the header. Reading header["raft_index"]
        # silently yielded 0 on every release.
        raft_index=_first_int(data, "raftIndex", "raft_index"),
        raft_term=_first_int(data, "raftTerm", "raft_term")
        or _first_int(header, "raft_term", "raftTerm"),
        raft_applied_index=_first_int(data, "raftAppliedIndex", "raft_applied_index"),
    )


async def _etcd_post(path: str) -> dict | None:
    """POST an empty body to an etcd v3 gateway endpoint."""
    client = etcd_client()
    if client is None:
        return None
    try:
        response = await client.post(
            f"https://localhost:2379{path}", json={}, timeout=3
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


@ttl_cache(seconds=300)
async def _collect_etcd_status() -> EtcdStatus:
    """Get detailed etcd metrics via the etcd API."""
    status_data, members_data = await asyncio.gather(
        _etcd_post("/v3/maintenance/status"),
        _etcd_post("/v3/cluster/member/list"),
    )

    status = EtcdStatus()
    if status_data:
        try:
            status = _parse_etcd_status(status_data)
        except (ValueError, KeyError):
            pass

    if members_data:
        for m in members_data.get("members", []):
            status.members.append(
                {
                    "id": str(m.get("ID", "")),
                    "name": m.get("name", ""),
                    "peer_urls": m.get("peerURLs", []),
                    "client_urls": m.get("clientURLs", []),
                }
            )

    return status


@ttl_cache(seconds=300)
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

    # The API server rejects anonymous requests to /healthz with a 401, so
    # an unauthenticated probe reported it permanently Unhealthy on a
    # perfectly working control plane. The others accept the header happily.
    token = await read_service_account_token()

    async def probe(name: str, url: str) -> K8sComponentStatus:
        # etcd needs client certs on Talos; the rest serve self-signed certs
        # on localhost, so verification is off either way.
        client = etcd_client() if name == "etcd" else insecure_client()
        body = ""
        if client is not None:
            try:
                response = await client.get(
                    url, timeout=2, headers={} if name == "etcd" else bearer(token)
                )
                body = response.text
            except httpx.HTTPError:
                body = ""

        if not body.strip():
            health_status, running = "Unknown", False
        elif body.strip().lower() == "ok" or '"health":"true"' in body.lower():
            health_status, running = "Healthy", True
        else:
            health_status, running = "Unhealthy", False

        return K8sComponentStatus(
            name=name,
            running=running,
            health_status=health_status,
            etcd_status=(
                await _collect_etcd_status() if name == "etcd" and running else None
            ),
        )

    results = list(await asyncio.gather(*(probe(n, u) for n, u in components.items())))

    return results


@ttl_cache(seconds=300)
async def collect_k8s_api_endpoint() -> K8sApiEndpoint:
    """Check the Kubernetes API endpoint health."""
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")

    if not host:
        return K8sApiEndpoint()

    token = await read_service_account_token()
    client = kubernetes_client() if token else insecure_client()

    healthy = False
    try:
        response = await client.get(
            f"https://{host}:{port}/healthz", headers=bearer(token), timeout=2
        )
        healthy = "ok" in response.text.lower()
    except httpx.HTTPError:
        healthy = False

    return K8sApiEndpoint(
        url=f"https://{host}:{port}",
        healthy=healthy,
    )


@ttl_cache(seconds=60)
async def collect_cluster_nodes() -> list[ClusterNode]:
    """List all nodes in the cluster with name, IP, role, and readiness."""
    # resourceVersion=0 is served from the API server's watch cache, so
    # listing every node each minute from every node does not turn into a
    # quorum read against etcd.
    data = await _k8s_get("/api/v1/nodes", params={"resourceVersion": "0"})
    if data is None:
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
        ssh_info=SSHInfo(
            enabled=SSH_ENABLED,
            port=SSH_PORT,
            password_auth=SSH_PASSWORD_AUTH,
        ),
    )
