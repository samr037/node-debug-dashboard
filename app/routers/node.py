from fastapi import APIRouter

from app.collectors.node import collect_node_info
from app.models.node import NodeInfo

router = APIRouter(tags=["node"])


@router.get("/node", response_model=NodeInfo)
async def get_node_info():
    """Node identity: hostname, versions, IPs, uptime, load."""
    return await collect_node_info()
