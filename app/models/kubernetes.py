from pydantic import BaseModel


class K8sNodeCondition(BaseModel):
    type: str = ""
    status: str = ""
    reason: str = ""
    message: str = ""
    last_transition: str = ""


class K8sNodeAddress(BaseModel):
    type: str = ""
    address: str = ""


class K8sNodeResources(BaseModel):
    cpu: str = ""
    memory: str = ""
    ephemeral_storage: str = ""
    pods: str = ""
    gpu_nvidia: str = ""


class K8sNodeInfo(BaseModel):
    labels: dict[str, str] = {}
    annotations_count: int = 0
    conditions: list[K8sNodeCondition] = []
    addresses: list[K8sNodeAddress] = []
    capacity: K8sNodeResources = K8sNodeResources()
    allocatable: K8sNodeResources = K8sNodeResources()
    kubelet_version: str = ""
    container_runtime: str = ""
    os_image: str = ""
    architecture: str = ""


class CertificateInfo(BaseModel):
    file_path: str = ""
    subject: str = ""
    issuer: str = ""
    not_before: str = ""
    not_after: str = ""
    serial_number: str = ""
    sha256_fingerprint: str = ""
    days_until_expiry: int | None = None
    expiry_severity: str = "ok"


class K8sComponentStatus(BaseModel):
    name: str = ""
    running: bool = False
    health_status: str = "Unknown"
    container_id: str = ""
    uptime: str = ""


class K8sApiEndpoint(BaseModel):
    url: str = ""
    healthy: bool = False


class KubernetesOverview(BaseModel):
    node_info: K8sNodeInfo = K8sNodeInfo()
    certificates: list[CertificateInfo] = []
    api_endpoint: K8sApiEndpoint = K8sApiEndpoint()
    components: list[K8sComponentStatus] = []
