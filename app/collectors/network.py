import asyncio
import json
import os

import httpx

from app.collectors.base import (
    in_host_network_namespace,
    read_file,
    run_command,
    ttl_cache,
)
from app.config import HOST_ROOT, HOST_SYS
from app.httpclient import (
    bearer,
    insecure_client,
    kubernetes_api_base,
    kubernetes_client,
    public_client,
    read_service_account_token,
)
from app.models.hardware import NICInfo


@ttl_cache()
async def collect_nics() -> list[NICInfo]:
    nics: list[NICInfo] = []
    skip_prefixes = ("lo", "veth", "cali", "lxc", "cni", "flannel", "docker", "br-")

    try:
        ifaces = os.listdir(f"{HOST_SYS}/class/net/")
    except OSError:
        return []

    for name in sorted(ifaces):
        if name == "lo" or any(name.startswith(p) for p in skip_prefixes):
            continue

        # MAC
        mac = (await read_file(f"{HOST_SYS}/class/net/{name}/address")).strip()

        # Driver
        driver = ""
        driver_link = f"{HOST_SYS}/class/net/{name}/device/driver"
        try:
            driver = os.path.basename(os.readlink(driver_link))
        except OSError:
            pass

        # Stats
        stats_dir = f"{HOST_SYS}/class/net/{name}/statistics"
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


def _parse_nameservers(resolv: str) -> list[str]:
    servers: list[str] = []
    for line in resolv.splitlines():
        parts = line.split()
        # A bare "nameserver" line has no address; skip it.
        if len(parts) >= 2 and parts[0] == "nameserver":
            servers.append(parts[1])
    return servers


async def _dig(server: str | None) -> str:
    """First A record for google.com from `server` (or the pod's resolver)."""
    cmd = ["dig", "+short", "+time=2", "+tries=1", "google.com"]
    if server:
        cmd.insert(1, server)
    stdout, _, rc = await run_command(cmd)
    if rc != 0:
        return ""
    for line in stdout.splitlines():
        if line.strip() and line.strip()[0].isdigit():
            return line.strip()
    return ""


def _parse_default_gateway(stdout: str) -> str:
    """Gateway(s) out of `ip route show default`.

    Handles "default via X dev Y", ECMP routes whose gateways sit on
    "nexthop via X" lines, and device-only routes ("default dev wg0"),
    which have no gateway address at all.
    """
    tokens = stdout.split()
    gateways: list[str] = []
    for prev, tok in zip(tokens, tokens[1:]):
        if prev == "via" and tok not in gateways:
            gateways.append(tok)
    if gateways:
        return ", ".join(gateways)
    for prev, tok in zip(tokens, tokens[1:]):
        if prev == "dev":
            return f"dev {tok}"
    return ""


@ttl_cache()
async def collect_connectivity() -> dict:
    # DNS config
    # The host's resolvers, unless the pod has its own network namespace:
    # then a host-local resolver such as 127.0.0.53 is unreachable from here
    # and would always fail, so test the pod's own resolvers instead.
    host_net = in_host_network_namespace()
    resolv = await read_file(f"{HOST_ROOT if host_net else ''}/etc/resolv.conf")
    dns_servers = _parse_nameservers(resolv)

    # DNS resolution, against the host nameservers listed above. A bare
    # `dig` uses the pod's resolv.conf, which with ClusterFirstWithHostNet
    # is CoreDNS, so "OK" used to sit next to servers it never tested.
    dns_ok = False
    dns_result = ""
    dns_failed_servers: list[str] = []
    targets = [f"@{s}" for s in dns_servers] or [None]
    results = await asyncio.gather(*(_dig(t) for t in targets))
    for target, answer in zip(targets, results):
        if answer:
            if not dns_ok:
                dns_result = answer
            dns_ok = True
        elif target is not None:
            dns_failed_servers.append(target[1:])

    # Internet
    internet_ok = False
    try:
        response = await public_client().head(
            "https://www.google.com", timeout=5, follow_redirects=False
        )
        internet_ok = 200 <= response.status_code < 400
    except httpx.HTTPError:
        internet_ok = False

    # K8s API
    k8s_ok = False
    if os.environ.get("KUBERNETES_SERVICE_HOST", ""):
        # /healthz is not always an anonymous endpoint, so the service
        # account token is sent, but only over a client that verifies the
        # API server against the SA CA. Without that CA (or without a
        # token) the probe goes out anonymously and unverified, never with
        # the token: insecure_client() must not carry it.
        try:
            token = await read_service_account_token()
            client = kubernetes_client() if token else None
            if client is None:
                client, token = insecure_client(), ""
            response = await client.get(
                f"{kubernetes_api_base()}/healthz",
                timeout=5,
                headers=bearer(token),
            )
            k8s_ok = (
                response.status_code == 200 and response.text.strip().lower() == "ok"
            )
        except httpx.HTTPError:
            k8s_ok = False

    # Default gateway
    stdout, _, rc = await run_command(["ip", "route", "show", "default"])
    gateway = _parse_default_gateway(stdout) if rc == 0 else ""

    return {
        "dns_servers": dns_servers,
        "dns_ok": dns_ok,
        "dns_result": dns_result,
        "dns_failed_servers": dns_failed_servers,
        "internet_ok": internet_ok,
        "kubernetes_api_ok": k8s_ok,
        "default_gateway": gateway,
    }
