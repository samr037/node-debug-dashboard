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


def ttl_cache(seconds: int = CACHE_TTL):
    """Decorator that caches async function results with a TTL."""

    def decorator(func: Any) -> Any:
        cache: dict[str, tuple[Any, float]] = {}

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = f"{args}:{kwargs}"
            now = time.monotonic()
            if key in cache and now - cache[key][1] < seconds:
                return cache[key][0]
            result = await func(*args, **kwargs)
            cache[key] = (result, now)
            return result

        wrapper.cache_clear = lambda: cache.clear()  # type: ignore[attr-defined]
        return wrapper

    return decorator
