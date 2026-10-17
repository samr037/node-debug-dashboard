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
    # Whether the drive answered a SMART health query at all. Virtual disks
    # and iSCSI LUNs do not, and must not be presented as healthy.
    smart_available: bool = False
    # None means "not reported", which is distinct from "reported as failing".
    health_passed: bool | None = None
    temperature_celsius: int | None = None
    power_on_hours: int | None = None
    wear_leveling_percent: int | None = None
    reallocated_sectors: int | None = None
    pending_sectors: int | None = None  # ATA 197, current pending sectors
    offline_uncorrectable: int | None = None  # ATA 198
    # smartctl exit-status bits 4-7, as text: findings smart_status.passed
    # does not reflect (e.g. a self-test log with read failures).
    smart_issues: list[str] = []
    # The drive is spun down (smartctl -n standby): asleep, not broken.
    standby: bool = False
    # Why SMART could not be read (timeout, no output, invalid or unparseable
    # JSON, device open/IDENTIFY failure). None for a sleeping drive.
    error: str | None = None
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
