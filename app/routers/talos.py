from fastapi import APIRouter

from app.collectors.talos import (
    collect_talos,
    collect_talos_certificates,
    collect_talos_machine_config,
)
from app.models.talos import TalosCertificateInfo, TalosMachineConfig, TalosOverview

router = APIRouter(tags=["talos"])


@router.get("", response_model=TalosOverview)
async def get_talos():
    """Full Talos overview: version, machine config, certificates."""
    return await collect_talos()


@router.get("/config", response_model=TalosMachineConfig)
async def get_talos_config():
    """Talos machine config (safe fields only, no secrets)."""
    return await collect_talos_machine_config()


@router.get("/certificates", response_model=list[TalosCertificateInfo])
async def get_talos_certificates():
    """Talos certificate inventory with expiry tracking."""
    return await collect_talos_certificates()
