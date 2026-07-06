from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)

from app.db.database import Base


class LawChange(Base):
    """법령 변경 건"""

    __tablename__ = "law_change"

    id = Column(Integer, primary_key=True)
    law_id = Column(String, index=True)          # 법령 ID
    law_mst = Column(String)                     # 법령 MST (상세 조회용)
    law_name = Column(String)                    # 법령명
    article_no = Column(String)                  # 조문 번호
    promulgation_date = Column(String)           # 공포일 (YYYYMMDD) — 알림 트리거
    effective_date = Column(String)              # 시행일 (YYYYMMDD) — 반영 마감
    change_type = Column(String)                 # rate/limit/date/formula/logic
    before_text = Column(Text)                   # 개정 전 조문
    after_text = Column(Text)                    # 개정 후 조문
    ai_summary = Column(Text)                    # AI 요약
    ai_impact = Column(Text)                     # AI 영향 분석
    status = Column(String, default="new")       # new/reviewing/pending_apply/done
    created_at = Column(DateTime, default=datetime.utcnow)


class Mapping(Base):
    """조문 ↔ 코드 매핑 (살아있는 데이터: AI 부트스트랩 + 사람 큐레이션)"""

    __tablename__ = "mapping"

    id = Column(Integer, primary_key=True)
    article_id = Column(String, index=True)      # 조문 식별자
    repo = Column(String)                        # 대상 repo
    path = Column(String)                        # 파일 경로
    symbol = Column(String)                      # 함수/상수/SQL 위치
    change_type = Column(String)
    confidence = Column(Float, default=0.0)      # AI 제안 신뢰도
    code_hash = Column(String)                   # drift 감지용
    verified = Column(Boolean, default=False)    # 사람 검증 여부


class Review(Base):
    """담당자 검토"""

    __tablename__ = "review"

    id = Column(Integer, primary_key=True)
    law_change_id = Column(Integer, ForeignKey("law_change.id"))
    reviewer = Column(String)
    comment = Column(Text)
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)


class PatchProposal(Base):
    """코드 수정안 (사람 승인 전 초안 — 자동 적용 금지)"""

    __tablename__ = "patch_proposal"

    id = Column(Integer, primary_key=True)
    law_change_id = Column(Integer, ForeignKey("law_change.id"))
    mapping_id = Column(Integer, ForeignKey("mapping.id"))
    diff = Column(Text)                          # 제안된 unified diff
    model_used = Column(String)
    approval_status = Column(String, default="draft")  # draft/approved/rejected
    golden_status = Column(String, nullable=True)  # passed/failed/apply_failed/skipped/error (None=미실행)
    golden_output = Column(Text, nullable=True)    # 골든 테스트 출력 (승인 판단 자료)
    created_at = Column(DateTime, default=datetime.utcnow)


class SyncState(Base):
    """수집 동기화 상태 (단일 행)"""

    __tablename__ = "sync_state"

    id = Column(Integer, primary_key=True, default=1)
    last_sync = Column(String, nullable=True)    # 마지막 수집 기준일 YYYYMMDD
    last_run_at = Column(DateTime, nullable=True)
