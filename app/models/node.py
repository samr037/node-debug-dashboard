from pydantic import BaseModel


class IPAddress(BaseModel):
    interface: str
    address: str
    prefix_length: int
    family: str  # "inet" or "inet6"


class NodeInfo(BaseModel):
    hostname: str
    kubernetes_node_name: str | None = None
    talos_version: str | None = None
    os_name: str
    kernel_version: str
    uptime_seconds: float
    uptime_human: str
    load_1m: float
    load_5m: float
    load_15m: float
    cpu_model: str
    cpu_cores: int
    cpu_threads: int
    ram_total_gb: float
    ip_addresses: list[IPAddress]
