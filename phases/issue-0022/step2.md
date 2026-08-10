# Step 2: replay-cli

## 읽어야 할 파일

- `/CLAUDE.md` (CRITICAL — **코드는 외부로 나가지 않는다**, 설정은 config 경유)
- `/docs/architecture/ARCHITECTURE.md`, `/docs/architecture/ADR.md` (**ADR-011·ADR-012**)
- `/app/evaluation/replay/runner.py` (`main()` — 현재 `--stub`만 받는다. 이 step이 확장할 지점)
- `/app/evaluation/replay/real_pipeline.py` (Step 1 — `build_real_pipeline`)
- `/app/evaluation/replay/index_cache.py` (Step 0)
- `/app/evaluation/replay/stub_pipeline.py` (지연 import 패턴 본보기)
- `/.gitignore`
- `/docs/operations/COMPANY_VALIDATION.md` (**§4-2** — 회사 실행 절차. 이 step이 갱신한다)
- `/config.py` (`settings.llm_backend`)

## 작업

### 1) CLI 확장 (`runner.py`의 `main()`)

`--pipeline {stub,real}` 옵션을 추가한다(기본값 없음 — 미지정 시 현재처럼 exit 2).

- `--pipeline stub`이면 기존 `--stub {perfect,partial,empty}`와 조합해 동작한다. 기존 사용법(`--stub perfect`)이 **그대로 계속 동작해야 한다** — 회귀 금지.
- `--pipeline real`이면 `build_real_pipeline(...)`을 쓴다.
- **무거운 모듈은 해당 분기 안에서만 import 한다.** `real_pipeline`을 파일 상단에서 import하면 ADR-011의 "runner는 임베딩·LLM을 import하지 않는다"가 깨진다. `stub_pipeline`이 이미 `main()` 안에서 지연 import되고 있으니 같은 방식을 따른다.
- `--index-root` 옵션(선택)으로 인덱스 캐시 루트를 바꿀 수 있게 한다. 기본은 Step 0의 `evaluation/replay_index`.

`runner.py`의 나머지(`run_case`·`run_fixtures`·실패 격리·privacy 선택)는 **수정하지 마라.**

### 2) 외부 전송 경고 — 이 step의 안전 항목

`propose_and_build`가 쓰는 LLM은 `LLM_BACKEND` 설정을 따른다. `claude`면 **대상 코드 스니펫이 Anthropic API로 나간다.** 기존 `apply` 경로와 같은 동작이지만, replay는 여러 케이스를 자동으로 연속 실행하므로 실수로 대량 전송될 여지가 크다.

`--pipeline real`이고 `settings.llm_backend != "local"`이면:

- 시작 전에 **명확한 경고를 stderr에 출력**한다(백엔드 이름, 케이스 수, "대상 코드 스니펫이 외부로 전송된다"는 사실).
- `--allow-external-llm` 플래그가 없으면 **실행하지 않고 exit 2**로 끝낸다.
- 이유: CLAUDE.md의 "코드는 외부로 나가지 않는다"는 CRITICAL 규칙이며, 회사 환경에서 무심코 돌렸을 때 되돌릴 수 없다. 명시적 opt-in을 요구하는 것이 이 규칙을 지키는 방법이다.

`LLM_BACKEND=local`(기본)이면 경고도 플래그도 필요 없다.

### 3) gitignore

`.gitignore`에 `evaluation/replay_index/`를 추가한다. 인덱스에는 대상 코드의 임베딩이 담기므로 커밋되면 안 된다.

### 4) 회사 런북 갱신 (`docs/operations/COMPANY_VALIDATION.md` §4-2)

현재 §4-2는 "배관이 확인되면 실제 파이프라인을 주입해 돌린다(주입 방법은 `runner.py`의 `ReplayPipeline` 시그니처 참조)"라고만 적혀 있다 — **실행 가능한 명령이 없다.** 실제 명령으로 교체한다:

- `--pipeline stub --stub perfect`로 배관·fixture 일관성 확인
- `--pipeline real`로 실제 실행
- 첫 실행은 인덱싱 때문에 수십 분 걸리고, 같은 base commit이면 두 번째부터 캐시가 적중한다는 점
- `LLM_BACKEND=claude`면 `--allow-external-llm`이 필요하며 **회사에서는 local 권장**이라는 점
- 기록할 항목에 인덱싱 소요시간·캐시 적중 여부 추가

## 테스트

`tests/test_replay_cli.py`(또는 기존 `tests/test_replay_runner.py`에 추가):

- `--stub perfect`만 준 기존 사용법이 계속 동작하는지(**회귀 고정**).
- `--pipeline` 미지정 시 exit 2.
- `--pipeline real` + `llm_backend="claude"` + 플래그 없음 → **exit 2이고 실제 파이프라인이 만들어지지 않는지**(monkeypatch로 `settings.llm_backend` 조작, `build_real_pipeline`이 호출되지 않음을 확인).
- 같은 조건 + `--allow-external-llm` → 진행하는지.
- `llm_backend="local"`이면 플래그 없이 진행하는지.
- `--pipeline real` 분기를 타지 않으면 `real_pipeline` 모듈이 import되지 않는지(지연 import 확인 — `sys.modules` 검사).
- `--index-root`가 전달되는지.

실제 임베딩·LLM을 트리거하지 마라 — `build_real_pipeline`을 monkeypatch로 가짜로 바꾼다.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
source .venv/bin/activate && python3 -m app.evaluation.replay.runner --fixtures evaluation/fixtures/replay/mock_cases.yaml --output-dir evaluation/results/replay-cli-smoke --pipeline stub --stub perfect && ls evaluation/results/replay-cli-smoke
```

```bash
git status --short && git worktree list
```

## 검증 절차

1. 위 AC를 순서대로 실행한다. 세 번째에서 작업 트리 변경·worktree 누수가 없어야 한다.
2. 체크리스트:
   - `runner.py` 상단에 `real_pipeline`·임베딩·LLM import가 없는가(지연 import 유지)?
   - 기존 `--stub` 사용법이 깨지지 않았는가?
   - `LLM_BACKEND=claude` + 플래그 없음이 실행을 막는가?
   - `.gitignore`에 `evaluation/replay_index/`가 있고 `git status`가 깨끗한가?
   - 런북 §4-2에 **실행 가능한 명령**이 들어갔는가?
3. `phases/issue-0022/index.json`의 step 2 갱신.

## 금지사항

- `real_pipeline`을 `runner.py` 상단에서 import 하지 마라. 이유: ADR-011이 정한 "runner는 임베딩·LLM을 import하지 않는다"가 깨지고 집 환경 테스트가 무거워진다.
- 기존 `--stub` 사용법을 바꾸지 마라. 이유: `#0018` 테스트와 런북이 그 형태를 쓴다.
- `LLM_BACKEND`가 local이 아닐 때 경고 없이 실행하지 마라. 이유: 대상 코드 스니펫이 외부로 나가며, CLAUDE.md CRITICAL 규칙 위반이 자동 반복된다.
- `--allow-external-llm`을 기본 활성으로 두지 마라. 이유: opt-in이어야 의미가 있다.
- `run_case`·`run_fixtures`·실패 격리·privacy 선택 로직을 수정하지 마라. 이유: `#0018`에서 검증된 동작이며 이번 범위가 아니다.
- 인덱스 캐시 디렉토리를 커밋하지 마라. 이유: 대상 코드의 임베딩이 담긴 반출 금지 자산이다.
- 기존 테스트를 깨뜨리지 마라.
