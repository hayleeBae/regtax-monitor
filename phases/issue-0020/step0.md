# Step 0: graph-traversal

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL — 코드 본문 반출 금지, 동작 보존)
- `/docs/specifications/CODE_GRAPH_SPEC.md` (§3 모델, §7 관계, §8 이웃 순회, §11 폭증방지)
- `/docs/architecture/ADR.md` (ADR-017)
- `/app/embedding/symbol_index.py` (`SymbolNode`·`SymbolEdge`·`SymbolGraph`·`EdgeKind`·`SymbolKind`·`load(adapter)` — 소비 대상. 이 모듈의 id 규약(`java:FQN`, `java:FQN#method`, `mybatis:ns.stmt` 등)을 정확히 이해하라)
- `/app/codebase/base.py` (`EXCLUDED_DIRS` — 산출물 제외)

## 작업

`SymbolGraph`를 seed에서 이웃으로 넓히는 **순수 순회 모듈**을 만든다. orchestrator·provider는 건드리지 않는다(다음 step).

신규 `app/retrieval/graph_expand.py`:

```python
@dataclass(frozen=True)
class GraphHit:
    node: SymbolNode
    edge_kind: EdgeKind
    relation_path: str   # 설명가능성용: "seed_id -[edge_kind]-> node_id" 형태

def seed_node_ids(graph: SymbolGraph, path: str, symbol: str | None) -> set[str]:
    """검색 후보 위치(path[, symbol])를 SymbolGraph 노드 id 집합으로 매핑.
    같은 path의 노드를 seed 후보로 삼되, symbol이 주어지면 이름 일치 노드를 우선한다."""

def neighbors(
    graph: SymbolGraph,
    seed_ids: set[str],
    edge_allowlist: frozenset[EdgeKind],
    depth: int = 1,
    max_depth: int = 2,
    max_neighbors: int = 20,
) -> list[GraphHit]:
    """seed_ids에서 edge_allowlist 종류의 엣지만 따라 이웃을 BFS로 넓힌다.
    - graph.edges로 인접 리스트를 만든다(양방향 고려는 구현 재량이나, seed→target 방향 우선).
    - visited 관리로 cycle을 막고, depth는 max_depth를 넘지 않는다.
    - max_neighbors로 총 반환 수를 제한한다(초과분 절단).
    - node.path의 구성요소에 EXCLUDED_DIRS가 있으면 제외한다(산출물).
    - seed 자신은 반환하지 않는다."""
```

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

추가 테스트(`tests/` 하위, 소형 SymbolGraph fixture를 직접 구성):
- depth=1이 직접 이웃만, depth=2가 2-홉까지 반환
- cycle이 있어도 무한 루프 없이 종료(visited)
- edge_allowlist에 없는 EdgeKind는 따라가지 않음
- max_neighbors 초과 시 절단
- EXCLUDED_DIRS 경로 노드 제외
- 빈 그래프/빈 seed → 빈 결과

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크: CODE_GRAPH_SPEC §8·§11 준수, 순수 함수(부작용 없음), 코드 본문 미접근.
3. `phases/issue-0020/index.json`의 step 0 업데이트:
   - 성공 → `"completed"` + `summary`(생성 파일, `neighbors`/`seed_node_ids` 시그니처, 폭증방지 방식)
   - 3회 실패 → `"error"` + `error_message`
   - 개입 필요 → `"blocked"` + `blocked_reason` 후 중단

## 금지사항

- orchestrator/provider/automation을 수정하지 마라. 이유: 이 step은 순회 모듈만. 배선은 step 1·2.
- `SymbolNode`에 코드 본문을 담거나 `read_file` 본문을 참조하지 마라. 이유: 캐시 반출 사고 방지(#0019 규약).
- depth 상한(max_depth) 없이 순회하지 마라. 이유: 휴리스틱 엣지에서 폭증(스펙 §11).
- 없는 관계를 만들지 마라(graph.edges에 있는 것만 따라간다). 이유: 오탐 억제.
- 기존 테스트를 깨뜨리지 마라.
