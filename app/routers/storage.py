from fastapi import APIRouter, HTTPException

from app.collectors.storage import (
    collect_all_smart,
    collect_disk_usage,
    collect_disks,
    collect_smart_for_device,
    collect_storage,
)
from app.models.storage import DiskInfo, DiskUsage, SmartHealth, StorageOverview

router = APIRouter(tags=["storage"])


@router.get("", response_model=StorageOverview)
async def get_storage():
    """All storage info: disks, SMART, usage."""
    return await collect_storage()


@router.get("/disks", response_model=list[DiskInfo])
async def get_disks():
    """Disk list with partitions."""
    return await collect_disks()


@router.get("/smart", response_model=list[SmartHealth])
async def get_all_smart():
    """SMART health for all disks."""
    return await collect_all_smart()


@router.get("/smart/{device}", response_model=SmartHealth)
async def get_smart(device: str):
    """SMART health for a specific disk (e.g. sda, nvme0)."""
    # Sanitize: only allow alphanumeric device names
    if not device.replace("/", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid device name")
    result = await collect_smart_for_device(device)
    if not result:
        raise HTTPException(
            status_code=404, detail=f"Device {device} not found or SMART unavailable"
        )
    return result


@router.get("/usage", response_model=list[DiskUsage])
async def get_usage():
    """Disk usage (df) with severity levels."""
    return await collect_disk_usage()
