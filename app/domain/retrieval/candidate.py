"""검색 provider 결과를 병합 전에 보존하는 공통 후보 모델."""

from __future__ import annotations

import math
import posixpath
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol

from app.domain.common.enums import RetrievalSource
from app.domain.common.serialization import to_jsonable


LINE_BUCKET_SIZE = 10


@dataclass(frozen=True)
class CandidateLocation:
    path: str
    symbol: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    content_hash: Optional[str] = None

    def __post_init__(self) -> None:
        normalized = _normalize_path(self.path)
        object.__setattr__(self, "path", normalized)
        if self.line_start is not None and self.line_start < 1:
            raise ValueError("line_start must be positive")
        if self.line_end is not None and self.line_end < 1:
            raise ValueError("line_end must be positive")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must not precede line_start")

    @property
    def dedup_key(self) -> str:
        if self.symbol:
            return f"{self.path}::symbol:{self.symbol.strip()}"
        line = self.line_start or 1
        bucket = (line // LINE_BUCKET_SIZE) * LINE_BUCKET_SIZE
        return f"{self.path}::line:{bucket}"


@dataclass(frozen=True)
class RetrievalEvidence:
    source: RetrievalSource
    raw_score: Optional[float]
    normalized_score: float
    matched_terms: tuple[str, ...] = ()
    matched_values: tuple[str, ...] = ()
    explanation: str = ""
    provider_version: str = "unknown"
    raw_payload: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_score(self.normalized_score, "normalized_score")
        if self.raw_score is not None and not math.isfinite(self.raw_score):
            raise ValueError("raw_score must be finite")
        if not self.provider_version.strip():
            raise ValueError("provider_version must be non-empty")
        object.__setattr__(self, "matched_terms", tuple(self.matched_terms))
        object.__setattr__(self, "matched_values", tuple(self.matched_values))
        object.__setattr__(self, "raw_payload", MappingProxyType(dict(self.raw_payload)))

    def to_dict(self, include_debug: bool = False) -> dict[str, Any]:
        result = {
            "source": self.source.value,
            "raw_score": self.raw_score,
            "normalized_score": self.normalized_score,
            "matched_terms": list(self.matched_terms),
            "matched_values": list(self.matched_values),
            "explanation": self.explanation,
            "provider_version": self.provider_version,
        }
        if include_debug:
            result["raw_payload"] = to_jsonable(self.raw_payload)
        return result


@dataclass(frozen=True)
class RetrievalCandidate:
    location: CandidateLocation
    evidences: tuple[RetrievalEvidence, ...]
    final_score: float
    rank: Optional[int] = None
    verified_state: Optional[str] = None
    stale: bool = False

    def __post_init__(self) -> None:
        _validate_score(self.final_score, "final_score")
        if self.rank is not None and self.rank < 1:
            raise ValueError("rank must be positive")
        if not self.evidences:
            raise ValueError("at least one evidence is required")
        object.__setattr__(self, "evidences", tuple(self.evidences))

    @property
    def dedup_key(self) -> str:
        return self.location.dedup_key

    def to_dict(self, include_debug: bool = False) -> dict[str, Any]:
        return {
            "location": to_jsonable(self.location),
            "evidences": [
                evidence.to_dict(include_debug=include_debug)
                for evidence in self.evidences
            ],
            "final_score": self.final_score,
            "rank": self.rank,
            "verified_state": self.verified_state,
            "stale": self.stale,
            "dedup_key": self.dedup_key,
        }


class ScoreNormalizer(Protocol):
    version: str

    def normalize(
        self,
        source: RetrievalSource,
        raw_score: float,
        raw_payload: Mapping[str, Any],
    ) -> float: ...


class IdentityScoreNormalizer:
    """이미 0~1 범위인 RAG similarity 등에 사용하는 기준 구현."""

    version = "identity-normalizer-v1"

    def normalize(
        self,
        source: RetrievalSource,
        raw_score: float,
        raw_payload: Mapping[str, Any],
    ) -> float:
        _validate_score(raw_score, "raw_score")
        return raw_score


def _normalize_path(path: str) -> str:
    value = path.strip().replace("\\", "/")
    if not value or value.startswith("/"):
        raise ValueError("candidate path must be a non-empty relative path")
    normalized = posixpath.normpath(value)
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("candidate path must stay inside the repository")
    return normalized.removeprefix("./")


def _validate_score(score: float, name: str) -> None:
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(f"{name} must be a finite value between 0 and 1")

