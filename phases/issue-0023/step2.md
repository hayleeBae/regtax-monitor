# Step 2: lawchange-migration

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/specifications/COLLECTION_SEMANTICS_SPEC.md` (§4 마이그레이션)
- `/app/db/models.py` (`LawChange` 모델 — 수정 대상)
- `/app/db/database.py` (`_migrate`의 경량 ADD COLUMN 패턴과 `_backfill_legacy_mapping_decisions`의 **idempotent 백필 패턴** — 이 둘을 본보기로 따른다)
- `/app/domain/changes/amendment.py` (step 0 산출물 — 백필 재파생에 사용)
- `/tests/test_mapping_decision_migration.py` (**마이그레이션 테스트 본보기** — 구 스키마를 만들고 _migrate를 돌리는 방식)

## 배경 (자기완결 요약)

step 1까지로 수집 계층이 4필드(`amendment_text`/`reason_text` 원문 + `before_text`/`after_text` 파생)를 반환한다. 이 step은 DB가 그것을 담게 한다. 기존 DB에는 잘못된 의미로 저장된 행(before←개정문, after←제개정이유)이 있으므로, 이관+재파생 백필이 필요하다. 이 프로젝트의 마이그레이션은 Alembic이 아니라 `database.py::_migrate`의 경량 ADD COLUMN + 멱등 백필 방식이다.

## 작업

### 1) `app/db/models.py`

`LawChange`에 nullable 컬럼 2개 추가:

```python
amendment_text = Column(Text, nullable=True)   # 개정문 원문
reason_text = Column(Text, nullable=True)      # 제개정이유 원문
```

### 2) `app/db/database.py`

- `_migrate()`의 `table_adds["law_change"]`에 `"amendment_text": "TEXT"`, `"reason_text": "TEXT"` 추가.
- 백필 함수 신규:

```python
def _backfill_collection_semantics(tables: set) -> None:
    """기존 행의 before/after(잘못된 의미: 개정문/제개정이유)를
    amendment/reason으로 이관하고 before/after를 파서로 재파생한다. idempotent."""
```

- 대상 행 조건: `amendment_text IS NULL AND source = 'law' AND before_text != ''`
  - `source='law'` 조건이 필수인 이유: 행정규칙 행(고시 등)은 `after_text=본문`이 **올바른** 의미로 이미 저장되어 있다 — 이관하면 오히려 망가진다.
- 이관: `amendment_text ← before_text`, `reason_text ← after_text`.
- 재파생: `derive_before_after(parse_amendment(amendment_text), fallback_text=amendment_text)` 결과로 `before_text`/`after_text` 갱신.
- 멱등성: 이미 이관된 행(`amendment_text IS NOT NULL`)은 건너뛴다. `init_db()`가 서버 기동마다 호출되므로 몇 번 실행해도 행당 이관은 1회다.
- `_migrate()` 말미에서 호출한다 (`_backfill_legacy_mapping_decisions`와 같은 자리).

## 테스트

`tests/test_collection_migration.py` 신규 (`test_mapping_decision_migration.py` 방식을 따라 임시 sqlite로):

1. 구 의미 행(law, before=개정문 P1 문형, after=이유문) 삽입 → `_migrate()` → `amendment_text`/`reason_text` 이관 확인 + `before_text`가 파생값으로 바뀌고 `after_text != reason_text`.
2. 행정규칙 행(source='고시', before='', after=본문) → 백필이 건드리지 않음 (amendment_text NULL 유지, after_text 불변).
3. 멱등성: `_migrate()` 2회 실행 후 1회 실행과 동일 상태 (재파생이 두 번 적용되어 값이 또 바뀌지 않아야 한다).
4. 신규 스키마로 생성된 빈 DB에서 `_migrate()`가 오류 없이 통과.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 기존 컬럼을 삭제·rename하지 않았는가?
   - `db → domain` import 방향인가? (`amendment.py`는 domain 최하층이라 db가 import해도 된다)
3. `phases/issue-0023/index.json`의 step 2를 업데이트한다.

## 금지사항

- 기존 컬럼(`before_text`/`after_text`)을 삭제하거나 rename하지 마라. 이유: 분석·검색·대시보드가 소비 중이며, 계약은 "의미 교정 + 원문 필드 추가"다.
- Alembic 등 마이그레이션 도구를 도입하지 마라. 이유: 이 프로젝트는 `_migrate()` 경량 패턴을 쓰며 의존성 추가는 설계 범위 밖이다.
- `app/main.py`를 수정하지 마라. 이유: 라우트 배선은 step 3이다.
- 백필에서 LLM을 호출하지 마라. 이유: 재파생은 결정론 파서만 사용한다 — 기동 시 실행되는 코드에 LLM 의존을 넣으면 Ollama 미기동 시 서버가 못 뜬다.
