from app.collectors.base import read_file, run_command, ttl_cache
from app.config import HOST_PROC
from app.models.hardware import CPUInfo


def _count(val: str | None) -> int | None:
    """Parse an lscpu count; ARM boards print '-' where topology is unknown."""
    try:
        return int(val) if val else None
    except ValueError:
        return None


def parse_lscpu(stdout: str) -> tuple[str, int, int, int]:
    """Return (model, sockets, cores_per_socket, threads) from lscpu output.

    On big.LITTLE SoCs lscpu prints one block per core type, each starting
    with 'Model name' and counting cores per cluster rather than per socket,
    so blocks are kept apart and their cores summed.
    """
    fields: dict[str, str] = {}
    blocks: list[dict[str, str]] = [{}]
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        if key == "Model name" and "Model name" in blocks[-1]:
            blocks.append({})
        blocks[-1][key] = val
        fields.setdefault(key, val)

    cores_total = 0
    for b in blocks:
        per = _count(b.get("Core(s) per socket")) or _count(
            b.get("Core(s) per cluster")
        )
        if per:
            cores_total += per * (
                _count(b.get("Socket(s)")) or _count(b.get("Cluster(s)")) or 1
            )

    sockets = _count(fields.get("Socket(s)")) or 1
    cores_per = max(cores_total // sockets, 1)
    threads = _count(fields.get("CPU(s)")) or sockets * cores_per
    models = dict.fromkeys(b["Model name"] for b in blocks if b.get("Model name"))
    return " + ".join(models), sockets, cores_per, threads


@ttl_cache()
async def collect_cpu() -> CPUInfo:
    stdout, _, _ = await run_command(["lscpu"])
    model, sockets, cores_per, threads = parse_lscpu(stdout)
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            fields.setdefault(key.strip(), val.strip())

    # Flags from /proc/cpuinfo ('Features' on arm64)
    cpuinfo = await read_file(f"{HOST_PROC}/cpuinfo")
    flags: list[str] = []
    for line in cpuinfo.splitlines():
        if line.startswith(("flags", "Features")) and ":" in line:
            flags = line.split(":", 1)[1].strip().split()
            break

    return CPUInfo(
        model=model or "Unknown",
        sockets=sockets,
        cores_per_socket=cores_per,
        threads=threads,
        architecture=fields.get("Architecture", "Unknown"),
        flags=flags[:50],  # Limit to avoid huge response
    )
