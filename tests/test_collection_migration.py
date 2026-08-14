"""Issue #0023 step 2 — 수집 필드 의미 정정(collection semantics) 마이그레이션 테스트.

구 수집 계층은 `before_text`에 개정문, `after_text`에 제개정이유를 잘못 저장했다.
`_migrate()`는 그 원문을 `amendment_text`/`reason_text`로 이관하고 before/after를
개정문 파서로 재파생한다. 행정규칙 행(source != 'law')은 건드리지 않는다.
`test_mapping_decision_migration.py`의 임시 sqlite 방식을 따른다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.database import Base, _migrate
from app.db.models import LawChange
from app.domain.changes.amendment import derive_before_after, parse_amendment

# 실 P1 문형 개정문 (step 0 파서가 인식하는 문형).
_AMENDMENT = '제59조의2제1항 중 "연 15만원"을 "연 25만원"으로 한다.'
_REASON = "근로장려금 지급 한도를 상향하여 저소득 근로자 지원을 강화함."


@pytest.fixture()
def session(monkeypatch, tmp_path):
    """임시 SQLite 파일 DB — `_migrate()`는 모듈 전역 engine을 사용한다."""
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _expected_derived() -> tuple[str, str]:
    return derive_before_after(parse_amendment(_AMENDMENT), fallback_text=_AMENDMENT)


def test_law_row_migrates_and_rederives(session) -> None:
    # 구 의미 행: before=개정문, after=제개정이유, amendment_text=NULL.
    session.add(
        LawChange(
            id=1,
            source="law",
            law_name="조세특례제한법",
            before_text=_AMENDMENT,
            after_text=_REASON,
        )
    )
    session.commit()

    _migrate()

    session.expire_all()
    row = session.get(LawChange, 1)
    # 원문 이관
    assert row.amendment_text == _AMENDMENT
    assert row.reason_text == _REASON
    # before/after 재파생
    exp_before, exp_after = _expected_derived()
    assert row.before_text == exp_before
    assert row.after_text == exp_after
    # 파생값은 원문과 다르다 (파싱 성공)
    assert row.before_text != row.amendment_text
    assert row.after_text != row.reason_text


def test_admin_rule_row_is_untouched(session) -> None:
    # 행정규칙 행: after=본문이 이미 올바른 의미 — 백필이 건드리면 안 된다.
    body = "고시 본문 전문 …"
    session.add(
        LawChange(
            id=2,
            source="고시",
            law_name="근로장려금 지급 고시",
            before_text="",
            after_text=body,
        )
    )
    session.commit()

    _migrate()

    session.expire_all()
    row = session.get(LawChange, 2)
    assert row.amendment_text is None
    assert row.reason_text is None
    assert row.before_text == ""
    assert row.after_text == body


def test_backfill_is_idempotent(session) -> None:
    session.add(
        LawChange(
            id=1,
            source="law",
            law_name="조세특례제한법",
            before_text=_AMENDMENT,
            after_text=_REASON,
        )
    )
    session.commit()

    _migrate()
    session.expire_all()
    once = session.get(LawChange, 1)
    once_state = (once.amendment_text, once.reason_text, once.before_text, once.after_text)

    _migrate()
    _migrate()
    session.expire_all()
    twice = session.get(LawChange, 1)
    twice_state = (
        twice.amendment_text,
        twice.reason_text,
        twice.before_text,
        twice.after_text,
    )

    # 재파생이 두 번 적용돼도 값이 또 바뀌지 않아야 한다.
    assert once_state == twice_state


def test_migrate_on_empty_new_schema_db(session) -> None:
    # 신규 스키마로 생성된 빈 DB에서 오류 없이 통과.
    _migrate()

    session.expire_all()
    assert session.query(LawChange).count() == 0
