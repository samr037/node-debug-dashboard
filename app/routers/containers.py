import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.collectors.containers import (
    CRI_ENDPOINT,
    collect_containers,
    collect_system_containers,
    collect_workload_containers,
)
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


@router.websocket("/{container_id}/logs")
async def container_logs(websocket: WebSocket, container_id: str):
    """Live log stream for a container via WebSocket."""
    # Sanitize: container IDs are hex strings
    if not all(c in "0123456789abcdef" for c in container_id):
        await websocket.close(code=1008, reason="Invalid container ID")
        return

    await websocket.accept()

    process = await asyncio.create_subprocess_exec(
        "crictl",
        "--runtime-endpoint",
        CRI_ENDPOINT,
        "logs",
        "-f",
        "--tail",
        "200",
        container_id,
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
