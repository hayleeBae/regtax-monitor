# Step 0: git-allowlist

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — **실제 repo는 절대 수정하지 않는다**)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙 — `app/evaluation/replay/`의 선언/실행 계층 분리, "replay의 git 호출은 전부 `git_cmd.py` wrapper를 통과한다")
- `/docs/architecture/ADR.md` (**ADR-011** — 이 작업의 근거. ADR-006 골든 테스트 스크래치 원칙도 함께)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§4 worktree 절차, §5 git allowlist, §9 실패 구분**)
- `/scripts/build_replay_repos.py` (**본보기** — `_run_git()`, `_git_env()`, `ensure_within()`. #0017에서 검증된 안전 패턴이며 여기서 다시 쓴다)
- `/app/golden.py` (기존 골든 실행 — `shell=True`를 쓰는 곳. **이 패턴을 따르지 않는 이유**를 ADR-011에서 확인하라)
- `/app/evaluation/replay/fixture.py`, `/app/evaluation/replay/loader.py` (#0017 — 선언 계층. 이 step은 실행 계층이다)

`scripts/build_replay_repos.py`의 `_run_git`/`_git_env`를 꼼꼼히 읽어라. 상속된 `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` 을 제거하는 이유(git 작업이 실제 저장소로 리다이렉트되는 것 차단)를 이해한 뒤 작업하라.

## 작업

`app/evaluation/replay/git_cmd.py`를 신규 생성한다. **이후 모든 replay step은 git을 이 모듈로만 호출한다.**

### 1) 서브커맨드 allowlist (스펙 §5)

```python
ALLOWED_SUBCOMMANDS: frozenset[str] = frozenset({
    "rev-parse", "cat-file", "diff", "show", "worktree", "status", "apply",
})
```

- `worktree`는 두 번째 토큰이 `add`/`remove`/`prune`일 때만 허용한다(스펙 §5는 add/remove를 명시하고, prune은 cleanup 회수에 필요하다 — ADR-011).
- `apply`는 `--check` 유무 모두 허용하되, **`--index`/`--cached`/`-3`/`--3way` 는 거부한다.** 이유: 이들은 index를 건드려 worktree 상태를 변경한다.
- allowlist 검사는 **이 모듈 안에서** 한다. 호출자가 우회할 수 있는 "raw" 탈출구를 만들지 마라(ADR-011).

### 2) 금지 인자

서브커맨드와 무관하게 아래를 거부한다:

- `-c`, `--exec-path`, `--upload-pack`, `--receive-pack`, `-C`, `--git-dir`, `--work-tree`, `--namespace` (호출자가 대상 repo를 바꾸거나 임의 명령을 실행할 수 있다)
- `-` 로 시작하는 값이 인자 위치에 오는 경우는 옵션이므로 그 자체로는 정상이다. 위 목록에 대해서만 **정확히 그 이름 또는 `이름=...` 형태**를 거부하라(부분일치 금지 — `--diff-filter` 같은 정상 옵션이 막힌다).
- 커밋·경로 인자와 옵션을 구분해야 하는 곳에서는 `--` 구분자를 쓰도록 호출자에게 강제할 필요는 없다. 다만 이 모듈이 `--` 를 인자로 받는 것은 허용한다.

### 3) 실행 함수

```python
DEFAULT_TIMEOUT_SECONDS = 120


class GitCommandError(RuntimeError):
    """git 실행 실패 — exit code, stderr 요약을 담는다."""


def run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    check: bool = True,
    git_bin: str = "git",
) -> subprocess.CompletedProcess: ...
```

핵심 규칙:

- **`shell=False`(인자 배열) 고정.** `shell=True` 를 쓰지 마라.
- **모든 호출에 timeout.** 인자 없이 부르면 `DEFAULT_TIMEOUT_SECONDS`. 타임아웃은 `GitCommandError` 로 변환한다(스펙 §9 — 실패 유형 구분).
- 환경은 `scripts/build_replay_repos.py::_git_env` 와 같은 방식으로 정리한다: 상속된 `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` 제거. 신원(user.name/email)은 이 모듈에서 **커밋을 만들지 않으므로** 주입하지 않아도 된다.
- `git` 실행파일이 없으면(`FileNotFoundError`) 명확한 메시지의 `GitCommandError`.
- `check=True`(기본)이면 비정상 종료 시 `GitCommandError`, `check=False`면 `CompletedProcess`를 그대로 돌려준다. 이유: `apply --check` 나 `status` 처럼 **실패 자체가 정보**인 호출이 있다.
- `capture_output=True, text=True`. stderr/stdout 을 예외 메시지에 담되 **4000자로 잘라라**(`app/golden.py::_MAX_OUTPUT` 과 같은 상한 개념).

### 4) 얇은 헬퍼 (선택)

자주 쓰는 호출은 얇은 헬퍼로 감싸도 좋다(`rev_parse`, `worktree_add`, `worktree_remove`, `worktree_prune`, `diff_name_status`, `show_file`). 단 **헬퍼도 반드시 `run_git`을 거쳐야** 하며 `subprocess`를 직접 부르지 마라.

## 테스트

`tests/test_replay_git_cmd.py` 신규 작성:

- allowlist 통과: `rev-parse HEAD`, `status --porcelain`, `diff --name-status A B`, `show tag:path`, `worktree add ...`, `worktree remove ...`, `worktree prune`, `apply --check patch`.
- allowlist 거부: `clone`, `push`, `fetch`, `checkout`, `reset`, `clean`, `commit`, `tag`, `config`, `remote`. **`checkout`/`reset`/`clean`/`push` 거부는 스펙 §4의 핵심 안전 제약이므로 반드시 개별 테스트로 고정하라.**
- `worktree` 두 번째 토큰이 `add`/`remove`/`prune` 이 아닌 경우(`worktree list`, `worktree lock`) 거부.
- `apply --index`/`--cached`/`-3`/`--3way` 거부.
- 금지 인자 거부: `-c user.name=x`, `--git-dir=/other`, `--work-tree=/other`, `-C /other`, `--upload-pack=evil`, `--exec-path=/tmp`.
- 부분일치로 정상 옵션이 막히지 않는지: `diff --diff-filter=M`, `show --stat` 은 통과해야 한다.
- 빈 args → 오류.
- timeout: 아주 짧은 timeout으로 인위적 지연을 만들어 `GitCommandError`가 나는지(가짜 `git_bin`으로 `sleep` 스크립트를 쓰거나 monkeypatch로 `subprocess.run`이 `TimeoutExpired`를 던지게 하라).
- `git_bin`이 존재하지 않을 때 `GitCommandError` + 메시지에 설치 안내.
- `check=False`면 실패해도 예외가 아니라 `CompletedProcess` 반환.
- 실제 git이 필요한 테스트는 `tmp_path`에 `git init`한 임시 repo에서만 수행하고, git이 없으면 skip 하라.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
grep -n "subprocess.run\|shell=" app/evaluation/replay/git_cmd.py
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 두 번째 AC 출력에서 `subprocess.run` 호출이 **1곳뿐**이고 `shell=True`가 없는가?
   - allowlist 검사가 wrapper 내부에 있고, 검사를 건너뛰는 공개 함수가 없는가?
   - `checkout`/`reset`/`clean`/`push`/`commit`이 거부되는가?
   - `app/evaluation/replay/`의 실행 계층 위치(ARCHITECTURE.md)를 따르는가?
3. 결과에 따라 `phases/issue-0018/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, allowlist 내용, 금지 인자 목록, run_git 시그니처 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요(git 미설치 등) → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- `shell=True`를 쓰지 마라. 이유: 경로·인자에 특수문자가 있으면 명령이 조립되어 임의 실행으로 이어진다.
- allowlist 검사를 건너뛰는 공개 함수(`run_raw` 등)를 만들지 마라. 이유: 검사가 wrapper 안에 있어야 호출 지점이 늘어도 빠뜨리지 않는다(ADR-011).
- `checkout`/`reset`/`clean`/`commit`/`push`/`fetch`/`clone`을 allowlist에 넣지 마라. 이유: 스펙 §4가 source working tree에서 이들을 명시적으로 금지한다. 원본 repo 훼손은 이 프로젝트에서 되돌릴 수 없는 사고다.
- timeout 없는 `subprocess` 호출을 만들지 마라. 이유: 스펙 §5가 모든 command에 timeout을 요구하며, 회사 대형 repo에서 무한 대기는 실행을 멈춘다.
- 금지 인자 검사를 부분일치(`in`)로 구현하지 마라. 이유: `--diff-filter` 같은 정상 옵션이 `--diff`… 부분일치에 걸려 막힌다. 정확한 이름 또는 `이름=` 접두만 본다.
- 이 step에서 worktree 생명주기·비교 로직·리포트를 만들지 마라. 이유: Step 1~5의 범위다.
- 기존 테스트를 깨뜨리지 마라.
