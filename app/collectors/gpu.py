import os

from app.collectors.base import run_command, ttl_cache
from app.config import HOST_ROOT
from app.models.hardware import GPUInfo


def _find_nvidia_smi() -> str:
    """Find nvidia-smi binary — check host paths first, then container PATH."""
    host_paths = [
        f"{HOST_ROOT}/usr/local/bin/nvidia-smi",
        f"{HOST_ROOT}/usr/bin/nvidia-smi",
    ]
    for path in host_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return "nvidia-smi"  # fallback to container PATH


@ttl_cache()
async def collect_gpus() -> list[GPUInfo]:
    nvidia_smi = _find_nvidia_smi()
    stdout, _, rc = await run_command(
        [
            nvidia_smi,
            "--query-gpu=index,name,driver_version,memory.total,memory.used,temperature.gpu,utilization.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
    )
    if rc != 0:
        return []

    gpus: list[GPUInfo] = []
    for line in stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue
        try:
            gpus.append(
                GPUInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    driver_version=parts[2],
                    memory_total_mb=int(float(parts[3])) if parts[3] != "[N/A]" else 0,
                    memory_used_mb=int(float(parts[4])) if parts[4] != "[N/A]" else 0,
                    temperature_c=int(parts[5]) if parts[5] != "[N/A]" else None,
                    utilization_percent=(
                        int(parts[6]) if parts[6] != "[N/A]" else None
                    ),
                    power_draw_w=(float(parts[7]) if parts[7] != "[N/A]" else None),
                )
            )
        except (ValueError, IndexError):
            continue
    return gpus
