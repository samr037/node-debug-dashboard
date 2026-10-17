"""Run independent collectors so one failure costs one section, not the page.

A plain asyncio.gather propagates the first exception, so a single collector
tripping over unexpected command output (lscpu printing "Socket(s): -" on an
ARM board, say) turned every aggregate endpoint into a 500 and threw away
everything the other collectors had gathered.
"""

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

logger = logging.getLogger(__name__)


async def gather_sections(
    **sections: Awaitable[Any],
) -> tuple[dict[str, Any], list[str]]:
    """Await every collector; a failed one yields None and is logged.

    Returns the results by name, and the names that failed.
    """
    results = await asyncio.gather(*sections.values(), return_exceptions=True)
    out: dict[str, Any] = {}
    failed: list[str] = []
    for name, result in zip(sections, results):
        if isinstance(result, BaseException):
            # Cancellation and the like are not collector bugs.
            if not isinstance(result, Exception):
                raise result
            logger.error("collector %r failed", name, exc_info=result)
            failed.append(name)
            result = None
        out[name] = result
    return out, failed


def dump(value: Any) -> Any:
    """model_dump a model or a list of them; None stays None."""
    if isinstance(value, list):
        return [v.model_dump() if hasattr(v, "model_dump") else v for v in value]
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def dump_list(value: Any) -> list:
    """Like dump, but a failed list section is an empty list, not None."""
    return dump(value) if value is not None else []
