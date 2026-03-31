import json
import os

from app.collectors.base import read_file, run_command, ttl_cache
from app.config import HOST_ROOT
from app.models.hardware import NICInfo


@ttl_cache()
async def collect_nics() -> list[NICInfo]:
    nics: list[NICInfo] = []
    skip_prefixes = ("lo", "veth", "cali", "lxc", "cni", "flannel", "docker", "br-")

    try:
        ifaces = os.listdir("/sys/class/net/")
    except OSError:
        return []

    for name in sorted(ifaces):
        if name == "lo" or any(name.startswith(p) for p in skip_prefixes):
            continue

        # MAC
        mac = (await read_file(f"/sys/class/net/{name}/address")).strip()

        # Driver
        driver = ""
        driver_link = f"/sys/class/net/{name}/device/driver"
        try:
            driver = os.path.basename(os.readlink(driver_link))
        except OSError:
            pass

        # Stats
        stats_dir = f"/sys/class/net/{name}/statistics"
        rx_errors = int((await read_file(f"{stats_dir}/rx_errors")).strip() or "0")
        tx_errors = int((await read_file(f"{stats_dir}/tx_errors")).strip() or "0")
        rx_dropped = int((await read_file(f"{stats_dir}/rx_dropped")).strip() or "0")
        tx_dropped = int((await read_file(f"{stats_dir}/tx_dropped")).strip() or "0")
        rx_crc = int((await read_file(f"{stats_dir}/rx_crc_errors")).strip() or "0")
        tx_carrier = int(
            (await read_file(f"{stats_dir}/tx_carrier_errors")).strip() or "0"
        )

        # IPs
        ips: list[str] = []
        stdout, _, rc = await run_command(["ip", "-j", "addr", "show", name])
        if rc == 0 and stdout.strip():
            try:
                for iface in json.loads(stdout):
                    for a in iface.get("addr_info", []):
                        ips.append(f"{a['local']}/{a['prefixlen']}")
            except (json.JSONDecodeError, KeyError):
                pass

        # ethtool for speed/duplex/link
        speed = ""
        duplex = ""
        link_detected = False
        stdout, _, rc = await run_command(["ethtool", name])
        if rc == 0:
            for line in stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("Speed:"):
                    speed = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("Duplex:"):
                    duplex = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("Link detected:"):
                    link_detected = "yes" in stripped.lower()

        nics.append(
            NICInfo(
                name=name,
                mac=mac,
                driver=driver,
                speed=speed,
                duplex=duplex,
                link_detected=link_detected,
                ip_addresses=ips,
                rx_errors=rx_errors,
                tx_errors=tx_errors,
                rx_dropped=rx_dropped,
                tx_dropped=tx_dropped,
                rx_crc_errors=rx_crc,
                tx_carrier_errors=tx_carrier,
            )
        )

    return nics


@ttl_cache()
async def collect_connectivity() -> dict:
    # DNS config
    resolv = await read_file(f"{HOST_ROOT}/etc/resolv.conf")
    dns_servers = [
        line.split()[1]
        for line in resolv.splitlines()
        if line.strip().startswith("nameserver")
    ]

    # DNS resolution
    dns_ok = False
    dns_result = ""
    stdout, _, rc = await run_command(["dig", "+short", "google.com"])
    if rc == 0 and stdout.strip():
        for line in stdout.splitlines():
            if line.strip() and line.strip()[0].isdigit():
                dns_result = line.strip()
                dns_ok = True
                break

    # Internet
    internet_ok = False
    stdout, _, rc = await run_command(
        [
            "curl",
            "-sSI",
            "--max-time",
            "5",
            "--connect-timeout",
            "5",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "https://www.google.com",
        ]
    )
    if rc == 0 and stdout.strip().startswith(("2", "3")):
        internet_ok = True

    # K8s API
    k8s_ok = False
    k8s_host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
    k8s_port = os.environ.get("KUBERNETES_SERVICE_PORT", "")
    if k8s_host:
        stdout, _, rc = await run_command(
            [
                "curl",
                "-sk",
                "--max-time",
                "5",
                f"https://{k8s_host}:{k8s_port}/healthz",
            ]
        )
        if "ok" in stdout:
            k8s_ok = True

    # Default gateway
    stdout, _, rc = await run_command(["ip", "route", "show", "default"])
    gateway = ""
    if rc == 0:
        parts = stdout.split()
        if len(parts) >= 3:
            gateway = parts[2]

    return {
        "dns_servers": dns_servers,
        "dns_ok": dns_ok,
        "dns_result": dns_result,
        "internet_ok": internet_ok,
        "kubernetes_api_ok": k8s_ok,
        "default_gateway": gateway,
    }
