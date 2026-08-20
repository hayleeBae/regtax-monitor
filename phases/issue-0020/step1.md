# Step 1: graph-expand-stage

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/specifications/CODE_GRAPH_SPEC.md` (§9 Retrieval 연결, §10 게이트, §12 설명가능성)
- `/docs/architecture/ADR.md` (ADR-017 — merge 이후 확장 단계, 이중 RAG 금지, off=불변)
- `/app/retrieval/orchestrator.py` (`RetrievalConfig`, `RetrievalOrchestrator.retrieve`: providers→merge→rerank→truncate. rerank가 `config.rerank_enabled` 가드로 선택 실행되는 패턴을 그대로 참고하라)
- `/app/domain/retrieval/candidate.py` (`RetrievalCandidate`·`RetrievalEvidence`·`CandidateLocation`)
- `/app/domain/common/enums.py` (`RetrievalSource.CODE_GRAPH` — 이미 존재)
- `/app/retrieval/providers.py` (기존 provider가 `RetrievalEvidence(source, raw_score, normalized_score)`로 후보를 만드는 방식)
- `/app/retrieval/graph_expand.py` (Step 0 산출물: `neighbors`, `seed_node_ids`, `GraphHit`)
- `/app/embedding/symbol_index.py` (`SymbolGraph`, `load(adapter)`, `EdgeKind`)

Step 0의 `graph_expand`와 orchestrator의 rerank 가드 패턴을 정확히 읽고 이어서 작업하라.

## 작업

그래프 확장을 **orchestrator의 merge 이후 선택적 단계**로 배선한다.

1. `RetrievalConfig`에 필드 추가(전부 기본값 — 기존 호출부 불변):
   - `graph_enabled: bool = False`
   - `graph_seed_top_n: int = 3`, `graph_depth: int = 1`, `graph_max_neighbors: int = 20`
   - `graph_edge_allowlist: frozenset[EdgeKind]` (기본: 고정밀 위주 — CONTAINS, SERVICE_TO_MAPPER)
   - EdgeKind→점수 매핑(기본: service_to_mapper 0.75, uses_constant 0.70, test_to_service 0.65, contains 0.60)
   - `weights`/`DEFAULT_WEIGHTS`에 `RetrievalSource.CODE_GRAPH` 항목 추가(필요 시)

2. `RetrievalOrchestrator`:
   - 생성자에 그래프 소스를 선택 주입(reranker처럼 `graph: SymbolGraph | None = None`). None이면 확장 단계 비활성.
   - `retrieve`에서 **merge 직후, rerank 직전**에 graph-expand 단계를 넣는다:
     ```text
     merged = _merge_candidates(...)
     if graph is not None and config.graph_enabled:
         merged = _graph_expand(merged, graph, config)   # seed=merged 상위 N 재사용
     # 이후 기존 rerank → 정렬 → truncate 그대로
     ```
   - `_graph_expand`: merged 상위 `graph_seed_top_n` 후보 각각에 대해 `seed_node_ids`로 노드 매핑 → `neighbors(...)` → 각 `GraphHit`를 `RetrievalSource.CODE_GRAPH` evidence(점수=EdgeKind 매핑값)로 만들어, **이미 있는 후보면 evidence 추가, 없으면 신규 후보로 추가**한 뒤 다시 병합한다. evidence에 `relation_path`를 설명으로 남긴다(가능하면).

3. `_make_mapping_service`(app/main.py) 등 orchestrator 생성부에 `graph=symbol_index.load(adapter)`를 주입(운영 경로에서 그래프가 로드되도록). 단 `graph_enabled` 기본 False라 동작은 불변.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

추가 테스트:
- **off-불변 회귀**: `graph_enabled=False`(기본)일 때 `retrieve` 결과가 그래프 주입 전과 동일(후보·순위·점수 불변)
- `graph_enabled=True`일 때 seed의 이웃이 CODE_GRAPH evidence로 후보에 반영됨
- **이중 RAG 미발생**: 확장 단계에서 `adapter.search`(RAG)가 호출되지 않음 — RagProvider를 호출 카운트 스텁으로 두고 확장이 추가 호출을 만들지 않음을 검증
- graph=None이면 확장 단계가 skip됨

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크: ADR-017/스펙 §9 일치, off=불변 보장, rerank는 여전히 확장 이후에 실행.
3. `phases/issue-0020/index.json`의 step 1 업데이트:
   - 성공 → `"completed"` + `summary`(RetrievalConfig 신규 필드, orchestrator 단계 위치, seed 재사용 방식)
   - 3회 실패 → `"error"` + `error_message`
   - 개입 필요 → `"blocked"` + `blocked_reason` 후 중단

## 금지사항

- `graph_enabled=False`에서 결과가 조금이라도 바뀌게 하지 마라. 이유: 동작 보존(ADR-017) — 회귀 테스트로 고정한다.
- 확장 seed를 위해 RAG(`adapter.search`)를 다시 호출하지 마라. 이유: 이중 RAG 금지(ADR-017) — seed는 merged 후보 재사용.
- graph-expand를 rerank **이후**나 truncate 이후에 두지 마라. 이유: 상위 K 밖으로 밀린 후보가 확장·rerank 이득을 못 받는다(ADR-009 순서 원칙).
- provider를 새로 추가하지 마라(그래프는 provider가 아니라 단계다). 이유: 이중 RAG·계약 변경 방지.
- 기존 테스트를 깨뜨리지 마라.
