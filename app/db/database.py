from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db() -> None:
    from app.db import models  # noqa: F401  (모델 등록)

    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate() -> None:
    """create_all은 기존 테이블에 새 컬럼을 추가하지 않으므로 가벼운 ADD COLUMN 보정."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    table_adds = {
        "patch_proposal": {"golden_status": "VARCHAR", "golden_output": "TEXT"},
        "law_change": {
            "source": "VARCHAR DEFAULT 'law'",
            "domain": "VARCHAR DEFAULT 'tax'",
            "amendment_text": "TEXT",
            "reason_text": "TEXT",
        },
    }
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, adds in table_adds.items():
            if table not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in adds.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    _backfill_legacy_mapping_decisions(tables)
    _backfill_collection_semantics(tables)


def _backfill_collection_semantics(tables: set) -> None:
    """기존 행의 before/after(잘못된 의미: 개정문/제개정이유)를
    amendment/reason으로 이관하고 before/after를 파서로 재파생한다. idempotent.

    구 수집 계층은 `before_text`에 개정문을, `after_text`에 제개정이유를 잘못
    저장했다(COLLECTION_SEMANTICS_SPEC §1). 이 함수는 그 원문을 신규 필드로
    이관한 뒤, before/after를 §3 결정론 파서(parse_amendment→derive_before_after)
    로 재파생한다. `amendment_text IS NOT NULL`인 행은 이미 이관됐으므로 건너뛴다
    — `init_db()`가 기동마다 호출돼도 행당 이관은 1회다. LLM은 쓰지 않는다.
    """
    from sqlalchemy import text

    from app.domain.changes.amendment import derive_before_after, parse_amendment

    if "law_change" not in tables:
        return

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, before_text, after_text
                FROM law_change
                WHERE amendment_text IS NULL
                  AND source = 'law'
                  AND before_text IS NOT NULL
                  AND before_text != ''
                """
            )
        ).all()

        for row_id, before_text, after_text in rows:
            amendment_text = before_text
            reason_text = after_text
            new_before, new_after = derive_before_after(
                parse_amendment(amendment_text), fallback_text=amendment_text
            )
            conn.execute(
                text(
                    """
                    UPDATE law_change
                    SET amendment_text = :amendment_text,
                        reason_text = :reason_text,
                        before_text = :before_text,
                        after_text = :after_text
                    WHERE id = :id
                    """
                ),
                {
                    "amendment_text": amendment_text,
                    "reason_text": reason_text,
                    "before_text": new_before,
                    "after_text": new_after,
                    "id": row_id,
                },
            )


def _backfill_legacy_mapping_decisions(tables: set) -> None:
    """기존 `Mapping.verified=True`에 legacy VERIFIED 결정 1건을 채운다 (스펙 §13).

    `init_db()`가 서버 기동마다 호출되므로 idempotent해야 한다 — 이미 결정 이력이
    있는 매핑은 건너뛰므로 몇 번 실행해도 매핑당 backfill 이벤트는 1건이다.
    """
    from datetime import datetime

    from sqlalchemy import text

    if "mapping" not in tables or "mapping_decision" not in tables:
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO mapping_decision (
                    mapping_id, decision, reason_code, reason_text, actor, created_at
                )
                SELECT m.id, 'verified', 'other', 'legacy verified backfill',
                       'system', :created_at
                FROM mapping m
                WHERE m.verified = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM mapping_decision d WHERE d.mapping_id = m.id
                  )
                """
            ),
            {"created_at": datetime.utcnow().isoformat(sep=" ")},
        )


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
