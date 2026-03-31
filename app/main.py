from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import (
    containers,
    hardware,
    kubernetes,
    network,
    node,
    overview,
    processes,
    sections,
    storage,
    system,
    talos,
    warnings,
)

app = FastAPI(
    title="Node Debug Dashboard",
    description="Hardware monitoring and diagnostics API for Talos Linux Kubernetes nodes",
    version="2.0.0",
)

# API routers
app.include_router(node.router, prefix="/api")
app.include_router(hardware.router, prefix="/api/hardware")
app.include_router(storage.router, prefix="/api/storage")
app.include_router(network.router, prefix="/api/network")
app.include_router(system.router, prefix="/api/system")
app.include_router(kubernetes.router, prefix="/api/kubernetes")
app.include_router(talos.router, prefix="/api/talos")
app.include_router(containers.router, prefix="/api/containers")
app.include_router(processes.router, prefix="/api/processes")
app.include_router(sections.router, prefix="/api/sections")
app.include_router(warnings.router, prefix="/api")
app.include_router(overview.router, prefix="/api")


@app.get("/api/health", tags=["health"])
async def health():
    """Health check for Kubernetes probes."""
    return {"status": "ok"}


# Static frontend — mount last so API routes take precedence
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
