from fastapi import APIRouter

from app.collectors.network import collect_connectivity, collect_nics
from app.models.hardware import NICInfo

router = APIRouter(tags=["network"])


@router.get("", response_model=list[NICInfo])
async def get_network():
    """Network interfaces with stats."""
    return await collect_nics()


@router.get("/connectivity")
async def get_connectivity():
    """DNS, internet, and Kubernetes API connectivity checks."""
    return await collect_connectivity()
