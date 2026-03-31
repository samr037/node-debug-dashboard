from pydantic import BaseModel


class Warning(BaseModel):
    severity: str  # "critical", "warning", "info"
    source: str  # "smart", "temperature", "memory", "dmesg", "network", "disk", "cpu"
    message: str
    device: str | None = None
