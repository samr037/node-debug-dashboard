from pydantic import BaseModel


class EFIBootEntry(BaseModel):
    number: str
    label: str
    active: bool = False
    path: str = ""


class EFIInfo(BaseModel):
    boot_current: str | None = None
    boot_order: list[str] = []
    entries: list[EFIBootEntry] = []
    timeout: str | None = None
