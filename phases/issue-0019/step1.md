# Step 1: symbol-graph

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 캐시는 gitignore·자동 재생성·커밋 금지, 코드 반출 금지)
- `/docs/architecture/ARCHITECTURE.md`, `/docs/architecture/ADR.md` (**ADR-013**)
- `/app/embedding/symbol_index.py` (Step 0 산출물 — `SymbolNode`, `SymbolKind`, `harvest_nodes`, 노드 id 규칙)
- `/app/embedding/term_dict.py` (**캐시 패턴 본보기** — `_CACHE` 경로, `load(refresh)`, json 직렬화, `OSError` 방어)
- `/app/embedding/const_inventory.py` (같은 캐시 패턴 + 값 매칭 참고)
- `/.gitignore` (기존 `*_cache.json` 엔트리 — 여기에 추가한다)

## 작업

Step 0 의 `symbol_index.py` 에 **엣지(관계)와 그래프 조립·캐시**를 더한다. 이 step 은 검색·provider 를 만들지 않는다(#0020).

### 1) 엣지 자료구조

```python
class EdgeKind(str, Enum):
    CONTAINS = "contains"          # class → method
    SERVICE_TO_MAPPER = "service_to_mapper"   # Java 호출 → MyBatis statement
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
```

### 2) 엣지 추출 (휴리스틱 — "일부 연결"이 목표)

```python
def link_edges(nodes: Sequence[SymbolNode], files: Mapping[str, str]) -> list[SymbolEdge]: ...
```

- **CONTAINS**: 메서드/상수 노드의 `container`(클래스 FQN)가 클래스 노드 id 와 맞으면 잇는다. 가장 확실한 엣지다.
- **SERVICE_TO_MAPPER**: Java 파일 본문에서 MyBatis namespace(또는 그 끝 클래스명)와 statement id 참조를 찾는다. 레거시 eHR 은 `namespace` 가 mapper 인터페이스 FQN 인 경우가 흔하므로, Java 코드에 `<namespace 끝 클래스명>.<statementId>` 또는 매퍼 메서드 호출 패턴이 있으면 해당 statement 노드로 잇는다. **못 찾으면 안 잇는다** — 억지로 만들지 마라(오탐이 관계 그래프를 오염시킨다).
- **TEST_TO_SERVICE**: test 메서드 본문/클래스에서 service 클래스명이나 메서드명을 참조하면 잇는다.
- **USES_CONSTANT**: 파일 본문에 상수명(`Class.CONST` 또는 `CONST`)이 등장하면 해당 constant 노드로 잇는다.
- 모든 매칭은 **id 로 실재하는 노드끼리만** 잇는다(dangling 엣지 금지). 대상 노드가 없으면 엣지를 만들지 않는다.

`files` 는 `{path: text}` 로, Step 0 의 harvest 가 이미 읽은 본문을 재사용한다(파일을 두 번 읽지 않게).

### 3) harvest·load·cache

```python
def harvest(adapter) -> SymbolGraph:
    """adapter 로 노드 추출(Step 0) → 엣지 연결 → SymbolGraph. skipped_files 포함."""


def load(adapter, refresh: bool = False) -> SymbolGraph: ...
```

- `load` 는 `term_dict.load` 와 같은 규칙: `symbol_index_cache.json`(프로젝트 루트, `_CACHE = Path(__file__).resolve().parents[2] / "symbol_index_cache.json"`)이 있고 `refresh=False` 면 로드, 없으면 harvest 후 캐시. `json.JSONDecodeError`·`OSError` 는 삼키고 재수확한다.
- 캐시는 `SymbolGraph` 를 JSON 직렬화/역직렬화한다(dataclass ↔ dict). **코드 본문은 캐시에 없다**(Step 0 에서 노드에 본문을 안 담았으므로 자동 충족 — 확인만).
- `adapter` 없거나 `list_files()` 가 비면 빈 그래프를 돌려준다(mock·미설정 대응, term_dict 의 `if not repo_root` 와 같은 취지).

### 4) gitignore

`.gitignore` 의 `*_cache.json` 블록에 `symbol_index_cache.json` 을 추가한다. 이유: eHR 내부 구조(클래스·매퍼·심볼 관계) 파생물이라 커밋되면 반출 사고다 — `term_dict_cache.json` 등과 같은 취급.

## 테스트

`tests/test_symbol_index.py` 에 추가한다:

- CONTAINS: 클래스와 그 메서드/상수가 이어지는지.
- SERVICE_TO_MAPPER: Java 가 매퍼 namespace.statementId 를 참조할 때 이어지고, **참조가 없으면 안 이어지는지**(오탐 방지).
- TEST_TO_SERVICE: test 가 service 를 참조할 때 이어지는지.
- USES_CONSTANT: 상수 사용처가 이어지는지.
- dangling 금지: 대상 노드가 없는 참조는 엣지를 만들지 않는지.
- `load` 캐시: 첫 호출은 harvest·캐시 쓰기, 둘째 호출은 캐시 로드(harvest 안 함 — 가짜 adapter 의 read 호출 수로 확인). `refresh=True` 면 재수확.
- 캐시 파일에 **코드 본문 문자열이 없는지**(저장된 json 을 문자열로 읽어 확인).
- 빈/None adapter → 빈 그래프.
- 캐시 테스트는 `_CACHE` 를 `tmp_path` 로 monkeypatch 해 **프로젝트 루트의 실제 캐시를 건드리지 마라.**

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
git check-ignore symbol_index_cache.json
```

## 검증 절차

1. 위 AC 를 실행한다. 두 번째 커맨드가 `symbol_index_cache.json` 을 출력해야 한다(gitignore 됨).
2. 체크리스트:
   - `symbol_index_cache.json` 이 gitignore 되고 `git status` 가 깨끗한가?
   - 엣지가 실재 노드끼리만 이어지는가(dangling 없음)?
   - 캐시에 코드 본문이 없는가?
   - 검색 배선·provider 를 만들지 않았는가(#0020)?
3. `phases/issue-0019/index.json` 의 step 1 갱신.

## 금지사항

- `symbol_index_cache.json` 을 gitignore 에 넣지 않거나 커밋하지 마라. 이유: eHR 내부 구조 파생물이라 반출 사고가 된다(CLAUDE.md).
- dangling 엣지(실재하지 않는 노드를 가리키는 엣지)를 만들지 마라. 이유: #0020 이 이 그래프로 이웃 확장을 하는데 허깨비 노드로 확장하면 잘못된 후보가 상위로 온다.
- 관계를 억지로 만들지 마라(못 찾으면 안 잇는다). 이유: 오탐 엣지가 진짜 관계보다 많아지면 그래프가 노이즈가 된다. "일부 연결"이 수용 기준이다.
- `CodeGraphProvider`·검색 orchestrator 배선·`RetrievalSource.CODE_GRAPH` 사용을 이 step 에서 하지 마라. 이유: #0020 의 범위다.
- 캐시에 코드 본문을 넣지 마라. 이유: 반출 위험.
- 캐시 테스트가 프로젝트 루트 `symbol_index_cache.json` 을 쓰게 두지 마라. `_CACHE` 를 `tmp_path` 로 monkeypatch 하라. 이유: 개발 환경 오염.
- 기존 테스트를 깨뜨리지 마라.
