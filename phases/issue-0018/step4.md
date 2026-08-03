# Step 4: golden-exec

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 사람 승인 게이트, 실제 repo 무변경)
- `/docs/architecture/ARCHITECTURE.md`, `/docs/architecture/ADR.md` (**ADR-011** — `app/golden.py`를 재사용하지 않는 이유가 여기 있다. ADR-006도 함께)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§4-8 골든 테스트, §5 timeout, §9 golden timeout 구분**)
- `/app/golden.py` (**기존 구현 — `shell=True`를 쓴다. 이 step은 이것을 재사용하지 않고 별도 경로를 만든다**)
- `/app/evaluation/replay/loader.py` (#0017 — `GOLDEN_COMMAND_ALLOWLIST`, `_check_golden_command`. 실행파일 검사가 이미 있다)
- `/app/evaluation/replay/fixture.py` (`ReplayExecution.golden_command`, `timeout_seconds`)
- `/docs/security/scan-20260802-issue-0017.md` (**발견 #1** — 이 step이 해결해야 할 이월 항목)

`app/golden.py`와 `app/evaluation/replay/loader.py`의 `_check_golden_command`를 모두 읽어라. 전자는 왜 `shell=True`여도 되는지, 후자가 왜 실행파일만 검사하는지 이해한 뒤 작업하라.

## 작업

`app/evaluation/replay/golden_exec.py`를 신규 생성한다. fixture의 `golden_command`를 **임시 worktree 안에서** 실행한다.

### 배경 — 왜 `app/golden.py`를 쓰지 않는가

`app/golden.py::run_golden_tests`는 `subprocess.run(cmd, shell=True, ...)`로 명령을 실행한다. 그 함수가 받는 `cmd`는 `config.golden_test_cmd`, 즉 **운영자가 `.env`에 직접 넣는 값**이라 `shell=True`가 타당하다. 반면 replay의 `golden_command`는 **fixture YAML에서 오고, fixture 파일은 주고받을 수 있다** — 신뢰 수준이 다르다(ADR-011). 그래서 replay는 shell 없이 인자 배열로 실행하는 별도 경로를 갖는다.

### 1) 인자 검증 (#0017 secscan 발견 #1 해결)

`app/evaluation/replay/loader.py`의 `_check_golden_command`는 **첫 토큰(실행파일)만** allowlist와 대조한다. 그래서 `mvn -f /other/pom.xml`, `pytest /other/dir`, `pytest -p <plugin>` 처럼 **인자로 실행 대상을 재지정**하는 값이 통과한다. 이 step에서 인자 수준 검증을 추가한다.

```python
def validate_golden_args(tokens: Sequence[str]) -> None:
    """실행 직전 인자 검증 — 위반이면 예외."""
```

거부 규칙:

- **절대경로 인자**: `/`로 시작하는 토큰(옵션의 값으로 붙은 `--opt=/abs` 형태 포함).
- **상위 탈출**: `..` 구성요소를 포함하는 경로 토큰.
- **대상 재지정 옵션**: `-f`, `--file`, `-p`, `--plugin`, `-C`, `--project-dir`, `--rootdir`, `-b`, `--build-file`, `--settings`, `-s`. 정확한 이름 또는 `이름=...` 형태로 판정한다(부분일치 금지 — `--fail-fast` 같은 정상 옵션이 막힌다).
- 실행파일(첫 토큰)은 로더의 `GOLDEN_COMMAND_ALLOWLIST`를 **재사용해** 다시 확인한다. 이유: fixture가 로더를 거치지 않고 직접 구성될 수 있는 호출 경로를 방어한다(이중 검사).

**allowlist를 이 모듈에 복제하지 마라** — `loader.py`에서 import한다.

### 2) 실행

```python
@dataclass(frozen=True)
class GoldenResult:
    status: str          # passed | failed | error | skipped
    output: str          # GOLDEN_OUTPUT 게이팅은 report.py 책임 — 여기서는 담아서 돌려준다
    duration_s: float
    exit_code: int | None


def run_golden(
    command: str | None,
    worktree: Path,
    timeout_seconds: int,
) -> GoldenResult: ...
```

핵심 규칙:

- `command`가 None/빈 값이면 `skipped`.
- `shlex.split(command)` → `validate_golden_args` → `subprocess.run(tokens, shell=False, cwd=worktree, timeout=timeout_seconds, capture_output=True, text=True)`.
- **`cwd`는 반드시 worktree 경로다.** 원본 repo나 프로젝트 루트에서 실행하지 마라.
- `cwd`가 실제로 존재하는 디렉토리인지 확인하고, 아니면 `error`.
- 타임아웃은 `status="error"`로 구분해 돌려준다(스펙 §9 "golden timeout"). 예외를 밖으로 던지지 말고 결과 객체로 표현하라 — runner가 케이스를 계속 진행해야 한다.
- 출력은 `app/golden.py`처럼 상한(4000자, 뒷부분 우선)을 두고 자른다.
- 환경변수는 상속하되 `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`을 제거한다(`scripts/build_replay_repos.py::_git_env`와 같은 이유 — 골든 명령이 git을 호출할 때 실제 저장소로 리다이렉트되는 것을 막는다).

### 3) ARCHITECTURE 갱신

`docs/architecture/ARCHITECTURE.md`의 `app/evaluation/replay/` 트리에 `golden_exec.py`를 **실행 계층**으로 추가하라. 레이어 규칙 문장은 수정하지 마라.

## 테스트

`tests/test_replay_golden_exec.py` 신규 작성:

- **인자 검증 거부 (핵심, 각각 개별 테스트)**: `mvn -f /other/pom.xml`, `mvn --file=/other/pom.xml`, `pytest /abs/dir`, `pytest -p evil_plugin`, `gradle -b /other/build.gradle`, `pytest --rootdir=/other`, `mvn -s /other/settings.xml`, `pytest ../../outside`.
- **정상 통과**: `mvn -q test -Dtest=X`, `pytest -k pattern`, `pytest tests/golden`, `./gradlew test --tests '*Golden*'`, `mvn --fail-fast test`(부분일치로 막히지 않는지).
- 실행파일 재검사: allowlist 밖 실행파일(`rm`, `bash`)이 거부되는지 — 로더를 거치지 않고 직접 호출해도 막혀야 한다.
- `command=None`/빈 문자열 → `skipped`.
- 실제 실행: `tmp_path`에 간단한 스크립트를 두고 **allowlist 안의 실행파일**로 성공/실패 케이스를 만들어라. allowlist에 없는 명령을 쓰려고 검증을 우회하지 마라 — 필요하면 `pytest`로 빈 테스트 디렉토리를 돌리는 식으로 구성한다. 환경에 해당 도구가 없으면 skip.
- 타임아웃 → `status="error"`이고 예외가 밖으로 나오지 않는지(monkeypatch로 `subprocess.run`이 `TimeoutExpired`를 던지게 하라).
- `cwd`가 없는 경로 → `error`.
- 출력 상한이 적용되는지.
- `shell=True`가 코드에 없는지(소스 문자열 검사도 가능하나, 아래 AC의 grep으로 대체 가능).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
grep -n "shell=" app/evaluation/replay/golden_exec.py
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 두 번째 커맨드 출력에 `shell=True`가 없어야 한다.
2. 아키텍처 체크리스트를 확인한다:
   - `app/golden.py::run_golden_tests`를 호출하지 않는가?
   - allowlist를 복제하지 않고 `loader.py`에서 import했는가?
   - `cwd`가 worktree로 고정되는가?
   - 금지 옵션 판정이 부분일치가 아닌 정확한 이름 매칭인가?
   - 타임아웃이 예외가 아니라 결과 객체로 표현되는가?
3. 결과에 따라 `phases/issue-0018/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, 거부 규칙 목록, GoldenResult 구조, cwd 고정·타임아웃 처리 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- `shell=True`를 쓰지 마라. 이유: `golden_command`는 fixture YAML에서 오며 fixture는 파일로 주고받을 수 있다 — shell을 열면 그 문자열이 그대로 실행된다(ADR-011).
- `app/golden.py::run_golden_tests`를 재사용하지 마라. 이유: 그 함수는 `shell=True`이며 `config.golden_test_cmd`(운영자 입력)에 맞춰 설계됐다. 신뢰 수준이 다른 입력을 같은 경로로 흘리면 #0017 allowlist가 무의미해진다.
- `app/golden.py`를 수정하지 마라. 이유: 기존 초안 검증 경로의 동작 보존이 우선이며, 이번 이슈 범위가 아니다. (그 함수의 `git apply`에 timeout이 없는 것은 알려진 사항이나 여기서 고치지 않는다.)
- allowlist를 이 모듈에 복사하지 마라. 이유: 두 곳이 되면 한쪽만 고쳐져 우회가 생긴다.
- 금지 옵션을 부분일치(`in`)로 판정하지 마라. 이유: `--fail-fast`가 `-f` 부분일치에 걸리는 식으로 정상 명령이 막힌다.
- 골든 실행을 원본 repo나 프로젝트 루트에서 하지 마라. 이유: 대상 코드가 아닌 곳에서 빌드가 돌면 결과가 무의미하고 부작용이 실제 저장소에 남는다.
- 타임아웃을 예외로 전파하지 마라. 이유: 한 케이스의 타임아웃이 전체 replay를 중단시키면 나머지 fixture 결과를 잃는다(스펙 §9는 실패 유형을 구분해 계속 진행할 것을 전제한다).
- 기존 테스트를 깨뜨리지 마라.
