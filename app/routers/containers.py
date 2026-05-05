import asyncio
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.collectors.base import run_command
from app.collectors.containers import (
    CRI_ENDPOINT,
    collect_containers,
    collect_system_containers,
    collect_workload_containers,
)
from app.config import HOST_ROOT
from app.models.containers import (
    ContainersOverview,
    SystemContainer,
    WorkloadContainer,
)

router = APIRouter(tags=["containers"])


@router.get("", response_model=ContainersOverview)
async def get_containers():
    """All containers: system services + workloads."""
    return await collect_containers()


@router.get("/system", response_model=list[SystemContainer])
async def get_system_containers():
    """Talos system service containers (etcd, kubelet, apiserver, etc.)."""
    return await collect_system_containers()


@router.get("/workloads", response_model=list[WorkloadContainer])
async def get_workload_containers():
    """Kubernetes workload containers on this node."""
    return await collect_workload_containers()


async def _get_container_log_path(container_id: str) -> str | None:
    """Get the host log file path for a container via crictl inspect."""
    stdout, _, rc = await run_command(
        ["crictl", "--runtime-endpoint", CRI_ENDPOINT, "inspect", container_id],
        timeout=5,
    )
    if rc != 0 or not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
        log_path = data.get("status", {}).get("logPath", "")
        if log_path:
            # Convert host path to our mount: /var/log/pods/... -> /host/var/log/pods/...
            return f"{HOST_ROOT}{log_path}"
    except (json.JSONDecodeError, KeyError):
        pass
    return None


@router.websocket("/{container_id}/logs")
async def container_logs(websocket: WebSocket, container_id: str):
    """Live log stream for a container via WebSocket."""
    # Sanitize: container IDs are hex strings
    if not all(c in "0123456789abcdef" for c in container_id):
        await websocket.close(code=1008, reason="Invalid container ID")
        return

    await websocket.accept()

    # Get log file path from crictl inspect, then tail -f it
    log_path = await _get_container_log_path(container_id)
    if not log_path:
        await websocket.send_text("[Error: could not find log path for container]\n")
        await websocket.close()
        return

    # Defence-in-depth: crictl reports the log path, but a malicious workload
    # could supply a path or symlink pointing outside the pod log tree. Resolve
    # symlinks and require the result to live under HOST_ROOT/var/log/pods.
    allowed_root = os.path.realpath(f"{HOST_ROOT}/var/log/pods")
    try:
        real_log_path = os.path.realpath(log_path)
    except OSError:
        real_log_path = ""
    if not real_log_path.startswith(allowed_root + os.sep):
        await websocket.send_text("[Error: log path outside allowed pod log tree]\n")
        await websocket.close()
        return

    process = await asyncio.create_subprocess_exec(
        "tail",
        "-n",
        "200",
        "-f",
        real_log_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        async for line in process.stdout:  # type: ignore[union-attr]
            await websocket.send_text(line.decode(errors="replace"))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
