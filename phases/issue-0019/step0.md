# Step 0: symbol-extract

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 코드 반출 금지, 캐시는 gitignore·자동 재생성, seam 규칙)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙, `app/embedding/` 위치)
- `/docs/architecture/ADR.md` (**ADR-013** — 이 작업의 근거)
- `/docs/roadmap/IMPLEMENTATION_ROADMAP.md` Issue #0019 절 (우선 지원 심볼·수용 기준)
- `/app/embedding/indexer.py` (**`_chunk_java` 의 중괄호 균형 매칭** — 이 로직을 재사용한다. `_chunk_sql`·`_chunk_xml` 도 참고)
- `/app/embedding/term_dict.py` (**본보기** — `harvest`/`load`/`_iter_source_files` 패턴, `_cache.json` 처리. 단 term_dict 는 파일을 직접 읽고, 이 모듈은 adapter 를 경유한다)
- `/app/codebase/base.py` (`CodebaseAdapter.list_files()`/`read_file()` — 소스 접근 seam)

`_chunk_java` 를 특히 꼼꼼히 읽어라. 메서드 경계를 찾는 중괄호 깊이 추적을 이 step 에서 심볼 경계 찾기로 재사용한다.

## 작업

`app/embedding/symbol_index.py` 를 신규 생성한다. 이 step 은 **노드(심볼) 추출까지만** 한다 — 엣지(관계)와 캐시는 Step 1, fixture 는 Step 2 다.

### 1) 노드 자료구조

```python
class SymbolKind(str, Enum):
    JAVA_CLASS = "java_class"
    JAVA_METHOD = "java_method"
    MYBATIS_STATEMENT = "mybatis_statement"   # select/insert/update/delete
    TEST_METHOD = "test_method"
    CONSTANT = "constant"


@dataclass(frozen=True)
class SymbolNode:
    id: str            # 안정적 식별자 (아래 규칙)
    kind: SymbolKind
    name: str          # 사람이 읽는 이름 (메서드명·statement id 등)
    path: str          # 심볼이 있는 파일 (adapter 상대 경로)
    container: str | None = None   # 소속 (클래스 FQN·mapper namespace 등)
```

**노드 id 규칙** (Step 1 의 엣지가 이 id 로 노드를 잇는다 — 안정적이어야 한다):
- Java 클래스: `java:<pkg>.<Class>` (package 없으면 `java:<Class>`)
- Java 메서드: `java:<pkg>.<Class>#<method>`
- MyBatis statement: `mybatis:<namespace>.<statementId>`
- test 메서드: `test:<pkg>.<Class>#<method>`
- constant: `const:<pkg>.<Class>.<CONST_NAME>`

### 2) 언어별 추출기 (정규식·휴리스틱)

**신규 라이브러리를 쓰지 마라.** 진짜 파서(javalang·tree-sitter 등) 금지 — 로드맵 비범위이며 회사망 SSL 에서 설치가 막힌다. 표준 라이브러리 `re` 와 `_chunk_java` 식 중괄호 매칭만 쓴다.

```python
def extract_java(path: str, text: str) -> list[SymbolNode]: ...
def extract_mybatis(path: str, text: str) -> list[SymbolNode]: ...
def extract_sql(path: str, text: str) -> list[SymbolNode]: ...
```

- **Java**: `package`·`class`/`interface`·메서드 시그니처·`static final` 상수(대문자 SNAKE_CASE)를 뽑는다. 메서드는 `_chunk_java` 처럼 중괄호로 본문 경계를 잡되, **본문 텍스트는 노드에 저장하지 마라**(경로·이름·컨테이너만 — 코드 본문은 캐시로 새면 반출 위험이다). test 여부는 파일 경로에 `test`(대소문자 무시)가 포함되거나 `@Test` 어노테이션이 있으면 `TEST_METHOD`, 아니면 `JAVA_METHOD`.
- **MyBatis XML**: `<mapper namespace="...">` 가 있는 XML 만 MyBatis 로 취급한다. `<select|insert|update|delete id="...">` 의 id 를 statement 노드로. **namespace 가 없는 XML(레이아웃·설정)은 건너뛴다** — 이유: eHR 에는 UI·설정 XML 이 섞여 있어 전부 statement 로 보면 오탐이 폭발한다.
- **SQL**: `_chunk_sql` 로 문장을 나누되, 노드로는 테이블·주요 문장 종류 정도만(최소). SQL 은 우선순위가 낮으니 과投資하지 마라.

### 3) harvest 진입점 (노드만)

```python
def harvest_nodes(adapter) -> list[SymbolNode]:
    """adapter.list_files() 를 순회하며 파일별 추출기로 노드를 모은다."""
```

- **소스 접근은 `adapter.list_files()` / `adapter.read_file(path)` 로만** 한다(CLAUDE.md seam, ADR-013). 직접 `Path.rglob` 로 파일시스템을 훑지 마라 — adapter 가 `EXCLUDED_DIRS`(빌드 산출물)와 CP949 를 처리한다.
- 확장자로 추출기를 고른다(`.java` → java, `.xml` → mybatis 시도, `.sql` → sql). 그 외 확장자는 건너뛴다.
- **파일 단위 try/except** — 한 파일 파싱이 예외를 내도 그 파일만 건너뛰고 나머지를 계속한다(수용 기준). 건너뛴 개수를 반환하거나 로깅한다. 예외로 전체 harvest 를 중단시키지 마라.
- 로그·예외에 파일 **본문**이나 절대경로를 넣지 마라(반출 위험).

## 테스트

`tests/test_symbol_index.py` 신규. 가짜 adapter(리스트+dict 로 `list_files`/`read_file` 구현)에 **inline 문자열**을 담아 검증한다 — 실제 파일·임베딩·DB 없이.

- Java: 클래스·메서드·상수 노드가 각 id 규칙대로 나오는지. 중첩 중괄호(제네릭·람다) 안에서 메서드 경계가 깨지지 않는지.
- test 파일(경로에 `test`/`@Test`)의 메서드가 `TEST_METHOD` 로 분류되는지.
- MyBatis: namespace 있는 XML 의 select/insert/update/delete id 가 statement 노드로, **namespace 없는 XML 은 노드 0개**인지.
- 파일 단위 격리: 깨진 Java(예: 중괄호 불균형) 한 파일이 있어도 예외 없이 나머지 파일 노드가 나오는지.
- 노드에 **코드 본문이 저장되지 않는지**(SymbolNode 필드에 스니펫 없음).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
grep -n "^import\|^from" app/embedding/symbol_index.py
```

## 검증 절차

1. 위 AC 를 실행한다. 두 번째 출력에 신규 서드파티 파서(javalang·tree_sitter·lxml 등)가 없어야 한다(표준 라이브러리 + app.* 만).
2. 체크리스트:
   - 소스 접근이 adapter 경유인가(`Path.rglob`·`open()` 직접 순회 없음)?
   - 파일 단위 try/except 로 실패가 격리되는가?
   - 노드에 코드 본문이 없는가?
   - 신규 의존성이 없는가?
3. `phases/issue-0019/index.json` 의 step 0 갱신 (성공 → completed + summary / 3회 실패 → error / 개입 필요 → blocked).

## 금지사항

- 서드파티 파서·신규 의존성을 추가하지 마라. 이유: 로드맵 비범위이고 회사망 SSL 에서 설치가 막힌다. `re` + 중괄호 매칭으로 충분하다(수용 기준=일부 연결).
- `Path.rglob`·`os.walk`·`open()` 으로 소스를 직접 순회하지 마라. 이유: adapter 의 `EXCLUDED_DIRS`(빌드 산출물)·CP949 를 우회하면 exploded WAR 심볼이 인덱스를 오염시킨다(ADR-013, 2026-08-05 실측).
- 노드에 코드 본문·스니펫을 저장하지 마라. 이유: 캐시가 커밋·반출되면 eHR 코드가 나간다.
- 엣지(관계)·캐시·gitignore·provider 를 이 step 에서 만들지 마라. 이유: Step 1·2 의 범위다.
- namespace 없는 XML 을 MyBatis statement 로 추출하지 마라. 이유: eHR 의 UI·설정 XML 이 오탐으로 쏟아진다.
- `app/embedding/term_dict.py`·`const_inventory.py` 를 수정하지 마라. 이유: 이번 범위가 아니며 기존 수확 동작을 보존한다.
- 기존 테스트를 깨뜨리지 마라.
