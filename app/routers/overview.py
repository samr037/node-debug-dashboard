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
from app.routers.degrade import dump, dump_list, gather_sections
from app.routers.warnings import get_warnings

router = APIRouter(tags=["overview"])


@router.get("/overview")
async def get_overview():
    """Full node overview — all sections aggregated. Primary endpoint for the dashboard."""
    # Collectors are independent and each spends its time waiting on
    # subprocesses or sockets, so running them one after another meant this
    # endpoint took as long as all of them added together. A collector that
    # fails leaves its section null (or empty) and is listed in "errors".
    r, failed = await gather_sections(
        node=collect_node_info(),
        cpu=collect_cpu(),
        memory=collect_memory(),
        pci=collect_pci(),
        usb=collect_usb(),
        nics=collect_nics(),
        sensors=collect_sensors(),
        gpus=collect_gpus(),
        storage=collect_storage(),
        efi=collect_efi(),
        connectivity=collect_connectivity(),
        k8s=collect_kubernetes(),
        talos=collect_talos(),
        containers=collect_containers(),
        processes=collect_processes(),
        warnings=get_warnings(),
    )

    return {
        "node": dump(r["node"]),
        "hardware": {
            "cpu": dump(r["cpu"]),
            "memory": dump(r["memory"]),
            "pci_devices": dump_list(r["pci"]),
            "usb_devices": dump_list(r["usb"]),
            "nics": dump_list(r["nics"]),
            "sensors": dump_list(r["sensors"]),
            "gpus": dump_list(r["gpus"]),
        },
        "storage": dump(r["storage"]),
        "system": {
            "efi": dump(r["efi"]),
        },
        "network": {
            "interfaces": dump_list(r["nics"]),
            "connectivity": r["connectivity"],
        },
        "kubernetes": dump(r["k8s"]),
        "talos": dump(r["talos"]),
        "containers": dump(r["containers"]),
        "processes": dump(r["processes"]),
        "warnings": dump_list(r["warnings"]),
        "errors": failed,
    }
