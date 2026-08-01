import asyncio
import time
from functools import wraps
from typing import Any

from app.config import CACHE_TTL, COMMAND_TIMEOUT


async def run_command(
    cmd: list[str], timeout: float = COMMAND_TIMEOUT
) -> tuple[str, str, int]:
    """Run a system command asynchronously, return (stdout, stderr, returncode)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        proc.kill()  # type: ignore[union-attr]
        return "", f"Command timed out: {' '.join(cmd)}", -1
    except FileNotFoundError:
        return "", f"Command not found: {cmd[0]}", -1


async def read_file(path: str) -> str:
    """Read a file's content, return empty string if not found."""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _read_file_sync, path)
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _read_file_sync(path: str) -> str:
    with open(path, errors="replace") as f:
        return f.read()


def ttl_cache(seconds: int = CACHE_TTL, maxsize: int = 64):
    """Decorator that caches async function results with a TTL.

    Entries are computed under a per-key lock. Without it, every caller that
    arrived while an entry was stale started its own copy of the work: the
    dashboard fetches all sections in parallel every ten seconds, and
    several collectors are shared between sections, so an expiry could
    launch the same smartctl or crictl sweep many times over.
    """

    def decorator(func: Any) -> Any:
        cache: dict[str, tuple[Any, float]] = {}
        locks: dict[str, asyncio.Lock] = {}

        def _fresh(key: str) -> tuple[bool, Any]:
            entry = cache.get(key)
            if entry is not None and time.monotonic() - entry[1] < seconds:
                return True, entry[0]
            return False, None

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = f"{args}:{kwargs}"

            hit, value = _fresh(key)
            if hit:
                return value

            lock = locks.setdefault(key, asyncio.Lock())
            async with lock:
                # Another caller may have populated it while we queued.
                hit, value = _fresh(key)
                if hit:
                    return value

                result = await func(*args, **kwargs)
                cache[key] = (result, time.monotonic())

                # Keyed on arguments, so this only grows for collectors that
                # take any — bounded in practice, bounded explicitly here.
                if len(cache) > maxsize:
                    oldest = min(cache, key=lambda k: cache[k][1])
                    cache.pop(oldest, None)
                    locks.pop(oldest, None)
                return result

        def _clear() -> None:
            cache.clear()
            locks.clear()

        wrapper.cache_clear = _clear  # type: ignore[attr-defined]
        return wrapper

    return decorator
