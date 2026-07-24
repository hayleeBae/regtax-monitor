"""audit payload 비밀정보 제거와 설정 hash."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


_SECRET_KEYS = {
    "api_key", "token", "authorization", "password", "passwd", "secret",
    "cookie", "oc_key", "law_api_oc", "anthropic_api_key",
}
_HASH_EXCLUDED_KEYS = _SECRET_KEYS | {
    "repo_root", "repository_path", "absolute_path", "timestamp", "created_at",
}
_INLINE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|token|password|passwd|secret|authorization|cookie)"
    r"\s*[:=]\s*[^\s,;]+"
)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            name = str(key)
            if name.lower() in _SECRET_KEYS:
                result[name] = "[REDACTED]"
            else:
                result[name] = sanitize_payload(item)
        return result
    if isinstance(value, str):
        return _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [sanitize_payload(item) for item in value]
    return value


def stable_settings_hash(settings: Mapping[str, Any]) -> str:
    safe = {
        str(key): sanitize_payload(value)
        for key, value in settings.items()
        if str(key).lower() not in _HASH_EXCLUDED_KEYS
    }
    canonical = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

