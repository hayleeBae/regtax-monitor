"""Issue #0023 step 3 — 수집 의미 정정의 라우트 배선 검증.

핵심 계약(스펙 §2, ADR-014):
  - 개정문 원문(amendment_text)·제개정이유(reason_text)는 LLM 분석 프롬프트
    컨텍스트로만 쓴다.
  - 값 델타 계산(ChangeNormalizer.normalize) 입력에는 절대 새어들지 않는다 —
    이 결함을 고치는 것이 이 이슈 전체의 목적이다.

무거운 의존성(임베딩·ChromaDB 인덱싱·LLM 실호출)은 건드리지 않는다:
lifespan 을 띄우지 않고 DB 세션만 tmp sqlite 로 갈아끼우며, LLM·DocsIndexer 는
fake 로 주입한다(CLAUDE.md — 테스트에서 무거운 의존성 금지).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.application.services import AnalysisService
from app.db.database import get_session
from app.db.models import Base, LawChange
from app.domain.changes.classification import RuleChangeClassifier
from app.domain.changes.normalization import ChangeNormalizer
from app.llm.common import analyze_prompt

REASON = "종합소득 기본세율 최저구간 과세표준 상한을 상향하려는 것임"
AMEND = '제55조제1항 중 "1천200만원"을 "1천400만원"으로 한다.'


# ── 1) reason_text 는 analyzer(프롬프트)로만, normalize 입력에는 안 들어간다 ──

def test_analysis_service_routes_reason_to_analyzer_not_normalizer() -> None:
    seen: dict = {}

    class RecordingNormalizer:
        def normalize(self, before: str, after: str):
            seen["normalize"] = (before, after)
            return ChangeNormalizer().normalize(before, after)

    def analyzer(before, after, context, amendment_text="", reason_text=""):
        seen["analyzer"] = {
            "amendment_text": amendment_text,
            "reason_text": reason_text,
        }
        return {"summary": "요약", "impact": "영향"}

    service = AnalysisService(RecordingNormalizer(), RuleChangeClassifier(), analyzer)
    service.analyze(
        "제55조제1항 1천200만원",
        "제55조제1항 1천400만원",
        "ctx",
        amendment_text=AMEND,
        reason_text=REASON,
    )

    # amendment/reason 은 analyzer 로 전달된다.
    assert seen["analyzer"]["reason_text"] == REASON
    assert seen["analyzer"]["amendment_text"] == AMEND
    # normalize 입력에는 제개정이유가 새어들지 않는다 (이 이슈의 존재 이유).
    before_in, after_in = seen["normalize"]
    assert REASON not in before_in and REASON not in after_in


def test_analyze_prompt_carries_amendment_and_reason() -> None:
    prompt = analyze_prompt("전", "후", "맥락", amendment_text=AMEND, reason_text=REASON)
    assert REASON in prompt
    assert AMEND in prompt
    # 없으면 블록을 붙이지 않는다 (계측·프롬프트 부풀림 방지).
    bare = analyze_prompt("전", "후", "맥락")
    assert "[개정문 원문]" not in bare
    assert "[제개정이유]" not in bare


# ── TestClient 픽스처 (DB 세션만 tmp sqlite 로 override) ──

@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sem.db'}")
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


# ── 2) mock collect 가 amendment_text/reason_text 를 저장한다 ──

def test_mock_collect_stores_amendment_and_reason(client, session, monkeypatch) -> None:
    # OC 키 유무는 실행 환경(.env)마다 다르다 — 실 API 호출·비결정성을 피하려고
    # mock 모드를 강제한다(LawApiClient._mock_mode). 검증 대상은 저장 배선이다.
    monkeypatch.setattr(main.settings, "law_api_oc", "")
    resp = client.post("/collect")
    assert resp.status_code == 200
    assert resp.json()["mock_mode"] is True

    law_rows = session.query(LawChange).filter(LawChange.source == "law").all()
    assert law_rows, "mock 법령 행이 저장되어야 한다"
    for row in law_rows:
        assert row.amendment_text, "개정문 원문이 저장되어야 한다"
        assert row.reason_text, "제개정이유 원문이 저장되어야 한다"
        # before_text 는 개정문 파싱으로 파생 — 원문과 달라야 한다(스펙 §2, AC2).
        assert row.before_text != row.amendment_text


# ── analyze 라우트: LLM·DocsIndexer 를 fake 로 주입 ──

class _FakeLLM:
    model = "fake-model"

    def analyze_change(self, before, after, context="", amendment_text="", reason_text=""):
        return {"summary": "요약", "impact": "영향"}

    def classify_change(self, before, after, normalized):
        return {
            "primary_type": "value_change",
            "confidence": 0.9,
            "reason": "수치 변경",
            "signals": [{"type": "money", "evidence": "1천200만원→1천400만원"}],
        }

    def complete(self, prompt, max_tokens=4096):
        return '{"summary": "요약", "impact": "영향"}'


class _StubDocsIndexer:
    """임베딩·ChromaDB 를 건드리지 않는 참고문서 검색 stub."""

    def __init__(self, *args, **kwargs):
        pass

    def search(self, query, k=2):
        return []


@pytest.fixture()
def fake_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "get_llm_client", lambda: _FakeLLM())
    monkeypatch.setattr("app.embedding.docs_index.DocsIndexer", _StubDocsIndexer)
    # audit manifest 가 프로젝트 디렉토리를 오염시키지 않도록 tmp 로 격리.
    monkeypatch.setattr(main.settings, "audit_artifact_dir", str(tmp_path / "audit"))
    return _FakeLLM


# ── 3) analyze 응답에 amendment_parsed 가 존재한다 ──

def test_analyze_response_includes_amendment_parsed(client, session, fake_llm) -> None:
    row = LawChange(
        id=1,
        law_id="MOCK-001",
        law_name="소득세법",
        article_no="제55조",
        source="law",
        amendment_text=AMEND,
        reason_text=REASON,
        before_text="제55조제1항 1천200만원",   # 개정문과 다름 → 파싱 성립
        after_text="제55조제1항 1천400만원",
    )
    session.add(row)
    session.commit()

    resp = client.post("/changes/1/analyze")
    assert resp.status_code == 200
    body = resp.json()
    assert "amendment_parsed" in body
    assert body["amendment_parsed"] is True


# ── 4) 행정규칙 건: before="" 여도 오류 없이 "추가" 해석으로 진행 ──

def test_analyze_admin_rule_with_empty_before_succeeds(client, session, fake_llm) -> None:
    row = LawChange(
        id=2,
        law_id="MOCK-ADM-001",
        law_name="2026년 적용 최저임금",
        article_no="",
        source="고시",             # 행정규칙 — 신구대조 없음
        amendment_text="",
        reason_text="",
        before_text="",            # 신설 공표 의미 (스펙 §3-5)
        after_text="시간급 최저임금액은 10,320원으로 한다.",
    )
    session.add(row)
    session.commit()

    resp = client.post("/changes/2/analyze")
    assert resp.status_code == 200
    body = resp.json()
    # 개정문이 없으므로 파싱은 성립하지 않는다.
    assert body["amendment_parsed"] is False
    assert body["summary"]
