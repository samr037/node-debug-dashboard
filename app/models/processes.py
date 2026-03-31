from pydantic import BaseModel


class ProcessInfo(BaseModel):
    pid: int
    ppid: int = 0
    user: str = ""
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    rss_kb: int = 0
    vsz_kb: int = 0
    state: str = ""
    command: str = ""


class ProcessesOverview(BaseModel):
    total_count: int = 0
    processes: list[ProcessInfo] = []
