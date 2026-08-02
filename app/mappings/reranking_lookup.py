"""#0016 검증 이력 문맥의 DB lookup — 점수는 계산하지 않는다.

`app/retrieval/orchestrator.py` 의 `CandidateReranker` 구현체로, merge 결과 후보에
붙일 `DecisionContext` 만 공급한다. boost/penalty 수치의 단일 출처는
`app/domain/mappings/reranking.py` 이며 이 모듈에는 어떤 delta 도 없다(ADR-009).

읽기 전용이다 — 검색 중 결정 이력을 만들지 않는다(VERIFIED_MAPPING_SPEC §8).
"""

from __future__ import annotations

from typing import Sequence

from app.db.models import Mapping, MappingDecision
from app.domain.mappings.decisions import (
    MappingDecisionRecord,
    MappingDecisionType,
    VerifiedReason,
    resolve_state,
)
from app.domain.mappings.reranking import RERANK_VERSION, DecisionContext
from app.domain.retrieval import CandidateLocation
from app.mappings.repository import _decision_record

LEGACY_BACKFILL_ACTOR = "system"
LEGACY_BACKFILL_REASON_TEXT = "legacy verified backfill"
"""#0015 backfill 이 넣는 값 — `app/db/database.py::_backfill_legacy_mapping_decisions`.

사람이 확인한 근거가 아니라 `Mapping.verified` 컬럼을 옮겨 담은 이벤트이므로
도메인 모듈이 legacy penalty 를 적용할 수 있게 표시한다(스펙 §10).
"""


class SqlAlchemyDecisionContextLookup:
    """article_id 하나에 걸린 매핑 결정 이력을 후보 키별 문맥으로 묶는다."""

    version = RERANK_VERSION

    def __init__(self, session, article_id: str) -> None:
        self.session = session
        self.article_id = article_id

    def contexts_for(
        self, query, candidates: Sequence
    ) -> dict[str, tuple[DecisionContext, ...]]:
        """`candidate.dedup_key` → 이력 문맥. 이력이 없는 매핑은 넣지 않는다.

        `query`·`candidates` 는 사용하지 않는다 — 후보마다 조회하면 rerank 가
        `final_top_k` 절단 전이라 후보 수만큼 N+1 이 된다(ADR-009 보강). 매핑 1회 +
        결정 이력 1회, 총 두 번의 질의로 article_id 단위를 한꺼번에 읽는다.
        """
        # `Mapping.verified` 로 거르지 않는다 — 거절 이력이 있는 매핑은
        # verified=False 라서, 필터를 걸면 rejected penalty 가 영원히 죽는다.
        mappings = (
            self.session.query(Mapping)
            .filter(Mapping.article_id == self.article_id)
            .all()
        )
        keys: dict[int, str] = {}
        rows: dict[int, Mapping] = {}
        for mapping in mappings:
            key = _dedup_key(mapping)
            if key is None:
                continue
            keys[mapping.id] = key
            rows[mapping.id] = mapping
        if not keys:
            return {}

        grouped: dict[str, list[DecisionContext]] = {}
        for mapping_id, records in self._histories(tuple(keys)).items():
            grouped.setdefault(keys[mapping_id], []).append(
                _build_context(rows[mapping_id], records)
            )
        return {key: tuple(contexts) for key, contexts in grouped.items()}

    def _histories(
        self, mapping_ids: Sequence[int]
    ) -> dict[int, list[MappingDecisionRecord]]:
        rows = (
            self.session.query(MappingDecision)
            .filter(MappingDecision.mapping_id.in_(mapping_ids))
            .order_by(
                MappingDecision.mapping_id,
                MappingDecision.created_at,
                MappingDecision.id,
            )
            .all()
        )
        histories: dict[int, list[MappingDecisionRecord]] = {}
        for row in rows:
            histories.setdefault(row.mapping_id, []).append(_decision_record(row))
        return histories


def _dedup_key(mapping: Mapping) -> str | None:
    """후보와 같은 규칙(`CandidateLocation.dedup_key`)으로 키를 만든다.

    계산식을 복제하면 조용히 어긋나 매칭이 전부 실패한다. 매핑 행에는 줄 정보가
    없으므로 symbol 이 비면 provider 가 만드는 후보와 같은 line 버킷 키가 된다.
    malformed path(빈 값·절대경로·상위 탈출)는 예외 대신 건너뛴다.
    """
    try:
        location = CandidateLocation(mapping.path or "", mapping.symbol or None)
    except ValueError:
        return None
    return location.dedup_key


def _build_context(
    mapping: Mapping, records: Sequence[MappingDecisionRecord]
) -> DecisionContext:
    """이력을 문맥 1건으로 접는다. 상태는 #0015 `resolve_state` 가 단일 출처다."""
    verified = [
        record
        for record in records
        if record.decision is MappingDecisionType.VERIFIED
    ]
    return DecisionContext(
        article_id=mapping.article_id,
        change_type=mapping.change_type,
        state=resolve_state(records),
        # 시간순 마지막 결정의 사유 — 목록은 created_at, id 순으로 읽는다.
        reason_code=records[-1].reason_code,
        rejection_count=sum(
            1
            for record in records
            if record.decision is MappingDecisionType.REJECTED
        ),
        golden_confirmed=any(
            record.reason_code == VerifiedReason.GOLDEN_TEST_CONFIRMED.value
            for record in verified
        ),
        historical_match=any(
            record.reason_code == VerifiedReason.MATCHED_HISTORICAL_CHANGE.value
            for record in verified
        ),
        legacy=any(_is_legacy_backfill(record) for record in verified),
    )


def _is_legacy_backfill(record: MappingDecisionRecord) -> bool:
    return (
        record.actor == LEGACY_BACKFILL_ACTOR
        and (record.reason_text or "") == LEGACY_BACKFILL_REASON_TEXT
    )
