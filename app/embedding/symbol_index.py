"""
코드 심볼 인덱스 — 노드(심볼) 추출 (Issue #0019 / ADR-013).

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

이 모듈은 노드까지만 만든다. 엣지(관계)·캐시는 Step 1, provider·검색 배선은 #0020.
"""
from __future__ import annotations

import logging
import re
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


def _mask_java(text: str) -> str:
    """주석·문자열 리터럴을 같은 길이의 공백으로 치환한다.

    주석 안의 `{`/`//` 나 문자열 안의 중괄호가 경계 추적을 깨뜨리는 것을 막는다.
    길이를 보존하므로 매치 위치를 원본과 그대로 견줄 수 있다."""
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
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
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
