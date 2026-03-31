from fastapi import APIRouter

from app.collectors.processes import collect_processes
from app.models.processes import ProcessesOverview

router = APIRouter(tags=["processes"])


@router.get("", response_model=ProcessesOverview)
async def get_processes():
    """Running processes on the host (top 200 by memory)."""
    return await collect_processes()
