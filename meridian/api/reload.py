"""Hot-reload of API keys (SIGHUP / POST /meridian/reload).

Atomic reference swap of ``state.key_index`` — in-flight requests keep using
the dict they already looked up; new requests see the new index.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from meridian.auth.keys import rebuild_key_index

if TYPE_CHECKING:
    from meridian.api.state import AppState

logger = logging.getLogger("meridian")

_reload_lock = threading.Lock()


def reload_keys(state: "AppState") -> int:
    """Rebuild key index from config + keys_file. Returns number of keys loaded.

    Raises ValueError/OSError on bad file or duplicate keys (state unchanged).
    """
    with _reload_lock:
        new_index = rebuild_key_index(state.config.auth)
        state.key_index = new_index
        n = len(new_index)
        logger.info("Reloaded API keys — %d key(s) active", n)
        return n
