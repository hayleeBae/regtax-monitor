"""Issue #0020 Step 0 — SymbolGraph 이웃 순회(graph_expand) 테스트.

실제 adapter/DB 없이 소형 SymbolGraph fixture를 직접 구성해 검증한다.
"""

from __future__ import annotations

from app.embedding.symbol_index import EdgeKind, SymbolEdge, SymbolGraph, SymbolKind, SymbolNode
from app.retrieval.graph_expand import GraphHit, neighbors, seed_node_ids


def _node(node_id: str, path: str, name: str | None = None) -> SymbolNode:
    return SymbolNode(
        id=node_id,
        kind=SymbolKind.JAVA_CLASS,
        name=name or node_id,
        path=path,
        container=None,
    )


NODE_A = _node("java:A", "A.java", "A")
NODE_B = _node("java:B", "B.java", "B")
NODE_C = _node("java:C", "C.java", "C")
NODE_D = _node("java:D", "D.java", "D")
NODE_E_EXCLUDED = _node("java:E", "out/artifacts/E.java", "E")

# A -CONTAINS-> B -SERVICE_TO_MAPPER-> C -USES_CONSTANT-> D -TEST_TO_SERVICE-> A (cycle)
# B -CONTAINS-> E (빌드 산출물 경로 — 제외 대상)
BASIC_GRAPH = SymbolGraph(
    nodes=(NODE_A, NODE_B, NODE_C, NODE_D, NODE_E_EXCLUDED),
    edges=(
        SymbolEdge("java:A", "java:B", EdgeKind.CONTAINS),
        SymbolEdge("java:B", "java:C", EdgeKind.SERVICE_TO_MAPPER),
        SymbolEdge("java:C", "java:D", EdgeKind.USES_CONSTANT),
        SymbolEdge("java:D", "java:A", EdgeKind.TEST_TO_SERVICE),  # cycle back to seed
        SymbolEdge("java:B", "java:E", EdgeKind.CONTAINS),
    ),
    skipped_files=0,
)

ALL_KINDS = frozenset(EdgeKind)


def test_depth1_direct_neighbors_only():
    hits = neighbors(BASIC_GRAPH, {"java:A"}, ALL_KINDS, depth=1, max_depth=2)
    ids = {h.node.id for h in hits}
    # depth 1 이므로 B(직접 이웃)와 E(B의 CONTAINS 대상이 아니라 A의 직접 이웃은 아님)는 제외.
    # A -> B 만 direct.
    assert ids == {"java:B"}


def test_depth2_reaches_two_hops():
    hits = neighbors(BASIC_GRAPH, {"java:A"}, ALL_KINDS, depth=2, max_depth=2)
    ids = {h.node.id for h in hits}
    # depth 2: A->B (1-hop), B->C, B->E (2-hop). E는 EXCLUDED_DIRS 이므로 결과에서 빠진다.
    assert ids == {"java:B", "java:C"}


def test_cycle_terminates_without_infinite_loop():
    # depth 상한을 넉넉히 줘도(3) max_depth=2 로 클램프되어 유한 시간 내 종료해야 한다.
    hits = neighbors(BASIC_GRAPH, {"java:A"}, ALL_KINDS, depth=3, max_depth=2)
    ids = [h.node.id for h in hits]
    assert len(ids) == len(set(ids))  # 중복 방문 없음
    assert "java:A" not in ids  # seed 자신은 반환하지 않음(사이클로 돌아와도 마찬가지)


def test_edge_allowlist_excludes_other_kinds():
    hits = neighbors(
        BASIC_GRAPH,
        {"java:A"},
        frozenset({EdgeKind.SERVICE_TO_MAPPER}),
        depth=2,
        max_depth=2,
    )
    assert hits == []  # A->B 는 CONTAINS 라 allowlist 에 없어 아예 못 나간다


def test_max_neighbors_truncates():
    hits = neighbors(BASIC_GRAPH, {"java:A"}, ALL_KINDS, depth=2, max_depth=2, max_neighbors=1)
    assert len(hits) == 1


def test_excluded_dirs_node_filtered_out():
    hits = neighbors(BASIC_GRAPH, {"java:B"}, ALL_KINDS, depth=1, max_depth=2)
    ids = {h.node.id for h in hits}
    assert "java:E" not in ids
    assert ids == {"java:C"}


def test_empty_graph_returns_empty():
    empty = SymbolGraph(nodes=(), edges=(), skipped_files=0)
    assert neighbors(empty, {"java:A"}, ALL_KINDS) == []


def test_empty_seed_returns_empty():
    assert neighbors(BASIC_GRAPH, set(), ALL_KINDS) == []


def test_seed_itself_not_returned():
    hits = neighbors(BASIC_GRAPH, {"java:A", "java:B"}, ALL_KINDS, depth=1, max_depth=2)
    ids = {h.node.id for h in hits}
    assert "java:A" not in ids
    assert "java:B" not in ids


def test_seed_node_ids_by_path_only():
    ids = seed_node_ids(BASIC_GRAPH, "A.java", None)
    assert ids == {"java:A"}


def test_seed_node_ids_prefers_symbol_name_match():
    graph = SymbolGraph(
        nodes=(
            _node("java:Foo", "Foo.java", "Foo"),
            SymbolNode(
                id="java:Foo#bar",
                kind=SymbolKind.JAVA_METHOD,
                name="bar",
                path="Foo.java",
                container="Foo",
            ),
        ),
        edges=(),
        skipped_files=0,
    )
    ids = seed_node_ids(graph, "Foo.java", "bar")
    assert ids == {"java:Foo#bar"}


def test_seed_node_ids_falls_back_when_symbol_not_found():
    ids = seed_node_ids(BASIC_GRAPH, "A.java", "nonexistent_symbol")
    assert ids == {"java:A"}


def test_seed_node_ids_no_matching_path_returns_empty():
    assert seed_node_ids(BASIC_GRAPH, "nope.java", None) == set()


def test_graph_hit_relation_path_is_readable():
    hits = neighbors(BASIC_GRAPH, {"java:A"}, ALL_KINDS, depth=1, max_depth=2)
    assert hits[0].relation_path == "java:A -[contains]-> java:B"
    assert isinstance(hits[0], GraphHit)
