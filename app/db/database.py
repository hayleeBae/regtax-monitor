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


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
