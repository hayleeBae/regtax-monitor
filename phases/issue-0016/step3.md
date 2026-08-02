# Step 3: reranking-lookup

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 설정은 `config.py` Settings로만, 하드코딩 금지)
- `/docs/architecture/ARCHITECTURE.md` (`app/mappings/`는 영속화 계층)
- `/docs/architecture/ADR.md` (**ADR-008, ADR-009 + 보강**)
- `/docs/specifications/VERIFIED_MAPPING_SPEC.md` (**§9 재사용 기준, §11 반복 거절**)
- `/app/domain/mappings/reranking.py` (Step 0 — `DecisionContext` 필드 의미)
- `/app/retrieval/orchestrator.py` (Step 2 — `CandidateReranker` 프로토콜, `contexts_for` 반환 규약)
- `/app/mappings/repository.py` (#0015 — 같은 패키지의 기존 repository 스타일)
- `/app/db/models.py` (`Mapping`, `MappingDecision` 스키마)
- `/app/main.py` 43~78행 (`_make_mapping_service` — 배선 지점, `verified_lookup` 클로저)
- `/config.py` (Settings)
- `/tests/test_mapping_decision_repository.py` (DB 테스트 스타일 — 세션 fixture 구성 방법)

## 작업

### 1) `app/mappings/reranking_lookup.py` 신규

`CandidateReranker` 프로토콜을 구현하는 DB lookup을 만든다.

```python
class SqlAlchemyDecisionContextLookup:
    version = RERANK_VERSION

    def __init__(self, session, article_id: str) -> None: ...

    def contexts_for(self, query, candidates) -> dict[str, tuple[DecisionContext, ...]]: ...
```

핵심 규칙:

- **`Mapping.verified` 필터를 걸지 마라.** `article_id` 기준으로 매핑 전체를 조회한다. 이유: 거절 이력이 있는 매핑은 `verified=False`인데, 이들이 조회되지 않으면 스펙 §10의 rejected penalty가 영원히 작동하지 않는다.
- **article_id 단위로 쿼리를 1회만 실행한다**(매핑 목록 1회 + 결정 이력 1회, 또는 join 1회). 후보마다 쿼리를 날리지 마라. 이유: rerank가 절단 전에 실행되므로 후보 수만큼 N+1 쿼리가 발생한다(ADR-009 보강의 트레이드오프 항목).
- 각 `Mapping` 행을 `CandidateLocation(mapping.path, mapping.symbol).dedup_key`와 같은 규칙으로 키를 만들어 후보와 매칭한다. **`dedup_key` 계산식을 직접 재구현하지 말고** `app.domain.retrieval.CandidateLocation`을 만들어 그 프로퍼티를 써라. 이유: 두 곳에 복제되면 조용히 어긋나 매칭이 전부 실패한다.
- 매핑의 `path`가 `CandidateLocation` 검증(빈 문자열·절대경로·상위 탈출)을 통과하지 못하면 그 매핑은 **건너뛴다**(예외를 던지지 마라). 이유: 스펙 §16 "malformed path → 후보 제외".
- 이력에서 `DecisionContext`를 구성한다:
  - `state` = `resolve_state(records)` (#0015 순수 함수 재사용 — 상태 계산을 복제하지 마라)
  - `reason_code` = 시간순 마지막 결정의 reason_code
  - `rejection_count` = 해당 매핑의 `REJECTED` 이벤트 수
  - `golden_confirmed` = `golden_test_confirmed` reason을 가진 VERIFIED 이력 존재
  - `historical_match` = `matched_historical_change` reason을 가진 VERIFIED 이력 존재
  - `legacy` = #0015 backfill로 생성된 이벤트에서 유래한 경우. backfill이 어떤 값으로 행을 넣는지는 `app/db/database.py`의 `_backfill_legacy_mapping_decisions()`를 **읽고 확인해서** 그 값과 일치시켜라(추측하지 마라).
  - `article_id`/`change_type` = 해당 `Mapping` 행의 값
- 이력이 하나도 없는 매핑은 `DecisionContext`를 만들지 마라(state None만 있는 빈 문맥은 delta 0이므로 무의미하다).

### 2) config 플래그 (`config.py`)

```python
verified_reranking_enabled: bool = True   # #0016 검증 이력 기반 검색 재정렬 (ADR-009, 기본 활성)
```

`.env.example`에도 같은 항목을 주석과 함께 추가한다.

### 3) 배선 (`app/main.py` `_make_mapping_service`)

- `settings.verified_reranking_enabled`가 True일 때만 `SqlAlchemyDecisionContextLookup(db, article_id)`를 `RetrievalOrchestrator(providers, reranker=...)`로 주입한다. False면 `reranker=None`.
- 기존 `verified_lookup` 클로저와 `VerifiedMappingProvider` 구성은 **그대로 둔다.** 이유: provider는 후보를 만들고 reranker는 순위를 조정하는 별개 역할이며, 지금 합치면 #0009 회귀 기준선이 무너진다.

## 테스트

`tests/test_mapping_reranking_lookup.py` 신규 작성:

- verified 이력이 있는 매핑 → 해당 후보 키에 state VERIFIED 문맥이 생기는지.
- **rejected 매핑(`verified=False`)도 조회되는지** — 이 step의 핵심 회귀.
- `REJECTED` 3건 → `rejection_count == 3`.
- golden/historical reason이 각 플래그로 반영되는지.
- backfill legacy 이벤트 → `legacy is True`.
- 이력 없는 매핑은 결과에 없는지.
- 다른 `article_id`의 매핑은 결과에 섞이지 않는지.
- malformed path(예: 빈 문자열, `/abs/path`)를 가진 매핑이 있어도 예외 없이 나머지가 반환되는지.
- 후보 매칭 키가 `candidate.dedup_key`와 실제로 일치하는지(후보 하나를 만들어 lookup 결과 키와 대조).

`tests/test_mapping_decision_api.py` 또는 새 테스트에 배선 회귀 1건:

- `verified_reranking_enabled=False`(monkeypatch)일 때 map 엔드포인트 응답의 `rerank_version`이 None이고, 기존 응답 키가 그대로인지.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/mappings/reranking_lookup.py`가 점수 계산을 하지 않는가? (수치는 Step 0 도메인 모듈에만 있어야 한다 — `grep -n "0\.35\|0\.30\|0\.20\|0\.50" app/mappings/reranking_lookup.py` 결과가 비어야 한다)
   - `resolve_state`를 재사용했는가(상태 계산 미복제)?
   - `dedup_key`를 `CandidateLocation`으로 계산했는가(문자열 조립 미복제)?
   - 설정이 `config.Settings`를 통해서만 읽히는가(하드코딩 없음)?
   - append-only 규약을 어기는 UPDATE/DELETE가 없는가? (lookup은 읽기 전용이어야 한다)
3. 결과에 따라 `phases/issue-0016/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, 조회 전략, config 플래그, 배선 지점 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- `Mapping.verified == True` 필터로 조회하지 마라. 이유: 거절 이력이 있는 매핑이 빠져 rejected penalty가 작동하지 않는다.
- 후보마다 DB 쿼리를 날리지 마라. 이유: rerank가 절단 전에 실행되므로 후보 수만큼 N+1이 발생한다.
- 이 모듈에서 rerank 수치를 계산하거나 delta를 반환하지 마라. 이유: 점수 규칙의 단일 출처는 `app/domain/mappings/reranking.py`다.
- `resolve_state` 로직을 복제하지 마라. 이유: #0015 상태 규칙(`VERIFIED→STALE→VERIFIED`=VERIFIED 등)이 두 벌이 되면 UI 표시와 검색 점수가 어긋난다.
- `verified_lookup` / `VerifiedMappingProvider`를 제거하거나 reranker로 대체하지 마라. 이유: provider(후보 생성)와 reranker(순위 조정)는 별개 역할이며, #0009 회귀 기준선을 무너뜨린다.
- 결정 이력을 쓰기(INSERT/UPDATE)하지 마라. 이유: 이 모듈은 읽기 전용이고, 검색 중 자동 stale 이벤트 생성은 스펙 §8이 명시적으로 금지한다.
- 하드코딩된 플래그 기본값을 코드에 넣지 마라(`config.py` Settings 경유). 이유: CLAUDE.md 설정 규칙.
- 기존 테스트를 깨뜨리지 마라.
