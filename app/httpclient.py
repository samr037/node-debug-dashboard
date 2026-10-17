"""Shared async HTTP clients.

Collectors used to shell out to curl for every request, which cost a
subprocess per call — around fifteen per Kubernetes refresh — and, worse,
put the service account token in the command line of that process. This
app publishes /proc/<pid>/cmdline through /api/processes, so it was capable
of leaking its own credentials to an unauthenticated caller.

Clients are built lazily and reused, so connections are pooled and the
certificate files under HOST_ROOT are not touched at import time.
"""

import os
import ssl
from collections.abc import Callable

import httpx

from app.config import COMMAND_TIMEOUT, HOST_ROOT

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
SA_TOKEN_PATH = f"{SA_DIR}/token"
SA_CA_PATH = f"{SA_DIR}/ca.crt"

# Talos keeps the etcd client certs here.
ETCD_CA = f"{HOST_ROOT}/system/secrets/etcd/ca.crt"
ETCD_CERT = f"{HOST_ROOT}/system/secrets/etcd/admin.crt"
ETCD_KEY = f"{HOST_ROOT}/system/secrets/etcd/admin.key"

_clients: dict[str, httpx.AsyncClient] = {}
# mtimes of the files each certificate-backed client was built from.
_stamps: dict[str, tuple[int, ...]] = {}
# Clients replaced after a cert rotation. Requests may still be in flight on
# them, so they are only closed at shutdown.
_retired: list[httpx.AsyncClient] = []


def _client(key: str, **kwargs) -> httpx.AsyncClient:
    client = _clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=COMMAND_TIMEOUT, **kwargs)
        _clients[key] = client
    return client


def _cert_client(
    key: str, files: tuple[str, ...], build: Callable[[], ssl.SSLContext]
) -> httpx.AsyncClient | None:
    """A client verified with certs read from disk, or None if they are unusable.

    The SSL context is only rebuilt when one of the files changes. Building it
    on every call parsed PEM files on the event loop and then threw the result
    away, since _client() returned the cached client regardless, so a rotated
    cert was never picked up.
    """
    try:
        stamp = tuple(os.stat(f).st_mtime_ns for f in files)
    except OSError:
        return None
    client = _clients.get(key)
    if client is not None and not client.is_closed and _stamps.get(key) == stamp:
        return client
    try:
        context = build()
    except (OSError, ssl.SSLError):
        return None
    if client is not None and not client.is_closed:
        _retired.append(client)
    client = httpx.AsyncClient(timeout=COMMAND_TIMEOUT, verify=context)
    _clients[key] = client
    _stamps[key] = stamp
    return client


def kubernetes_client() -> httpx.AsyncClient | None:
    """Talks to the API server, verified against the service account CA.

    Returns None when the CA cannot be loaded. There is deliberately no
    fallback to verify=False: callers send the service account token, and the
    token can be mounted without ca.crt (custom projected volume, permissions).
    """
    return _cert_client(
        "kubernetes",
        (SA_CA_PATH,),
        lambda: ssl.create_default_context(cafile=SA_CA_PATH),
    )


def _local_apiserver_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=SA_CA_PATH)
    # The serving cert need not list "localhost"; the cluster CA signature is
    # what proves the peer is the API server and not whatever bound 6443.
    context.check_hostname = False
    return context


def local_apiserver_client() -> httpx.AsyncClient | None:
    """For the kube-apiserver probe on localhost:6443, which gets the token.

    Nothing listens on 6443 on a worker, so an unverified client would hand
    the token to any host-network process that binds it. None without a CA.
    """
    return _cert_client("apiserver-local", (SA_CA_PATH,), _local_apiserver_context)


def kubernetes_api_base() -> str:
    """Base URL of the API server.

    Uses the service IP Kubernetes injects into every pod. The DNS name only
    resolves from a hostNetwork pod with dnsPolicy ClusterFirstWithHostNet,
    and not at all while CoreDNS is down. The service IP is in the API
    server certificate's SANs, so verification still holds.
    """
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
    if not host:
        return "https://kubernetes.default.svc"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 literal
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "") or "443"
    return f"https://{host}:{port}"


def insecure_client() -> httpx.AsyncClient:
    """For localhost component health probes served with self-signed certs.

    Never send the service account token through this client.
    """
    return _client("insecure", verify=False)


def public_client() -> httpx.AsyncClient:
    """For outbound internet reachability checks."""
    return _client("public")


def _etcd_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ETCD_CA)
    context.load_cert_chain(ETCD_CERT, ETCD_KEY)
    # etcd's peer cert is issued for the member, not "localhost".
    context.check_hostname = False
    return context


def etcd_client() -> httpx.AsyncClient | None:
    """Client-certificate authenticated etcd client, or None if certs absent."""
    return _cert_client("etcd", (ETCD_CA, ETCD_CERT, ETCD_KEY), _etcd_context)


async def close_clients() -> None:
    """Release pooled connections on shutdown."""
    for client in [*_clients.values(), *_retired]:
        if not client.is_closed:
            await client.aclose()
    _clients.clear()
    _stamps.clear()
    _retired.clear()


async def read_service_account_token() -> str:
    """Read the projected token fresh — the kubelet rotates it in place."""
    import anyio

    try:
        return (await anyio.Path(SA_TOKEN_PATH).read_text()).strip()
    except OSError:
        return ""


def bearer(token: str) -> dict[str, str]:
    """Auth header for the API server.

    Passing this as a header keeps the token out of any process command
    line, which is the whole point of not using curl here.
    """
    return {"Authorization": f"Bearer {token}"} if token else {}
