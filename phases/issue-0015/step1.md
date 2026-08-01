# Step 1: db-model-migration

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙)
- `/docs/architecture/ADR.md` (ADR-008 — append-only + compat cache)
- `/docs/specifications/VERIFIED_MAPPING_SPEC.md` (§2 필드, §13 Migration — idempotent 필수)
- `/app/db/models.py` (기존 테이블 — 특히 `Mapping`, `AuditEvent` 스타일)
- `/app/db/database.py` (`init_db`, `_migrate` 기존 패턴 — ADD COLUMN 보정 방식)
- `/app/domain/mappings/decisions.py` (Step 0 산출물 — enum 값)
- `/tests/test_audit_runs.py` (DB 모델 테스트 스타일 참고)

Step 0에서 만든 `MappingDecisionType` 등 enum 값을 그대로 사용하라.

## 작업

### 1) `MappingDecision` 테이블 (`app/db/models.py`)

`AuditEvent` 스타일로 append-only 테이블을 추가한다:

```python
class MappingDecision(Base):
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
```

- 기존 `Mapping` 테이블·`verified` 컬럼은 **그대로 둔다**(삭제·변경 금지).

### 2) legacy backfill migration (`app/db/database.py` `_migrate()`)

`_migrate()` 함수 안에, 새 테이블 생성은 `create_all`이 처리하므로, **기존 `Mapping.verified == True`인데 아직 decision 이벤트가 하나도 없는 매핑에 대해 legacy VERIFIED 이벤트 1건을 backfill** 하는 로직을 추가한다.

- backfill 이벤트: `decision="verified"`, `reason_code="other"`, `actor="system"`, `reason_text="legacy verified backfill"`.
- **반드시 idempotent** — 이미 해당 mapping_id에 decision 행이 있으면 삽입하지 않는다. 이유: `init_db()`는 서버 기동마다 호출되므로 2회 이상 실행돼도 backfill 이벤트는 매핑당 1건이어야 한다.
- 기존 `_migrate()`의 ADD COLUMN 보정 로직은 유지한다. 새 로직은 그 뒤에 덧붙인다.
- `mapping_decision` 테이블이 아직 없으면(구버전 DB) `create_all` 이후 실행되므로 존재한다고 가정 가능하나, 방어적으로 테이블 존재를 확인하고 없으면 skip 하라.

### 3) 테스트

`tests/test_mapping_decision_migration.py` 신규 작성 (기존 `tests/test_audit_runs.py`의 임시 SQLite DB 픽스처 스타일 참고, 무거운 의존성 트리거 금지):
- 임시 DB에 `Mapping(verified=True)` 2건 + `Mapping(verified=False)` 1건을 넣고 `_migrate()` 실행 → verified 2건에만 backfill 이벤트 생성(verified=False에는 없음).
- `_migrate()`를 **2회 연속 실행** → backfill 이벤트 수가 늘지 않음(idempotency).
- 이미 수동 decision이 있는 verified 매핑은 backfill 되지 않음.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `Mapping` 테이블/`verified` 컬럼이 그대로 보존됐는가?
   - backfill이 idempotent한가? (테스트로 고정됐는가)
   - enum 값이 Step 0과 일치하는가?
3. 결과에 따라 `phases/issue-0015/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "MappingDecision 테이블 + idempotent backfill 요약"`
   - 실패(3회) → `"status": "error"`, `"error_message"`
   - 개입 필요 → `"status": "blocked"`, `"blocked_reason"`

## 금지사항

- `Mapping` 테이블이나 `verified` 컬럼을 삭제·이름변경 하지 마라. 이유: 기존 `apply`/`get_mappings`가 이 컬럼에 의존한다(동작 보존).
- backfill을 non-idempotent하게 만들지 마라. 이유: `init_db()`가 기동마다 호출돼 이벤트가 중복 누적된다(스펙 §13).
- API 엔드포인트나 repository를 이 step에서 만들지 마라. 이유: Step 2·3의 범위다.
- 기존 테스트를 깨뜨리지 마라.
