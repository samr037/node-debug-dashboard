from fastapi import APIRouter

from app.collectors.cpu import collect_cpu
from app.collectors.gpu import collect_gpus
from app.collectors.memory import collect_memory
from app.collectors.network import collect_nics
from app.collectors.pci import collect_pci
from app.collectors.sensors import collect_sensors
from app.collectors.usb import collect_usb
from app.models.hardware import (
    CPUInfo,
    GPUInfo,
    HardwareOverview,
    MemoryInfo,
    NICInfo,
    PCIDevice,
    SensorReading,
    USBDevice,
)

router = APIRouter(tags=["hardware"])


@router.get("", response_model=HardwareOverview)
async def get_hardware():
    """All hardware info aggregated."""
    return HardwareOverview(
        cpu=await collect_cpu(),
        memory=await collect_memory(),
        pci_devices=await collect_pci(),
        usb_devices=await collect_usb(),
        nics=await collect_nics(),
        sensors=await collect_sensors(),
        gpus=await collect_gpus(),
    )


@router.get("/cpu", response_model=CPUInfo)
async def get_cpu():
    """CPU model, cores, threads, architecture, flags."""
    return await collect_cpu()


@router.get("/memory", response_model=MemoryInfo)
async def get_memory():
    """RAM total + DIMM inventory + ECC status."""
    return await collect_memory()


@router.get("/pci", response_model=list[PCIDevice])
async def get_pci():
    """PCI devices with vendor, driver, class."""
    return await collect_pci()


@router.get("/usb", response_model=list[USBDevice])
async def get_usb():
    """USB devices."""
    return await collect_usb()


@router.get("/nics", response_model=list[NICInfo])
async def get_nics():
    """Network interfaces: driver, MAC, speed, error counters."""
    return await collect_nics()


@router.get("/sensors", response_model=list[SensorReading])
async def get_sensors():
    """Temperature, fan, and voltage sensor readings."""
    return await collect_sensors()


@router.get("/gpus", response_model=list[GPUInfo])
async def get_gpus():
    """NVIDIA GPU info (if available)."""
    return await collect_gpus()
