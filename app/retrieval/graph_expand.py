"""SymbolGraph 이웃 순회 — 순수 함수 모듈 (Issue #0020 Step 0 / ADR-017, CODE_GRAPH_SPEC §8).

seed 후보(RAG가 이미 찾은 상위 후보)를 `SymbolGraph`의 노드 id로 매핑하고,
`edges`만 따라 이웃을 BFS로 넓힌다. orchestrator·provider 배선은 다음 step(#0020 Step 1)의
몫이며, 이 모듈은 그래프를 읽기만 한다 — 부작용도, 코드 본문 접근도 없다.

폭증 방지(스펙 §11): depth 상한(max_depth), max_neighbors 절단, visited 로 cycle 차단,
edge_allowlist 로 관계 종류 제한, EXCLUDED_DIRS 로 산출물 노드 제외. 없는 관계(graph.edges에
없는 edge)는 따라가지 않는다 — 오탐 억제가 재현율보다 우선하는 지점이 아니라, 그래프
확장 자체가 "이미 실재하는 관계만" 이라는 #0019 규약을 그대로 지킨다."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from app.codebase.base import CodebaseAdapter
from app.embedding.symbol_index import EdgeKind, SymbolGraph, SymbolNode


@dataclass(frozen=True)
class GraphHit:
    node: SymbolNode
    edge_kind: EdgeKind
    relation_path: str


def _is_excluded_path(path: str) -> bool:
    """path 구성요소에 EXCLUDED_DIRS 가 있으면 제외 (산출물)."""
    return bool(CodebaseAdapter.EXCLUDED_DIRS & set(Path(path).parts))


def seed_node_ids(graph: SymbolGraph, path: str, symbol: str | None) -> set[str]:
    """검색 후보 위치(path[, symbol])를 SymbolGraph 노드 id 집합으로 매핑.

    같은 path 의 노드를 seed 후보로 삼되, symbol 이 주어지면 이름이 일치하는
    노드로 좁힌다(일치하는 것이 있으면 그것만, 없으면 path 전체로 폴백)."""
    same_path = [n for n in graph.nodes if n.path == path]
    if not same_path:
        return set()
    if symbol:
        by_name = [n for n in same_path if n.name == symbol]
        if by_name:
            return {n.id for n in by_name}
    return {n.id for n in same_path}


def neighbors(
    graph: SymbolGraph,
    seed_ids: set[str],
    edge_allowlist: frozenset[EdgeKind],
    depth: int = 1,
    max_depth: int = 2,
    max_neighbors: int = 20,
) -> list[GraphHit]:
    """seed_ids 에서 edge_allowlist 종류의 엣지만 따라 이웃을 BFS 로 넓힌다."""
    if not graph.nodes or not seed_ids or not edge_allowlist or max_neighbors <= 0:
        return []

    effective_depth = min(depth, max_depth)
    if effective_depth <= 0:
        return []

    nodes_by_id = {n.id: n for n in graph.nodes}
    adjacency: dict[str, list[tuple[str, EdgeKind]]] = {}
    for edge in graph.edges:
        if edge.kind not in edge_allowlist:
            continue
        adjacency.setdefault(edge.src, []).append((edge.dst, edge.kind))

    visited: set[str] = set(seed_ids)
    # (node_id, edge_kind_used_to_reach_it, relation_path, depth)
    queue: deque[tuple[str, EdgeKind, str, int]] = deque()
    for seed_id in seed_ids:
        for dst_id, kind in adjacency.get(seed_id, []):
            if dst_id in visited:
                continue
            visited.add(dst_id)
            queue.append((dst_id, kind, f"{seed_id} -[{kind.value}]-> {dst_id}", 1))

    hits: list[GraphHit] = []
    while queue and len(hits) < max_neighbors:
        node_id, kind, relation_path, current_depth = queue.popleft()
        node = nodes_by_id.get(node_id)
        if node is None:
            continue
        if _is_excluded_path(node.path):
            continue
        hits.append(GraphHit(node=node, edge_kind=kind, relation_path=relation_path))
        if len(hits) >= max_neighbors:
            break
        if current_depth >= effective_depth:
            continue
        for dst_id, next_kind in adjacency.get(node_id, []):
            if dst_id in visited:
                continue
            visited.add(dst_id)
            queue.append(
                (
                    dst_id,
                    next_kind,
                    f"{relation_path} -[{next_kind.value}]-> {dst_id}",
                    current_depth + 1,
                )
            )

    return hits[:max_neighbors]
