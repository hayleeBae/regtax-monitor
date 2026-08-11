"""
코드 심볼 인덱스 — 노드(심볼) 추출 + 관계 그래프 (Issue #0019 / ADR-013).

eHR 레거시는 깔끔히 파싱되지 않고, 회사망 SSL 제약 때문에 진짜 파서(javalang·
tree-sitter)를 새로 깔 수도 없다. 그래서 `indexer._chunk_java` 가 쓰던 것과 같은
정규식 + 중괄호 균형 매칭만으로 최소 심볼을 뽑는다. 목표는 "완전한 분석"이 아니라
#0020 이 이웃 확장에 쓸 재료(그래프의 노드)다 — 수용 기준도 '일부 연결'이다.

규약 (`term_dict.py`·`const_inventory.py` 가족과 동일):
  - 소스 접근은 `CodebaseAdapter` 만 경유한다(`list_files()`/`read_file()`).
    직접 파일 순회는 `EXCLUDED_DIRS`(exploded WAR 등 빌드 산출물)와 CP949 처리를
    우회해 인덱스를 오염시킨다.
  - 노드에는 **코드 본문을 담지 않는다**. 경로·이름·컨테이너만 — 이 산출물은
    캐시로 떨어지므로 본문을 담으면 eHR 코드 반출 사고가 된다.
  - 파일 하나의 파싱 실패가 전체 추출을 멈추지 않는다(파일 단위 try/except).

엣지(관계)는 휴리스틱이며 **"일부 연결"이 수용 기준**이다 — 못 찾으면 안 잇는다.
오탐 엣지가 진짜 관계보다 많아지면 #0020 의 이웃 확장이 잘못된 후보를 상위로 올린다.
provider·검색 배선은 이 모듈이 아니라 #0020 이 한다.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class SymbolKind(str, Enum):
    JAVA_CLASS = "java_class"
    JAVA_METHOD = "java_method"
    MYBATIS_STATEMENT = "mybatis_statement"   # select/insert/update/delete
    TEST_METHOD = "test_method"
    CONSTANT = "constant"
    # .sql 파일의 문장. MyBatis statement 와 성격이 달라(namespace·id 없음)
    # 같은 kind 로 뭉뜽그리지 않는다 — 우선순위가 낮아 최소 정보만 담는다.
    SQL_STATEMENT = "sql_statement"


@dataclass(frozen=True)
class SymbolNode:
    id: str            # 안정적 식별자 (Step 1 의 엣지가 이 id 로 노드를 잇는다)
    kind: SymbolKind
    name: str          # 사람이 읽는 이름 (메서드명·statement id 등)
    path: str          # 심볼이 있는 파일 (adapter 상대 경로)
    container: str | None = None   # 소속 (클래스 FQN·mapper namespace 등)


class EdgeKind(str, Enum):
    CONTAINS = "contains"                      # class → method/constant
    SERVICE_TO_MAPPER = "service_to_mapper"    # Java 호출 → MyBatis statement
    TEST_TO_SERVICE = "test_to_service"        # test 메서드 → service 클래스/메서드
    USES_CONSTANT = "uses_constant"            # 파일/메서드 → constant


@dataclass(frozen=True)
class SymbolEdge:
    src: str      # SymbolNode.id
    dst: str      # SymbolNode.id
    kind: EdgeKind


@dataclass(frozen=True)
class SymbolGraph:
    nodes: tuple[SymbolNode, ...]
    edges: tuple[SymbolEdge, ...]
    skipped_files: int            # 파싱 실패로 건너뛴 파일 수 (관측성)


_CACHE = Path(__file__).resolve().parents[2] / "symbol_index_cache.json"

# 상수·메서드처럼 클래스에 소속된 노드 (CONTAINS 의 dst 후보)
_MEMBER_KINDS = frozenset(
    {SymbolKind.JAVA_METHOD, SymbolKind.TEST_METHOD, SymbolKind.CONSTANT}
)
_STATEMENT_KINDS = frozenset(
    {SymbolKind.MYBATIS_STATEMENT, SymbolKind.SQL_STATEMENT}
)


# ── Java ──────────────────────────────────────────────────

_JAVA_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_JAVA_TYPE = re.compile(r"\b(?:class|interface|enum)\s+(\w+)\b")

# 메서드 시그니처: 수식어* 제네릭? 반환형 이름(파라미터) throws? {
# _chunk_java 의 패턴을 넓혔다 — JUnit 5 처럼 수식어 없는 메서드도 잡아야 하므로
# 수식어를 0회 이상으로 두고, 대신 제어문(if/for/...)을 키워드 목록으로 걸러낸다.
_JAVA_METHOD = re.compile(
    r"(?:(?:public|private|protected|static|final|synchronized|abstract|native|default|strictfp)\s+)*"
    r"(?:<[^;{}]*>\s*)?"
    r"(?P<ret>[\w.$]+(?:\s*<[^;{}]*>)?(?:\s*\[\s*\])*)\s+"
    r"(?P<name>\w+)\s*\([^;{}]*\)\s*(?:throws\s+[\w\s.,<>]+?)?\s*\{"
)

# 제어문·선언 키워드가 반환형/이름 자리에 오면 메서드가 아니다.
_JAVA_KEYWORDS = frozenset(
    {
        "if", "else", "for", "while", "do", "switch", "case", "catch", "try",
        "finally", "return", "new", "throw", "assert", "synchronized", "class",
        "interface", "enum", "package", "import", "instanceof",
    }
)

# static final <타입> UPPER_SNAKE = ... / ;
_JAVA_CONST = re.compile(
    r"\b(?:static\s+final|final\s+static)\s+"
    r"[\w.$]+(?:\s*<[^;{}]*>)?(?:\s*\[\s*\])*\s+"
    r"(?P<name>[A-Z][A-Z0-9_]*)\s*[=;]"
)

_TEST_ANNOTATION = re.compile(r"@Test\b")


def _mask_java(text: str, mask_strings: bool = True) -> str:
    """주석·문자열 리터럴을 같은 길이의 공백으로 치환한다.

    주석 안의 `{`/`//` 나 문자열 안의 중괄호가 경계 추적을 깨뜨리는 것을 막는다.
    길이를 보존하므로 매치 위치를 원본과 그대로 견줄 수 있다.

    `mask_strings=False` 는 주석만 지운다 — MyBatis statement 참조는
    `selectOne("ns.statementId")` 처럼 문자열 리터럴 안에 있는 경우가 많아
    엣지 연결에서는 문자열을 살려야 한다. 문자열 스캔 자체는 그대로 유지한다
    (`"http://x"` 의 `//` 를 주석으로 오인하지 않기 위해)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif ch == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append("".join(c if c == "\n" else " " for c in text[i:end]))
            i = end
        elif ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            chunk = text[i:j]
            out.append(
                "".join(c if c == "\n" else " " for c in chunk)
                if mask_strings
                else chunk
            )
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _ensure_balanced(masked: str) -> None:
    """파일 전체의 중괄호가 균형인지 확인. 깨진 파일은 여기서 걸러 건너뛴다."""
    depth = 0
    for ch in masked:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("중괄호 균형이 맞지 않음")
    if depth != 0:
        raise ValueError("중괄호 균형이 맞지 않음")


def _block_end(masked: str, open_idx: int) -> int:
    """`open_idx` 의 `{` 와 짝을 이루는 `}` 다음 위치. (_chunk_java 의 깊이 추적)"""
    depth = 0
    for pos in range(open_idx, len(masked)):
        ch = masked[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return pos + 1
    raise ValueError("중괄호 균형이 맞지 않음")


def _java_class_spans(masked: str, package: str) -> list[tuple[int, int, str, str, str | None]]:
    """(시작, 끝, FQN, 이름, 소속) 목록. 중첩 클래스는 바깥 클래스를 소속으로 갖는다."""
    spans: list[tuple[int, int, str, str, str | None]] = []
    stack: list[tuple[int, int, str]] = []
    for m in _JAVA_TYPE.finditer(masked):
        open_idx = masked.find("{", m.end())
        if open_idx == -1:
            continue
        end = _block_end(masked, open_idx)
        while stack and m.start() >= stack[-1][1]:
            stack.pop()
        parent = stack[-1][2] if stack else None
        name = m.group(1)
        if parent:
            fqn, container = f"{parent}.{name}", parent
        elif package:
            fqn, container = f"{package}.{name}", package
        else:
            fqn, container = name, None
        spans.append((m.start(), end, fqn, name, container))
        stack.append((m.start(), end, fqn))
    return spans


def _enclosing_class(
    spans: list[tuple[int, int, str, str, str | None]], pos: int
) -> str | None:
    """pos 를 감싸는 가장 안쪽 클래스의 FQN."""
    best: tuple[int, str] | None = None
    for start, end, fqn, _name, _container in spans:
        if start <= pos < end and (best is None or start > best[0]):
            best = (start, fqn)
    return best[1] if best else None


def _has_test_annotation(masked: str, method_start: int) -> bool:
    """메서드 선언 직전(직전 `;`/`{`/`}` 이후) 구간에 @Test 가 붙어 있는지."""
    boundary = max(masked.rfind(c, 0, method_start) for c in ";{}")
    return bool(_TEST_ANNOTATION.search(masked[boundary + 1 : method_start]))


def extract_java(path: str, text: str) -> list[SymbolNode]:
    """Java 파일에서 클래스·메서드·상수 노드를 뽑는다. (본문은 담지 않는다)"""
    masked = _mask_java(text)
    _ensure_balanced(masked)

    pkg_match = _JAVA_PACKAGE.search(masked)
    package = pkg_match.group(1) if pkg_match else ""
    spans = _java_class_spans(masked, package)
    is_test_path = "test" in path.lower()

    found: list[tuple[int, SymbolNode]] = [
        (
            start,
            SymbolNode(
                id=f"java:{fqn}",
                kind=SymbolKind.JAVA_CLASS,
                name=name,
                path=path,
                container=container,
            ),
        )
        for start, _end, fqn, name, container in spans
    ]

    body_end = -1
    for m in _JAVA_METHOD.finditer(masked):
        if m.start() < body_end:
            continue  # 다른 메서드 본문 안(익명 클래스·람다) — 별도 심볼로 보지 않는다
        ret = m.group("ret").split("<")[0].strip()
        name = m.group("name")
        if ret in _JAVA_KEYWORDS or name in _JAVA_KEYWORDS:
            continue
        owner = _enclosing_class(spans, m.start())
        if owner is None:
            continue
        body_end = _block_end(masked, m.end() - 1)
        is_test = is_test_path or _has_test_annotation(masked, m.start())
        kind = SymbolKind.TEST_METHOD if is_test else SymbolKind.JAVA_METHOD
        prefix = "test" if is_test else "java"
        found.append(
            (
                m.start(),
                SymbolNode(
                    id=f"{prefix}:{owner}#{name}",
                    kind=kind,
                    name=name,
                    path=path,
                    container=owner,
                ),
            )
        )

    for m in _JAVA_CONST.finditer(masked):
        owner = _enclosing_class(spans, m.start())
        if owner is None:
            continue
        name = m.group("name")
        found.append(
            (
                m.start(),
                SymbolNode(
                    id=f"const:{owner}.{name}",
                    kind=SymbolKind.CONSTANT,
                    name=name,
                    path=path,
                    container=owner,
                ),
            )
        )

    found.sort(key=lambda item: item[0])
    return _dedup([node for _start, node in found])


# ── MyBatis XML ───────────────────────────────────────────

_XML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_MAPPER_NS = re.compile(
    r"<\s*mapper\b[^>]*\bnamespace\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE
)
_MAPPER_STATEMENT = re.compile(
    r"<\s*(select|insert|update|delete)\b([^>]*)>", re.IGNORECASE
)
_XML_ID = re.compile(r"\bid\s*=\s*[\"']([^\"']+)[\"']")


def extract_mybatis(path: str, text: str) -> list[SymbolNode]:
    """`<mapper namespace=...>` 가 있는 XML 에서만 statement 노드를 뽑는다.

    eHR 에는 UI·설정 XML 이 섞여 있어 namespace 없는 XML 까지 statement 로 보면
    오탐이 폭발한다 — namespace 가 없으면 노드 0개다."""
    body = _XML_COMMENT.sub(" ", text)
    ns_match = _MAPPER_NS.search(body)
    if not ns_match:
        return []
    namespace = ns_match.group(1)

    nodes: list[SymbolNode] = []
    for m in _MAPPER_STATEMENT.finditer(body):
        id_match = _XML_ID.search(m.group(2))
        if not id_match:
            continue
        statement_id = id_match.group(1)
        nodes.append(
            SymbolNode(
                id=f"mybatis:{namespace}.{statement_id}",
                kind=SymbolKind.MYBATIS_STATEMENT,
                name=statement_id,
                path=path,
                container=namespace,
            )
        )
    return _dedup(nodes)


# ── SQL ───────────────────────────────────────────────────

_SQL_COMMENT = re.compile(r"--[^\n]*|/\*[\s\S]*?\*/")
_SQL_VERB = re.compile(
    r"^\s*(select|insert|update|delete|create\s+table|alter\s+table)\b",
    re.IGNORECASE | re.MULTILINE,
)
_SQL_TABLE = re.compile(
    r"\b(?:from|into|update|table|join)\s+[\"'`]?([\w.]+)", re.IGNORECASE
)


def extract_sql(path: str, text: str) -> list[SymbolNode]:
    """.sql 파일에서 문장 종류와 주 테이블 정도만 뽑는다 (우선순위 낮음, 최소 정보)."""
    from app.embedding.indexer import _chunk_sql

    nodes: list[SymbolNode] = []
    for stmt in _chunk_sql(_SQL_COMMENT.sub(" ", text)):
        verb_match = _SQL_VERB.search(stmt)
        table_match = _SQL_TABLE.search(stmt)
        if not verb_match or not table_match:
            continue
        verb = re.sub(r"\s+", "_", verb_match.group(1).strip()).lower()
        table = table_match.group(1).lower()
        nodes.append(
            SymbolNode(
                id=f"sql:{path}#{verb}:{table}",
                kind=SymbolKind.SQL_STATEMENT,
                name=f"{verb} {table}",
                path=path,
                container=table,
            )
        )
    return _dedup(nodes)


# ── harvest ───────────────────────────────────────────────

_EXTRACTORS = {
    ".java": extract_java,
    ".xml": extract_mybatis,
    ".sql": extract_sql,
}


def _dedup(nodes: list[SymbolNode]) -> list[SymbolNode]:
    """같은 id(오버로드·반복 statement)는 첫 번째만 남긴다. 순서는 보존."""
    seen: set[str] = set()
    unique: list[SymbolNode] = []
    for node in nodes:
        if node.id in seen:
            continue
        seen.add(node.id)
        unique.append(node)
    return unique


def _harvest_files(adapter) -> tuple[list[SymbolNode], dict[str, str], int]:
    """(노드, {경로: 본문}, 건너뛴 파일 수). 본문은 호출자가 재사용하되 저장하지 않는다."""
    nodes: list[SymbolNode] = []
    texts: dict[str, str] = {}
    skipped = 0
    for path in adapter.list_files():
        extractor = _EXTRACTORS.get(Path(path).suffix.lower())
        if extractor is None:
            continue
        try:
            text = adapter.read_file(path)
            extracted = extractor(path, text)
        except Exception as exc:  # 한 파일의 실패가 전체 추출을 멈추지 않는다
            skipped += 1
            # 본문·절대경로는 로그에 남기지 않는다 (반출 위험)
            logger.warning("심볼 추출 실패로 건너뜀: %s (%s)", path, type(exc).__name__)
            continue
        texts[path] = text
        nodes.extend(extracted)
    return _dedup(nodes), texts, skipped


def harvest_nodes(adapter) -> list[SymbolNode]:
    """adapter.list_files() 를 순회하며 파일별 추출기로 노드를 모은다."""
    nodes, texts, skipped = _harvest_files(adapter)
    logger.info(
        "심볼 노드 수확: 파일 %d개, 노드 %d개, 건너뜀 %d개", len(texts), len(nodes), skipped
    )
    return nodes


# ── 엣지 연결 (휴리스틱) ───────────────────────────────────

def _strip_comments(path: str, text: str) -> str:
    """참조 탐색용 본문 — 주석만 제거한다(주석 처리된 코드로 엣지를 만들지 않기 위해).

    문자열 리터럴은 남긴다: MyBatis statement 참조가 그 안에 있다."""
    suffix = Path(path).suffix.lower()
    if suffix == ".java":
        return _mask_java(text, mask_strings=False)
    if suffix == ".xml":
        return _XML_COMMENT.sub(" ", text)
    if suffix == ".sql":
        return _SQL_COMMENT.sub(" ", text)
    return text


def _mentions(body: str, name: str) -> bool:
    """단어 경계 기준 언급 여부. 대소문자 무시 — 필드명(`taxMapper`)이 타입명
    (`TaxMapper`)과 대소문자만 다른 관례를 잡기 위해서다."""
    return re.search(rf"\b{re.escape(name)}\b", body, re.IGNORECASE) is not None


def _calls(body: str, name: str) -> bool:
    """`.name(` 형태의 호출이 있는지. (수신자는 따지지 않는다 — 휴리스틱)"""
    return re.search(rf"\.{re.escape(name)}\s*\(", body) is not None


def _file_owners(
    local: Sequence[SymbolNode], class_ids: frozenset[str]
) -> list[SymbolNode]:
    """파일 단위 관계의 src 로 쓸 노드.

    Java 는 최상위 클래스(중첩 클래스까지 src 로 두면 같은 엣지가 중복된다),
    클래스가 없는 XML·SQL 은 그 파일의 statement 노드다."""
    classes = [
        n
        for n in local
        if n.kind is SymbolKind.JAVA_CLASS and f"java:{n.container}" not in class_ids
    ]
    if classes:
        return classes
    return [n for n in local if n.kind in _STATEMENT_KINDS]


def _link_mappers(
    owners: Sequence[SymbolNode],
    path: str,
    body: str,
    mappers: Mapping[str, list[SymbolNode]],
) -> list[SymbolEdge]:
    """SERVICE_TO_MAPPER — namespace(또는 끝 클래스명).statementId 참조 / 매퍼 호출."""
    edges: list[SymbolEdge] = []
    for namespace, statements in mappers.items():
        simple = namespace.rsplit(".", 1)[-1]
        mapper_mentioned = _mentions(body, simple)
        for statement in statements:
            if statement.path == path:
                continue   # 매퍼 XML 자신은 호출자가 아니다
            sid = statement.name
            hit = (
                f"{namespace}.{sid}" in body
                or f"{simple}.{sid}" in body
                # 매퍼 인터페이스 호출: 타입/필드명이 보이고 statement 이름을 호출한다
                or (mapper_mentioned and _calls(body, sid))
            )
            if not hit:
                continue   # 못 찾으면 안 잇는다
            edges.extend(
                SymbolEdge(owner.id, statement.id, EdgeKind.SERVICE_TO_MAPPER)
                for owner in owners
            )
    return edges


def _link_tests(
    tests: Sequence[SymbolNode],
    path: str,
    body: str,
    classes_by_name: Mapping[str, list[SymbolNode]],
    methods_by_owner: Mapping[str, list[SymbolNode]],
    test_paths: frozenset[str],
) -> list[SymbolEdge]:
    """TEST_TO_SERVICE — test 파일이 참조하는 service 클래스/메서드."""
    if not tests:
        return []
    targets: list[str] = []
    for name, candidates in classes_by_name.items():
        if not re.search(rf"\b{re.escape(name)}\b", body):
            continue   # 클래스명은 대소문자를 구분해 본다 (오탐 억제)
        for klass in candidates:
            if klass.path == path or klass.path in test_paths:
                continue   # 자기 자신·다른 test 클래스는 service 가 아니다
            fqn = klass.id.removeprefix("java:")
            targets.append(klass.id)
            targets.extend(
                method.id
                for method in methods_by_owner.get(fqn, ())
                if _calls(body, method.name)
            )
    return [
        SymbolEdge(test.id, target, EdgeKind.TEST_TO_SERVICE)
        for test in tests
        for target in targets
    ]


def _link_constants(
    owners: Sequence[SymbolNode],
    body: str,
    constants: Sequence[SymbolNode],
    unique_names: frozenset[str],
    is_java: bool,
) -> list[SymbolEdge]:
    """USES_CONSTANT — `Class.CONST`(모든 파일) 또는 `CONST`(Java 파일, 이름이
    그래프 전체에서 유일할 때만). 이름이 겹치는 상수를 맨몸 이름으로 잇는 것은
    어느 클래스의 상수인지 알 수 없어 오탐이다."""
    edges: list[SymbolEdge] = []
    for const in constants:
        container = const.container or ""
        simple = container.rsplit(".", 1)[-1]
        qualified = (
            f"{container}.{const.name}" in body or f"{simple}.{const.name}" in body
        )
        bare = (
            is_java
            and const.name in unique_names
            and re.search(rf"\b{re.escape(const.name)}\b", body) is not None
        )
        if not (qualified or bare):
            continue
        declaring = f"java:{container}"
        edges.extend(
            SymbolEdge(owner.id, const.id, EdgeKind.USES_CONSTANT)
            for owner in owners
            if owner.id != declaring   # 선언 클래스는 CONTAINS 가 이미 담는다
        )
    return edges


def _dedup_edges(edges: list[SymbolEdge]) -> list[SymbolEdge]:
    """같은 (src, dst, kind) 는 한 번만. 순서는 보존."""
    seen: set[tuple[str, str, EdgeKind]] = set()
    unique: list[SymbolEdge] = []
    for edge in edges:
        key = (edge.src, edge.dst, edge.kind)
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique


def link_edges(
    nodes: Sequence[SymbolNode], files: Mapping[str, str]
) -> list[SymbolEdge]:
    """노드 사이의 관계를 휴리스틱으로 잇는다.

    `files` 는 `{경로: 본문}` — harvest 가 이미 읽은 본문을 재사용한다(재독 방지).
    모든 엣지는 **실재하는 노드 id 끼리만** 잇는다(dangling 금지): 대상 노드가
    없으면 엣지를 만들지 않는다."""
    class_ids = frozenset(n.id for n in nodes if n.kind is SymbolKind.JAVA_CLASS)

    mappers: dict[str, list[SymbolNode]] = defaultdict(list)
    classes_by_name: dict[str, list[SymbolNode]] = defaultdict(list)
    methods_by_owner: dict[str, list[SymbolNode]] = defaultdict(list)
    by_path: dict[str, list[SymbolNode]] = defaultdict(list)
    constants: list[SymbolNode] = []
    test_paths: set[str] = set()

    edges: list[SymbolEdge] = []
    for node in nodes:
        by_path[node.path].append(node)
        if node.kind is SymbolKind.JAVA_CLASS:
            classes_by_name[node.name].append(node)
        elif node.kind is SymbolKind.MYBATIS_STATEMENT and node.container:
            mappers[node.container].append(node)
        elif node.kind is SymbolKind.JAVA_METHOD and node.container:
            methods_by_owner[node.container].append(node)
        elif node.kind is SymbolKind.CONSTANT:
            constants.append(node)
        if node.kind is SymbolKind.TEST_METHOD:
            test_paths.add(node.path)
        # CONTAINS — container 가 실재하는 클래스 노드일 때만. 가장 확실한 엣지다.
        if node.kind in _MEMBER_KINDS and node.container:
            owner_id = f"java:{node.container}"
            if owner_id in class_ids:
                edges.append(SymbolEdge(owner_id, node.id, EdgeKind.CONTAINS))

    counts = Counter(const.name for const in constants)
    unique_names = frozenset(name for name, n in counts.items() if n == 1)
    frozen_test_paths = frozenset(test_paths)

    for path, text in files.items():
        local = by_path.get(path)
        if not local:
            continue   # 노드가 없는 파일은 관계의 주체가 될 수 없다
        body = _strip_comments(path, text)
        owners = _file_owners(local, class_ids)
        tests = [n for n in local if n.kind is SymbolKind.TEST_METHOD]
        edges.extend(_link_mappers(owners, path, body, mappers))
        edges.extend(
            _link_tests(
                tests, path, body, classes_by_name, methods_by_owner, frozen_test_paths
            )
        )
        edges.extend(
            _link_constants(
                owners,
                body,
                constants,
                unique_names,
                Path(path).suffix.lower() == ".java",
            )
        )
    return _dedup_edges(edges)


# ── 그래프 조립·캐시 ───────────────────────────────────────

_EMPTY_GRAPH = SymbolGraph(nodes=(), edges=(), skipped_files=0)


def harvest(adapter) -> SymbolGraph:
    """adapter 로 노드 추출(Step 0) → 엣지 연결 → SymbolGraph."""
    if adapter is None:
        return _EMPTY_GRAPH
    nodes, texts, skipped = _harvest_files(adapter)
    if not nodes:
        return SymbolGraph(nodes=(), edges=(), skipped_files=skipped)
    edges = link_edges(nodes, texts)
    logger.info(
        "심볼 그래프 수확: 파일 %d개, 노드 %d개, 엣지 %d개, 건너뜀 %d개",
        len(texts),
        len(nodes),
        len(edges),
        skipped,
    )
    return SymbolGraph(nodes=tuple(nodes), edges=tuple(edges), skipped_files=skipped)


def _to_dict(graph: SymbolGraph) -> dict:
    """캐시용 JSON 직렬화. 노드에 본문이 없으므로 캐시에도 코드 본문이 없다."""
    return {
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind.value,
                "name": n.name,
                "path": n.path,
                "container": n.container,
            }
            for n in graph.nodes
        ],
        "edges": [
            {"src": e.src, "dst": e.dst, "kind": e.kind.value} for e in graph.edges
        ],
        "skipped_files": graph.skipped_files,
    }


def _from_dict(payload: dict) -> SymbolGraph:
    return SymbolGraph(
        nodes=tuple(
            SymbolNode(
                id=n["id"],
                kind=SymbolKind(n["kind"]),
                name=n["name"],
                path=n["path"],
                container=n.get("container"),
            )
            for n in payload["nodes"]
        ),
        edges=tuple(
            SymbolEdge(src=e["src"], dst=e["dst"], kind=EdgeKind(e["kind"]))
            for e in payload["edges"]
        ),
        skipped_files=int(payload.get("skipped_files", 0)),
    )


def load(adapter, refresh: bool = False) -> SymbolGraph:
    """캐시가 있으면 로드, 없으면 수확 후 캐시 (`term_dict.load` 와 같은 규칙).

    adapter 가 없거나 노드가 0개면 빈 그래프이며 **캐시를 쓰지 않는다** —
    미설정 환경의 빈 결과가 캐시에 굳으면 되돌리기 어렵다."""
    if adapter is None:
        return _EMPTY_GRAPH
    if _CACHE.exists() and not refresh:
        try:
            return _from_dict(json.loads(_CACHE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
            pass   # 깨진 캐시는 조용히 재수확한다
    graph = harvest(adapter)
    if not graph.nodes:
        return graph
    try:
        _CACHE.write_text(
            json.dumps(_to_dict(graph), ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass
    return graph
