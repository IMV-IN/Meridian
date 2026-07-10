"""Pytest session hooks — keep process exit clean on CI."""

from __future__ import annotations

import gc
import os

# Must be set before uvicorn/uvloop is first imported by test modules.
os.environ.setdefault("FORCED_ASYNCIO_LOOP", "1")


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    """Encourage clean teardown before interpreter shutdown (avoids exit SIGSEGV)."""
    # Hint any registered mock servers to stop.
    for obj in gc.get_objects():
        try:
            # uvicorn.Server instances
            if obj.__class__.__name__ == "Server" and hasattr(obj, "should_exit"):
                obj.should_exit = True
        except Exception:
            continue
    gc.collect()
