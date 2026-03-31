import os
import re

from app.collectors.base import read_file, run_command, ttl_cache
from app.config import HOST_ROOT
from app.models.node import IPAddress, NodeInfo


@ttl_cache()
async def collect_node_info() -> NodeInfo:
    # Hostname
    hostname = (await read_file(f"{HOST_ROOT}/etc/hostname")).strip()
    if not hostname:
        stdout, _, _ = await run_command(["hostname"])
        hostname = stdout.strip() or "unknown"

    # OS
    os_release = await read_file(f"{HOST_ROOT}/etc/os-release")
    os_name = "Unknown"
    for line in os_release.splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
            break

    # Kernel
    stdout, _, _ = await run_command(["uname", "-r"])
    kernel = stdout.strip()

    # CPU info
    stdout, _, _ = await run_command(["lscpu"])
    cpu_model = ""
    cpu_sockets = 1
    cpu_cores_per = 1
    cpu_threads = 1
    for line in stdout.splitlines():
        if "Model name:" in line:
            cpu_model = line.split(":", 1)[1].strip()
        elif "Socket(s):" in line:
            cpu_sockets = int(line.split(":", 1)[1].strip())
        elif "Core(s) per socket:" in line:
            cpu_cores_per = int(line.split(":", 1)[1].strip())
        elif "CPU(s):" in line and "NUMA" not in line and "On-line" not in line:
            cpu_threads = int(line.split(":", 1)[1].strip())
    cpu_cores = cpu_sockets * cpu_cores_per

    # RAM
    meminfo = await read_file("/proc/meminfo")
    ram_kb = 0
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            ram_kb = int(re.search(r"\d+", line).group())  # type: ignore[union-attr]
            break
    ram_gb = round(ram_kb / 1048576, 1)

    # Uptime
    uptime_raw = await read_file("/proc/uptime")
    uptime_secs = float(uptime_raw.split()[0]) if uptime_raw else 0
    days = int(uptime_secs // 86400)
    hours = int((uptime_secs % 86400) // 3600)
    minutes = int((uptime_secs % 3600) // 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    uptime_human = " ".join(parts)

    # Load
    loadavg = await read_file("/proc/loadavg")
    load_parts = loadavg.split()
    load_1m = float(load_parts[0]) if len(load_parts) > 0 else 0
    load_5m = float(load_parts[1]) if len(load_parts) > 1 else 0
    load_15m = float(load_parts[2]) if len(load_parts) > 2 else 0

    # Talos version
    talos_version = (await read_file(f"{HOST_ROOT}/etc/talos/version")).strip() or None

    # K8s node name
    k8s_node = os.environ.get("KUBERNETES_NODE_NAME")

    # IP addresses
    ip_addresses = await _collect_ip_addresses()

    return NodeInfo(
        hostname=hostname,
        kubernetes_node_name=k8s_node,
        talos_version=talos_version,
        os_name=os_name,
        kernel_version=kernel,
        uptime_seconds=uptime_secs,
        uptime_human=uptime_human,
        load_1m=load_1m,
        load_5m=load_5m,
        load_15m=load_15m,
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        cpu_threads=cpu_threads,
        ram_total_gb=ram_gb,
        ip_addresses=ip_addresses,
    )


async def _collect_ip_addresses() -> list[IPAddress]:
    stdout, _, rc = await run_command(["ip", "-j", "addr", "show"])
    if rc != 0 or not stdout.strip():
        return []
    import json

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    result: list[IPAddress] = []
    skip = {"lo", "cni0", "flannel.1"}
    for iface in data:
        name = iface.get("ifname", "")
        if name in skip or name.startswith(("veth", "cali", "lxc")):
            continue
        for addr_info in iface.get("addr_info", []):
            result.append(
                IPAddress(
                    interface=name,
                    address=addr_info.get("local", ""),
                    prefix_length=addr_info.get("prefixlen", 0),
                    family=addr_info.get("family", ""),
                )
            )
    return result
