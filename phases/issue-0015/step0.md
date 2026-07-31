# Step 0: domain-decisions

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 특히 도메인 계층 순수성)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙: `app/domain/`은 순수 계약)
- `/docs/architecture/ADR.md` (ADR-008 — 이 작업의 근거)
- `/docs/specifications/VERIFIED_MAPPING_SPEC.md` (구현 계약 — §2 모델, §3 reason code, §4 상태 계산, §8 stale validator)
- `/app/domain/audit/records.py` (본보기 패턴 — frozen dataclass + str Enum + `__post_init__` 검증)
- `/app/domain/common/enums.py` (기존 enum 스타일)

이전에 만들어진 audit 도메인 코드(`app/domain/audit/records.py`)를 꼼꼼히 읽고, 같은 스타일(frozen dataclass, `str, Enum`, immutable payload)을 그대로 따르라.

## 작업

순수 도메인 모듈 `app/domain/mappings/decisions.py`와 패키지 `app/domain/mappings/__init__.py`를 신규 생성한다. **DB·FastAPI·LLM에 의존하지 않는 순수 Python만** 담는다.

### 1) enum (스펙 §2, §3)

```python
class MappingDecisionType(str, Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    STALE = "stale"
    REVOKED = "revoked"

class VerifiedReason(str, Enum):    # 스펙 §3 Verified 목록
    CONFIRMED_BY_OWNER = "confirmed_by_owner"
    MATCHED_HISTORICAL_CHANGE = "matched_historical_change"
    GOLDEN_TEST_CONFIRMED = "golden_test_confirmed"
    EXACT_CONSTANT_CONFIRMED = "exact_constant_confirmed"
    DOMAIN_MAPPING_CONFIRMED = "domain_mapping_confirmed"
    OTHER = "other"

class RejectedReason(str, Enum):    # 스펙 §3 Rejected 목록
    WRONG_MODULE = "wrong_module"
    LEGACY_CODE = "legacy_code"
    FALSE_POSITIVE_TERM = "false_positive_term"
    SAME_VALUE_UNRELATED = "same_value_unrelated"
    GENERATED_CODE = "generated_code"
    TEST_ONLY = "test_only"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    OTHER = "other"

class StaleReason(str, Enum):       # 스펙 §3 Stale 목록
    FILE_MISSING = "file_missing"
    SYMBOL_MISSING = "symbol_missing"
    CONTENT_CHANGED = "content_changed"
    MODULE_MOVED = "module_moved"
    REPOSITORY_REPLACED = "repository_replaced"
```

- reason_code 유효성 검증에 쓸 수 있도록, decision 타입 → 허용 reason 값 집합을 돌려주는 헬퍼(예: `allowed_reason_codes(decision: MappingDecisionType) -> frozenset[str]`)를 제공하라. REVOKED는 reason 필수 아님(빈 집합 또는 OTHER 허용) — 스펙에 REVOKED reason 목록이 없으므로 reason_code는 선택(None 허용)으로 둔다.

### 2) 레코드 (스펙 §2 필드, §7 유효성 정보)

```python
@dataclass(frozen=True)
class MappingDecisionRecord:
    mapping_id: int
    decision: MappingDecisionType
    reason_code: str | None = None
    reason_text: str | None = None
    repository_commit: str | None = None   # 유효성 스냅샷 (nullable best-effort)
    path_hash: str | None = None
    symbol_hash: str | None = None
    actor: str = "owner"                    # 인증 부재 → 자기신고, 기본 owner
    created_at: datetime | None = None
```

- `__post_init__`에서 `mapping_id`가 양수인지, `decision`이 `MappingDecisionType`인지 검증하라. `reason_text`에는 코드 본문을 넣지 않는다는 것은 호출자 책임이므로 여기서 강제하지 않는다(단 docstring에 명시).

### 3) 상태 계산 순수 함수 (스펙 §4)

```python
def resolve_state(
    decisions: Sequence[MappingDecisionRecord],
) -> MappingDecisionType | None:
    """이력을 시간순으로 접어 최신 상태를 계산한다. 비어 있으면 None."""
```

규칙(스펙 §4):
- 시간순(created_at, 동률이면 입력 순서)으로 접는다.
- `VERIFIED → STALE → VERIFIED` 의 최종 상태는 VERIFIED.
- `REVOKED`는 직전 verified 상태를 취소한다(상태를 None 또는 revoked로). 후보 자체를 삭제하지는 않는다 — 이 함수는 상태만 반환하고 후보 목록을 건드리지 않는다.
- 마지막 이벤트가 곧 현재 상태가 되도록 단순 fold로 구현하되, 위 예시가 성립하는지 테스트로 고정하라.

### 4) stale validator helper (스펙 §8)

```python
def check_stale(
    *,
    file_exists: bool,
    symbol_present: bool,
    stored_file_hash: str | None,
    current_file_hash: str | None,
) -> str | None:
    """stale 사유(StaleReason.value) 또는 'modified_but_valid' 또는 None을 반환.
    파일 없음 → file_missing, symbol 없음 → symbol_missing,
    hash 다르지만 symbol 유효 → 'modified_but_valid' (자동 stale 아님).
    이 함수는 판정만 하며 이벤트를 생성하지 않는다(스펙 §8)."""
```

### 5) 패키지 export

`app/domain/mappings/__init__.py`에서 위 심볼들을 export 한다(audit `__init__.py` 스타일).

## 테스트

`tests/test_mapping_decisions.py` 신규 작성:
- resolver: 빈 이력→None, 단일 VERIFIED→VERIFIED, VERIFIED→STALE→VERIFIED→VERIFIED, VERIFIED→REVOKED→취소됨, REJECTED 최신→REJECTED.
- `allowed_reason_codes`가 타입별 올바른 집합 반환.
- `MappingDecisionRecord` 잘못된 mapping_id(0/음수) → ValueError.
- `check_stale`: file_missing / symbol_missing / modified_but_valid / None(정상) 각 케이스.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/domain/mappings/`가 FastAPI/SQLAlchemy/LLM SDK를 import하지 않는가? (`grep -n "import" app/domain/mappings/*.py`로 확인)
   - ARCHITECTURE.md 디렉토리 구조(`app/domain/mappings/decisions.py`)를 따르는가?
   - 스펙 §2·§3·§4의 이름·값과 정확히 일치하는가?
3. 결과에 따라 `phases/issue-0015/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일과 export 심볼, resolver 규칙 요약"`
   - 실패(3회) → `"status": "error"`, `"error_message"`
   - 개입 필요 → `"status": "blocked"`, `"blocked_reason"`

## 금지사항

- `app/domain/mappings/`에서 FastAPI, SQLAlchemy, anthropic/httpx 등 프레임워크·네트워크 라이브러리를 import 하지 마라. 이유: 도메인 계층 순수성(ADR/ARCHITECTURE 레이어 규칙) — 이 규칙이 깨지면 감사·재현 계층이 프레임워크에 묶인다.
- DB 테이블·엔드포인트를 이 step에서 만들지 마라. 이유: Step 1·3의 범위다.
- enum 값을 스펙과 다르게 짓지 마라. 이유: `VERIFIED_MAPPING_SPEC.md`가 구현 계약이다.
- 기존 테스트를 깨뜨리지 마라.
