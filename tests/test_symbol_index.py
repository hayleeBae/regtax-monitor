"""Issue #0019 Step 0·1 — 코드 심볼 노드 추출 + 관계 그래프 테스트.

실제 파일·임베딩·DB 없이 가짜 adapter 에 inline 소스를 담아 검증한다.
캐시 테스트는 `_CACHE` 를 tmp_path 로 monkeypatch 한다 — 프로젝트 루트의 실제
`symbol_index_cache.json` 을 건드리지 않는다.
"""

from __future__ import annotations

import dataclasses
import json

from app.embedding import symbol_index
from app.embedding.symbol_index import (
    EdgeKind,
    SymbolEdge,
    SymbolKind,
    SymbolNode,
    extract_java,
    extract_mybatis,
    extract_sql,
    harvest,
    harvest_nodes,
    link_edges,
    load,
)

JAVA_SERVICE = """
package com.example.tax;

import java.util.List;

/** 소득세법 제55조 — 주석 안의 { 중괄호 } 와 "문자열" 은 무시되어야 한다. */
public class TaxService {

    private static final long CHILD_CREDIT = 150000L;
    private static final double[][] TAX_BRACKETS = {{14_000_000, 0.06}};

    public long calculate(long taxBase) {
        Runnable r = new Runnable() {
            public void run() {
                System.out.println("익명 클래스 안의 메서드");
            }
        };
        List<String> names = load();
        names.forEach(n -> { System.out.println(n); });
        if (taxBase > 0) {
            return taxBase;
        }
        return CHILD_CREDIT;
    }

    private Map<String, List<Integer>> load() {
        return null;
    }
}
"""

JAVA_TEST = """
package com.example.tax;

public class TaxServiceTest {

    @Test
    public void childCredit() {
        new TaxService().calculate(1000L);
    }
}
"""

MAPPER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mapper namespace="com.example.tax.TaxMapper">
    <!-- <select id="commentedOut"> 주석 안 statement 는 무시 </select> -->
    <select id="findCredit" resultType="long">SELECT n0200 FROM tax</select>
    <update id="updateCredit">UPDATE tax SET n0200 = 1</update>
</mapper>
"""

LAYOUT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<screen>
    <select id="deductionCombo" label="공제구분"/>
    <update id="saveButton"/>
</screen>
"""

SQL_SOURCE = """
-- 소득세법 제55조 세율 테이블 (주석 안의 SELECT 는 무시)
CREATE TABLE income_tax_rates (
    id          INTEGER PRIMARY KEY,
    tax_rate    DECIMAL(5,4) NOT NULL
);

INSERT INTO income_tax_rates (id, tax_rate) VALUES (1, 0.0600);
"""

BROKEN_JAVA = """
package com.example.tax;

public class Broken {
    public void oops() {
        if (true) {
    }
"""

# 매퍼를 두 방식으로 호출하고 다른 클래스의 상수를 쓰는 DAO.
JAVA_DAO = """
package com.example.tax;

public class TaxDao {

    private TaxMapper taxMapper;

    public long load(long id) {
        long credit = taxMapper.findCredit(id);
        long updated = sqlSession.update("com.example.tax.TaxMapper.updateCredit", id);
        return credit + updated + TaxService.CHILD_CREDIT;
    }
}
"""

# 매퍼를 주석에서만 언급한다 — 엣지가 생기면 안 된다.
JAVA_NO_MAPPER = """
package com.example.tax;

public class TaxReport {

    public String describe(long id) {
        // 매퍼는 TaxMapper.findCredit 이지만 여기서 호출하지는 않는다 (주석뿐)
        return String.valueOf(id);
    }
}
"""

SERVICE_PATH = "src/tax/TaxService.java"
DAO_PATH = "src/tax/TaxDao.java"
REPORT_PATH = "src/tax/TaxReport.java"
TEST_PATH = "src/test/java/com/example/tax/TaxServiceTest.java"
MAPPER_PATH = "src/mapper/TaxMapper.xml"

ALL_FILES = {
    SERVICE_PATH: JAVA_SERVICE,
    DAO_PATH: JAVA_DAO,
    REPORT_PATH: JAVA_NO_MAPPER,
    TEST_PATH: JAVA_TEST,
    MAPPER_PATH: MAPPER_XML,
    "src/webapp/TaxScreen.xml": LAYOUT_XML,
    "sql/income_tax_rates.sql": SQL_SOURCE,
}


class FakeAdapter:
    """list_files/read_file 만 가진 최소 adapter (실제 파일시스템 접근 없음)."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = dict(files)
        self.reads: list[str] = []

    def list_files(self) -> list[str]:
        return list(self._files)

    def read_file(self, path: str) -> str:
        self.reads.append(path)
        return self._files[path]


def _ids(nodes: list[SymbolNode]) -> set[str]:
    return {node.id for node in nodes}


def test_java_class_method_constant_ids_follow_the_rule() -> None:
    nodes = extract_java("src/tax/TaxService.java", JAVA_SERVICE)

    assert "java:com.example.tax.TaxService" in _ids(nodes)
    assert "java:com.example.tax.TaxService#calculate" in _ids(nodes)
    assert "java:com.example.tax.TaxService#load" in _ids(nodes)
    assert "const:com.example.tax.TaxService.CHILD_CREDIT" in _ids(nodes)
    assert "const:com.example.tax.TaxService.TAX_BRACKETS" in _ids(nodes)

    by_id = {node.id: node for node in nodes}
    klass = by_id["java:com.example.tax.TaxService"]
    assert klass.kind is SymbolKind.JAVA_CLASS
    assert klass.container == "com.example.tax"

    method = by_id["java:com.example.tax.TaxService#calculate"]
    assert method.kind is SymbolKind.JAVA_METHOD
    assert method.name == "calculate"
    assert method.container == "com.example.tax.TaxService"
    assert method.path == "src/tax/TaxService.java"


def test_nested_braces_do_not_break_method_boundaries() -> None:
    """익명 클래스·람다·제네릭이 있어도 메서드 경계가 밀리지 않는다."""
    nodes = extract_java("src/tax/TaxService.java", JAVA_SERVICE)

    methods = [n for n in nodes if n.kind is SymbolKind.JAVA_METHOD]
    # calculate 본문 안의 익명 클래스 run() 은 별도 심볼로 잡지 않는다.
    assert [m.name for m in methods] == ["calculate", "load"]
    # 제네릭 반환형(Map<String, List<Integer>>) 뒤의 메서드도 인식된다.
    assert "java:com.example.tax.TaxService#load" in _ids(nodes)


def test_test_methods_are_classified_by_path_or_annotation() -> None:
    by_path = extract_java("src/test/java/com/example/tax/TaxServiceTest.java", JAVA_TEST)
    assert "test:com.example.tax.TaxServiceTest#childCredit" in _ids(by_path)
    method = next(n for n in by_path if n.name == "childCredit")
    assert method.kind is SymbolKind.TEST_METHOD

    # 경로에 test 가 없어도 @Test 어노테이션이면 test 메서드다.
    by_annotation = extract_java("src/main/java/com/example/tax/CreditChecks.java", JAVA_TEST)
    method = next(n for n in by_annotation if n.name == "childCredit")
    assert method.kind is SymbolKind.TEST_METHOD
    assert method.id == "test:com.example.tax.TaxServiceTest#childCredit"


def test_mybatis_statements_need_a_namespace() -> None:
    nodes = extract_mybatis("src/mapper/TaxMapper.xml", MAPPER_XML)

    assert _ids(nodes) == {
        "mybatis:com.example.tax.TaxMapper.findCredit",
        "mybatis:com.example.tax.TaxMapper.updateCredit",
    }
    assert {n.kind for n in nodes} == {SymbolKind.MYBATIS_STATEMENT}
    assert {n.container for n in nodes} == {"com.example.tax.TaxMapper"}

    # namespace 없는 UI·설정 XML 은 statement 로 보지 않는다 (오탐 폭발 방지).
    assert extract_mybatis("src/webapp/TaxScreen.xml", LAYOUT_XML) == []


def test_sql_statements_keep_only_verb_and_table() -> None:
    nodes = extract_sql("sql/income_tax_rates.sql", SQL_SOURCE)

    assert _ids(nodes) == {
        "sql:sql/income_tax_rates.sql#create_table:income_tax_rates",
        "sql:sql/income_tax_rates.sql#insert:income_tax_rates",
    }
    assert {n.kind for n in nodes} == {SymbolKind.SQL_STATEMENT}
    assert {n.container for n in nodes} == {"income_tax_rates"}


def test_harvest_isolates_a_broken_file() -> None:
    adapter = FakeAdapter(
        {
            "src/tax/Broken.java": BROKEN_JAVA,
            "src/tax/TaxService.java": JAVA_SERVICE,
            "src/mapper/TaxMapper.xml": MAPPER_XML,
            "src/webapp/TaxScreen.xml": LAYOUT_XML,
            "README.md": "심볼 추출 대상이 아닌 확장자",
        }
    )

    nodes = harvest_nodes(adapter)

    assert "java:com.example.tax.TaxService#calculate" in _ids(nodes)
    assert "mybatis:com.example.tax.TaxMapper.findCredit" in _ids(nodes)
    assert not any(n.id.startswith("java:com.example.tax.Broken") for n in nodes)
    # 확장자가 다른 파일은 읽되 추출하지 않는다 → 노드 없음
    assert not any(n.path == "README.md" for n in nodes)


def test_harvest_reads_only_through_the_adapter() -> None:
    adapter = FakeAdapter(
        {
            "src/tax/TaxService.java": JAVA_SERVICE,
            "src/mapper/TaxMapper.xml": MAPPER_XML,
            "docs/guide.md": "대상 아님",
        }
    )

    harvest_nodes(adapter)

    # 지원 확장자만 adapter 로 읽는다 (직접 파일시스템 순회 없음).
    assert adapter.reads == ["src/tax/TaxService.java", "src/mapper/TaxMapper.xml"]


def test_nodes_carry_no_code_body() -> None:
    adapter = FakeAdapter(
        {
            "src/tax/TaxService.java": JAVA_SERVICE,
            "src/mapper/TaxMapper.xml": MAPPER_XML,
        }
    )

    nodes = harvest_nodes(adapter)

    assert {f.name for f in dataclasses.fields(SymbolNode)} == {
        "id",
        "kind",
        "name",
        "path",
        "container",
    }
    for node in nodes:
        values = " ".join(str(v) for v in dataclasses.astuple(node))
        assert "{" not in values and "\n" not in values
        assert "SELECT" not in values.upper() or node.path.endswith(".xml")
        assert "150000L" not in values


# ── Step 1: 엣지·그래프 ────────────────────────────────────


def _full_graph() -> symbol_index.SymbolGraph:
    return harvest(FakeAdapter(ALL_FILES))


def _edges(graph: symbol_index.SymbolGraph, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in graph.edges if e.kind is kind}


def test_contains_links_class_to_its_members() -> None:
    graph = _full_graph()
    contains = _edges(graph, EdgeKind.CONTAINS)

    klass = "java:com.example.tax.TaxService"
    assert (klass, f"{klass}#calculate") in contains
    assert (klass, f"{klass}#load") in contains
    assert (klass, "const:com.example.tax.TaxService.CHILD_CREDIT") in contains
    assert (
        "java:com.example.tax.TaxServiceTest",
        "test:com.example.tax.TaxServiceTest#childCredit",
    ) in contains


def test_service_to_mapper_links_only_on_a_real_reference() -> None:
    graph = _full_graph()
    links = _edges(graph, EdgeKind.SERVICE_TO_MAPPER)

    # 매퍼 인터페이스 호출(taxMapper.findCredit)과 namespace.statementId 문자열 참조
    assert links == {
        ("java:com.example.tax.TaxDao", "mybatis:com.example.tax.TaxMapper.findCredit"),
        ("java:com.example.tax.TaxDao", "mybatis:com.example.tax.TaxMapper.updateCredit"),
    }
    # 매퍼를 언급조차 않는 서비스, 주석에서만 언급하는 파일은 이어지지 않는다.
    srcs = {src for src, _dst in links}
    assert "java:com.example.tax.TaxService" not in srcs
    assert "java:com.example.tax.TaxReport" not in srcs


def test_test_to_service_links_test_method_to_service() -> None:
    graph = _full_graph()
    links = _edges(graph, EdgeKind.TEST_TO_SERVICE)

    test_method = "test:com.example.tax.TaxServiceTest#childCredit"
    assert (test_method, "java:com.example.tax.TaxService") in links
    assert (test_method, "java:com.example.tax.TaxService#calculate") in links
    # 자기 자신(test 클래스)으로 되짚는 엣지는 만들지 않는다.
    assert (test_method, "java:com.example.tax.TaxServiceTest") not in links
    # src 는 항상 test 메서드다.
    assert {src for src, _dst in links} == {test_method}


def test_uses_constant_links_the_using_file() -> None:
    graph = _full_graph()
    links = _edges(graph, EdgeKind.USES_CONSTANT)

    child_credit = "const:com.example.tax.TaxService.CHILD_CREDIT"
    assert ("java:com.example.tax.TaxDao", child_credit) in links
    # 선언 클래스 자신은 CONTAINS 가 담으므로 USES_CONSTANT 를 중복으로 만들지 않는다.
    assert ("java:com.example.tax.TaxService", child_credit) not in links
    # 상수를 언급하지 않는 파일은 이어지지 않는다.
    assert ("java:com.example.tax.TaxReport", child_credit) not in links


def test_edges_never_dangle() -> None:
    graph = _full_graph()
    ids = {node.id for node in graph.nodes}

    assert graph.edges
    for edge in graph.edges:
        assert edge.src in ids, edge
        assert edge.dst in ids, edge


def test_missing_target_nodes_produce_no_edges() -> None:
    """매퍼·상수 노드가 그래프에 없으면 참조가 있어도 엣지를 만들지 않는다."""
    nodes = extract_java(DAO_PATH, JAVA_DAO)

    edges = link_edges(nodes, {DAO_PATH: JAVA_DAO})

    assert edges == [
        SymbolEdge(
            src="java:com.example.tax.TaxDao",
            dst="java:com.example.tax.TaxDao#load",
            kind=EdgeKind.CONTAINS,
        )
    ]


def test_load_writes_cache_then_reads_it(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "symbol_index_cache.json"
    monkeypatch.setattr(symbol_index, "_CACHE", cache)
    adapter = FakeAdapter(ALL_FILES)

    first = load(adapter)
    reads_after_harvest = len(adapter.reads)

    assert cache.exists()
    assert reads_after_harvest > 0
    assert first.nodes and first.edges

    second = load(adapter)

    assert len(adapter.reads) == reads_after_harvest   # 캐시 로드 — 재수확 없음
    assert second == first                             # 직렬화 왕복이 그래프를 보존한다

    third = load(adapter, refresh=True)

    assert len(adapter.reads) > reads_after_harvest    # refresh 는 재수확한다
    assert third == first


def test_cache_holds_no_code_body(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "symbol_index_cache.json"
    monkeypatch.setattr(symbol_index, "_CACHE", cache)

    load(FakeAdapter(ALL_FILES))
    raw = cache.read_text(encoding="utf-8")

    for leaked in (
        "private static final",
        "public class",
        "System.out.println",
        "150000L",
        "SELECT n0200",
        "@Test",
        "resultType",
    ):
        assert leaked not in raw, leaked
    # 역직렬화는 enum 을 되살린다 (문자열이 아니라 SymbolKind/EdgeKind).
    graph = symbol_index._from_dict(json.loads(raw))
    assert {type(n.kind) for n in graph.nodes} == {SymbolKind}
    assert {type(e.kind) for e in graph.edges} == {EdgeKind}


def test_broken_cache_is_re_harvested(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "symbol_index_cache.json"
    cache.write_text("{ 깨진 json", encoding="utf-8")
    monkeypatch.setattr(symbol_index, "_CACHE", cache)

    graph = load(FakeAdapter(ALL_FILES))

    assert graph.nodes


def test_empty_or_missing_adapter_yields_an_empty_graph(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "symbol_index_cache.json"
    monkeypatch.setattr(symbol_index, "_CACHE", cache)

    for adapter in (None, FakeAdapter({}), FakeAdapter({"README.md": "대상 아님"})):
        graph = load(adapter)
        assert graph.nodes == ()
        assert graph.edges == ()
        assert graph.skipped_files == 0

    assert harvest(None).nodes == ()
    # 빈 결과는 캐시에 굳히지 않는다 (미설정 환경 대응).
    assert not cache.exists()


def test_graph_records_skipped_files() -> None:
    graph = harvest(
        FakeAdapter({"src/tax/Broken.java": BROKEN_JAVA, SERVICE_PATH: JAVA_SERVICE})
    )

    assert graph.skipped_files == 1
    assert "java:com.example.tax.TaxService" in {n.id for n in graph.nodes}
