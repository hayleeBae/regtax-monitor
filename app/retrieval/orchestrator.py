"""여러 검색 provider의 실패를 격리하고 후보를 병합·정렬한다."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from typing import Protocol, Sequence

from app.domain.common.enums import RetrievalSource
from app.domain.retrieval import RetrievalCandidate, RetrievalEvidence


SCORING_VERSION = "retrieval-scoring-v1"
DEFAULT_WEIGHTS = {
    RetrievalSource.VERIFIED_MAPPING: 0.35,
    RetrievalSource.CONSTANT_MATCH: 0.25,
    RetrievalSource.TERM_DICTIONARY: 0.20,
    RetrievalSource.RAG: 0.15,
    RetrievalSource.CODE_GRAPH: 0.05,
    RetrievalSource.HISTORICAL_COMMIT: 0.10,
}


class RetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    domain: str | None = None
    repository_commit: str | None = None
    top_k_per_provider: int = 8

    @property
    def query_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderResult:
    source: RetrievalSource
    candidates: tuple[RetrievalCandidate, ...]
    warnings: tuple[str, ...] = ()
    duration_ms: int = 0


class RetrievalProvider(Protocol):
    source: RetrievalSource
    version: str

    def retrieve(self, query: RetrievalQuery) -> ProviderResult: ...


@dataclass(frozen=True)
class ProviderStatus:
    status: str
    duration_ms: int = 0
    candidate_count: int = 0
    message: str = ""


@dataclass(frozen=True)
class RetrievalConfig:
    enabled_sources: frozenset[RetrievalSource] = field(
        default_factory=lambda: frozenset(DEFAULT_WEIGHTS)
    )
    final_top_k: int = 10
    weights: dict[RetrievalSource, float] = field(
        default_factory=lambda: dict(DEFAULT_WEIGHTS)
    )
    scoring_version: str = SCORING_VERSION

    def __post_init__(self) -> None:
        if self.final_top_k < 1:
            raise ValueError("final_top_k must be positive")


@dataclass(frozen=True)
class RetrievalResponse:
    candidates: tuple[RetrievalCandidate, ...]
    provider_statuses: dict[str, ProviderStatus]
    scoring_version: str
    query_hash: str
    repository_commit: str | None
    warnings: tuple[str, ...]
    duration_ms: int

    def to_dict(self) -> dict:
        public = [candidate.to_dict() for candidate in self.candidates]
        return {
            "candidates": public,
            "provider_statuses": {
                key: {
                    "status": value.status,
                    "duration_ms": value.duration_ms,
                    "candidate_count": value.candidate_count,
                    "message": value.message,
                }
                for key, value in self.provider_statuses.items()
            },
            "scoring_version": self.scoring_version,
            "query_hash": self.query_hash,
            "repository_commit": self.repository_commit,
            "warnings": list(self.warnings),
            "duration_ms": self.duration_ms,
            "rag_hits": _compat_candidates(self.candidates, RetrievalSource.RAG),
            "dict_matches": _compat_candidates(
                self.candidates, RetrievalSource.TERM_DICTIONARY
            ),
            "const_matches": _compat_candidates(
                self.candidates, RetrievalSource.CONSTANT_MATCH
            ),
        }


class RetrievalOrchestrator:
    def __init__(self, providers: Sequence[RetrievalProvider]) -> None:
        self.providers = tuple(providers)

    def retrieve(
        self,
        query: RetrievalQuery,
        config: RetrievalConfig | None = None,
    ) -> RetrievalResponse:
        config = config or RetrievalConfig()
        started = time.monotonic()
        statuses: dict[str, ProviderStatus] = {}
        warnings: list[str] = []
        candidates: list[RetrievalCandidate] = []
        enabled_count = 0
        success_count = 0
        for provider in self.providers:
            key = provider.source.value
            if provider.source not in config.enabled_sources:
                statuses[key] = ProviderStatus("disabled")
                continue
            enabled_count += 1
            provider_started = time.monotonic()
            try:
                result = provider.retrieve(query)
            except Exception as exc:
                duration = int((time.monotonic() - provider_started) * 1000)
                statuses[key] = ProviderStatus("error", duration, message=str(exc))
                warnings.append(f"{key}: {exc}")
                continue
            success_count += 1
            candidates.extend(result.candidates)
            warnings.extend(result.warnings)
            statuses[key] = ProviderStatus(
                "success", result.duration_ms, len(result.candidates)
            )
        if enabled_count and success_count == 0:
            raise RetrievalError("RETRIEVAL_ERROR: all enabled providers failed")
        merged = _merge_candidates(candidates, config, warnings)
        ranked = tuple(
            replace(candidate, rank=index)
            for index, candidate in enumerate(merged[: config.final_top_k], start=1)
        )
        duration = int((time.monotonic() - started) * 1000)
        return RetrievalResponse(
            ranked,
            statuses,
            config.scoring_version,
            query.query_hash,
            query.repository_commit,
            tuple(warnings),
            duration,
        )


def _merge_candidates(
    candidates: Sequence[RetrievalCandidate],
    config: RetrievalConfig,
    warnings: list[str],
) -> list[RetrievalCandidate]:
    grouped: dict[str, list[RetrievalCandidate]] = {}
    hashes: dict[str, str | None] = {}
    for candidate in candidates:
        key = candidate.dedup_key
        content_hash = candidate.location.content_hash
        if key in hashes and hashes[key] and content_hash and hashes[key] != content_hash:
            warnings.append(f"content hash conflict: {key}")
            key = f"{key}::hash:{content_hash}"
        hashes.setdefault(key, content_hash)
        grouped.setdefault(key, []).append(candidate)

    merged: list[RetrievalCandidate] = []
    for group in grouped.values():
        best_by_source: dict[RetrievalSource, RetrievalEvidence] = {}
        for candidate in group:
            for evidence in candidate.evidences:
                current = best_by_source.get(evidence.source)
                if current is None or evidence.normalized_score > current.normalized_score:
                    best_by_source[evidence.source] = evidence
        evidences = tuple(
            sorted(best_by_source.values(), key=lambda item: (-item.normalized_score, item.source.value))
        )
        score = sum(
            config.weights.get(evidence.source, 0.0) * evidence.normalized_score
            for evidence in evidences
        )
        if len(evidences) >= 3:
            score += 0.10
        elif len(evidences) >= 2:
            score += 0.05
        stale = all(candidate.stale for candidate in group)
        if stale:
            score -= 0.50
        score = round(max(0.0, min(1.0, score)), 6)
        merged.append(
            RetrievalCandidate(
                group[0].location,
                evidences,
                score,
                verified_state=next(
                    (item.verified_state for item in group if item.verified_state), None
                ),
                stale=stale,
            )
        )
    return sorted(merged, key=lambda item: (-item.final_score, item.location.path))


def _compat_candidates(
    candidates: Sequence[RetrievalCandidate], source: RetrievalSource
) -> list[dict]:
    result = []
    for candidate in candidates:
        evidence = next((item for item in candidate.evidences if item.source is source), None)
        if evidence is None:
            continue
        result.append(
            {
                "path": candidate.location.path,
                "symbol": candidate.location.symbol,
                "score": evidence.raw_score,
                "normalized_score": evidence.normalized_score,
                "matched_terms": list(evidence.matched_terms),
                "matched_values": list(evidence.matched_values),
            }
        )
    return result

