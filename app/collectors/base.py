import asyncio
import os
import signal
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from app.config import CACHE_TTL, COMMAND_TIMEOUT, HOST_PROC


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the child and everything it forked.

    The child leads its own session, so its pid is also its process group.
    Killing only the direct pid left grandchildren — nvidia-smi under
    `nsenter --pid`, a shell's background jobs — running and holding the
    stdout/stderr pipes open.
    """
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Kill the process group and wait for it, without blocking for long.

    A process stuck in uninterruptible I/O (smartctl on a dying disk)
    ignores SIGKILL; closing the transport then at least releases our end
    of the pipes instead of leaking them with every retry.
    """
    _kill_group(proc)
    try:
        await asyncio.wait_for(proc.wait(), timeout=1)
    except asyncio.TimeoutError:
        pass
    finally:
        _close_transport(proc)


def _close_transport(proc: asyncio.subprocess.Process) -> None:
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        transport.close()


async def run_command(
    cmd: list[str], timeout: float = COMMAND_TIMEOUT
) -> tuple[str, str, int]:
    """Run a system command asynchronously, return (stdout, stderr, returncode)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group, so a timeout can kill the whole tree.
            start_new_session=True,
        )
    except FileNotFoundError:
        return "", f"Command not found: {cmd[0]}", -1
    except OSError as e:
        return "", f"Command failed to start: {cmd[0]}: {e}", -1

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _reap(proc)
        return "", f"Command timed out: {' '.join(cmd)}", -1
    except BaseException:
        # Cancelled (shutdown, reload, a caller giving up): don't orphan it,
        # nor its pipes. No await here; the event loop still reaps it.
        _kill_group(proc)
        _close_transport(proc)
        raise
    return (
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        proc.returncode or 0,
    )


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


def ttl_cache(
    seconds: int = CACHE_TTL,
    maxsize: int = 64,
    cache_if: Callable[[Any], bool] | None = None,
    retry_after: int = 10,
):
    """Decorator that caches async function results with a TTL.

    Entries are computed under a per-key lock. Without it, every caller that
    arrived while an entry was stale started its own copy of the work: the
    dashboard fetches all sections in parallel every ten seconds, and
    several collectors are shared between sections, so an expiry could
    launch the same smartctl or crictl sweep many times over.

    Exceptions are not cached, but callers that were already queued behind
    the failing computation get its exception rather than each re-running
    the work in turn.

    ``cache_if`` marks a result as degraded (an API blip, say) when it
    returns False: such a result is kept only ``retry_after`` seconds, so
    one failed fetch does not stick for the full TTL.
    """

    def decorator(func: Any) -> Any:
        # key -> (value, expires_at)
        cache: dict[str, tuple[Any, float]] = {}
        locks: dict[str, asyncio.Lock] = {}
        # key -> (exception, monotonic time it was raised)
        failures: dict[str, tuple[BaseException, float]] = {}

        def _fresh(key: str) -> tuple[bool, Any]:
            entry = cache.get(key)
            if entry is not None and time.monotonic() < entry[1]:
                return True, entry[0]
            return False, None

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = f"{args}:{kwargs}"

            hit, value = _fresh(key)
            if hit:
                return value

            arrived = time.monotonic()
            lock = locks.setdefault(key, asyncio.Lock())
            async with lock:
                # Another caller may have populated it while we queued.
                hit, value = _fresh(key)
                if hit:
                    return value
                # ...or failed while we queued: share that outcome.
                failed = failures.get(key)
                if failed is not None and failed[1] >= arrived:
                    raise failed[0]

                try:
                    result = await func(*args, **kwargs)
                except Exception as e:
                    failures[key] = (e, time.monotonic())
                    raise
                failures.pop(key, None)

                ttl = seconds
                if cache_if is not None and not cache_if(result):
                    ttl = min(seconds, retry_after)
                cache[key] = (result, time.monotonic() + ttl)

                # Keyed on arguments, so this only grows for collectors that
                # take any — bounded in practice, bounded explicitly here.
                if len(cache) > maxsize:
                    oldest = min(cache, key=lambda k: cache[k][1])
                    cache.pop(oldest, None)
                    locks.pop(oldest, None)
                if len(failures) > maxsize:
                    failures.pop(min(failures, key=lambda k: failures[k][1]), None)
                return result

        def _clear() -> None:
            cache.clear()
            locks.clear()
            failures.clear()

        wrapper.cache_clear = _clear  # type: ignore[attr-defined]
        return wrapper

    return decorator


def in_host_network_namespace() -> bool:
    """Whether the pod shares the node's network namespace (hostNetwork).

    Off host networking, localhost is the pod: component health endpoints,
    etcd on 127.0.0.1 and the host's own resolver are out of reach. When the
    host's namespace cannot be read, assume host networking (the default).
    """
    try:
        return os.readlink("/proc/self/ns/net") == os.readlink(f"{HOST_PROC}/1/ns/net")
    except OSError:
        return True
