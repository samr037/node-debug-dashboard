import logging

from fastapi import APIRouter, HTTPException

from app.collectors.containers import collect_containers
from app.collectors.cpu import collect_cpu
from app.collectors.efi import collect_efi
from app.collectors.gpu import collect_gpus
from app.collectors.kubernetes import collect_cluster_nodes, collect_kubernetes
from app.collectors.memory import collect_memory
from app.collectors.network import collect_connectivity, collect_nics
from app.collectors.node import collect_node_info
from app.collectors.pci import collect_pci
from app.collectors.processes import collect_processes
from app.collectors.sensors import collect_sensors
from app.collectors.storage import collect_storage
from app.collectors.talos import collect_talos
from app.collectors.usb import collect_usb
from app.routers.degrade import dump, dump_list, gather_sections
from app.routers.warnings import get_warnings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sections"])


async def _collect_hardware() -> dict:
    # One bad collector nulls (or empties) its own field, not the section.
    r, failed = await gather_sections(
        cpu=collect_cpu(),
        memory=collect_memory(),
        pci=collect_pci(),
        usb=collect_usb(),
        nics=collect_nics(),
        sensors=collect_sensors(),
        gpus=collect_gpus(),
    )
    return {
        "cpu": dump(r["cpu"]),
        "memory": dump(r["memory"]),
        "pci_devices": dump_list(r["pci"]),
        "usb_devices": dump_list(r["usb"]),
        "nics": dump_list(r["nics"]),
        "sensors": dump_list(r["sensors"]),
        "gpus": dump_list(r["gpus"]),
        "errors": failed,
    }


async def _collect_system() -> dict:
    return {"efi": (await collect_efi()).model_dump()}


SECTION_MAP = {
    "node": lambda: collect_node_info(),
    "hardware": _collect_hardware,
    "storage": lambda: collect_storage(),
    "system": _collect_system,
    "network": None,  # handled below
    "kubernetes": lambda: collect_kubernetes(),
    "talos": lambda: collect_talos(),
    "containers": lambda: collect_containers(),
    "processes": lambda: collect_processes(),
    "warnings": lambda: get_warnings(),
    "cluster_nodes": lambda: collect_cluster_nodes(),
}


@router.get("/{section_name}")
async def get_section(section_name: str):
    """Fetch a single dashboard section by name."""
    if section_name == "network":
        r, failed = await gather_sections(
            nics=collect_nics(), connectivity=collect_connectivity()
        )
        return {
            "interfaces": dump_list(r["nics"]),
            "connectivity": r["connectivity"],
            "errors": failed,
        }

    collector = SECTION_MAP.get(section_name)
    if collector is None:
        raise HTTPException(status_code=404, detail=f"Unknown section: {section_name}")

    try:
        result = await collector()
    except Exception:
        # A section is a single collector here, so there is nothing partial
        # to return. 503 rather than an unhandled 500: the frontend already
        # marks a failed section and keeps rendering the others.
        logger.exception("section %r failed", section_name)
        raise HTTPException(
            status_code=503, detail=f"Section {section_name} could not be collected"
        ) from None

    return dump(result)
