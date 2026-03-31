from pydantic import BaseModel


class Partition(BaseModel):
    name: str
    size: str
    type: str  # "part", "disk", "lvm", etc.
    mountpoint: str | None = None
    fstype: str | None = None


class DiskInfo(BaseModel):
    name: str
    model: str | None = None
    serial: str | None = None
    size: str
    type: str  # "disk"
    transport: str | None = None  # "sata", "nvme", "usb"
    rotational: bool = False
    partitions: list[Partition] = []


class SmartAttribute(BaseModel):
    id: int
    name: str
    value: int
    worst: int
    threshold: int
    raw_value: str


class SmartHealth(BaseModel):
    device: str
    model: str | None = None
    serial: str | None = None
    health_passed: bool = True
    temperature_celsius: int | None = None
    power_on_hours: int | None = None
    wear_leveling_percent: int | None = None
    reallocated_sectors: int | None = None
    attributes: list[SmartAttribute] = []


class DiskUsage(BaseModel):
    filesystem: str
    mount: str
    fs_type: str
    size: str
    used: str
    available: str
    used_percent: int
    severity: str = "ok"  # "ok", "warning", "critical"


class StorageOverview(BaseModel):
    disks: list[DiskInfo] = []
    smart: list[SmartHealth] = []
    usage: list[DiskUsage] = []
