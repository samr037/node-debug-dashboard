from app.collectors.base import run_command, ttl_cache
from app.models.hardware import GPUInfo


@ttl_cache()
async def collect_gpus() -> list[GPUInfo]:
    stdout, _, rc = await run_command(
        [
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
