# Step 2: answer-diff

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙)
- `/docs/architecture/ARCHITECTURE.md`, `/docs/architecture/ADR.md` (**ADR-011**)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§2 원칙 — "answer commit 전체를 무조건 정답으로 보지 않음", §4-9 answer diff 추출, §7 지표, §11 unrelated exclusion**)
- `/app/evaluation/replay/git_cmd.py` (Step 0 — `run_git`. git은 이것으로만 부른다)
- `/app/evaluation/replay/fixture.py` (#0017 — `ReplayScope.relevant_paths`/`excluded_paths`/`expected_replacements`)
- `/evaluation/fixtures/replay/mock_cases.yaml`, `/evaluation/fixtures/replay_sources/case3_unrelated_noise/` (**무관 변경이 섞인 케이스** — 이 step이 다루는 핵심 상황)

`case3_unrelated_noise`의 base/answer 트리를 직접 비교해보고, answer commit에 코드 변경과 문서 변경이 함께 들어 있다는 것을 확인한 뒤 작업하라.

## 작업

`app/evaluation/replay/answer_diff.py`를 신규 생성한다. answer commit이 실제로 바꾼 것을 추출하고, **사람이 지정한 scope로 걸러** 정답 집합을 만든다.

### 1) 자료구조

```python
@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str          # A | M | D | R... (git --name-status 코드)
    added_lines: int
    removed_lines: int


@dataclass(frozen=True)
class AnswerDiff:
    """answer commit의 변경 중 scope로 걸러낸 정답 집합."""

    in_scope: tuple[ChangedFile, ...]      # relevant_paths에 속하고 excluded가 아닌 것
    out_of_scope: tuple[ChangedFile, ...]  # answer commit에는 있으나 정답이 아닌 것
    excluded: tuple[ChangedFile, ...]      # excluded_paths에 명시적으로 걸린 것
```

`out_of_scope`와 `excluded`를 **버리지 말고 남겨라.** 이유: 스펙 §2가 "answer commit 전체를 정답으로 보지 않는다"고 정했지만, 무엇을 왜 뺐는지 리포트에 보여야 사람이 scope 지정이 옳았는지 판단할 수 있다.

### 2) 추출

```python
def extract_answer_diff(
    repo_path: Path,
    base_commit: str,
    answer_commit: str,
    scope: ReplayScope,
) -> AnswerDiff: ...
```

- `diff --name-status <base> <answer>` 로 변경 파일 목록을, `diff --numstat <base> <answer>` 로 줄 수를 얻는다. 두 호출 모두 `git_cmd.run_git` 경유.
- **원본 repo에서 읽기만 한다** — 이 함수는 worktree가 필요 없다(commit 대 commit 비교).
- 경로 매칭 규칙:
  - `relevant_paths`의 항목이 파일 경로와 정확히 같거나, **디렉토리 접두**(`module-tax/` 처럼)일 때 in-scope로 본다.
  - `excluded_paths`가 relevant보다 **우선**한다. 둘 다 걸리면 excluded.
  - 매칭은 정규화된 POSIX 경로로 한다(`\` → `/`).
- rename(`R100 old new`) 처리: git이 주는 신규 경로를 기준으로 판정하고, `status`에 원본 코드를 남긴다. 판단이 애매하면 신규 경로만 쓰고 주석으로 한계를 남겨라.

### 3) 기대 교체 대조

```python
def check_expected_replacements(
    repo_path: Path,
    answer_commit: str,
    scope: ReplayScope,
) -> tuple[ReplacementCheck, ...]: ...
```

- fixture의 `expected_replacements`가 **실제 answer commit 시점 파일 내용과 맞는지** 확인한다(`show <answer>:<path>` 로 파일을 읽어 `after` 문자열이 있는지, `before`가 없는지).
- 결과는 파일별 `found_after: bool`, `found_before: bool`, `path_exists: bool` 정도의 값 객체로 돌려준다.
- 이 함수의 목적은 **fixture 자체가 옳은지 검증**하는 것이다 — 생성된 초안 평가가 아니다(그건 Step 3). fixture가 틀렸는데 초안을 평가하면 결과 전체가 무의미하다.
- `match_mode`가 `normalized_text`면 공백을 정규화해 비교한다(`app/evaluation/case.py`의 `SUPPORTED_MATCH_MODES` 참조).

### 4) ARCHITECTURE 갱신

`docs/architecture/ARCHITECTURE.md`의 `app/evaluation/replay/` 트리에 `answer_diff.py`를 **실행 계층**으로 추가하라. 레이어 규칙 문장은 수정하지 마라.

## 테스트

`tests/test_replay_answer_diff.py` 신규 작성. `scripts/build_replay_repos.py`로 `tmp_path`에 mock repo를 빌드해 쓰고, git이 없으면 skip.

- **case3(unrelated noise) 필터링 (핵심)**: answer diff에 코드 파일과 문서 파일이 모두 나오지만, `excluded_paths`에 문서가 있으면 `in_scope`에는 코드만, `excluded`에는 문서가 들어가는지. 스펙 §11 "unrelated exclusion"이다.
- **case2(condition + test)**: `relevant_paths`가 2개일 때 둘 다 `in_scope`인지.
- **case1(value change)**: 단일 파일이 `in_scope`, 나머지 없음.
- `relevant_paths`에 없는 변경이 `out_of_scope`로 남는지(버려지지 않는지).
- excluded가 relevant보다 우선하는지(같은 경로를 양쪽에 넣은 fixture는 로더가 막지만, 함수 단위로는 확인하라).
- 디렉토리 접두 매칭이 동작하는지(`src/main/` 지정 시 하위 파일이 in-scope).
- `check_expected_replacements`: 맞는 fixture → `found_after=True`/`found_before=False`, 틀린 fixture(after 문자열이 실제로 없음) → `found_after=False`.
- 존재하지 않는 경로를 expected_replacements에 넣으면 `path_exists=False`로 보고되고 예외가 아닌지.
- 원본 repo가 변경되지 않았는지(`status --porcelain` 전후 비교).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `grep -n "subprocess" app/evaluation/replay/answer_diff.py` 가 비어 있는가(git은 wrapper 경유)?
   - `out_of_scope`/`excluded`를 버리지 않고 보존하는가?
   - worktree 없이 commit 대 commit 비교만 하는가(원본 무변경)?
3. 결과에 따라 `phases/issue-0018/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, AnswerDiff 구조, scope 매칭 규칙, expected_replacements 대조 결과 형태 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- answer commit의 변경 전체를 정답으로 쓰지 마라. 이유: 스펙 §2·§13이 명시적으로 금지한다. 실제 커밋에는 리팩토링·문서·무관 수정이 섞이며, 그것까지 맞히라고 요구하면 지표가 영원히 낮게 나와 의미를 잃는다.
- `out_of_scope`/`excluded` 목록을 버리지 마라. 이유: 무엇을 왜 제외했는지 보이지 않으면 사람이 scope 지정의 타당성을 검토할 수 없다.
- `subprocess`를 직접 호출하지 마라. 이유: allowlist·timeout이 `git_cmd.py`에만 있다.
- worktree를 만들거나 파일을 쓰지 마라. 이유: 이 step은 읽기 전용 비교이며 worktree 생명주기는 Step 1의 책임이다.
- 지표(Recall·Jaccard 등)를 여기서 계산하지 마라. 이유: Step 3(`report.py`)의 범위다. 이 step은 "정답 집합"을 만들 뿐이다.
- 기존 테스트를 깨뜨리지 마라.
