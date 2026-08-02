"""Issue #0016 검증 이력 lookup 테스트.

이 모듈은 rerank 문맥만 공급한다 — 점수·delta 계산은
`app/domain/mappings/reranking.py` 단일 출처이므로 여기서 검증하지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Mapping, MappingDecision
from app.domain.common.enums import RetrievalSource
from app.domain.mappings.decisions import (
    MappingDecisionType,
    RejectedReason,
    VerifiedReason,
)
from app.domain.mappings.reranking import RERANK_VERSION
from app.domain.retrieval import (
    CandidateLocation,
    RetrievalCandidate,
    RetrievalEvidence,
)
from app.mappings.reranking_lookup import SqlAlchemyDecisionContextLookup
from app.retrieval.orchestrator import RetrievalQuery

ARTICLE_ID = "L-1:12"
OTHER_ARTICLE_ID = "L-9:3"
_BASE_TIME = datetime(2026, 3, 1, 9, 0)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rerank.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _mapping(session, mapping_id: int, **kwargs) -> Mapping:
    row = Mapping(
        id=mapping_id,
        article_id=kwargs.pop("article_id", ARTICLE_ID),
        path=kwargs.pop("path", "src/tax.py"),
        symbol=kwargs.pop("symbol", "calc_tax"),
        change_type=kwargs.pop("change_type", "value_change"),
        verified=kwargs.pop("verified", False),
        **kwargs,
    )
    session.add(row)
    session.commit()
    return row


def _decision(
    session,
    mapping_id: int,
    decision: MappingDecisionType,
    *,
    minutes: int = 0,
    reason_code: str | None = None,
    reason_text: str | None = None,
    actor: str = "owner",
) -> None:
    session.add(
        MappingDecision(
            mapping_id=mapping_id,
            decision=decision.value,
            reason_code=reason_code,
            reason_text=reason_text,
            actor=actor,
            created_at=_BASE_TIME + timedelta(minutes=minutes),
        )
    )
    session.commit()


def _query() -> RetrievalQuery:
    return RetrievalQuery(
        "소득세법 제12조", article_id=ARTICLE_ID, change_type="value_change"
    )


def _candidate(path: str, symbol: str | None) -> RetrievalCandidate:
    evidence = RetrievalEvidence(
        RetrievalSource.VERIFIED_MAPPING, 1.0, 1.0, provider_version="test"
    )
    return RetrievalCandidate(CandidateLocation(path, symbol), (evidence,), 1.0)


def _lookup(session, article_id: str = ARTICLE_ID):
    return SqlAlchemyDecisionContextLookup(session, article_id)


def test_version_is_the_domain_rerank_version(session) -> None:
    assert _lookup(session).version == RERANK_VERSION


def test_verified_history_becomes_verified_context(session) -> None:
    _mapping(session, 1, verified=True)
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
    )

    contexts = _lookup(session).contexts_for(_query(), ())

    (context,) = contexts["src/tax.py::symbol:calc_tax"]
    assert context.state is MappingDecisionType.VERIFIED
    assert context.reason_code == VerifiedReason.CONFIRMED_BY_OWNER.value
    assert context.article_id == ARTICLE_ID
    assert context.change_type == "value_change"
    assert context.rejection_count == 0
    assert context.golden_confirmed is False
    assert context.historical_match is False
    assert context.legacy is False


def test_rejected_mapping_is_returned_even_though_verified_is_false(session) -> None:
    """핵심 회귀 — verified=False 를 걸러내면 rejected penalty 가 영원히 죽는다."""
    _mapping(session, 1, symbol="calc_leave", verified=False)
    _decision(
        session,
        1,
        MappingDecisionType.REJECTED,
        reason_code=RejectedReason.WRONG_MODULE.value,
    )

    contexts = _lookup(session).contexts_for(_query(), ())

    (context,) = contexts["src/tax.py::symbol:calc_leave"]
    assert context.state is MappingDecisionType.REJECTED
    assert context.reason_code == RejectedReason.WRONG_MODULE.value
    assert context.rejection_count == 1


def test_rejection_count_counts_every_rejected_event(session) -> None:
    _mapping(session, 1)
    for index in range(3):
        _decision(
            session,
            1,
            MappingDecisionType.REJECTED,
            minutes=index,
            reason_code=RejectedReason.FALSE_POSITIVE_TERM.value,
        )

    (context,) = _lookup(session).contexts_for(_query(), ())[
        "src/tax.py::symbol:calc_tax"
    ]

    assert context.rejection_count == 3
    assert context.state is MappingDecisionType.REJECTED


def test_golden_and_historical_reasons_set_their_flags(session) -> None:
    _mapping(session, 1, verified=True)
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        minutes=0,
        reason_code=VerifiedReason.GOLDEN_TEST_CONFIRMED.value,
    )
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        minutes=1,
        reason_code=VerifiedReason.MATCHED_HISTORICAL_CHANGE.value,
    )

    (context,) = _lookup(session).contexts_for(_query(), ())[
        "src/tax.py::symbol:calc_tax"
    ]

    assert context.golden_confirmed is True
    assert context.historical_match is True
    # reason_code 는 시간순 마지막 결정의 값이다.
    assert context.reason_code == VerifiedReason.MATCHED_HISTORICAL_CHANGE.value


def test_backfilled_legacy_event_is_marked_legacy(session) -> None:
    """#0015 backfill(`_backfill_legacy_mapping_decisions`)이 넣는 값과 일치해야 한다."""
    _mapping(session, 1, verified=True)
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.OTHER.value,
        reason_text="legacy verified backfill",
        actor="system",
    )

    (context,) = _lookup(session).contexts_for(_query(), ())[
        "src/tax.py::symbol:calc_tax"
    ]

    assert context.legacy is True
    assert context.state is MappingDecisionType.VERIFIED


def test_owner_verified_history_is_not_legacy(session) -> None:
    _mapping(session, 1, verified=True)
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
        reason_text="담당자 확인",
        actor="haylee",
    )

    (context,) = _lookup(session).contexts_for(_query(), ())[
        "src/tax.py::symbol:calc_tax"
    ]

    assert context.legacy is False


def test_mapping_without_history_is_absent(session) -> None:
    _mapping(session, 1, path="src/hr.py", symbol="calc_leave")

    assert _lookup(session).contexts_for(_query(), ()) == {}


def test_other_article_mappings_do_not_leak(session) -> None:
    _mapping(session, 1)
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
    )
    _mapping(session, 2, article_id=OTHER_ARTICLE_ID, path="src/other.py")
    _decision(
        session,
        2,
        MappingDecisionType.REJECTED,
        reason_code=RejectedReason.WRONG_MODULE.value,
    )

    contexts = _lookup(session).contexts_for(_query(), ())

    assert list(contexts) == ["src/tax.py::symbol:calc_tax"]


@pytest.mark.parametrize("bad_path", ["", "/abs/path.py", "../escape.py"])
def test_malformed_path_is_skipped_without_raising(session, bad_path) -> None:
    _mapping(session, 1, path=bad_path, symbol="broken")
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
    )
    _mapping(session, 2, path="src/tax.py", symbol="calc_tax")
    _decision(
        session,
        2,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
    )

    contexts = _lookup(session).contexts_for(_query(), ())

    assert list(contexts) == ["src/tax.py::symbol:calc_tax"]


def test_keys_match_candidate_dedup_key_with_and_without_symbol(session) -> None:
    _mapping(session, 1, path="src/tax.py", symbol="calc_tax")
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
    )
    _mapping(session, 2, path="src/hr.py", symbol="")
    _decision(
        session,
        2,
        MappingDecisionType.REJECTED,
        reason_code=RejectedReason.TEST_ONLY.value,
    )

    contexts = _lookup(session).contexts_for(_query(), ())

    with_symbol = _candidate("src/tax.py", "calc_tax")
    without_symbol = _candidate("src/hr.py", None)
    assert with_symbol.dedup_key in contexts
    assert without_symbol.dedup_key in contexts
    assert set(contexts) == {with_symbol.dedup_key, without_symbol.dedup_key}


def test_mappings_sharing_a_dedup_key_are_collected_together(session) -> None:
    _mapping(session, 1, change_type="value_change")
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
    )
    _mapping(session, 2, change_type="rate_change")
    _decision(
        session,
        2,
        MappingDecisionType.REJECTED,
        reason_code=RejectedReason.WRONG_MODULE.value,
    )

    contexts = _lookup(session).contexts_for(_query(), ())

    states = {
        context.state for context in contexts["src/tax.py::symbol:calc_tax"]
    }
    assert states == {MappingDecisionType.VERIFIED, MappingDecisionType.REJECTED}


def test_lookup_does_not_write_decisions(session) -> None:
    _mapping(session, 1)
    _decision(
        session,
        1,
        MappingDecisionType.VERIFIED,
        reason_code=VerifiedReason.CONFIRMED_BY_OWNER.value,
    )
    before = session.query(MappingDecision).count()

    _lookup(session).contexts_for(
        _query(), (_candidate("src/tax.py", "calc_tax"),)
    )

    assert session.query(MappingDecision).count() == before
