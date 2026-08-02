"""여러 검색 provider의 실패를 격리하고 후보를 병합·정렬한다."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from typing import Mapping, Protocol, Sequence

from app.domain.common.enums import RetrievalSource
from app.domain.mappings.reranking import DecisionContext, rerank_delta
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
    # 문맥 게이팅(ADR-009 보강 1항)용 — 기본값 None으로 기존 위치 인자 호출을 유지한다.
    article_id: str | None = None
    change_type: str | None = None

    @property
    def query_hash(self) -> str:
        # 문맥은 해시에 넣지 않는다 — audit 기록·기존 테스트가 해시 안정성에 의존한다.
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


class CandidateReranker(Protocol):
    """merge 결과 후보에 붙는 결정 이력을 공급하는 seam(구현체는 #0016 step 3).

    반환 키는 `candidate.dedup_key` 다 — merge 이후의 후보 동일성 판정이 이미
    dedup_key 기준이므로 다른 키를 쓰면 매칭이 어긋난다.
    """

    version: str

    def contexts_for(
        self, query: RetrievalQuery, candidates: Sequence[RetrievalCandidate]
    ) -> Mapping[str, Sequence[DecisionContext]]: ...


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
    # 검증 이력 rerank 는 기본 활성(ADR-009). off 면 rerank 단계를 통째로 건너뛴다.
    rerank_enabled: bool = True

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
    # rerank 가 실행되지 않았으면 None — SCORING_VERSION 과 별개로 노출한다(ADR-009).
    rerank_version: str | None = None

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
            "rerank_version": self.rerank_version,
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
    def __init__(
        self,
        providers: Sequence[RetrievalProvider],
        reranker: CandidateReranker | None = None,
    ) -> None:
        self.providers = tuple(providers)
        self.reranker = reranker

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
        # 단계 순서 고정: merge → rerank → 정렬 → final_top_k 절단 → rank 부여.
        # rerank 를 절단 뒤에 두면 상위 K 밖의 검증 후보가 boost 를 받아도 올라올 수
        # 없다(ADR-009 보강 2항).
        merged = _merge_candidates(candidates, config, warnings)
        rerank_version: str | None = None
        if self.reranker is not None and config.rerank_enabled:
            merged, rerank_version = self._rerank(query, merged, warnings)
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
            rerank_version,
        )

    def _rerank(
        self,
        query: RetrievalQuery,
        merged: list[RetrievalCandidate],
        warnings: list[str],
    ) -> tuple[list[RetrievalCandidate], str | None]:
        """검증 이력 delta 를 적용하고 재정렬한다. 점수 규칙은 도메인 모듈이 단일 출처다.

        검증 이력 조회 실패가 검색 전체를 죽이면 "개정을 놓침"으로 이어지므로,
        provider 실패와 같은 방식으로 warning 만 남기고 rerank 없이 진행한다
        (RETRIEVAL_EXPERIMENT_SPEC §16 "verified DB 실패: warning").
        """
        reranker = self.reranker
        assert reranker is not None
        try:
            version = reranker.version
            contexts = reranker.contexts_for(query, tuple(merged))
            reranked: list[RetrievalCandidate] = []
            for candidate in merged:
                entries = contexts.get(candidate.dedup_key) or ()
                if not entries:
                    reranked.append(candidate)
                    continue
                delta = rerank_delta(
                    entries,
                    query_article_id=query.article_id,
                    query_change_type=query.change_type,
                    # merge 가 이미 stale -0.50 을 적용했으면 rerank 는 얹지 않는다
                    # (ADR-009 보강 3항 — 총 -0.50 cap).
                    merge_stale_applied=candidate.stale,
                )
                if not delta:
                    reranked.append(candidate)
                    continue
                score = round(max(0.0, min(1.0, candidate.final_score + delta)), 6)
                reranked.append(replace(candidate, final_score=score))
        except Exception as exc:
            warnings.append(f"rerank: {exc}")
            return merged, None
        # 동점 시 결과가 흔들리면 ablation 재현성이 깨진다 — merge 와 같은 정렬 키.
        reranked.sort(key=lambda item: (-item.final_score, item.location.path))
        return reranked, version


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

