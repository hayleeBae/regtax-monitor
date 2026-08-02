# Step 0: rerank-domain

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 특히 도메인 계층 순수성)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙: `app/domain/`은 순수 계약)
- `/docs/architecture/ADR.md` (**ADR-009 + 그 아래 "ADR-009 보강" 절** — 이 작업의 근거)
- `/docs/specifications/VERIFIED_MAPPING_SPEC.md` (구현 계약 — **§9 재사용 기준, §10 Reranking 수치, §11 반복 거절**)
- `/docs/specifications/RETRIEVAL_EXPERIMENT_SPEC.md` (**§15 Verified reranking**)
- `/app/domain/mappings/decisions.py` (#0015 산출물 — 같은 패키지의 기존 스타일이자 이 step이 재사용할 enum)
- `/app/domain/mappings/__init__.py` (export 스타일)
- `/app/domain/common/enums.py` (`ChangeType` V2 어휘 — 38~54행 주석을 반드시 읽어라)

`app/domain/mappings/decisions.py`를 꼼꼼히 읽고 같은 스타일(frozen dataclass, `str, Enum`, `__post_init__` 검증, 스펙 조항을 docstring에 인용)을 그대로 따르라.

## 작업

순수 도메인 모듈 `app/domain/mappings/reranking.py`를 신규 생성한다. **DB·FastAPI·LLM에 의존하지 않는 순수 Python만** 담는다. 이 step은 계산 규칙만 만들고, DB 조회와 orchestrator 배선은 다루지 않는다.

### 1) 재사용 등급 (스펙 §9)

```python
class ReuseClass(str, Enum):
    EXACT = "exact"            # §9-1: 같은 법령/조문 + 같은 change type
    COMPATIBLE = "compatible"  # §9-2: 같은 조문 + 호환 type
    UNRELATED = "unrelated"    # 문맥 불일치 — delta 0
```

### 2) 결정 문맥 (후보 위치 1건의 이력 요약)

```python
@dataclass(frozen=True)
class DecisionContext:
    article_id: str | None            # 이 이력이 속한 매핑의 article_id
    change_type: str | None           # 이 이력이 속한 매핑의 change_type (레거시 자유 문자열일 수 있음)
    state: MappingDecisionType | None # resolve_state() 결과 (이력 없으면 None)
    reason_code: str | None = None    # 최신 결정의 reason_code
    rejection_count: int = 0          # 같은 문맥에서 누적된 REJECTED 이벤트 수 (§11)
    golden_confirmed: bool = False    # VerifiedReason.GOLDEN_TEST_CONFIRMED 이력 보유
    historical_match: bool = False    # VerifiedReason.MATCHED_HISTORICAL_CHANGE 이력 보유
    legacy: bool = False              # #0015 backfill로 생성된 legacy verified 이벤트에서 유래
```

`state`는 `app.domain.mappings.decisions.MappingDecisionType`을 그대로 쓴다 — **새 enum을 만들지 마라.**

### 3) 문맥 게이팅 (스펙 §9 + ADR-009 보강)

```python
def classify_reuse(
    query_article_id: str | None,
    query_change_type: str | None,
    context: DecisionContext,
) -> ReuseClass: ...
```

규칙:

- `query_article_id`가 없거나 `context.article_id`와 다르면 → **UNRELATED**. 조문이 다르면 어떤 경우에도 boost/penalty를 적용하지 않는다(§9 "문맥이 다르면 boost하지 않는다", §11 "다른 법령에서의 거절을 영구 차단으로 쓰지 않는다").
- 조문이 같을 때 change_type 비교는 **보수적으로** 한다:
  - 양쪽 값이 모두 `ChangeType`(V2 어휘)으로 파싱되면 → 같으면 EXACT, `COMPATIBLE_CHANGE_TYPES` 그룹에 함께 있으면 COMPATIBLE, 아니면 UNRELATED.
  - 한쪽이라도 `ChangeType`으로 파싱되지 않으면(레거시 `rate`/`limit`/`date`/`formula`/`logic` 등) → **문자열 완전일치일 때만 EXACT, 그 외는 전부 UNRELATED.**
  - 양쪽 다 None/빈 값이면 UNRELATED (무문맥 boost 금지 — ADR-009 보강 1항).
- 호환 그룹은 모듈 상수로 명시한다. 예:
  ```python
  COMPATIBLE_CHANGE_TYPES: tuple[frozenset[ChangeType], ...] = (
      frozenset({ChangeType.VALUE_CHANGE, ChangeType.RATE_CHANGE, ChangeType.TABLE_CHANGE}),
      frozenset({ChangeType.CONDITION_CHANGE, ChangeType.NEW_FIELD}),
  )
  ```
  `ChangeType.UNKNOWN`·`ChangeType.NO_CODE_IMPACT`는 어떤 그룹에도 넣지 마라. 이유: 미분류·무영향 건이 검증 매핑을 끌어올리면 오탐이 된다.

### 4) delta 계산 (스펙 §10, ADR-009 보강 3항)

```python
RERANK_VERSION = "verified-rerank-v1"

def rerank_delta(
    contexts: Sequence[DecisionContext],
    *,
    query_article_id: str | None,
    query_change_type: str | None,
    merge_stale_applied: bool = False,
) -> float: ...
```

한 후보 위치에 붙은 모든 `DecisionContext`를 받아 최종 delta(음수 가능)를 반환한다. 규칙:

- 각 context를 `classify_reuse`로 게이팅하고 **UNRELATED는 건너뛴다**(0 기여).
- 스펙 §10 수치를 모듈 상수로 선언하고 사용한다:
  - EXACT + state VERIFIED → `+0.35`
  - COMPATIBLE + state VERIFIED → `+0.20`
  - `golden_confirmed` → `+0.05`, `historical_match` → `+0.05` (verified boost에 가산)
  - EXACT + state REJECTED → `-0.30`
  - 반복 거절: `rejection_count >= 2`일 때 총 거절 penalty를 **최대 `-0.50`까지** 강화한다(§11 — EXACT 문맥에서만).
  - `legacy` → `-0.15` (legacy backfill 이력은 사람이 실제로 확인한 근거가 아니므로 boost를 깎는다)
- **state가 STALE이면 verified boost를 전부 제거한다**(스펙 §10 "stale boost 제거 + penalty"). 추가 stale penalty는 `merge_stale_applied=True`이면 **적용하지 않는다** — orchestrator의 `_merge_candidates`가 이미 -0.50을 적용했기 때문이며, 총 stale penalty를 -0.50으로 cap하는 것이 ADR-009 트레이드오프 결정이다. `merge_stale_applied=False`일 때만 자체 stale penalty(-0.50)를 적용한다.
- state가 REVOKED이면 verified boost를 주지 않는다(취소된 검증). penalty도 주지 않는다.
- 최종 delta는 `[-0.50, +0.45]` 범위로 clamp한다. 이유: 단일 신호가 다른 모든 근거를 압도하면 스펙 §15의 "검증 이력만으로 다른 exact evidence를 제거하지 않는다"를 위반한다.
- 부동소수 오차가 테스트를 깨지 않도록 반환 전 `round(..., 6)` 한다.

### 5) 패키지 export

`app/domain/mappings/__init__.py`에 `ReuseClass`, `DecisionContext`, `classify_reuse`, `rerank_delta`, `RERANK_VERSION`, `COMPATIBLE_CHANGE_TYPES`를 추가 export 한다. 기존 export를 제거하지 마라.

## 테스트

`tests/test_mapping_reranking.py` 신규 작성. 최소 아래를 덮어라:

- `classify_reuse`: 같은 조문+같은 V2 type → EXACT / 같은 조문+호환 type → COMPATIBLE / 같은 조문+비호환 type → UNRELATED / **다른 조문이면 type이 같아도 UNRELATED** / query_article_id None → UNRELATED.
- 레거시 문자열(`"rate"` 등): 완전일치 → EXACT, `"rate"` vs `"rate_change"` → UNRELATED(임의 변환 금지 확인).
- `rerank_delta`: exact verified = +0.35 / compatible verified = +0.20 / golden·historical 가산 / exact rejected = -0.30 / rejection_count 3 → -0.50 / legacy verified = +0.35-0.15 / UNRELATED 문맥은 delta 0.
- stale: `merge_stale_applied=True`면 추가 penalty 없음(이중 계산 방지), `False`면 -0.50. 두 경우 모두 verified boost가 제거되는지.
- REVOKED 상태는 boost·penalty 모두 0.
- 빈 `contexts` → 0.0.
- clamp 경계: 여러 verified 문맥이 겹쳐도 +0.45를 넘지 않는다.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
grep -n "^import\|^from" app/domain/mappings/reranking.py
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/domain/mappings/reranking.py`가 FastAPI/SQLAlchemy/httpx/anthropic을 import하지 않는가? (두 번째 AC 커맨드 출력으로 확인 — `app.domain.*`와 표준 라이브러리만 나와야 한다)
   - ARCHITECTURE.md 디렉토리 구조(`app/domain/mappings/reranking.py`)를 따르는가?
   - 스펙 §9·§10·§11의 수치·등급 이름과 일치하는가?
   - `MappingDecisionType`을 재사용했는가(중복 enum 미생성)?
3. 결과에 따라 `phases/issue-0016/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, export 심볼, 게이팅·delta 규칙 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- `app/domain/mappings/`에서 FastAPI, SQLAlchemy, anthropic/httpx 등 프레임워크·네트워크 라이브러리를 import 하지 마라. 이유: 도메인 계층 순수성(ARCHITECTURE.md 레이어 규칙) — 깨지면 감사·재현 계층이 프레임워크에 묶인다.
- 레거시 `change_type`(`rate`/`limit`/`date`/`formula`/`logic`)을 V2 `ChangeType`으로 변환하는 매핑표를 만들지 마라. 이유: `formula`/`logic`의 V2 대응이 근거 없이 발명되며, 잘못된 boost는 "개정을 놓침" 다음으로 비싼 오탐을 만든다. 보수적 완전일치 규칙이 승인된 결정이다.
- DB 조회·orchestrator 수정·config 플래그 추가를 이 step에서 하지 마라. 이유: Step 2·3의 범위다.
- `MappingDecisionType`과 겹치는 새 상태 enum을 만들지 마라. 이유: 상태 어휘가 두 벌이 되면 #0015 이력과 어긋난다.
- `SCORING_VERSION`(`app/retrieval/orchestrator.py`)을 건드리지 마라. 이유: ADR-009가 v1 유지·별도 `rerank_version` 노출로 결정했다.
- 기존 테스트를 깨뜨리지 마라.
