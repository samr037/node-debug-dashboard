from fastapi import APIRouter

from app.collectors.processes import collect_processes
from app.models.processes import ProcessesOverview

router = APIRouter(tags=["processes"])


@router.get("", response_model=ProcessesOverview)
async def get_processes():
    """Running host processes: the top 200 by memory plus up to 50 more top
    CPU consumers, ordered by memory.

    Command lines are redacted: Authorization headers, Bearer tokens,
    --token/--password style flags, password=/token=/access_token= pairs and
    URL credentials are replaced with ***.
    """
    return await collect_processes()
