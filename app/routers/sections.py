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
from app.routers.warnings import get_warnings

router = APIRouter(tags=["sections"])


async def _collect_hardware() -> dict:
    cpu = await collect_cpu()
    memory = await collect_memory()
    pci = await collect_pci()
    usb = await collect_usb()
    nics = await collect_nics()
    sensors = await collect_sensors()
    gpus = await collect_gpus()
    return {
        "cpu": cpu.model_dump(),
        "memory": memory.model_dump(),
        "pci_devices": [d.model_dump() for d in pci],
        "usb_devices": [d.model_dump() for d in usb],
        "nics": [n.model_dump() for n in nics],
        "sensors": [s.model_dump() for s in sensors],
        "gpus": [g.model_dump() for g in gpus],
    }


SECTION_MAP = {
    "node": lambda: collect_node_info(),
    "hardware": _collect_hardware,
    "storage": lambda: collect_storage(),
    "system": None,  # handled below
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
    if section_name == "system":
        efi = await collect_efi()
        return {"efi": efi.model_dump()}

    if section_name == "network":
        nics = await collect_nics()
        connectivity = await collect_connectivity()
        return {
            "interfaces": [n.model_dump() for n in nics],
            "connectivity": connectivity,
        }

    collector = SECTION_MAP.get(section_name)
    if collector is None:
        raise HTTPException(status_code=404, detail=f"Unknown section: {section_name}")

    result = await collector()

    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, list):
        return [r.model_dump() if hasattr(r, "model_dump") else r for r in result]
    return result
