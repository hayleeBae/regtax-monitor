# Step 2: repository

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙)
- `/docs/architecture/ARCHITECTURE.md` (`app/mappings/` repository 위치, append-only 규칙)
- `/docs/architecture/ADR.md` (ADR-008)
- `/docs/specifications/VERIFIED_MAPPING_SPEC.md` (§4 상태 계산, §6 API가 쓸 조회)
- `/app/audit/repository.py` (본보기 — append-only repository, `update_event`는 `NotImplementedError`, record↔row 변환, `_naive_utc`/`_aware_utc`)
- `/app/domain/mappings/decisions.py` (Step 0 — `MappingDecisionRecord`, `resolve_state`)
- `/app/db/models.py` (Step 1 — `MappingDecision` 테이블)

`app/audit/repository.py`의 구조(생성자에 session, record→row 변환, aware/naive UTC 변환)를 그대로 답습하라.

## 작업

`app/mappings/__init__.py`와 `app/mappings/repository.py`를 신규 생성한다.

```python
class SqlAlchemyMappingDecisionRepository:
    def __init__(self, session) -> None: ...

    def append(self, record: MappingDecisionRecord) -> int:
        """decision 행을 추가하고 새 id를 반환. created_at이 None이면 utcnow로 채운다."""

    def list_for_mapping(self, mapping_id: int) -> tuple[MappingDecisionRecord, ...]:
        """created_at, id 순으로 정렬된 이력을 반환(resolve_state 입력용)."""

    def current_state(self, mapping_id: int):
        """list_for_mapping + resolve_state로 최신 상태(MappingDecisionType | None) 반환."""

    def update(self, *args, **kwargs):
        raise NotImplementedError("mapping decisions are append-only")
```

- record↔row 변환은 `app/audit/repository.py`의 `_naive_utc`/`_aware_utc` 헬퍼와 동일한 방식으로 datetime을 다룬다(중복 정의해도 무방하나 audit과 동일 동작).
- `list_for_mapping`의 정렬 기준은 `resolve_state`의 시간순 규칙과 일치해야 한다(created_at, 동률이면 id).

## 테스트

`tests/test_mapping_decision_repository.py` 신규 작성 (임시 SQLite DB 픽스처):
- `append` 후 `list_for_mapping`이 삽입 레코드를 시간순으로 반환.
- `current_state`가 VERIFIED→REVOKED 이력에서 올바른 최신 상태 반환.
- `update(...)` 호출 시 `NotImplementedError`.
- 서로 다른 mapping_id의 이력이 섞이지 않음.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - append-only가 강제되는가? (`update`이 `NotImplementedError`)
   - `app/mappings/repository.py` 위치가 ARCHITECTURE.md와 일치하는가?
   - `resolve_state`(Step 0)를 재사용하고 상태 계산 로직을 복제하지 않았는가?
3. 결과에 따라 `phases/issue-0015/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "repository 메서드와 append-only 강제 방식 요약"`
   - 실패(3회) → `"status": "error"`, `"error_message"`
   - 개입 필요 → `"status": "blocked"`, `"blocked_reason"`

## 금지사항

- 수정/삭제 메서드를 실질 동작하게 만들지 마라(`update`은 `NotImplementedError`). 이유: append-only 감사 무결성(ADR-008).
- 상태 계산 로직을 여기서 다시 구현하지 마라. 이유: Step 0의 `resolve_state`를 재사용해야 단일 진실 소스가 유지된다.
- API 엔드포인트를 이 step에서 만들지 마라. 이유: Step 3의 범위다.
- 기존 테스트를 깨뜨리지 마라.
