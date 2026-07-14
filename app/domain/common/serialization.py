"""JSON 직렬화 규칙 — 도메인 값 객체를 JSON 호환 구조로 변환한다.

규칙:
- str/Enum → enum 값 문자열
- datetime → ISO 8601 문자열. tz 정보가 없으면 UTC 로 간주한다.
- dataclass → 필드 dict (재귀)
- Mapping(dict, MappingProxyType 등) → dict (재귀)
- list/tuple/set → list (재귀)
- 그 외 원시값(str/int/float/bool/None) → 그대로

이 모듈은 표준 라이브러리에만 의존한다.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


def to_jsonable(value: Any) -> Any:
    """임의의 도메인 값을 json.dumps 가능한 구조로 변환한다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        # bool 은 int 의 서브클래스지만 위 검사로 그대로 통과 — 의도된 동작.
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _isoformat_utc(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_jsonable(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    raise TypeError(f"cannot serialize value of type {type(value)!r}")


def _isoformat_utc(dt: datetime) -> str:
    """tz-naive datetime 은 UTC 로 간주하여 ISO 8601 문자열을 만든다."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
