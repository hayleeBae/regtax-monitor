"""Issue #0019 Step 0 — 코드 심볼 노드 추출 테스트.

실제 파일·임베딩·DB 없이 가짜 adapter 에 inline 소스를 담아 검증한다.
"""

from __future__ import annotations

import dataclasses

from app.embedding.symbol_index import (
    SymbolKind,
    SymbolNode,
    extract_java,
    extract_mybatis,
    extract_sql,
    harvest_nodes,
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
