# Step 3: route-wiring

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL — LLM 호출은 `app/llm/`의 seam 경유, 프롬프트·파싱은 `common.py` 공유·백엔드 분기 복제 금지)
- `/docs/specifications/COLLECTION_SEMANTICS_SPEC.md` (§2 — 어느 필드가 어디에 쓰이는지 계약, §6 AC5)
- `/app/collector/law_api.py` (step 1 산출 — `fetch_detail` 4필드+`amendment_parsed` 반환)
- `/app/db/models.py` (step 2 산출 — `LawChange.amendment_text`/`reason_text`)
- `/app/main.py`의 `/collect`(약 336행~), `/changes/{id}/fetch-detail`(약 454행~), `/changes/{id}/analyze`(약 562행~), `/changes/{id}/apply`의 정책 입력 normalize 부분(약 1062행 부근)
- `/app/application/services.py` (`AnalysisService.analyze` — normalize→classify→analyzer 순서)
- `/app/llm/common.py` (분석 프롬프트 빌더 — 여기의 공유 함수를 확장한다)
- `/tests/test_pipeline_services.py` (**fake 주입 테스트 본보기** — LLM 실호출 없이 서비스를 검증하는 방식)

## 배경 (자기완결 요약)

step 0~2로 파서·수집 계층·DB가 준비됐다. 이 step은 라우트를 배선한다: 저장 시 4필드를 담고, **LLM 분석 프롬프트에는 원문(amendment+reason)을, 값 델타 계산(normalize)에는 파생(before/after)만** 들어가게 한다. 제개정이유를 델타 계산에 넣으면 이 작업 전체가 무효가 된다 — 그것이 바로 고치려는 결함이다.

## 작업

### 1) 저장 배선

- `/collect`의 신규 건 상세 자동 조회와 `/changes/{id}/fetch-detail`에서, `fetch_detail()` 반환의 `amendment_text`/`reason_text`를 `LawChange`에 저장한다. 행정규칙 경로(`fetch_admin_rule_detail`)도 동일 (빈 문자열 저장).

### 2) 분석 배선

- `/changes/{id}/analyze`에서:
  - `ChangeNormalizer.normalize(...)` 입력은 **`before_text`/`after_text`만** (기존 시그니처 유지 — 의미가 교정된 값이 들어간다).
  - LLM 분석 프롬프트 컨텍스트에 `amendment_text`(있으면)와 `reason_text`(있으면)를 추가한다. 프롬프트 빌더는 `app/llm/common.py`의 공유 함수를 확장하고, local/claude 백엔드 어느 쪽에도 분기 복제를 만들지 마라.
  - 분석 응답/저장 결과에 `amendment_parsed: bool`을 포함한다 — DB에 저장된 `amendment_text`가 있고 `before_text`가 그와 다르면 True로 계산해도 되고, step 1의 반환값을 저장해 두었다가 써도 된다. 구현 재량이되 **응답에서 확인 가능**해야 한다 (계측 목적 — 폴백 비율 추적).
- `/changes/{id}/apply`의 정책 입력 normalize도 `before_text`/`after_text`를 쓰는지 확인만 한다 (이미 그렇게 되어 있다 — 값의 의미가 교정될 뿐 코드 변경은 불필요할 것이다. 다르면 맞춰라).

### 3) `AnalysisService` 확장 (필요 시)

- `app/application/services.py`의 `AnalysisService.analyze()`가 amendment/reason을 analyzer 콜백에 넘길 수 있게 시그니처를 **기본값 있는 인자**로 확장한다 (기존 호출자 무수정 원칙 — ADR-009 보강 1의 전례를 따른다).

## 테스트

기존 테스트 파일 확장 또는 신규 (`tests/test_route_collection_semantics.py` 권장):

1. **fake LLM/analyzer 주입**으로 analyze 흐름 검증: 프롬프트(또는 analyzer 입력)에 `reason_text`가 포함되고, `normalize` 입력에는 포함되지 않음을 확인. `test_pipeline_services.py`의 fake 패턴을 따르라.
2. mock collect→fetch-detail 후 `LawChange.amendment_text`/`reason_text`가 저장됨 (TestClient + mock 모드, LLM 불필요).
3. analyze 응답에 `amendment_parsed`가 존재.
4. 행정규칙 건 analyze: before=""여도 오류 없이 "추가" 해석으로 진행.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - CLAUDE.md CRITICAL: 프롬프트 로직이 `common.py`에 공유되고 백엔드별 복제가 없는가?
   - 승인 게이트를 우회하는 경로를 만들지 않았는가?
   - normalize 입력에 reason_text가 새어 들어가는 경로가 없는가? (이 step의 존재 이유)
3. `phases/issue-0023/index.json`의 step 3을 업데이트한다.

## 금지사항

- 테스트에서 실제 LLM(Ollama/Anthropic)·임베딩 모델·ChromaDB 인덱싱을 트리거하지 마라. 이유: CLAUDE.md 규칙 — 무거운 의존성은 테스트에서 직접 트리거 금지. fake 주입으로 검증한다.
- `reason_text`를 `ChangeNormalizer` 입력·검색 질의의 before/after 자리에 넣지 마라. 이유: 이 결함을 고치는 것이 issue-0023 전체의 목적이다.
- `app/llm/claude_client.py`와 `local_client.py`에 프롬프트 분기를 복제하지 마라. 이유: CLAUDE.md CRITICAL — 공유 로직은 `common.py` 한 곳이다.
- 기존 테스트를 깨뜨리지 마라.
