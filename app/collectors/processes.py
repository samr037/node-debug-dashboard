import os
import re
import time

from app.collectors.base import ttl_cache
from app.config import HOST_PROC
from app.models.processes import ProcessesOverview, ProcessInfo

_CLK_TCK = os.sysconf("SC_CLK_TCK")

# CPU tick counters from the previous collection, keyed on (pid, starttime).
# Replaced wholesale on each pass, so it cannot grow past the live process count.
_prev_samples: dict[tuple[int, int], tuple[int, float]] = {}

# State code to human-readable mapping
_STATE_MAP = {
    "R": "running",
    "S": "sleeping",
    "D": "disk sleep",
    "Z": "zombie",
    "T": "stopped",
    "t": "tracing stop",
    "X": "dead",
    "I": "idle",
}


def _parse_uid_map(passwd_content: str) -> dict[int, str]:
    """Parse /etc/passwd content into a uid -> username mapping."""
    uid_map: dict[int, str] = {}
    for line in passwd_content.splitlines():
        parts = line.split(":")
        if len(parts) >= 3:
            try:
                uid_map[int(parts[2])] = parts[0]
            except ValueError:
                continue
    return uid_map


def _list_pids_sync(proc_root: str) -> list[int]:
    """List numeric PID directories in the proc filesystem."""
    pids: list[int] = []
    try:
        for entry in os.listdir(proc_root):
            if entry.isdigit():
                pids.append(int(entry))
    except OSError:
        pass
    return pids


def _read_file_sync(path: str) -> str:
    """Read file synchronously, return empty string on error."""
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _cpu_percent(
    pid: int,
    starttime: int,
    cpu_ticks: int,
    uptime_secs: float,
    now: float,
    prev_samples: dict[tuple[int, int], tuple[int, float]],
) -> float:
    """CPU usage for one process, as a percentage of one core.

    Prefers the delta against the previous collection, which is what an
    operator actually wants: how busy is this process *now*. Falls back to
    an average over the process's own lifetime the first time we see a PID.

    The previous sample is keyed on (pid, starttime) so a recycled PID is
    treated as a new process rather than producing a nonsense delta.
    """
    if _CLK_TCK <= 0:
        return 0.0

    prev = prev_samples.get((pid, starttime))
    if prev is not None:
        prev_ticks, prev_time = prev
        elapsed = now - prev_time
        # Counters are monotonic; a negative delta means we misread something.
        if elapsed > 0 and cpu_ticks >= prev_ticks:
            return round((cpu_ticks - prev_ticks) / _CLK_TCK / elapsed * 100, 2)

    # First sighting: average over how long this process has actually been
    # alive, not over system uptime — dividing by uptime makes every process
    # started after boot look idle.
    age_secs = uptime_secs - (starttime / _CLK_TCK)
    if age_secs <= 0:
        return 0.0
    return round(cpu_ticks / _CLK_TCK / age_secs * 100, 2)


def _collect_processes_sync() -> ProcessesOverview:
    """Synchronous process collection — runs in executor."""
    global _prev_samples

    proc_root = HOST_PROC

    # System uptime
    uptime_raw = _read_file_sync(f"{proc_root}/uptime")
    if not uptime_raw:
        return ProcessesOverview()
    uptime_secs = float(uptime_raw.split()[0])

    # CPU deltas are measured against the previous collection. Building a
    # fresh dict each pass drops processes that have exited, so this stays
    # bounded by the live process count.
    prev_samples = _prev_samples
    samples: dict[tuple[int, int], tuple[int, float]] = {}
    now = time.monotonic()

    # Total memory from meminfo
    meminfo = _read_file_sync(f"{proc_root}/meminfo")
    total_mem_kb = 0
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            match = re.search(r"\d+", line)
            if match:
                total_mem_kb = int(match.group())
            break

    # UID -> username mapping (Talos has no passwd, so this may be empty)
    passwd_content = _read_file_sync("/host/etc/passwd")
    uid_map = _parse_uid_map(passwd_content)

    # Enumerate PIDs
    pids = _list_pids_sync(proc_root)
    processes: list[ProcessInfo] = []

    for pid in pids:
        try:
            pid_dir = f"{proc_root}/{pid}"

            # Parse /proc/<pid>/stat
            stat_raw = _read_file_sync(f"{pid_dir}/stat")
            if not stat_raw:
                continue

            # comm is in parentheses and may contain spaces/parens,
            # so find the last ')' to split reliably
            paren_end = stat_raw.rfind(")")
            if paren_end == -1:
                continue
            after_comm = stat_raw[paren_end + 2 :].split()
            if len(after_comm) < 20:
                continue

            state_char = after_comm[0]
            ppid = int(after_comm[1])
            utime = int(after_comm[11])
            stime = int(after_comm[12])
            starttime = int(after_comm[19])

            cpu_ticks = utime + stime
            cpu_percent = _cpu_percent(
                pid, starttime, cpu_ticks, uptime_secs, now, prev_samples
            )
            samples[(pid, starttime)] = (cpu_ticks, now)

            # Parse /proc/<pid>/status for VmRSS, VmSize, Uid
            status_raw = _read_file_sync(f"{pid_dir}/status")
            rss_kb = 0
            vsz_kb = 0
            uid = -1
            for sline in status_raw.splitlines():
                if sline.startswith("VmRSS:"):
                    match = re.search(r"\d+", sline)
                    if match:
                        rss_kb = int(match.group())
                elif sline.startswith("VmSize:"):
                    match = re.search(r"\d+", sline)
                    if match:
                        vsz_kb = int(match.group())
                elif sline.startswith("Uid:"):
                    parts = sline.split()
                    if len(parts) >= 2:
                        try:
                            uid = int(parts[1])
                        except ValueError:
                            pass

            # MEM percent
            mem_percent = 0.0
            if total_mem_kb > 0:
                mem_percent = round(rss_kb / total_mem_kb * 100, 2)

            # Username from UID
            user = uid_map.get(uid, str(uid)) if uid >= 0 else ""

            # Command line
            cmdline_raw = _read_file_sync(f"{pid_dir}/cmdline")
            if cmdline_raw:
                command = cmdline_raw.replace("\x00", " ").strip()
            else:
                # Fall back to comm from stat
                paren_start = stat_raw.find("(")
                command = (
                    stat_raw[paren_start + 1 : paren_end] if paren_start != -1 else ""
                )
                command = f"[{command}]"

            state = _STATE_MAP.get(state_char, state_char)

            processes.append(
                ProcessInfo(
                    pid=pid,
                    ppid=ppid,
                    user=user,
                    cpu_percent=cpu_percent,
                    mem_percent=mem_percent,
                    rss_kb=rss_kb,
                    vsz_kb=vsz_kb,
                    state=state,
                    command=command,
                )
            )
        except (ValueError, IndexError, OSError):
            # Process disappeared mid-read or parsing error — skip it
            continue

    _prev_samples = samples

    # Sort by memory percent descending, limit to top 200
    processes.sort(key=lambda p: p.mem_percent, reverse=True)
    top_processes = processes[:200]

    return ProcessesOverview(
        total_count=len(processes),
        processes=top_processes,
    )


@ttl_cache()
async def collect_processes() -> ProcessesOverview:
    """Collect process information from the host /proc filesystem."""
    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _collect_processes_sync)
