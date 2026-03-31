from pydantic import BaseModel


class TalosNetworkInterface(BaseModel):
    name: str
    addresses: list[str] = []
    routes: list[str] = []
    dhcp: bool = False


class TalosMachineConfig(BaseModel):
    machine_type: str = ""  # "controlplane" or "worker"
    install_disk: str = ""
    install_image: str = ""
    cluster_name: str = ""
    cluster_endpoint: str = ""
    network_interfaces: list[TalosNetworkInterface] = []
    extensions: list[str] = []
    config_available: bool = False
    error: str = ""


class TalosVersionInfo(BaseModel):
    version: str = ""
    schematic_id: str = ""


class TalosCertificateInfo(BaseModel):
    name: str
    subject: str = ""
    issuer: str = ""
    not_before: str = ""
    not_after: str = ""
    serial_number: str = ""
    sha256_fingerprint: str = ""
    days_until_expiry: int | None = None
    expiry_severity: str = "ok"


class TalosOverview(BaseModel):
    version: TalosVersionInfo = TalosVersionInfo()
    machine_config: TalosMachineConfig = TalosMachineConfig()
    certificates: list[TalosCertificateInfo] = []
