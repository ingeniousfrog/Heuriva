from __future__ import annotations

import re
from collections.abc import Iterable

SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
)


def redact_text(text: str, *, secrets: Iterable[str] = ()) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_mapping(mapping: dict[str, object], *, secrets: Iterable[str] = ()) -> dict[str, object]:
    return {key: redact_text(str(value), secrets=secrets) for key, value in mapping.items()}
