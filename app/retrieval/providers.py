"""기존 RAG·사전·상수·검증 매핑을 공통 후보로 바꾸는 얇은 adapter."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Sequence

from app.codebase.base import CodeHit
from app.domain.common.enums import RetrievalSource
from app.domain.retrieval import CandidateLocation, RetrievalCandidate, RetrievalEvidence
from app.embedding.const_inventory import load_inventory, match_constants
from app.embedding.term_dict import load, load_locations, match_codes, rank_locations
from app.retrieval.orchestrator import ProviderResult, RetrievalQuery


class RagProvider:
    source = RetrievalSource.RAG
    version = "rag-provider-v1"

    def __init__(self, search: Callable[[str, int], Sequence[CodeHit]]) -> None:
        self.search = search

    def retrieve(self, query: RetrievalQuery) -> ProviderResult:
        started = time.monotonic()
        candidates = []
        for hit in self.search(query.text, query.top_k_per_provider):
            score = max(0.0, min(1.0, hit.score))
            evidence = RetrievalEvidence(
                self.source,
                hit.score,
                score,
                explanation="임베딩 유사도 검색",
                provider_version=self.version,
                raw_payload={"snippet": hit.snippet},
            )
            candidates.append(
                RetrievalCandidate(
                    CandidateLocation(hit.path, hit.symbol), (evidence,), score
                )
            )
        return _result(self.source, candidates, started)


class DictionaryProvider:
    source = RetrievalSource.TERM_DICTIONARY
    version = "dictionary-provider-v1"

    def __init__(self, repo_root: str) -> None:
        self.repo_root = repo_root

    def retrieve(self, query: RetrievalQuery) -> ProviderResult:
        started = time.monotonic()
        table = load(self.repo_root)
        locations = load_locations(self.repo_root)
        candidates = []
        for code, term, raw_score in match_codes(
            query.text, table, query.top_k_per_provider
        ):
            score = round(min(0.99, 0.7 + raw_score / 100), 3)
            for path in rank_locations(locations.get(code, []))[:3]:
                evidence = RetrievalEvidence(
                    self.source,
                    raw_score,
                    score,
                    matched_terms=(term,),
                    explanation=f"용어 사전 코드 {code} 일치",
                    provider_version=self.version,
                    raw_payload={"code": code},
                )
                candidates.append(
                    RetrievalCandidate(
                        CandidateLocation(path, code), (evidence,), score
                    )
                )
        return _result(self.source, candidates, started)


class ConstantProvider:
    source = RetrievalSource.CONSTANT_MATCH
    version = "constant-provider-v1"

    def __init__(self, repo_root: str) -> None:
        self.repo_root = repo_root

    def retrieve(self, query: RetrievalQuery) -> ProviderResult:
        started = time.monotonic()
        inventory = load_inventory(self.repo_root)
        candidates = []
        for value, expression, raw_score, files in match_constants(
            query.text, inventory, query.top_k_per_provider
        ):
            score = round(min(0.99, 0.75 + raw_score / 40), 3)
            for path in rank_locations(files)[:3]:
                evidence = RetrievalEvidence(
                    self.source,
                    raw_score,
                    score,
                    matched_values=(value,),
                    explanation=f"상수값 {expression} 일치",
                    provider_version=self.version,
                    raw_payload={"expression": expression},
                )
                candidates.append(
                    RetrievalCandidate(
                        CandidateLocation(path, value), (evidence,), score
                    )
                )
        return _result(self.source, candidates, started)


@dataclass(frozen=True)
class VerifiedMappingRecord:
    path: str
    symbol: str | None
    valid: bool
    state: str = "approved"
    content_hash: str | None = None


class VerifiedMappingProvider:
    source = RetrievalSource.VERIFIED_MAPPING
    version = "verified-provider-v1"

    def __init__(
        self,
        lookup: Callable[[RetrievalQuery], Sequence[VerifiedMappingRecord]],
    ) -> None:
        self.lookup = lookup

    def retrieve(self, query: RetrievalQuery) -> ProviderResult:
        started = time.monotonic()
        candidates = []
        for record in self.lookup(query):
            score = 1.0 if record.valid else 0.0
            evidence = RetrievalEvidence(
                self.source,
                score,
                score,
                explanation="사람이 검증한 매핑" if record.valid else "검증 매핑이 현재 코드와 불일치",
                provider_version=self.version,
            )
            candidates.append(
                RetrievalCandidate(
                    CandidateLocation(
                        record.path,
                        record.symbol,
                        content_hash=record.content_hash,
                    ),
                    (evidence,),
                    score,
                    verified_state=record.state,
                    stale=not record.valid,
                )
            )
        return _result(self.source, candidates, started)


def _result(source, candidates, started) -> ProviderResult:
    return ProviderResult(
        source,
        tuple(candidates),
        (),
        int((time.monotonic() - started) * 1000),
    )

