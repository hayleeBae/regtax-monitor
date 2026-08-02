# Step 1: query-context

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 두 개의 교체 이음새(seam) 규칙)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙, 데이터 흐름)
- `/docs/architecture/ADR.md` (**ADR-009 보강 1항** — 이 step의 근거)
- `/docs/specifications/RETRIEVAL_EXPERIMENT_SPEC.md` (§13~§16 응답 계약)
- `/app/retrieval/orchestrator.py` (`RetrievalQuery` — 이 step의 수정 대상)
- `/app/application/services.py` (`MappingService.map()` — 수정 대상)
- `/app/main.py` 700~740행 (map 엔드포인트), 43~78행 (`_make_mapping_service`)
- `/app/evaluation/retrieval_benchmark.py` `run_orchestrator_cases()` (기존 `RetrievalQuery` 호출자 — 깨지면 안 된다)
- `/tests/test_retrieval_orchestrator.py`, `/tests/test_retrieval_contract.py` (기존 회귀 테스트 스타일)

이 step은 **rerank 로직을 전혀 넣지 않는다.** 문맥 값이 검색 seam을 타고 흐르도록 통로만 넓히고, 그 과정에서 기존 동작이 한 톨도 바뀌지 않았음을 테스트로 고정하는 것이 전부다.

## 작업

### 1) `RetrievalQuery` 확장 (`app/retrieval/orchestrator.py`)

```python
@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    domain: str | None = None
    repository_commit: str | None = None
    top_k_per_provider: int = 8
    article_id: str | None = None      # 신규 — 문맥 게이팅 대상 조문 식별자
    change_type: str | None = None     # 신규 — 문맥 게이팅 대상 변경 유형
```

- **반드시 기본값 None으로, 기존 필드 뒤에 추가한다.** 이유: `RetrievalQuery("query")` 형태의 위치 인자 호출이 코드와 테스트 전반에 있고, 순서를 바꾸면 조용히 깨진다.
- `query_hash`는 **지금 그대로 `text`만 해싱한다.** 이유: audit 기록·기존 테스트가 해시 안정성에 의존한다. 문맥이 해시에 들어가야 한다는 판단이 서더라도 이 step에서 바꾸지 마라.

### 2) `MappingService.map()` 확장 (`app/application/services.py`)

```python
def map(
    self,
    query_text: str,
    top_k: int = 5,
    enabled_sources: frozenset[RetrievalSource] | None = None,
    article_id: str | None = None,     # 신규
    change_type: str | None = None,    # 신규
) -> MappingResult: ...
```

받은 값을 `RetrievalQuery`에 그대로 실어 넘기기만 한다. 기본값 None 유지.

### 3) 호출부 배선 (`app/main.py`)

- map 엔드포인트(현재 `article_id = f"{row.law_id}:{row.article_no}"` 직후 `service.map(query, top_k=k)`를 호출하는 곳)에서 `article_id=article_id, change_type=row.change_type`을 넘긴다.
- 초안 생성 경로에서 `mapping_service.map(query, top_k=5)`를 호출하는 곳(현재 1050행 근방, `policy_candidates` 계산)도 동일하게 넘긴다. 이유: 두 경로가 같은 후보 집합을 보아야 정책 판단과 검색 결과가 어긋나지 않는다.
- `LawChange.change_type`은 레거시 자유 문자열(`rate`/`limit`/…)일 수 있고 None일 수도 있다. **여기서 변환하거나 기본값을 채우지 마라** — 그대로 넘긴다.

## 테스트

기존 파일에 추가한다(신규 파일 불필요):

- `tests/test_retrieval_orchestrator.py`:
  - `RetrievalQuery("query")`가 여전히 동작하고 `article_id`/`change_type`이 None인지.
  - `article_id`/`change_type`을 준 쿼리와 안 준 쿼리의 **응답이 동일한지**(이 step에서는 아직 아무것도 달라지면 안 된다). `query_hash`가 두 경우 같은지도 확인하라.
- `tests/test_pipeline_services.py`(또는 `MappingService`를 다루는 기존 테스트 파일):
  - `MappingService.map(text)`가 인자 없이 동작하고, `article_id`/`change_type`을 주면 orchestrator에 전달되는지(가짜 orchestrator로 전달 인자 캡처).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. **직전 step 대비 실패 0, 기존 테스트 수정 없이 통과해야 한다.**
2. 아키텍처 체크리스트를 확인한다:
   - CLAUDE.md seam 규칙 — 검색 접근이 여전히 orchestrator를 통해서만 이루어지는가?
   - `app/evaluation/retrieval_benchmark.py`의 기존 `RetrievalQuery(...)` 호출이 수정 없이 동작하는가?
   - 응답 payload 키가 하나도 늘거나 줄지 않았는가? (이 step은 응답 계약을 바꾸지 않는다)
3. 결과에 따라 `phases/issue-0016/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "확장한 시그니처와 배선 지점, 회귀 고정 테스트 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- rerank 계산·DB 조회·config 플래그를 이 step에서 추가하지 마라. 이유: Step 2·3의 범위이며, 통로 확장만 따로 검증해야 회귀 원인을 분리할 수 있다.
- `RetrievalQuery`의 기존 필드 순서를 바꾸거나 새 필드를 필수 인자로 만들지 마라. 이유: 위치 인자 호출자가 조용히 깨진다.
- `query_hash` 계산식을 바꾸지 마라. 이유: audit 기록과 기존 테스트가 해시 안정성에 의존한다.
- `LawChange.change_type` 값을 V2 `ChangeType`으로 변환하지 마라. 이유: 어휘 변환은 근거 없는 의미 발명이며, Step 0이 보수적 완전일치로 처리하기로 승인된 결정이다.
- 기존 테스트를 깨뜨리지 마라.
