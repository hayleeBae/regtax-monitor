"""이슈 #0025 step 2 — /changes/{id}/apply DB 라우팅 통합 테스트.

DB_DATA_ROUTING_SPEC §5·§11: 레지스트리 정확 매칭 건은 매핑 조회·LLM patch
생성 경로에 진입하지 않고 db_update_guidance 안내를 반환해야 한다. 매칭 판정은
매핑(Mapping) 유무보다 먼저 이루어져야 하므로, 매칭 건은 매핑이 없어도 422로
빠지면 안 된다.

무거운 의존성(임베딩·ChromaDB·LLM)은 이 테스트에서 건드리지 않는다(CLAUDE.md).
lifespan 을 띄우지 않고 DB 세션만 tmp sqlite 로 갈아끼우며, get_llm_client와
_make_mapping_service는 호출 시 즉시 실패하는 스텁으로 교체해 "진입하지 않음"을
직접 검증한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.collector.registry import DbItem, Domain
from app.db.database import get_session
from app.db.models import Base, LawChange, Mapping

LAW_ID = "001766"
ARTICLE_NO = "제129조"
ARTICLE_ID = f"{LAW_ID}:{ARTICLE_NO}"


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'apply_db_routing.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def client(session):
    main.app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.pop(get_session, None)


def _forbid_llm(monkeypatch) -> None:
    def _boom():
        raise AssertionError("db_match 경로에서 LLM 클라이언트를 호출하면 안 된다")

    monkeypatch.setattr(main, "get_llm_client", _boom)


def _forbid_mapping_service(monkeypatch) -> None:
    def _boom(db, article_id):
        raise AssertionError("db_match 경로에서 매핑 검색(retrieval)에 진입하면 안 된다")

    monkeypatch.setattr(main, "_make_mapping_service", _boom)


def _domains_with_match() -> dict:
    return {
        "tax": Domain(
            key="tax",
            label="세법(연말정산)",
            laws=[],
            admin_rule_queries=[],
            db_items=[
                DbItem(
                    law_id=LAW_ID,
                    article_pattern="제129조",
                    item_label="근로소득 간이세액표",
                    db_hint="급여 세액 산정표(DB 관리)",
                    guidance="본 개정은 코드 patch 대상이 아니라 DB 데이터 갱신 대상입니다.",
                )
            ],
        )
    }


def _domains_without_match() -> dict:
    return {
        "tax": Domain(
            key="tax", label="세법(연말정산)", laws=[], admin_rule_queries=[], db_items=[]
        )
    }


def _make_change(session, **overrides) -> LawChange:
    row = LawChange(
        id=1,
        law_id=LAW_ID,
        law_name="소득세법",
        article_no=ARTICLE_NO,
        before_text="근로소득 간이세액표 상 세율은 8%로 한다.",
        after_text="근로소득 간이세액표 상 세율은 6%로 한다.",
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


def test_apply_returns_db_update_guidance_without_llm_or_mapping_lookup(
    client, session, monkeypatch
) -> None:
    monkeypatch.setattr(main, "load_domains", _domains_with_match)
    _forbid_llm(monkeypatch)
    _forbid_mapping_service(monkeypatch)
    _make_change(session)
    # 매핑이 전혀 없어도(=매핑 조회 없이도) db_update_guidance 로 응답해야 한다.

    resp = client.post("/changes/1/apply")

    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    assert body["decision"] == "db_update_guidance"
    assert body["item_label"] == "근로소득 간이세액표"
    assert body["law_name"] == "소득세법"
    assert body["article"] == ARTICLE_NO
    assert body["before"]
    assert body["after"]
    assert body["guidance"] == "본 개정은 코드 patch 대상이 아니라 DB 데이터 갱신 대상입니다."
    # 실제 DB 스키마 원문(db_hint)은 응답에 새지 않는다(스펙 §8).
    assert "db_hint" not in body
    assert "diff_preview" not in body
    assert "run_id" in body


def test_apply_db_match_takes_priority_over_existing_mapping(
    client, session, monkeypatch
) -> None:
    """매칭 판정은 매핑 유무보다 먼저 — 매핑이 있어도 db_update_guidance 로 라우팅된다."""
    monkeypatch.setattr(main, "load_domains", _domains_with_match)
    _forbid_llm(monkeypatch)
    _forbid_mapping_service(monkeypatch)
    _make_change(session)
    session.add(
        Mapping(
            article_id=ARTICLE_ID,
            path="src/tax/WithholdingTax.java",
            symbol="calcRate",
            confidence=0.9,
            verified=True,
        )
    )
    session.commit()

    resp = client.post("/changes/1/apply")

    assert resp.status_code == 200
    assert resp.json()["decision"] == "db_update_guidance"


def test_apply_unmatched_change_keeps_existing_422_when_no_mapping(
    client, session, monkeypatch
) -> None:
    """회귀: db_items 미매칭 건은 기존 동작(매핑 없으면 422) 그대로 유지된다."""
    monkeypatch.setattr(main, "load_domains", _domains_without_match)
    _make_change(session)

    resp = client.post("/changes/1/apply")

    assert resp.status_code == 422
    assert "매핑이 없습니다" in resp.json()["detail"]
