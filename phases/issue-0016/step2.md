# Step 2: rerank-stage

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙)
- `/docs/architecture/ARCHITECTURE.md` (데이터 흐름 — map 단계의 rerank 위치가 명시돼 있다)
- `/docs/architecture/ADR.md` (**ADR-009 + 보강 2항·3항** — 이 step의 핵심 근거)
- `/docs/specifications/VERIFIED_MAPPING_SPEC.md` §10
- `/docs/specifications/RETRIEVAL_EXPERIMENT_SPEC.md` §13~§16 (응답 계약, 오류 처리)
- `/app/retrieval/orchestrator.py` (수정 대상 — `retrieve()`, `_merge_candidates()`, `RetrievalConfig`, `RetrievalResponse`)
- `/app/domain/mappings/reranking.py` (Step 0 산출물 — `DecisionContext`, `rerank_delta`, `RERANK_VERSION`)
- `/app/domain/retrieval/candidate.py` (`RetrievalCandidate` — frozen dataclass, `stale` 필드)
- `/tests/test_retrieval_orchestrator.py`, `/tests/test_retrieval_contract.py`

Step 0에서 만든 `rerank_delta`의 시그니처와 `merge_stale_applied` 인자의 의미를 정확히 이해한 뒤 작업하라.

## 작업

orchestrator에 후처리 rerank 단계를 넣는다. **점수 계산 규칙은 Step 0 도메인 모듈에만 있고, 이 step은 단계를 끼워넣고 순서를 보장하는 일만 한다.**

### 1) Reranker 프로토콜 (`app/retrieval/orchestrator.py`)

```python
class CandidateReranker(Protocol):
    version: str

    def contexts_for(
        self, query: RetrievalQuery, candidates: Sequence[RetrievalCandidate]
    ) -> Mapping[str, Sequence[DecisionContext]]: ...
```

- 반환 키는 `candidate.dedup_key`다. 이유: merge 이후의 후보 동일성 판정이 이미 `dedup_key` 기준이므로 같은 키를 쓰지 않으면 매칭이 어긋난다.
- 구현체(DB 조회)는 이 step에서 만들지 않는다 — Step 3의 범위다. 여기서는 테스트용 가짜 구현으로만 검증한다.

### 2) `RetrievalOrchestrator` 주입과 실행 순서

```python
class RetrievalOrchestrator:
    def __init__(
        self,
        providers: Sequence[RetrievalProvider],
        reranker: CandidateReranker | None = None,
    ) -> None: ...
```

`retrieve()`의 단계 순서를 아래로 **고정**한다:

```
merge → rerank(delta 적용) → 정렬 → final_top_k 절단 → rank 부여
```

- **rerank는 반드시 `final_top_k` 절단 전에 실행한다.** 이유: 절단 뒤에 두면 상위 K 밖의 검증 후보가 boost를 받아도 올라올 수 없어 "유효한 verified 후보가 상단으로 이동"이라는 #0016 수용 기준이 구조적으로 불가능해진다(ADR-009 보강 2항).
- 정렬 키는 기존과 동일하게 `(-final_score, location.path)`를 유지한다. 이유: 동점 시 결과가 흔들리면 ablation 재현성이 깨진다.
- delta 적용 후 점수는 `[0.0, 1.0]`으로 clamp하고 `round(..., 6)` 한다(기존 merge와 동일 규칙).
- `RetrievalCandidate`는 frozen이므로 `dataclasses.replace`로 새 인스턴스를 만든다.

### 3) stale 이중 계산 차단 (ADR-009 보강 3항)

`_merge_candidates`는 이미 stale 후보에 `-0.50`을 적용한 뒤 `max(0.0, ...)`로 clamp한다. 따라서 rerank 시점에는 페널티가 얼마나 먹혔는지 알 수 없다.

- merge 결과 후보의 `candidate.stale`을 그대로 `rerank_delta(..., merge_stale_applied=candidate.stale)`로 넘긴다. `rerank_delta`가 추가 stale penalty를 얹지 않도록 하는 것이 총 -0.50 cap의 구현이다.
- `_merge_candidates`의 stale 처리 로직 자체는 **바꾸지 마라.**

### 4) 플래그와 실패 격리

- `RetrievalConfig`에 `rerank_enabled: bool = True`를 추가한다(기본 활성 — ADR-009).
- `reranker is None` 또는 `rerank_enabled is False`면 rerank 단계를 통째로 건너뛴다. 이 경우 응답은 **이 step 이전과 바이트 단위로 동일해야 한다.**
- reranker가 예외를 던지면 provider 실패와 같은 방식으로 격리한다: 전체 검색을 실패시키지 말고 `warnings`에 `f"rerank: {exc}"`를 넣고 rerank 없이 진행한다. 이유: 스펙 §16 "verified DB 실패: warning" — 검증 이력 조회 실패가 검색 자체를 죽이면 "개정을 놓침"으로 이어진다.

### 5) 응답에 `rerank_version` 노출

- `RetrievalResponse`에 `rerank_version: str | None` 필드를 추가하고 `to_dict()`에 `"rerank_version"` 키로 넣는다.
- rerank가 실행되지 않았으면 `None`.
- **`SCORING_VERSION`은 `"retrieval-scoring-v1"` 그대로 둔다.** 이유: ADR-009가 §1 scoring 안정 유지 + 별도 버전 노출로 결정했다.

## 테스트

`tests/test_retrieval_orchestrator.py`에 추가한다:

- reranker 미주입 시 응답이 기존과 동일(후보 순서·점수·payload 키).
- `rerank_enabled=False`면 reranker를 주입해도 호출되지 않고 결과가 동일.
- **절단 전 rerank 확인(핵심 회귀)**: `final_top_k=3`이고 후보가 5개일 때, 원래 5위였던 후보에 큰 boost를 주면 최종 상위 3에 들어오는지. 이 테스트가 없으면 이 step의 존재 이유가 검증되지 않는다.
- delta 적용 후 점수가 `[0,1]`을 벗어나지 않고 rank가 1부터 연속인지.
- reranker가 예외를 던져도 검색이 성공하고 `warnings`에 `rerank:` 항목이 남는지.
- `to_dict()["rerank_version"]`이 rerank 실행 시 값, 미실행 시 None인지.
- stale 후보에 대해 `merge_stale_applied=True`로 호출되는지(가짜 reranker/스파이로 인자 캡처).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 점수 규칙(수치 상수)이 `app/domain/mappings/reranking.py`에만 있고 orchestrator에 복제되지 않았는가?
   - `app/retrieval/orchestrator.py`가 SQLAlchemy·FastAPI를 import하지 않는가? (DB 접근은 Step 3의 lookup 구현체 책임)
   - 단계 순서가 merge → rerank → 정렬 → 절단 → rank 인가?
   - `SCORING_VERSION`이 `retrieval-scoring-v1` 그대로인가?
3. 결과에 따라 `phases/issue-0016/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "추가한 프로토콜/필드, 단계 순서, 실패 격리 동작 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- rerank 수치(+0.35/-0.30 등)를 orchestrator에 하드코딩하지 마라. 이유: 점수 규칙은 Step 0 도메인 모듈이 단일 출처이며, 복제하면 ablation 결과와 코드가 어긋난다.
- `_merge_candidates`의 source 가중치·multi-source 보너스·stale -0.50 로직을 수정하지 마라. 이유: ADR-009가 "기존 source 가중치에 섞지 않고 후처리로 분리"를 결정했고, merge를 건드리면 #0009·#0010 회귀 기준선이 무너진다.
- `SCORING_VERSION` 값을 바꾸지 마라. 이유: ADR-009 결정이며 기존 벤치마크 결과와의 비교 가능성이 깨진다.
- orchestrator에서 DB·세션·모델을 import 하지 마라. 이유: 검색 계층이 영속화에 묶이면 ablation runner가 DB 없이 실행되지 않는다.
- reranker 예외를 그대로 전파시키지 마라. 이유: 검증 이력 조회 실패가 검색 전체를 죽이면 수집·감지 재현율 우선 원칙(CLAUDE.md 도메인 컨텍스트)에 정면으로 반한다.
- 기존 테스트를 깨뜨리지 마라.
