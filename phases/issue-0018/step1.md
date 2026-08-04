# Step 1: worktree-lifecycle

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — **실제 repo는 절대 수정하지 않는다**)
- `/docs/architecture/ARCHITECTURE.md` (`app/evaluation/replay/` 선언/실행 계층, git은 wrapper 경유)
- `/docs/architecture/ADR.md` (**ADR-011** — worktree 방식과 보장된 정리를 택한 근거·트레이드오프)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§4 worktree 절차 1~4·11, §9 실패 구분, §11 테스트 목록**)
- `/app/evaluation/replay/git_cmd.py` (Step 0 산출물 — `run_git`, `GitCommandError`. **git은 이것으로만 부른다**)
- `/app/evaluation/replay/fixture.py`, `/loader.py` (#0017 — `ReplayRepository.path`/`path_env` 의미)
- `/scripts/build_replay_repos.py` (`ensure_within` 안전 패턴, mock repo 구조와 태그 이름)
- `/evaluation/fixtures/replay/mock_cases.yaml` (테스트에 쓸 mock 케이스)

## 작업

`app/evaluation/replay/worktree.py`를 신규 생성한다. 이 step의 산출물은 **컨텍스트 매니저 하나**이며, 파이프라인·비교·리포트는 다루지 않는다.

### 1) repo 경로 해석

```python
def resolve_repo_path(repository: ReplayRepository, project_root: Path) -> Path: ...
```

- `repository.path`가 있으면 `project_root / path`.
- `repository.path_env`가 있으면 **여기서 처음으로 환경변수를 읽는다**(#0017 로더는 읽지 않는다 — 실행 계층인 이 모듈의 책임이다). 미설정이거나 빈 값이면 명확한 오류.
- 해석된 경로가 존재하는 git repo인지 `rev-parse --git-dir` 로 확인한다.
- 오류 메시지에 **해석된 절대경로를 넣지 마라** — 회사 경로가 로그·리포트로 새는 것을 막는다. 환경변수 *이름*만 언급한다.

### 2) 사전 검증 (스펙 §4-1·§4-4)

```python
def assert_clean_worktree(repo_path: Path) -> None: ...
```

- `status --porcelain` 결과가 비어 있지 않으면 오류로 중단한다. 이유: 원본이 dirty한 상태에서 worktree를 만들면 사용자가 작업 중인 변경과 replay 결과가 섞여 해석이 불가능하고, 사고 시 원인 분리가 안 된다.
- `base_commit`·`answer_commit`이 실제로 존재하는지 `rev-parse --verify <rev>^{commit}` 으로 확인한다. 없으면 스펙 §9의 "commit 없음"에 해당하는 구분 가능한 오류를 낸다.

### 3) 컨텍스트 매니저

```python
@contextmanager
def replay_worktree(
    repo_path: Path,
    base_commit: str,
    *,
    keep_on_error: bool = False,
) -> Iterator[Path]: ...
```

절차(스펙 §4-2·3·11):

1. `tempfile.mkdtemp(prefix="regtax_replay_")` 로 임시 root 생성.
2. `worktree add --detach <tmp>/work <base_commit>` — **반드시 `--detach`**. 브랜치를 만들거나 checkout 하지 않는다.
3. worktree 경로를 yield.
4. **`finally`에서** `worktree remove --force <path>` → `worktree prune` → 임시 root `shutil.rmtree(ignore_errors=True)`.

핵심 규칙:

- **cleanup은 예외가 나도 반드시 수행한다.** 이것이 이 step의 존재 이유이고 로드맵 수용 기준("실패 후 임시 디렉토리 정리")이다.
- cleanup 자체가 실패해도 원래 예외를 삼키지 마라 — cleanup 실패는 경고로 남기고 원래 예외를 그대로 올린다(스펙 §9 "cleanup 실패"를 별도 구분).
- `keep_on_error=True`는 디버깅용 opt-in이다. 기본값은 반드시 `False`.
- 임시 디렉토리 삭제 전에 그 경로가 `tempfile` 이 만든 root 하위인지 확인하라(`scripts/build_replay_repos.py::ensure_within` 과 같은 방어).
- 원본 repo의 working tree에 **어떤 쓰기도 하지 않는다.** `worktree add`가 `.git/worktrees/` 에 메타데이터를 만드는 것이 유일한 흔적이며, 이는 remove/prune으로 회수된다(ADR-011).

### 4) ARCHITECTURE 갱신

`docs/architecture/ARCHITECTURE.md`의 `app/evaluation/replay/` 트리에 `worktree.py` 항목을 **실행 계층**으로 추가하라. 현재 트리에는 `git_cmd.py`/`runner.py`/`report.py`만 적혀 있다. 레이어 규칙 문장은 수정하지 마라 — 파일 목록만 갱신한다.

## 테스트

`tests/test_replay_worktree.py` 신규 작성. 실제 git이 필요하므로 `scripts/build_replay_repos.py`로 `tmp_path`에 mock repo를 빌드해 쓰거나 `tmp_path`에 직접 `git init` 하라. git이 없으면 skip.

스펙 §11이 요구하는 항목 중 이 step에 해당하는 것:

- **commit validation**: 없는 revision → 구분 가능한 오류.
- **worktree lifecycle**: 컨텍스트 진입 시 worktree가 생기고 base commit 시점 파일 내용이 맞는지, 종료 후 디렉토리가 사라졌는지.
- **original repo unchanged (핵심)**: 컨텍스트 실행 전후로 원본의 `status --porcelain`, `rev-parse HEAD`, 그리고 **worktree 목록**이 같은지. `git worktree list` 출력이 실행 전후 동일해야 한다(누수 없음).
- **exception cleanup (핵심)**: 컨텍스트 안에서 예외를 던져도 worktree와 임시 디렉토리가 정리되는지. 로드맵 수용 기준이다.
- **dirty repo 거부**: 원본에 커밋되지 않은 변경이 있으면 진입 자체가 실패하는지.
- `keep_on_error=True`일 때만 남는지.
- `path_env` 해석: 환경변수 미설정 → 오류, 설정 → 해석 성공. **오류 메시지에 절대경로가 없는지** 문자열로 확인.
- cleanup 실패 시 원래 예외가 보존되는지(monkeypatch로 remove를 실패시켜라).

무거운 의존성(임베딩·LLM·ChromaDB)을 트리거하지 마라.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
git worktree list
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 두 번째 커맨드 출력에 **이 저장소의 worktree만** 보여야 한다(테스트가 남긴 항목이 없어야 한다).
2. 아키텍처 체크리스트를 확인한다:
   - git 호출이 전부 `git_cmd.run_git` 경유인가? (`grep -n "subprocess" app/evaluation/replay/worktree.py` 결과가 비어야 한다)
   - `--detach` 없이 worktree를 만드는 경로가 없는가?
   - cleanup이 `finally`에 있는가?
   - `git status --short`로 저장소가 깨끗한가?
3. 결과에 따라 `phases/issue-0018/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, 컨텍스트 매니저 시그니처, 사전 검증 항목, cleanup 순서 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- 원본 repo에서 `checkout`/`reset`/`clean`/`commit`/`push`를 실행하지 마라. 이유: 스펙 §4가 명시적으로 금지하며, 회사 실제 업무 저장소가 대상이라 훼손은 되돌릴 수 없다.
- `subprocess`를 직접 호출하지 마라. 이유: allowlist·timeout이 `git_cmd.py` wrapper에만 있어서, 우회하면 안전장치 전체가 무력해진다.
- cleanup을 `finally` 밖에 두지 마라. 이유: 예외 경로에서 worktree가 남으면 원본 repo에 흔적이 누적되고, 로드맵 수용 기준을 못 채운다.
- cleanup 실패 시 원래 예외를 삼키지 마라. 이유: 진짜 원인이 가려지고 스펙 §9의 실패 구분이 무의미해진다.
- `keep_on_error` 기본값을 `True`로 두지 마라. 이유: 기본 경로에서 임시 디렉토리가 쌓인다.
- 오류 메시지·로그에 해석된 repo 절대경로를 넣지 마라. 이유: 회사 경로가 리포트로 새면 CLAUDE.md 반출 금지에 저촉된다. 환경변수 이름만 쓴다.
- 파이프라인 호출·diff 비교·리포트 저장을 이 step에서 하지 마라. 이유: Step 2~5의 범위다.
- 기존 테스트를 깨뜨리지 마라.
