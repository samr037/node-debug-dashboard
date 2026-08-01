"""Route-level tests against the FastAPI app.

These use the in-process ASGI transport, so they never touch a real node.
"""

import httpx
import pytest

from app.main import app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_unknown_section_is_404(client):
    resp = await client.get("/api/sections/does-not-exist")
    assert resp.status_code == 404


async def test_openapi_schema_builds(client):
    """Every router mounts cleanly and the response models serialise."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    for expected in ("/api/health", "/api/warnings", "/api/sections/{section_name}"):
        assert expected in paths
