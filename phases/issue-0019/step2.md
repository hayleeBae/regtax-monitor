# Step 2: symbol-fixture

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 실제 eHR 코드 반출 금지)
- `/docs/architecture/ADR.md` (**ADR-013** — 수용 기준 "Service↔Mapper↔Test 일부 연결")
- `/docs/roadmap/IMPLEMENTATION_ROADMAP.md` Issue #0019 절 (수용 기준 원문)
- `/app/embedding/symbol_index.py` (Step 0·1 — `harvest`, `SymbolGraph`, 노드/엣지 종류와 id 규칙)
- `/app/codebase/mock_adapter.py` (`MockCodebaseAdapter(repo_root, indexer=None)` — 이 step 이 fixture 를 이걸로 읽는다)
- `/app/codebase/real_adapter.py` (`EXCLUDED_DIRS` — fixture 에 빌드 디렉토리를 넣어 제외 검증)
- `/evaluation/fixtures/repositories/mock_tax/` (기존 mock 코드 fixture 스타일 참고)

## 배경 — 왜 fixture 가 필요한가

`mock_repo/` 에는 **MyBatis 매퍼도, Java 테스트도, Service→Mapper 구조도 없다**(Java 2개·python 테스트뿐). 그래서 수용 기준 "Service↔Mapper↔Test 일부 연결"을 기존 mock 으로는 증명할 수 없다. 이 step 은 그 관계를 갖춘 **합성 fixture** 를 만들고, `MockCodebaseAdapter` 로 읽어 end-to-end 로 증명한다(#0017 이 replay fixture 를 합성으로 만든 것과 같은 방식).

## 작업

### 1) 합성 fixture (`evaluation/fixtures/symbols/`)

Service↔Mapper↔Test 삼각형과 상수 사용을 갖춘 최소 합성 트리를 만든다. **실제 eHR 코드를 베끼지 마라 — `com.example.*` 합성 코드다.**

최소 구성:
- `src/main/java/com/example/tax/TaxService.java` — `TaxMapper` 를 호출하고 `TaxConstants.CHILD_CREDIT` 를 참조하는 Service 클래스+메서드
- `src/main/java/com/example/tax/TaxMapper.java` — 매퍼 인터페이스(namespace 대응)
- `src/main/resources/mapper/TaxMapper.xml` — `<mapper namespace="com.example.tax.TaxMapper">` + `<select id="findCredit">` 등 statement 2개
- `src/main/java/com/example/tax/TaxConstants.java` — `static final` 상수 1~2개
- `src/test/java/com/example/tax/TaxServiceTest.java` — `@Test` 메서드가 `TaxService` 를 참조
- `src/main/webapp/ui/TaxScreen.xml` — **namespace 없는** UI 레이아웃 XML(=MyBatis 아님, statement 0개로 걸러져야 함)
- `build/generated/Junk.java` — **빌드 디렉토리** 안의 파일(adapter EXCLUDED_DIRS 로 제외되어야 함)
- `src/main/java/com/example/tax/Broken.java` — 중괄호 불균형 등 **일부러 깨진 파일**(파싱 실패로 건너뛰어야 함, 전체는 안 죽음)

fixture 파일은 커밋 대상이다(합성 코드라 반출 위험 없음).

### 2) end-to-end 테스트 (`tests/test_symbol_fixture.py` 신규)

`MockCodebaseAdapter(repo_root=<fixture 경로>)` 로 `symbol_index.harvest(adapter)` 를 실행해 검증한다:

- **Service↔Mapper↔Test 연결(수용 기준)**: `TaxServiceTest` test 메서드 → `TaxService`(TEST_TO_SERVICE), `TaxService` → `TaxMapper.xml` 의 statement(SERVICE_TO_MAPPER), `TaxConstants.CHILD_CREDIT` 사용(USES_CONSTANT)이 그래프에 있는지.
- **CONTAINS**: 각 클래스와 그 메서드가 이어지는지.
- **MyBatis 판별**: `TaxMapper.xml` 의 statement 는 노드가 되고, **namespace 없는 `TaxScreen.xml` 은 statement 노드 0개**인지.
- **빌드 제외**: `build/generated/Junk.java` 의 심볼이 그래프에 **없는지**(adapter EXCLUDED_DIRS 가 제외 — ADR-013 핵심).
- **실패 격리(수용 기준)**: `Broken.java` 가 있어도 harvest 가 예외 없이 끝나고 `skipped_files >= 1` 이며 나머지 노드는 정상인지.
- **코드 본문 부재**: 그래프 노드에 코드 스니펫이 없는지.

이 테스트는 실제 파일을 읽지만 **임베딩·ChromaDB·LLM·DB 는 트리거하지 않는다**(MockCodebaseAdapter 의 `list_files`/`read_file` 만 쓴다 — indexer 를 주입하지 마라).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
source .venv/bin/activate && python3 -c "
from app.codebase.mock_adapter import MockCodebaseAdapter
from app.embedding.symbol_index import harvest
g = harvest(MockCodebaseAdapter(repo_root='evaluation/fixtures/symbols'))
print('nodes', len(g.nodes), 'edges', len(g.edges), 'skipped', g.skipped_files)
print('edge kinds', sorted({e.kind.value for e in g.edges}))
"
```

## 검증 절차

1. 위 AC 를 실행한다. 두 번째 커맨드에 `service_to_mapper`·`test_to_service`·`contains`·`uses_constant` 가 edge kinds 에 나오고 `skipped >= 1` 이어야 한다.
2. 체크리스트:
   - fixture 가 `com.example.*` 합성 코드인가(실제 eHR 아님)?
   - `build/` 하위 심볼이 그래프에서 제외됐는가(adapter EXCLUDED_DIRS)?
   - namespace 없는 XML 이 statement 로 안 잡히는가?
   - 깨진 파일이 전체 harvest 를 중단시키지 않는가(`skipped_files >= 1`)?
3. `phases/issue-0019/index.json` 의 step 2 갱신.

## 금지사항

- 실제 eHR 코드를 fixture 에 베껴 넣지 마라. 이유: 외부 반출 금지 대상이다. `com.example.*` 합성 코드를 쓴다.
- 수용 기준을 통과시키려고 Step 0·1 의 추출·연결 로직을 느슨하게 고치지 마라. 이유: fixture 가 로직에 맞춰야지 그 반대가 아니다. 로직이 fixture 를 못 읽으면 로직의 결함이다.
- `symbol_index.py` 에 검색 배선·provider 를 추가하지 마라. 이유: #0020 의 범위다.
- 테스트에서 indexer(임베딩)를 주입하거나 ChromaDB·LLM 을 트리거하지 마라. 이유: CLAUDE.md 규칙. 심볼 추출은 임베딩과 무관하다.
- fixture 를 `mock_repo/` 안에 넣지 마라. 이유: mock_repo 는 기존 골든·검색 경로가 쓰는 트리다. 별도 `evaluation/fixtures/symbols/` 에 둔다.
- 기존 테스트를 깨뜨리지 마라.
