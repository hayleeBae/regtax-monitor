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
    if "patch_proposal" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("patch_proposal")}
    adds = {"golden_status": "VARCHAR", "golden_output": "TEXT"}
    with engine.begin() as conn:
        for name, ddl in adds.items():
            if name not in have:
                conn.execute(text(f"ALTER TABLE patch_proposal ADD COLUMN {name} {ddl}"))


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
