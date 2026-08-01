"""Issue #0015 매핑 결정 API 테스트 (기존 verify API 회귀 포함).

무거운 의존성(임베딩·ChromaDB 인덱싱·LLM)은 건드리지 않는다 — lifespan 을 띄우지
않고(`with TestClient(...)` 미사용) DB 세션만 tmp sqlite 로 갈아끼운다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import get_session
from app.db.models import Base, LawChange, Mapping
from app.main import app

ARTICLE_ID = "L-1:12"


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    db.add(LawChange(id=1, law_id="L-1", article_no="12"))
    db.add_all(
        [
            Mapping(
                id=1,
                article_id=ARTICLE_ID,
                path="src/tax.py",
                symbol="calc_tax",
                confidence=0.8,
                verified=False,
            ),
            Mapping(
                id=2,
                article_id=ARTICLE_ID,
                path="src/hr.py",
                symbol="calc_leave",
                confidence=0.5,
                verified=False,
            ),
        ]
    )
    db.commit()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _verified_mappings(session):
    """apply 가 사용하는 verified 필터와 같은 질의 (회귀 확인용)."""
    return (
        session.query(Mapping)
        .filter(Mapping.article_id == ARTICLE_ID, Mapping.verified == True)  # noqa: E712
        .order_by(Mapping.confidence.desc())
        .all()
    )


def test_verify_true_keeps_response_shape_and_appends_verified_decision(client, session):
    response = client.patch("/mappings/1/verify?verified=true")

    assert response.status_code == 200
    assert response.json() == {
        "mapping_id": 1,
        "path": "src/tax.py",
        "symbol": "calc_tax",
        "verified": True,
    }

    session.expire_all()
    assert session.get(Mapping, 1).verified is True

    history = client.get("/mappings/1/decisions").json()
    assert [d["decision"] for d in history] == ["verified"]
    assert history[0]["actor"] == "owner"
    assert client.get("/mappings/1/state").json()["state"] == "verified"


def test_verify_false_revokes_and_updates_compat_cache(client, session):
    client.patch("/mappings/1/verify?verified=true")

    response = client.patch("/mappings/1/verify?verified=false&actor=haylee")

    assert response.status_code == 200
    assert response.json()["verified"] is False
    session.expire_all()
    assert session.get(Mapping, 1).verified is False

    history = client.get("/mappings/1/decisions").json()
    assert [d["decision"] for d in history] == ["verified", "revoked"]
    assert history[-1]["actor"] == "haylee"

    state = client.get("/mappings/1/state").json()
    assert state["state"] == "revoked"
    assert state["verified"] is False
    assert state["decision_count"] == 2


def test_apply_still_sees_only_verified_mappings(client, session):
    assert _verified_mappings(session) == []

    client.patch("/mappings/1/verify?verified=true")
    session.expire_all()
    assert [m.id for m in _verified_mappings(session)] == [1]

    verified_only = client.get("/changes/1/mappings?verified_only=true").json()
    assert [m["id"] for m in verified_only] == [1]

    client.patch("/mappings/1/verify?verified=false")
    session.expire_all()
    assert _verified_mappings(session) == []
    assert client.get("/changes/1/mappings?verified_only=true").json() == []


def test_post_rejected_decision_shows_in_history_and_state(client, session):
    client.patch("/mappings/1/verify?verified=true")

    response = client.post(
        "/mappings/1/decisions",
        json={
            "decision": "rejected",
            "reason_code": "wrong_module",
            "reason_text": "다른 모듈의 동일 상수",
            "actor": "haylee",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["decision"] == "rejected"
    assert body["state"] == "rejected"
    assert body["verified"] is False
    assert body["decision_id"] > 0

    history = client.get("/mappings/1/decisions").json()
    assert [d["decision"] for d in history] == ["verified", "rejected"]
    assert history[-1]["reason_code"] == "wrong_module"

    state = client.get("/mappings/1/state").json()
    assert state["state"] == "rejected"
    assert state["reason_code"] == "wrong_module"
    assert state["reason_text"] == "다른 모듈의 동일 상수"
    assert state["repository_commit"] is None

    session.expire_all()
    assert session.get(Mapping, 1).verified is False


def test_stale_then_reverified_resolves_to_verified(client, session):
    client.patch("/mappings/1/verify?verified=true")
    client.post(
        "/mappings/1/decisions",
        json={"decision": "stale", "reason_code": "file_missing"},
    )
    session.expire_all()
    assert session.get(Mapping, 1).verified is False

    client.post(
        "/mappings/1/decisions",
        json={"decision": "verified", "reason_code": "golden_test_confirmed"},
    )

    assert client.get("/mappings/1/state").json()["state"] == "verified"
    session.expire_all()
    assert session.get(Mapping, 1).verified is True


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "rejected", "reason_code": "file_missing"},  # stale 사유
        {"decision": "verified", "reason_code": "wrong_module"},  # rejected 사유
        {"decision": "revoked", "reason_code": "confirmed_by_owner"},
        {"decision": "approved"},  # 존재하지 않는 decision
    ],
)
def test_invalid_decision_or_reason_code_is_rejected(client, session, payload):
    response = client.post("/mappings/1/decisions", json=payload)

    assert response.status_code == 422
    assert client.get("/mappings/1/decisions").json() == []
    session.expire_all()
    assert session.get(Mapping, 1).verified is False


def test_verify_rejects_reason_code_of_other_decision_type(client):
    response = client.patch("/mappings/1/verify?verified=true&reason_code=wrong_module")

    assert response.status_code == 422
    assert client.get("/mappings/1/decisions").json() == []


def test_unknown_mapping_returns_404(client):
    assert client.patch("/mappings/999/verify").status_code == 404
    assert client.get("/mappings/999/decisions").status_code == 404
    assert client.get("/mappings/999/state").status_code == 404
    assert (
        client.post("/mappings/999/decisions", json={"decision": "verified"}).status_code
        == 404
    )


def test_histories_of_different_mappings_do_not_mix(client):
    client.patch("/mappings/1/verify?verified=true")
    client.post(
        "/mappings/2/decisions",
        json={"decision": "rejected", "reason_code": "legacy_code"},
    )

    assert [d["decision"] for d in client.get("/mappings/1/decisions").json()] == [
        "verified"
    ]
    assert [d["decision"] for d in client.get("/mappings/2/decisions").json()] == [
        "rejected"
    ]
