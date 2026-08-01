"""ttl_cache behaviour."""

import asyncio

from app.collectors.base import ttl_cache


async def test_result_is_cached_within_ttl():
    calls = 0

    @ttl_cache(seconds=60)
    async def collect():
        nonlocal calls
        calls += 1
        return calls

    assert await collect() == 1
    assert await collect() == 1
    assert calls == 1


async def test_concurrent_callers_share_one_computation():
    """A stale entry must not start N copies of the same work.

    The dashboard fetches every section in parallel on a ten second timer
    and several collectors are shared between sections, so an unlocked
    cache launched the same subprocess sweep once per waiting caller.
    """
    calls = 0

    @ttl_cache(seconds=60)
    async def collect():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return calls

    results = await asyncio.gather(*(collect() for _ in range(10)))

    assert calls == 1
    assert results == [1] * 10


async def test_entries_expire():
    calls = 0

    @ttl_cache(seconds=0)
    async def collect():
        nonlocal calls
        calls += 1
        return calls

    await collect()
    await collect()
    assert calls == 2


async def test_arguments_key_the_cache():
    @ttl_cache(seconds=60)
    async def collect(device: str):
        return device.upper()

    assert await collect("sda") == "SDA"
    assert await collect("sdb") == "SDB"


async def test_cache_is_bounded():
    """Keyed collectors must not grow without limit.

    Proven by observing eviction: the oldest key is recomputed after enough
    distinct keys have pushed it out, while the newest is still a hit.
    """
    calls: list[int] = []

    @ttl_cache(seconds=600, maxsize=4)
    async def collect(n: int):
        calls.append(n)
        return n

    for n in range(20):
        await collect(n)
    assert calls == list(range(20))

    # Key 0 was evicted long ago, so it recomputes.
    await collect(0)
    assert calls[-1] == 0

    # Key 19 is still resident, so it does not.
    await collect(19)
    assert calls[-1] == 0


async def test_cache_clear_resets():
    calls = 0

    @ttl_cache(seconds=600)
    async def collect():
        nonlocal calls
        calls += 1
        return calls

    await collect()
    collect.cache_clear()
    await collect()
    assert calls == 2


async def test_failures_are_not_cached():
    """An exception must not poison the entry or leave the lock held."""
    calls = 0

    @ttl_cache(seconds=600)
    async def collect():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return "ok"

    try:
        await collect()
    except RuntimeError:
        pass

    assert await collect() == "ok"
