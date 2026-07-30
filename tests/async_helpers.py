import asyncio
import concurrent.futures


def run_async(coro):
    """
    Run a coroutine to completion from a plain sync test function.

    Playwright's sync API (used by tests/browser/*) leaves the main thread's
    asyncio state such that a later bare `asyncio.run(...)` call raises
    "cannot be called from a running event loop", even though no event loop
    is actually running from the test's own point of view. Running the
    coroutine in a fresh worker thread sidesteps that pollution entirely,
    since asyncio's "current running loop" tracking is thread-local.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
