from fastapi import APIRouter

from app.collectors.efi import collect_efi
from app.models.system import EFIInfo

router = APIRouter(tags=["system"])


@router.get("/efi", response_model=EFIInfo)
async def get_efi():
    """UEFI boot order and entries."""
    return await collect_efi()
