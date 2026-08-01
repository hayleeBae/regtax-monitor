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
