from fastapi import APIRouter

from app.collectors.containers import collect_containers
from app.collectors.cpu import collect_cpu
from app.collectors.efi import collect_efi
from app.collectors.gpu import collect_gpus
from app.collectors.kubernetes import collect_kubernetes
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

router = APIRouter(tags=["overview"])


@router.get("/overview")
async def get_overview():
    """Full node overview — all sections aggregated. Primary endpoint for the dashboard."""
    node = await collect_node_info()
    cpu = await collect_cpu()
    memory = await collect_memory()
    pci = await collect_pci()
    usb = await collect_usb()
    nics = await collect_nics()
    sensors = await collect_sensors()
    gpus = await collect_gpus()
    storage = await collect_storage()
    efi = await collect_efi()
    connectivity = await collect_connectivity()
    k8s = await collect_kubernetes()
    talos_info = await collect_talos()
    containers_info = await collect_containers()
    processes_info = await collect_processes()
    warnings = await get_warnings()

    return {
        "node": node.model_dump(),
        "hardware": {
            "cpu": cpu.model_dump(),
            "memory": memory.model_dump(),
            "pci_devices": [d.model_dump() for d in pci],
            "usb_devices": [d.model_dump() for d in usb],
            "nics": [n.model_dump() for n in nics],
            "sensors": [s.model_dump() for s in sensors],
            "gpus": [g.model_dump() for g in gpus],
        },
        "storage": storage.model_dump(),
        "system": {
            "efi": efi.model_dump(),
        },
        "network": {
            "interfaces": [n.model_dump() for n in nics],
            "connectivity": connectivity,
        },
        "kubernetes": k8s.model_dump(),
        "talos": talos_info.model_dump(),
        "containers": containers_info.model_dump(),
        "processes": processes_info.model_dump(),
        "warnings": [w.model_dump() for w in warnings],
    }
