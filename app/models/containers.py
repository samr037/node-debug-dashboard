from pydantic import BaseModel


class ContainerStats(BaseModel):
    cpu_percent: float = 0.0
    memory_bytes: int = 0
    memory_human: str = ""


class SystemContainer(BaseModel):
    name: str
    container_id: str = ""
    state: str = ""
    image: str = ""
    uptime: str = ""
    created_at: str = ""
    stats: ContainerStats = ContainerStats()


class WorkloadContainer(BaseModel):
    pod_name: str
    namespace: str
    container_name: str
    container_id: str = ""
    image: str = ""
    state: str = ""
    uptime: str = ""
    created_at: str = ""
    stats: ContainerStats = ContainerStats()


class ContainersOverview(BaseModel):
    system_containers: list[SystemContainer] = []
    workload_containers: list[WorkloadContainer] = []
