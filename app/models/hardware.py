from pydantic import BaseModel


class CPUInfo(BaseModel):
    model: str
    sockets: int
    cores_per_socket: int
    threads: int
    architecture: str
    flags: list[str] = []


class DimmInfo(BaseModel):
    locator: str
    size_mb: int | None = None
    type: str | None = None
    speed_mhz: int | None = None
    manufacturer: str | None = None
    serial: str | None = None
    part_number: str | None = None


class MemoryInfo(BaseModel):
    total_gb: float
    available_gb: float
    used_percent: float
    dimms: list[DimmInfo] = []
    ecc_correctable_errors: int = 0
    ecc_uncorrectable_errors: int = 0


class PCIDevice(BaseModel):
    slot: str
    class_name: str
    vendor: str
    device: str
    subsystem_vendor: str = ""
    subsystem_device: str = ""
    rev: str = ""
    driver: str = ""


class USBDevice(BaseModel):
    bus: str
    device: str
    id: str
    name: str


class NICInfo(BaseModel):
    name: str
    mac: str = ""
    driver: str = ""
    speed: str = ""
    duplex: str = ""
    link_detected: bool = False
    ip_addresses: list[str] = []
    rx_errors: int = 0
    tx_errors: int = 0
    rx_dropped: int = 0
    tx_dropped: int = 0
    rx_crc_errors: int = 0
    tx_carrier_errors: int = 0


class SensorReading(BaseModel):
    name: str
    label: str
    value: float
    unit: str  # "°C", "RPM", "V"
    critical: float | None = None
    warning: float | None = None
    is_alarm: bool = False


class GPUInfo(BaseModel):
    index: int
    name: str
    driver_version: str = ""
    memory_total_mb: int = 0
    memory_used_mb: int = 0
    temperature_c: int | None = None
    utilization_percent: int | None = None
    power_draw_w: float | None = None


class HardwareOverview(BaseModel):
    cpu: CPUInfo
    memory: MemoryInfo
    pci_devices: list[PCIDevice] = []
    usb_devices: list[USBDevice] = []
    nics: list[NICInfo] = []
    sensors: list[SensorReading] = []
    gpus: list[GPUInfo] = []
