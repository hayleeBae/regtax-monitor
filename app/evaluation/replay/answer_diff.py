"""answer commit 의 변경을 scope 로 걸러 정답 집합을 만든다 — HISTORICAL_REPLAY_SPEC
§2·§4-9·§11, ADR-011.

`worktree.py` 가 "과거 시점 코드를 펼치는" 실행 계층이라면, 이 모듈은 **정답이 무엇인지
정의하는** 실행 계층이다. commit 대 commit 비교뿐이므로 worktree 도, 파일 쓰기도 필요
없다 — 원본 repo 는 읽기만 한다(스펙 §2 "원본 repo 무변경").

## 왜 answer commit 전체가 정답이 아닌가

실제 개정 커밋에는 리팩토링·문서·무관 수정이 함께 들어온다(스펙 §10-3 이 이 상황을
mock 케이스로 못박았다). 그것까지 맞히라고 요구하면 지표가 영원히 낮게 나와 "초안이
쓸 만한가"를 판별하지 못한다. 그래서 정답은 **사람이 지정한 `relevant_paths`** 로
좁힌다(스펙 §2·§13).

## 왜 제외한 것을 버리지 않는가

`out_of_scope`·`excluded` 를 지우면 리포트에는 "정답 N건 중 M건" 만 남고, scope 지정이
타당했는지 사람이 되짚을 방법이 사라진다. 무엇을 왜 뺐는지가 보여야 fixture 를 고칠
수 있으므로 세 갈래를 모두 보존한다(스펙 §11 unrelated exclusion).

## 지표를 여기서 계산하지 않는 이유

Recall·Jaccard 는 "생성된 초안"과 이 정답 집합을 맞대야 나온다(스펙 §7). 정답 정의와
채점을 한 파일에 두면 채점 기준을 바꾸려다 정답 정의가 흔들린다 — 산출은 `report.py`
몫이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from app.evaluation.case import ExpectedReplacement
from app.evaluation.replay.fixture import ReplayScope
from app.evaluation.replay.git_cmd import GitCommandError, run_git

logger = logging.getLogger(__name__)

_RENAME_STATUSES = ("R", "C")
"""신규 경로가 따로 오는 status 접두 — `R100`/`C075` 처럼 유사도 숫자가 붙는다."""


class AnswerDiffError(RuntimeError):
    """answer diff 추출 실패 — git 실행이 되지 않았거나 출력을 해석하지 못했다.

    메시지에는 **repo 절대경로와 git stderr 를 담지 않는다**. 실데이터 repo 경로는
    `path_env` 로만 들어오며(ADR-010) 리포트·로그로 새면 그대로 반출이다
    (`worktree.py` 의 `_scrub` 과 같은 이유). 필요한 원인은 revision 이름과 exit
    code 로 충분하고, 원본 예외는 `__cause__` 로 남긴다.
    """


# ---------------------------------------------------------------------------
# 값 객체
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangedFile:
    """answer commit 이 바꾼 파일 하나."""

    path: str
    status: str  # A | M | D | R100 … (git --name-status 코드 원본)
    added_lines: int
    removed_lines: int


@dataclass(frozen=True)
class AnswerDiff:
    """answer commit 의 변경 중 scope 로 걸러낸 정답 집합.

    세 갈래는 서로 겹치지 않으며 합치면 answer commit 의 전체 변경이다 — 리포트가
    "왜 이 파일이 정답에서 빠졌는가"에 답할 수 있어야 한다(스펙 §11).
    """

    in_scope: tuple[ChangedFile, ...]  # relevant_paths 에 속하고 excluded 가 아닌 것
    out_of_scope: tuple[ChangedFile, ...]  # answer commit 에는 있으나 정답이 아닌 것
    excluded: tuple[ChangedFile, ...]  # excluded_paths 에 명시적으로 걸린 것

    @property
    def all_changed(self) -> tuple[ChangedFile, ...]:
        """answer commit 의 전체 변경 — 세 갈래를 합친 것(원본 순서 유지)."""
        merged = [*self.in_scope, *self.out_of_scope, *self.excluded]
        return tuple(sorted(merged, key=lambda changed: changed.path))


@dataclass(frozen=True)
class ReplacementCheck:
    """fixture 의 `expected_replacements` 한 건이 answer commit 과 맞는지.

    "초안이 잘 만들어졌는가"가 아니라 **fixture 자체가 옳은가**를 본다(스펙 §12
    "answer 와 file/replacement 비교"의 전제). fixture 가 틀린 채로 초안을 채점하면
    결과 전체가 무의미하므로 replay 실행 전에 이 검사를 통과시킨다.
    """

    path: str
    match_mode: str
    path_exists: bool
    found_after: bool
    found_before: bool

    @property
    def ok(self) -> bool:
        """fixture 가 answer commit 과 일관적인가 — 파일이 있고, after 가 있고,
        before 가 남아 있지 않다."""
        return self.path_exists and self.found_after and not self.found_before


# ---------------------------------------------------------------------------
# 경로 정규화·매칭
# ---------------------------------------------------------------------------


def normalize_path(path: str) -> str:
    """비교용 POSIX 상대 경로로 정규화한다.

    Windows 구분자(`\\`)와 `./` 접두, 중복 슬래시를 없앤다. fixture 는 사람이 쓰고
    git 은 항상 `/` 를 주므로, 정규화 없이 비교하면 같은 파일이 다르게 보인다.
    """
    text = (path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    parts = [part for part in text.split("/") if part and part != "."]
    return "/".join(parts)


def path_matches(path: str, spec: str) -> bool:
    """`path` 가 scope 항목 `spec` 에 걸리는가 — 정확 일치 또는 **디렉토리 접두**.

    `module-tax/` 처럼 디렉토리를 지정하는 쓰임을 지원하되, 접두 비교는 반드시
    구분자 경계에서 한다. 단순 `startswith` 면 `src/main` 이 `src/main2/x.java` 까지
    삼킨다.
    """
    normalized_path = normalize_path(path)
    normalized_spec = normalize_path(spec)
    if not normalized_spec or not normalized_path:
        return False
    if normalized_path == normalized_spec:
        return True
    return normalized_path.startswith(f"{normalized_spec}/")


def _matches_any(path: str, specs: Iterable[str]) -> bool:
    return any(path_matches(path, spec) for spec in specs)


def classify_changes(changes: Iterable[ChangedFile], scope: ReplayScope) -> AnswerDiff:
    """변경 목록을 scope 기준으로 in_scope / out_of_scope / excluded 로 가른다.

    **excluded 가 relevant 보다 우선한다.** 같은 경로가 양쪽에 걸리는 fixture 는
    로더가 막지만(`_parse_scope` 의 overlap 검사), 디렉토리 접두 때문에 겹치는
    경우는 로더가 잡지 못한다 — 예: `relevant_paths: ["src/"]` +
    `excluded_paths: ["src/docs/"]`. 이때 "빼라고 명시한 쪽"을 따르는 것이
    사람 의도에 가깝다.
    """
    in_scope: list[ChangedFile] = []
    out_of_scope: list[ChangedFile] = []
    excluded: list[ChangedFile] = []

    for changed in changes:
        if _matches_any(changed.path, scope.excluded_paths):
            excluded.append(changed)
        elif _matches_any(changed.path, scope.relevant_paths):
            in_scope.append(changed)
        else:
            out_of_scope.append(changed)

    return AnswerDiff(
        in_scope=tuple(in_scope),
        out_of_scope=tuple(out_of_scope),
        excluded=tuple(excluded),
    )


# ---------------------------------------------------------------------------
# git 출력 파싱
# ---------------------------------------------------------------------------


def _nul_tokens(output: str) -> list[str]:
    return [token for token in (output or "").split("\0") if token != ""]


def parse_name_status(output: str) -> list[tuple[str, str]]:
    """`diff --name-status -z` 출력을 `[(status, path), ...]` 로 파싱한다.

    `-z` 를 쓰는 이유: 기본 출력은 비ASCII 경로를 `"src/\\355\\225\\234.java"` 처럼
    따옴표·8진 이스케이프로 감싼다(`core.quotePath`). 한글 파일명이 있는 회사
    repo 에서 그대로 매칭하면 전부 out_of_scope 로 떨어진다.

    rename/copy(`R100 old new`)는 **신규 경로**를 기준으로 판정한다 — 개정 이후
    코드가 어디에 있는지가 정답이기 때문이다. `status` 에는 git 이 준 코드를 그대로
    남겨 리포트가 "이 파일은 이동해서 잡혔다"를 말할 수 있게 한다. 한계: 원본
    경로(`old`)는 여기서 버리므로, 정답 파일이 relevant scope 안에서 밖으로
    옮겨졌는지는 이 자료구조만으로 알 수 없다.
    """
    tokens = _nul_tokens(output)
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        i += 1
        takes_two = status.startswith(_RENAME_STATUSES)
        needed = 2 if takes_two else 1
        if i + needed > len(tokens):
            raise AnswerDiffError(
                f"git diff --name-status 출력을 해석하지 못했습니다 (status={status!r})."
            )
        path = tokens[i + 1] if takes_two else tokens[i]
        i += needed
        entries.append((status, normalize_path(path)))
    return entries


def _parse_count(value: str) -> int:
    """numstat 의 숫자 필드 — 바이너리 파일은 `-` 로 오므로 0 으로 읽는다."""
    try:
        return int(value)
    except ValueError:
        return 0


def parse_numstat(output: str) -> dict:
    """`diff --numstat -z` 출력을 `{path: (added, removed)}` 로 파싱한다.

    비rename 은 `"7\\t1\\tREADME.md"` 한 토큰, rename 은 `"1\\t1\\t"` 뒤에 old·new 가
    별도 토큰으로 온다(git 2.41 확인).
    """
    tokens = _nul_tokens(output)
    counts: dict = {}
    i = 0
    while i < len(tokens):
        fields = tokens[i].split("\t")
        i += 1
        if len(fields) < 3:
            raise AnswerDiffError(
                f"git diff --numstat 출력을 해석하지 못했습니다: {tokens[i - 1]!r}"
            )
        added, removed, inline_path = fields[0], fields[1], "\t".join(fields[2:])
        if inline_path:
            path = inline_path
        else:
            # rename/copy — 다음 두 토큰이 old, new 다. 신규 경로를 쓴다.
            if i + 2 > len(tokens):
                raise AnswerDiffError(
                    "git diff --numstat 의 rename 항목에 경로가 부족합니다."
                )
            path = tokens[i + 1]
            i += 2
        counts[normalize_path(path)] = (_parse_count(added), _parse_count(removed))
    return counts


# ---------------------------------------------------------------------------
# 추출 (스펙 §4-9)
# ---------------------------------------------------------------------------


def extract_answer_diff(
    repo_path: Path,
    base_commit: str,
    answer_commit: str,
    scope: ReplayScope,
) -> AnswerDiff:
    """base→answer 의 변경을 읽어 scope 로 거른 정답 집합을 돌려준다 (스펙 §4-9).

    **원본 repo 에서 읽기만 한다** — commit 대 commit 비교이므로 worktree 가 필요
    없다. 임시 worktree 는 파이프라인 실행(Step 1·`worktree.py`)의 도구이지 정답
    추출의 도구가 아니다.

    변경 파일 목록(`--name-status`)과 줄 수(`--numstat`)를 나눠 부르는 이유:
    한 번에 주는 형식(`--raw` 조합)은 rename 유사도와 줄 수를 동시에 주지 않아
    파싱이 오히려 복잡해진다. 두 호출 모두 `git_cmd.run_git` 을 거친다.
    """
    repo = Path(repo_path)
    name_status = _run_diff(
        ["diff", "--name-status", "-z", base_commit, answer_commit],
        repo,
        base_commit,
        answer_commit,
    )
    numstat = _run_diff(
        ["diff", "--numstat", "-z", base_commit, answer_commit],
        repo,
        base_commit,
        answer_commit,
    )

    counts = parse_numstat(numstat)
    changes = [
        ChangedFile(
            path=path,
            status=status,
            added_lines=counts.get(path, (0, 0))[0],
            removed_lines=counts.get(path, (0, 0))[1],
        )
        for status, path in parse_name_status(name_status)
    ]
    return classify_changes(changes, scope)


def _run_diff(args: list, repo: Path, base_commit: str, answer_commit: str) -> str:
    try:
        return run_git(args, cwd=repo).stdout
    except GitCommandError as exc:
        raise AnswerDiffError(
            "answer diff 추출에 실패했습니다 "
            f"(base={base_commit!r}, answer={answer_commit!r}, exit={exc.returncode})."
        ) from exc


# ---------------------------------------------------------------------------
# 기대 교체 대조 (fixture 검증)
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """`normalized_text` 매칭용 공백 정규화 — `app/evaluation/experiments.py` 와 같은 규칙."""
    return " ".join((text or "").split())


def _contains(source: str, needle: str, match_mode: str) -> bool:
    """`needle` 이 비어 있으면 항상 False 다 — 빈 문자열은 어디에나 "있다"고 나와
    fixture 오류를 통과시킨다."""
    if not needle.strip():
        return False
    if match_mode == "normalized_text":
        return _normalize_text(needle) in _normalize_text(source)
    return needle in source


def _read_file_at(repo: Path, revision: str, path: str) -> Optional[str]:
    """`git show <rev>:<path>` — 없으면 `None`.

    `check=False` 로 부르는 이유: 경로가 없는 것은 **보고할 결과**(`path_exists=False`)
    이지 예외 상황이 아니다. fixture 검증 도중 첫 오타에서 멈추면 나머지 항목의
    문제를 한 번에 볼 수 없다.
    """
    try:
        proc = run_git(["show", f"{revision}:{path}"], cwd=repo, check=False)
    except GitCommandError as exc:
        raise AnswerDiffError(
            f"answer commit 의 파일을 읽지 못했습니다 (answer={revision!r})."
        ) from exc
    except UnicodeDecodeError:
        # 바이너리·비UTF-8 파일. "있지만 문자열 비교 불가"이므로 빈 내용으로 다뤄
        # found_after=False 가 되게 한다 — fixture 가 잘못 가리키고 있다는 신호다.
        logger.warning("텍스트로 읽을 수 없는 파일을 expected_replacements 가 가리킵니다.")
        return ""
    if proc.returncode != 0:
        return None
    return proc.stdout


def check_expected_replacements(
    repo_path: Path,
    answer_commit: str,
    scope: ReplayScope,
) -> tuple[ReplacementCheck, ...]:
    """fixture 의 `expected_replacements` 가 answer commit 시점 내용과 맞는지 본다.

    판정: answer 시점 파일에 `after` 가 있고 `before` 가 없어야 fixture 가 옳다.
    생성된 초안을 채점하는 것이 **아니다**(그것은 `report.py` 의 일이다) — 여기서
    걸러야 할 것은 "fixture 가 실제 커밋과 어긋난 상태"다. 어긋난 fixture 로 초안을
    채점하면 지표 전체가 무의미해진다.

    `path_exists=False` 도 예외가 아니라 결과로 돌려준다 — 경로 오타 하나 때문에
    나머지 검증 결과를 잃지 않기 위해서다.
    """
    repo = Path(repo_path)
    checks: list[ReplacementCheck] = []
    for replacement in scope.expected_replacements:
        checks.append(_check_one(repo, answer_commit, replacement))
    return tuple(checks)


def _check_one(
    repo: Path, answer_commit: str, replacement: ExpectedReplacement
) -> ReplacementCheck:
    path = normalize_path(replacement.path)
    content = _read_file_at(repo, answer_commit, path)
    if content is None:
        return ReplacementCheck(
            path=path,
            match_mode=replacement.match_mode,
            path_exists=False,
            found_after=False,
            found_before=False,
        )
    return ReplacementCheck(
        path=path,
        match_mode=replacement.match_mode,
        path_exists=True,
        found_after=_contains(content, replacement.after, replacement.match_mode),
        found_before=_contains(content, replacement.before, replacement.match_mode),
    )
