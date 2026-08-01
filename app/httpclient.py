"""Shared async HTTP clients.

Collectors used to shell out to curl for every request, which cost a
subprocess per call — around fifteen per Kubernetes refresh — and, worse,
put the service account token in the command line of that process. This
app publishes /proc/<pid>/cmdline through /api/processes, so it was capable
of leaking its own credentials to an unauthenticated caller.

Clients are built lazily and reused, so connections are pooled and the
certificate files under HOST_ROOT are not touched at import time.
"""

import ssl

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


def _client(key: str, **kwargs) -> httpx.AsyncClient:
    client = _clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=COMMAND_TIMEOUT, **kwargs)
        _clients[key] = client
    return client


def kubernetes_client() -> httpx.AsyncClient:
    """Talks to the API server, verified against the service account CA."""
    try:
        return _client("kubernetes", verify=SA_CA_PATH)
    except (OSError, ssl.SSLError):
        # No service account mounted (running outside a pod).
        return _client("insecure", verify=False)


def insecure_client() -> httpx.AsyncClient:
    """For localhost component health probes served with self-signed certs."""
    return _client("insecure", verify=False)


def public_client() -> httpx.AsyncClient:
    """For outbound internet reachability checks."""
    return _client("public")


def etcd_client() -> httpx.AsyncClient | None:
    """Client-certificate authenticated etcd client, or None if certs absent."""
    try:
        context = ssl.create_default_context(cafile=ETCD_CA)
        context.load_cert_chain(ETCD_CERT, ETCD_KEY)
    except (OSError, ssl.SSLError):
        return None
    # etcd's peer cert is issued for the member, not "localhost".
    context.check_hostname = False
    return _client("etcd", verify=context)


async def close_clients() -> None:
    """Release pooled connections on shutdown."""
    for client in list(_clients.values()):
        if not client.is_closed:
            await client.aclose()
    _clients.clear()


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
