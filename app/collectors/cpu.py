from app.collectors.base import read_file, run_command, ttl_cache
from app.models.hardware import CPUInfo


@ttl_cache()
async def collect_cpu() -> CPUInfo:
    stdout, _, _ = await run_command(["lscpu"])

    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            fields[key.strip()] = val.strip()

    # Flags from /proc/cpuinfo
    cpuinfo = await read_file("/proc/cpuinfo")
    flags: list[str] = []
    for line in cpuinfo.splitlines():
        if line.startswith("flags"):
            flags = line.split(":", 1)[1].strip().split()
            break

    sockets = int(fields.get("Socket(s)", "1"))
    cores_per = int(fields.get("Core(s) per socket", "1"))

    return CPUInfo(
        model=fields.get("Model name", "Unknown"),
        sockets=sockets,
        cores_per_socket=cores_per,
        threads=int(fields.get("CPU(s)", str(sockets * cores_per))),
        architecture=fields.get("Architecture", "Unknown"),
        flags=flags[:50],  # Limit to avoid huge response
    )
