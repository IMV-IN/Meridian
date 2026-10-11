"""Bounded cumulative text-prefix hashes. Never retain prompt text in the index."""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Remaining fields affect the rendered prompt or are unknown extensions. Include
# them in the namespace rather than assuming their semantics are irrelevant.
_GENERATION_FIELDS = {
    "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens",
    "temperature", "top_p", "top_k", "min_p", "seed", "stop", "n", "user",
    "presence_penalty", "frequency_penalty", "logprobs", "top_logprobs", "logit_bias",
}


def prefix_keys(
    body: Any, *, scope: str = "public", block_chars: int = 256, max_checkpoints: int = 16,
) -> tuple[str, ...]:
    """Longest first; match leading serialized chat spans, not tokenizer blocks.

    Model, tenant and prompt modifiers partition the index. Unsupported multimodal
    bodies fall back to ordinary routing. Serialization stops at the configured
    character budget, even for long messages.
    """
    if not isinstance(body, dict) or block_chars < 1 or max_checkpoints < 1:
        return ()
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return ()
    normalized = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            return ()
        if not isinstance(message.get("content"), str):
            return ()
        # Role precedes content so a long shared text in different roles cannot
        # produce the same prefix before its role is hashed.
        normalized.append({
            "role": message["role"],
            **{k: message[k] for k in sorted(message) if k != "role"},
        })
    limit = block_chars * max_checkpoints
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    try:
        header = {k: body[k] for k in sorted(body) if k not in _GENERATION_FIELDS}
        namespace = json.dumps([scope, header], sort_keys=True, ensure_ascii=False, allow_nan=False)
        # Oversized tool schemas/modifiers are unsupported rather than partially
        # hashed: two different schemas must never share an index namespace.
        if len(namespace) > limit:
            return ()
        text = ""
        for chunk in encoder.iterencode(normalized):
            text += chunk[:limit - len(text)]
            if len(text) == limit:
                break
    except (TypeError, ValueError):
        return ()
    seed = hashlib.blake2b(namespace.encode(), digest_size=16)
    keys = []
    for start in range(0, len(text), block_chars):
        seed.update(text[start:start + block_chars].encode())
        keys.append(seed.hexdigest())
    return tuple(reversed(keys))
