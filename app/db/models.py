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
    UniqueConstraint,
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
    # 출처 구분: "law"(법령) 또는 행정규칙 종류("고시"/"훈령"/"예규"/"공고"/"행정규칙").
    # 행정규칙은 고시명에 종류가 안 드러나는 경우가 많아 수집 시점의 종류를 저장한다.
    source = Column(String, default="law")
    # 도메인 (domains.json의 키: "tax"/"hr" 등) — 담당자 라우팅·필터용
    domain = Column(String, index=True, default="tax")
    promulgation_date = Column(String)           # 공포일 (YYYYMMDD) — 알림 트리거
    effective_date = Column(String)              # 시행일 (YYYYMMDD) — 반영 마감
    change_type = Column(String)                 # rate/limit/date/formula/logic
    amendment_text = Column(Text, nullable=True)  # 개정문 원문
    reason_text = Column(Text, nullable=True)     # 제개정이유 원문
    before_text = Column(Text)                   # 개정 전 조문 (개정문 파싱 파생)
    after_text = Column(Text)                    # 개정 후 조문 (개정문 파싱 파생)
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


class MappingDecision(Base):
    """매핑 검증 결정 (append-only — ADR-008).

    `Mapping.verified`는 최신 상태 compatibility cache로 남기고, 실제 이력은
    이 테이블에 쌓는다. decision/reason_code 값은
    `app.domain.mappings.decisions`의 enum 값과 일치한다.
    """

    __tablename__ = "mapping_decision"

    id = Column(Integer, primary_key=True)
    mapping_id = Column(Integer, ForeignKey("mapping.id"), nullable=False, index=True)
    decision = Column(String, nullable=False)          # MappingDecisionType.value
    reason_code = Column(String, nullable=True)
    reason_text = Column(Text, nullable=True)
    repository_commit = Column(String, nullable=True)  # 유효성 스냅샷 (best-effort)
    path_hash = Column(String, nullable=True)
    symbol_hash = Column(String, nullable=True)
    actor = Column(String, nullable=False, default="owner")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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


class ExecutionRun(Base):
    """주요 application 실행 단위."""

    __tablename__ = "execution_run"

    run_id = Column(String, primary_key=True)
    parent_run_id = Column(String, nullable=True, index=True)
    run_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)
    law_change_id = Column(Integer, nullable=True, index=True)
    proposal_id = Column(Integer, nullable=True)
    evaluation_run_id = Column(String, nullable=True)
    source_hash = Column(String, nullable=True)
    repository_alias = Column(String, nullable=True)
    repository_commit = Column(String, nullable=True)
    settings_hash = Column(String, nullable=True)
    llm_backend = Column(String, nullable=True)
    llm_model = Column(String, nullable=True)
    embedding_model = Column(String, nullable=True)
    prompt_versions = Column(Text, default="{}")
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    error_category = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)


class AuditEvent(Base):
    """append-only 구조화 실행 이벤트."""

    __tablename__ = "audit_event"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_no", name="uq_audit_run_sequence"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(String, nullable=False, index=True)
    sequence_no = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False, index=True)
    occurred_at = Column(DateTime, nullable=False)
    payload = Column(Text, default="{}")
    artifact_refs = Column(Text, default="[]")
