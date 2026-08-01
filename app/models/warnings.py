from pydantic import BaseModel


# Named NodeWarning rather than Warning: the latter shadows the Python
# builtin exception class in every module that imports it.
class NodeWarning(BaseModel):
    severity: str  # "critical", "warning", "info"
    source: str  # "smart", "temperature", "memory", "dmesg", "network", "disk", "cpu"
    message: str
    device: str | None = None
