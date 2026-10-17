import asyncio
import json
import os
import re
from urllib.parse import urlsplit

import anyio
from fastapi import APIRouter, WebSocket

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

# crictl resolves any unique prefix, so a short ID would let a caller walk
# every container on the node one hex digit at a time. The UI always sends
# the full ID.
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


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
    # Browsers apply no CORS to WebSocket handshakes, so without this any
    # page an operator visits could open the stream and read pod logs.
    # Non-browser clients send no Origin and are left to the auth layer.
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    if origin is not None and urlsplit(origin).netloc.lower() != host.lower():
        await websocket.close(code=1008, reason="Cross-origin request refused")
        return

    if not _CONTAINER_ID.fullmatch(container_id):
        await websocket.close(code=1008, reason="Invalid container ID")
        return

    await websocket.accept()

    # Get log file path from crictl inspect, then tail -F it
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

    # -F follows the name, not the descriptor: kubelet rotates by renaming
    # 0.log away and creating a new one, and -f would keep reading the old
    # inode and go silent. stderr is discarded since nothing reads it.
    process = await asyncio.create_subprocess_exec(
        "tail",
        "-n",
        "200",
        "-F",
        real_log_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        async with anyio.create_task_group() as tg:

            async def pump() -> None:
                try:
                    async for line in process.stdout:  # type: ignore[union-attr]
                        await websocket.send_text(line.decode(errors="replace"))
                except Exception:
                    pass  # client gone mid-send, or tail closed its output
                tg.cancel_scope.cancel()

            async def until_disconnect() -> None:
                # Sending alone never notices a closed client on a quiet
                # container, which leaked the tail and held up shutdown.
                # Reading does.
                try:
                    while (await websocket.receive())["type"] != "websocket.disconnect":
                        pass
                except Exception:
                    pass
                tg.cancel_scope.cancel()

            tg.start_soon(pump)
            tg.start_soon(until_disconnect)
    finally:
        # No await needed, so it happens even if this handler is cancelled.
        if process.returncode is None:
            process.kill()
        with anyio.CancelScope(shield=True):
            await process.wait()
