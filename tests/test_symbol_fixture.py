"""Issue #0019 Step 2 — 합성 fixture 로 심볼 그래프를 end-to-end 검증한다.

`mock_repo/` 에는 MyBatis 매퍼도 Java 테스트도 Service→Mapper 구조도 없어서
수용 기준("Service↔Mapper↔Test 일부 연결", ADR-013)을 증명할 수 없다. 그래서
그 관계를 갖춘 최소 합성 트리(`evaluation/fixtures/symbols/`, `com.example.*`)를
`MockCodebaseAdapter` 로 읽어 실제 파일 경로에서 확인한다.

임베딩·ChromaDB·LLM·DB 는 건드리지 않는다 — indexer 를 주입하지 않으므로
adapter 는 `list_files()`/`read_file()` 만 쓴다.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.codebase.mock_adapter import MockCodebaseAdapter
from app.embedding import symbol_index
from app.embedding.symbol_index import EdgeKind, SymbolGraph, SymbolKind, harvest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "evaluation" / "fixtures" / "symbols"

SERVICE = "java:com.example.tax.TaxService"
MAPPER_XML = "src/main/resources/mapper/TaxMapper.xml"
UI_XML = "src/main/webapp/ui/TaxScreen.xml"


@pytest.fixture(scope="module")
def graph() -> SymbolGraph:
    """fixture 트리 전체를 adapter 경유로 한 번만 수확한다."""
    return harvest(MockCodebaseAdapter(repo_root=str(FIXTURE_ROOT)))


def _ids(graph: SymbolGraph, kind: SymbolKind) -> set[str]:
    return {n.id for n in graph.nodes if n.kind is kind}


def _edges(graph: SymbolGraph, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in graph.edges if e.kind is kind}


# ---------------------------------------------------------------------------
# 수용 기준 — Service ↔ Mapper ↔ Test 연결
# ---------------------------------------------------------------------------


def test_test_links_to_service(graph: SymbolGraph) -> None:
    """TEST_TO_SERVICE — @Test 메서드가 service 클래스·메서드를 가리킨다."""
    links = _edges(graph, EdgeKind.TEST_TO_SERVICE)
    test_method = "test:com.example.tax.TaxServiceTest#calculatesCreditFromMapper"
    assert (test_method, SERVICE) in links
    assert (test_method, f"{SERVICE}#calculateCredit") in links


def test_service_links_to_mapper_statements(graph: SymbolGraph) -> None:
    """SERVICE_TO_MAPPER — service 가 호출하는 statement 두 개 모두 이어진다."""
    links = _edges(graph, EdgeKind.SERVICE_TO_MAPPER)
    assert (SERVICE, "mybatis:com.example.tax.TaxMapper.findCredit") in links
    assert (SERVICE, "mybatis:com.example.tax.TaxMapper.updateCredit") in links


def test_service_uses_constant(graph: SymbolGraph) -> None:
    """USES_CONSTANT — `TaxConstants.CHILD_CREDIT` 참조가 이어진다."""
    links = _edges(graph, EdgeKind.USES_CONSTANT)
    assert (SERVICE, "const:com.example.tax.TaxConstants.CHILD_CREDIT") in links


def test_all_edge_kinds_present(graph: SymbolGraph) -> None:
    """네 관계가 모두 실제 트리에서 발동한다 (수용 기준의 '일부 연결')."""
    assert {e.kind for e in graph.edges} == set(EdgeKind)


def test_edges_reference_existing_nodes(graph: SymbolGraph) -> None:
    """dangling 엣지가 없다 — 모든 src/dst 가 실재 노드다."""
    node_ids = {n.id for n in graph.nodes}
    for edge in graph.edges:
        assert edge.src in node_ids
        assert edge.dst in node_ids


# ---------------------------------------------------------------------------
# CONTAINS
# ---------------------------------------------------------------------------


def test_class_contains_its_members(graph: SymbolGraph) -> None:
    """클래스 → 메서드·상수·test 메서드가 이어진다."""
    links = _edges(graph, EdgeKind.CONTAINS)
    assert (SERVICE, f"{SERVICE}#calculateCredit") in links
    assert (
        "java:com.example.tax.TaxConstants",
        "const:com.example.tax.TaxConstants.CHILD_CREDIT",
    ) in links
    assert (
        "java:com.example.tax.TaxServiceTest",
        "test:com.example.tax.TaxServiceTest#calculatesCreditFromMapper",
    ) in links


# ---------------------------------------------------------------------------
# MyBatis 판별 — namespace 없는 XML 은 statement 가 아니다
# ---------------------------------------------------------------------------


def test_mapper_xml_yields_statements(graph: SymbolGraph) -> None:
    statements = [
        n
        for n in graph.nodes
        if n.kind is SymbolKind.MYBATIS_STATEMENT and n.path == MAPPER_XML
    ]
    assert {n.name for n in statements} == {"findCredit", "updateCredit"}
    assert all(n.container == "com.example.tax.TaxMapper" for n in statements)


def test_ui_xml_without_namespace_yields_no_statements(graph: SymbolGraph) -> None:
    """`TaxScreen.xml` 은 `<select id=...>` 를 갖지만 UI 레이아웃이라 노드가 없다.

    fixture 가 실제로 statement 처럼 생긴 태그를 담고 있어야 판별을 증명한다 —
    태그가 없으면 노드 0개는 당연한 결과라 아무것도 검증하지 못한다. 파일 주석에는
    `<mapper namespace=...>` 문자열이 들어 있어, 주석 제거가 namespace 탐색보다
    먼저 일어나는지도 함께 고정한다.
    """
    ui_xml = (FIXTURE_ROOT / UI_XML).read_text(encoding="utf-8")
    assert '<select id="taxYearCombo"' in ui_xml
    assert symbol_index._MAPPER_NS.search(ui_xml) is not None  # 주석 안에는 있고
    assert symbol_index._MAPPER_NS.search(  # 주석을 걷어내면 없다
        symbol_index._XML_COMMENT.sub(" ", ui_xml)
    ) is None
    assert [n for n in graph.nodes if n.path == UI_XML] == []


# ---------------------------------------------------------------------------
# 빌드 산출물 제외 (ADR-013 핵심)
# ---------------------------------------------------------------------------


def test_build_output_is_excluded(graph: SymbolGraph) -> None:
    """`build/generated/Junk.java` 는 adapter 의 EXCLUDED_DIRS 에서 걸러진다.

    파일 자체는 정상 Java 라 파싱은 되지만(=건너뛴 것이 아니라 애초에 목록에 없다)
    산출물 심볼이 인덱스를 오염시키면 매핑이 재생성 경로를 가리키게 된다.
    """
    assert (FIXTURE_ROOT / "build" / "generated" / "Junk.java").exists()
    listed = MockCodebaseAdapter(repo_root=str(FIXTURE_ROOT)).list_files()
    assert [f for f in listed if f.startswith("build/")] == []
    assert [n for n in graph.nodes if n.path.startswith("build/")] == []
    assert [n for n in graph.nodes if "Junk" in n.id or "Generated" in n.id] == []


# ---------------------------------------------------------------------------
# 실패 격리 — 깨진 파일 하나가 전체를 멈추지 않는다
# ---------------------------------------------------------------------------


def test_broken_file_is_skipped_without_stopping_harvest(graph: SymbolGraph) -> None:
    assert graph.skipped_files >= 1
    assert [n for n in graph.nodes if "Broken" in n.id] == []
    # 나머지는 정상 — 깨진 파일과 같은 디렉토리의 클래스가 그대로 남아 있다
    assert SERVICE in _ids(graph, SymbolKind.JAVA_CLASS)
    assert "java:com.example.tax.TaxMapper" in _ids(graph, SymbolKind.JAVA_CLASS)


# ---------------------------------------------------------------------------
# 반출 방어 — 그래프에 코드 본문이 없다
# ---------------------------------------------------------------------------


def test_nodes_carry_no_code_body(graph: SymbolGraph) -> None:
    """노드 필드는 식별자·경로뿐이다. 캐시로 떨어지므로 본문이 실리면 반출 사고다."""
    assert {f.name for f in dataclasses.fields(graph.nodes[0])} == {
        "id",
        "kind",
        "name",
        "path",
        "container",
    }
    for node in graph.nodes:
        for value in (node.id, node.name, node.container or "", node.path):
            # 코드 조각이면 줄바꿈·중괄호·세미콜론 중 하나는 반드시 섞인다
            assert not set(value) & set("{};\n")
