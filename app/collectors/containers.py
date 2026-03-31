import json
import time

from app.collectors.base import run_command, ttl_cache
from app.config import HOST_ROOT
from app.models.containers import (
    ContainerStats,
    ContainersOverview,
    SystemContainer,
    WorkloadContainer,
)

CRI_ENDPOINT = f"unix://{HOST_ROOT}/run/containerd/containerd.sock"

SYSTEM_NAMESPACES = {"kube-system"}
SYSTEM_CONTAINER_NAMES = {
    "etcd",
    "kube-apiserver",
    "kube-scheduler",
    "kube-controller-manager",
    "kube-proxy",
}


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def _uptime_from_created(created_ns: int) -> str:
    if created_ns <= 0:
        return ""
    secs = int(time.time() - created_ns / 1_000_000_000)
    if secs < 0:
        return ""
    days = secs // 86400
    hours = (secs % 86400) // 3600
    mins = (secs % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    return " ".join(parts)


@ttl_cache()
async def _get_pods() -> dict[str, dict]:
    """Get pod metadata keyed by pod ID."""
    stdout, _, rc = await run_command(
        ["crictl", "--runtime-endpoint", CRI_ENDPOINT, "pods", "-o", "json"],
        timeout=15,
    )
    if rc != 0 or not stdout.strip():
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    pods: dict[str, dict] = {}
    for item in data.get("items", []):
        pod_id = item.get("id", "")
        pods[pod_id] = {
            "name": item.get("metadata", {}).get("name", ""),
            "namespace": item.get("metadata", {}).get("namespace", ""),
            "state": item.get("state", ""),
        }
    return pods


@ttl_cache()
async def _get_containers() -> list[dict]:
    """Get container list."""
    stdout, _, rc = await run_command(
        ["crictl", "--runtime-endpoint", CRI_ENDPOINT, "ps", "-a", "-o", "json"],
        timeout=15,
    )
    if rc != 0 or not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    return data.get("containers", [])


@ttl_cache()
async def _get_stats() -> dict[str, dict]:
    """Get container stats keyed by container ID."""
    stdout, _, rc = await run_command(
        ["crictl", "--runtime-endpoint", CRI_ENDPOINT, "stats", "-o", "json"],
        timeout=15,
    )
    if rc != 0 or not stdout.strip():
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    stats: dict[str, dict] = {}
    for item in data.get("stats", []):
        cid = item.get("attributes", {}).get("id", "")
        cpu = item.get("cpu", {})
        mem = item.get("memory", {})
        stats[cid] = {
            "cpu_nano": cpu.get("usageCoreNanoSeconds", {}).get("value", 0),
            "memory_bytes": mem.get("workingSetBytes", {}).get("value", 0),
        }
    return stats


def _build_stats(stats_map: dict[str, dict], container_id: str) -> ContainerStats:
    s = stats_map.get(container_id, {})
    mem_bytes = int(s.get("memory_bytes", 0))
    # CPU is cumulative nanoseconds — we show it as-is for now
    # A proper rate requires two samples; for the dashboard we show memory
    return ContainerStats(
        cpu_percent=0.0,
        memory_bytes=mem_bytes,
        memory_human=_human_bytes(mem_bytes),
    )


@ttl_cache()
async def collect_system_containers() -> list[SystemContainer]:
    containers = await _get_containers()
    pods = await _get_pods()
    stats = await _get_stats()

    result: list[SystemContainer] = []
    for c in containers:
        cid = c.get("id", "")
        name = c.get("metadata", {}).get("name", "")
        pod_id = c.get("podSandboxId", "")
        pod = pods.get(pod_id, {})
        ns = pod.get("namespace", "")

        is_system = ns in SYSTEM_NAMESPACES or name in SYSTEM_CONTAINER_NAMES
        if not is_system:
            continue

        created_ns = int(c.get("createdAt", 0))
        image_ref = c.get("imageRef", "") or c.get("image", {}).get("image", "")

        result.append(
            SystemContainer(
                name=name,
                container_id=cid,
                state=c.get("state", ""),
                image=image_ref.split("@")[0] if "@" in image_ref else image_ref,
                uptime=_uptime_from_created(created_ns),
                created_at=str(created_ns),
                stats=_build_stats(stats, cid),
            )
        )
    return sorted(result, key=lambda x: x.name)


@ttl_cache()
async def collect_workload_containers() -> list[WorkloadContainer]:
    containers = await _get_containers()
    pods = await _get_pods()
    stats = await _get_stats()

    result: list[WorkloadContainer] = []
    for c in containers:
        cid = c.get("id", "")
        name = c.get("metadata", {}).get("name", "")
        pod_id = c.get("podSandboxId", "")
        pod = pods.get(pod_id, {})
        ns = pod.get("namespace", "")

        is_system = ns in SYSTEM_NAMESPACES or name in SYSTEM_CONTAINER_NAMES
        if is_system:
            continue

        created_ns = int(c.get("createdAt", 0))
        image_ref = c.get("imageRef", "") or c.get("image", {}).get("image", "")

        result.append(
            WorkloadContainer(
                pod_name=pod.get("name", ""),
                namespace=ns,
                container_name=name,
                container_id=cid,
                image=image_ref.split("@")[0] if "@" in image_ref else image_ref,
                state=c.get("state", ""),
                uptime=_uptime_from_created(created_ns),
                created_at=str(created_ns),
                stats=_build_stats(stats, cid),
            )
        )
    return sorted(result, key=lambda x: (x.namespace, x.pod_name, x.container_name))


@ttl_cache()
async def collect_containers() -> ContainersOverview:
    return ContainersOverview(
        system_containers=await collect_system_containers(),
        workload_containers=await collect_workload_containers(),
    )
