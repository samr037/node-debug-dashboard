import math

from app.collectors.base import run_command, ttl_cache
from app.models.hardware import GPUInfo


def _num(val: str, cast: type = float) -> float | int | None:
    """Parse one nvidia-smi field; sentinels like '[N/A]', '[Not Supported]',
    '[Unknown Error]' or '[GPU is lost]' become None instead of dropping the GPU."""
    try:
        num = float(val)
    except ValueError:
        return None
    # nan/inf would crash int() or the JSON encoder
    return cast(num) if math.isfinite(num) else None


@ttl_cache()
async def collect_gpus() -> list[GPUInfo]:
    # nvidia-smi must run in the host's mount namespace (Talos uses custom glibc)
    stdout, _, rc = await run_command(
        [
            "nsenter",
            "--target",
            "1",
            "--mount",
            "--uts",
            "--ipc",
            "--net",
            "--pid",
            "--",
            "nvidia-smi",
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
                    memory_total_mb=_num(parts[3], int) or 0,
                    memory_used_mb=_num(parts[4], int) or 0,
                    temperature_c=_num(parts[5], int),
                    utilization_percent=_num(parts[6], int),
                    power_draw_w=_num(parts[7]),
                )
            )
        except (ValueError, IndexError):
            continue
    return gpus
