"""Behaviour when the pod is not on the host network (hostNetwork=false)."""

import pytest

from app.collectors import base, kubernetes, network
from app.routers import warnings as warnings_router


def test_in_host_network_namespace(monkeypatch):
    links = {"/proc/self/ns/net": "net:[1]", "/host-proc/1/ns/net": "net:[1]"}
    monkeypatch.setattr(base, "HOST_PROC", "/host-proc")
    monkeypatch.setattr(base.os, "readlink", lambda p: links[p])
    assert base.in_host_network_namespace()

    links["/host-proc/1/ns/net"] = "net:[2]"
    assert not base.in_host_network_namespace()


def test_unreadable_host_namespace_assumes_host_network(monkeypatch):
    def denied(path):
        raise PermissionError(path)

    monkeypatch.setattr(base.os, "readlink", denied)
    assert base.in_host_network_namespace()


async def test_components_unavailable_off_host_network(monkeypatch, clear_caches):
    monkeypatch.setattr(kubernetes, "in_host_network_namespace", lambda: False)
    components = await kubernetes.collect_k8s_components()
    assert components and {c.health_status for c in components} == {"Unavailable"}
    assert not any(c.running for c in components)


async def test_dns_uses_pod_resolver_off_host_network(monkeypatch, clear_caches):
    read = []

    async def read_file(path):
        read.append(path)
        return "nameserver 10.96.0.10\n"

    async def dig(target):
        return "1.2.3.4"

    class Client:
        async def head(self, *a, **k):
            raise network.httpx.ConnectError("offline")

    monkeypatch.setattr(network, "in_host_network_namespace", lambda: False)
    monkeypatch.setattr(network, "read_file", read_file)
    monkeypatch.setattr(network, "_dig", dig)
    monkeypatch.setattr(network, "public_client", lambda: Client())
    result = await network.collect_connectivity()
    assert read[0] == "/etc/resolv.conf"
    assert result["dns_servers"] == ["10.96.0.10"]


@pytest.mark.parametrize("host_net", [True, False])
async def test_warning_says_hostnetwork_is_off(monkeypatch, clear_caches, host_net):
    monkeypatch.setattr(warnings_router, "in_host_network_namespace", lambda: host_net)

    async def none():
        return []

    async def no_node():
        return None

    for name in (
        "collect_dmesg_warnings", "collect_all_smart", "collect_sensors",
        "collect_nics", "collect_disk_usage", "collect_k8s_certificates",
        "collect_talos_certificates", "collect_k8s_components",
    ):  # fmt: skip
        monkeypatch.setattr(warnings_router, name, none)
    monkeypatch.setattr(warnings_router, "collect_memory", no_node)
    monkeypatch.setattr(warnings_router, "collect_k8s_node_info", no_node)
    out = await warnings_router.get_warnings()
    info = [w for w in out if "hostNetwork is off" in w.message]
    assert bool(info) is not host_net
    assert all(w.severity == "info" for w in info)
