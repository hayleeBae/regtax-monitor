# Code Graph Specification

- 문서 상태: **실물 정합 갱신 (2026-08-20, 이슈 #0020 설계 승인)** — 이 문서는 실제 구현
  `app/embedding/symbol_index.py`(#0019)를 기준으로 한다. 이전 버전이 기술하던
  CodeSymbol/CodeRelation·SQLite·neighbors API·commit snapshot은 **실제로 구현되지 않았고,
  도입하지 않는다**(ADR-017). 코드가 기준이다.
- 관련 Issue: `#0019`(그래프 구축, 완료), `#0020`(검색 연결)
- 버전: `code-graph-v1`

## 1. 목적

텍스트 검색이 찾은 계산 로직 주변의 Mapper·VO·테스트 등 함께 수정할 가능성이 높은
파일을 확장 검색한다. 그래프는 RAG를 대체하지 않으며, 이미 나온 후보를 seed로 넓히는
**후처리**다.

## 2. 범위 / 비범위

- 범위: Java class/method/constant, MyBatis statement, SQL statement 노드 + 휴리스틱 관계,
  그리고 이들을 검색에 연결(#0020).
- 비범위: 완전한 compiler analysis, reflection, runtime graph, **SQLite/Neo4j 저장**,
  **tree-sitter 등 신규 파서 도입**(회사망 SSL 제약 — regex+중괄호 매칭만).

## 3. 모델 (실물)

`SymbolKind`: JAVA_CLASS, JAVA_METHOD, MYBATIS_STATEMENT, TEST_METHOD, CONSTANT, SQL_STATEMENT.

`EdgeKind`: CONTAINS(class→member), SERVICE_TO_MAPPER(호출→statement),
TEST_TO_SERVICE(test→service), USES_CONSTANT.

```python
@dataclass(frozen=True)
class SymbolNode:
    id: str; kind: SymbolKind; name: str; path: str; container: str | None
@dataclass(frozen=True)
class SymbolEdge:
    src: str; dst: str; kind: EdgeKind          # confidence 없음 — kind가 점수를 정한다
@dataclass(frozen=True)
class SymbolGraph:
    nodes: tuple[SymbolNode, ...]; edges: tuple[SymbolEdge, ...]; skipped_files: int
```

- **노드에 코드 본문을 담지 않는다**(경로·이름·컨테이너만) — 캐시 반출 사고 방지.
- 엣지는 휴리스틱이며 "일부 연결"이 수용 기준이다. dangling(대상 노드 없는 엣지) 금지.

## 4. Symbol ID (실물)

읽기 쉬운 안정 식별자: `java:FQN`, `java:FQN#method`, `test:FQN#method`,
`const:FQN.NAME`, `mybatis:namespace.statementId`, `sql:path#verb:table`. sha256 미사용.
중복 id(오버로드·반복 statement)는 첫 번째만.

## 5. 저장 (실물)

인메모리 `SymbolGraph` + JSON 캐시 `symbol_index_cache.json`(gitignore, 자동 재생성,
본문 없음). **SQLite/commit-keyed snapshot 없음** — `term_dict`/`const_inventory`와 같은
캐시 규약. 빈 그래프는 캐시하지 않는다.

## 6. Build

```python
def harvest(adapter) -> SymbolGraph            # adapter.list_files()/read_file() 경유
def load(adapter, refresh=False) -> SymbolGraph  # 캐시 로드 or 수확
```

파일 하나의 파싱 실패는 warning 후 계속(파일 단위 try/except, `skipped_files`로 계측).
소스 접근은 `CodebaseAdapter`만 경유한다(`EXCLUDED_DIRS`·인코딩 처리 공유).

## 7. 관계 (휴리스틱)

CONTAINS(container가 실재 클래스일 때만), SERVICE_TO_MAPPER(namespace/매퍼호출 참조),
TEST_TO_SERVICE(test 파일이 참조하는 service 클래스/메서드), USES_CONSTANT(`Class.CONST`
또는 그래프 전역 유일명일 때 맨몸 `CONST`). 못 찾으면 안 잇는다(오탐 억제 우선).

## 8. 이웃 순회 (#0020 신설 — #0019엔 없음)

`SymbolGraph.edges`로 인접 리스트를 만들고 seed 노드에서 이웃을 넓힌다.

```python
def neighbors(graph, seed_ids, edge_allowlist, depth=1, max_neighbors=...) -> list[(SymbolNode, EdgeKind, path)]
```

기본 depth 1, 최대 depth 2. visited/cycle 관리. edge_allowlist·seed top-N·max_neighbor·
산출물(EXCLUDED_DIRS) 제외로 폭증 방지.

## 9. Retrieval 연결 (#0020 — 조정 설계, ADR-017)

**그래프는 독립 provider가 아니라 orchestrator의 "merge 이후 확장 단계"다.**

파이프라인: `providers → merge → **graph-expand** → rerank → truncate`.

- **`config.graph_enabled`(기본 False)로 가드** — off면 결과가 오늘과 **바이트 동일**(동작
  보존). 기존 rerank 단계(`if reranker and config.rerank_enabled`)와 동일 패턴.
- **seed = merge된 상위 N 후보 재사용** — RagProvider가 이미 계산한 후보를 seed로 삼는다.
  **RAG를 다시 돌리지 않는다**(이중 RAG 금지).
- seed 후보 → 그 경로/심볼을 `SymbolNode`에 매핑 → `neighbors()`로 관계 파일 확장 →
  확장 파일을 `RetrievalSource.CODE_GRAPH` evidence로 후보에 얹거나 신규 후보로 추가.
- EdgeKind별 고정 점수: service_to_mapper 0.75, uses_constant 0.70, test_to_service 0.65,
  contains 0.60. (confidence가 없어 kind가 점수를 정한다. 절대값은 9월 W4 캘리브레이션 대상.)

## 10. 자동화 게이트 — CODE_GRAPH는 독립 근거가 아니다 (D5 확정)

그래프 evidence는 seed에 **인과적으로 종속**되므로 automation 정책의
`min_independent_sources` 카운트에서 **제외**한다. 따라서:
- graph 단독 후보는 물론, RAG seed + graph 후보도 graph를 두 번째 독립 근거로 세지 않는다.
- 그래프는 **draft 허용을 단독으로도 borrowed seed로도 유발하지 못한다.** (스펙 원문
  "graph 단독 불가"를 인과 종속까지 넓혀 해석 — 안전 우선, 재현율>초안품질 원칙.)

## 11. 설명 가능성 / 오류

- evidence에 relation path와 설명(seed→edge→target)을 담는다.
- malformed 파일 skip, unresolved 관계 미생성, 그래프 로드 실패 시 확장 단계 skip(기존 결과
  유지). 확장이 비어도 오류 아님.

## 12. 평가 (ablation, §14 기존 유지)

`retrieval_benchmark`에서 graph on/off를 비교: 관련 파일 Recall@5, test file recall,
unnecessary file rate, candidate count, latency. **그래프가 불필요후보율을 높이면 그대로
보고**한다(그래프가 늘 이득은 아니다 — ablation이 depth/edge 확대를 결정). 현 ablation은
합성 mock 기반이며, 실 eHR 수치는 9월 W1(실 fixture) 이후 재측정한다.

## 13. 테스트

Java symbol/overload, MyBatis link, service call, test relation, malformed isolation,
cycle/depth 제한, **graph_enabled=off 시 결과 불변(회귀 고정)**, CODE_GRAPH가 독립 source로
안 세짐(게이트), 이중 RAG 미발생(seed 재사용).

## 14. 수용 기준 (AC)

- mock repo 심볼 그래프 로드, 이웃 순회(depth/allowlist/폭증방지)
- graph-expand 단계 on/off — off면 기존 결과 불변
- seed 재사용(RAG 재실행 없음) 검증
- CODE_GRAPH가 자동화 2-source를 단독/borrowed 로 충족하지 않음
- ablation report(recall·불필요후보·latency)
- `bash scripts/verify.sh full` 통과

## 14-1. 실측 검증 (2026-08-20, 맥 M3 — 건수만, 경로·코드 미기재)

실 eHR 인덱스 + 실 25 법령개정(regtax.db)에 graph on/off 적용:
- 그래프 구축: 노드 4,969 / 엣지 4,150 (정상).
- **graph on/off 차이: 0/25** (CODE_GRAPH evidence 0건, top5 변화 0건, 추가 후보 0). 느슨한 설정
  (seed_top_n 8·depth 2·엣지 4종·max 50)으로도 **0/25** — 설정이 아니라 근본.
- mock ablation과 일치(recall 이득 없음, 불필요후보만 증가).

**근본 원인:** `symbol_index._EXTRACTORS = java/xml/sql`만이라 **xfdl 미파싱**. eHR 세법 로직·한도값이
xfdl에 있어(§eHR 인덱싱 실측) 상위 후보(xfdl)에 그래프 노드가 없다 → 확장 불가. java/xml seed도
cross-file 이웃이 거의 없다(CONTAINS=같은 파일).

**판정:** `graph_enabled=False` 기본 유지가 옳다.

**후속 실험 결론 (2026-08-20, ADR-018):** xfdl 심볼 + 파일 간 특이수치 공동출현 엣지(SHARES_VALUE)를
추가해 그래프를 3배(노드 15,796·엣지 14,568)로 키워 재측정했으나 **여전히 0/25**. 엣지 94%가
same-file CONTAINS, **SERVICE_TO_MAPPER=0** — eHR이 서비스 호출을 **SvcID 런타임 간접참조**로 해석해
콜그래프가 코드 텍스트에 없다(xfdl뿐 아니라 java→mapper도 동일). **그래프 검색은 이 코드베이스의
레버가 아니라고 확정하고, 실험 코드는 채택하지 않는다.** `graph_enabled=False` 영구 유지.

## 15. Claude Code 요청문

SQLite/Neo4j/신규 파서 도입 금지. parser 실패 한 파일이 전체를 멈추지 않게. graph-expand는
merge된 seed를 재사용(이중 RAG 금지)하고, off면 동작 불변. graph는 draft를 단독/종속으로도
허용하지 않는다. ablation에서 recall과 불필요후보를 함께 보고한다.
