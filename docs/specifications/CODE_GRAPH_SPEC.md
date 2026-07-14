# Code Graph Specification

- 문서 상태: Later Phase Implementation Ready
- 관련 Issue: `#0019`, `#0020`
- 버전: `code-graph-v1`

## 1. 목적

텍스트 검색이 찾은 계산 로직 주변의 Mapper, VO, 테스트 등 함께 수정할 가능성이 높은 파일을 확장 검색한다. 그래프는 RAG를 대체하지 않는다.

## 2. 초기 범위

- Java class/method/call/constant usage
- MyBatis namespace와 statement id
- mapper interface method ↔ XML statement
- 테스트 class/method ↔ production method
- SQL alias와 단순 VO property

비범위: 완전한 compiler analysis, reflection, runtime graph, 모든 framework, Neo4j 필수 도입.

## 3. 모델

`SymbolKind`: JAVA_CLASS, JAVA_METHOD, JAVA_FIELD, MAPPER_NAMESPACE, MAPPER_STATEMENT, SQL_COLUMN, TEST_CLASS, TEST_METHOD.

`RelationType`: CONTAINS, CALLS, IMPLEMENTS, MAPS_TO, READS_FIELD, WRITES_FIELD, TESTED_BY, USES_CONSTANT.

```python
@dataclass(frozen=True)
class CodeSymbol:
    symbol_id: str
    kind: SymbolKind
    qualified_name: str
    path: str
    line_start: int
    line_end: int
    content_hash: str
    parser_version: str

@dataclass(frozen=True)
class CodeRelation:
    source_id: str
    target_id: str
    relation_type: RelationType
    confidence: float
    evidence: str
    extractor_version: str
```

## 4. Symbol ID

`sha256(repository_id + path + kind + qualified_name)`. line은 ID에 넣지 않는다. overload는 signature 포함.

## 5. 저장

초기 SQLite: code_index_snapshots, code_symbols, code_relations. snapshot key는 commit + parser version + repository id.

## 6. Parser

Java는 tree-sitter 등 경량 parser 우선, dependency 추가는 ADR. regex fallback은 package/class/method/call/constant 최소 정보만 추출하고 confidence를 낮춘다.

MyBatis는 XML parser로 namespace, statement, resultMap, type, alias, include를 추출한다.

## 7. 관계

- Java mapper interface qualified name = XML namespace
- method name = statement id
- service call과 mapper method
- test call과 service method
- constant 위치와 enclosing symbol

모호하면 relation confidence를 낮춘다.

## 8. Snapshot Build

```python
class CodeGraphIndexer:
    def build(self, codebase: CodebaseAdapter, repository_commit: str) -> CodeGraphSnapshot: ...
```

commit 변경 시 새 snapshot. unchanged file hash 재사용은 optional.

## 9. Query

```python
class CodeGraph:
    def neighbors(self, symbol_id: str, relation_types: set[RelationType], depth: int = 1, limit: int = 20): ...
```

기본 depth 1, depth 2 제한. cycle/visited 관리.

## 10. Retrieval 연결

seed candidate에서 관계 파일 확장:

```text
TaxService.calculateChildTaxCredit
→ CALLS TaxMapper.selectChildren
→ MAPS_TO TaxMapper.xml#selectChildren
→ TESTED_BY TaxServiceTest.childCredit
```

예시 score: maps_to 0.75, uses_constant 0.70, tested_by 0.65, calls 0.60, regex relation 최대 0.40.

Graph 단독 evidence는 automation multi-source 조건을 충족하지 않는다.

## 11. 폭증 방지

seed top N, relation allowlist, depth, module filter, max neighbor, visited, generated/vendor 제외.

## 12. 설명 가능성

evidence에 relation path와 설명을 포함한다.

## 13. 오류

파일별 parser 실패는 warning 후 계속, malformed XML skip, duplicate symbol disambiguate, unresolved call 미생성, commit mismatch 시 provider skip, repo size/time limit.

## 14. 평가

`graph_hybrid`와 기준선에서 관련 파일 Recall@5, test file recall, unnecessary file rate, candidate count, latency를 비교한다.

## 15. 테스트

Java symbol/overload, MyBatis link, service call, test relation, malformed isolation, cycle/depth, graph evidence, commit mismatch.

## 16. 수용 기준

- mock repo symbol index
- SQLite 관계 저장
- graph provider on/off
- graph 단독 draft 허용 금지
- ablation report

## 17. Claude Code 요청문

```text
Issue #0019와 #0020은 앞선 평가·검색·감사 기능 완료 후 구현하라.
SQLite를 사용하고 Neo4j를 도입하지 않는다.
parser 실패 파일 하나가 전체 index를 중단하지 않게 한다.
GraphProvider는 seed candidate를 확장하며 graph 단독으로 DRAFT_ALLOWED를 충족하지 않는다.
graph_hybrid 실험에서 recall과 불필요 후보를 함께 보고한다.
```
