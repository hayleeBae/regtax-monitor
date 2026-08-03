"""결정적 stub 파이프라인 — HISTORICAL_REPLAY_SPEC §11, ADR-011 (Issue #0018).

runner 의 `ReplayPipeline` seam 에 끼우는 **가짜 파이프라인**이다. 임베딩·벡터 DB·추론
백엔드를 쓰지 않고 fixture 와 worktree 파일만으로 초안을 만든다 — 집 환경에서 조립
경로(worktree → 초안 → apply → 골든 → 채점 → 리포트)를 결정적으로 검증하기 위해서다
(CLAUDE.md — 테스트에서 무거운 의존성 금지). 회사에서는 같은 자리에 실제 파이프라인을
주입한다.

## 왜 worktree 파일을 실제로 읽는가

diff 문자열을 손으로 꾸며내면 `git apply --check` 가 통과하는지 여부가 stub 의 작문
실력에 달리고, 지표 `git_apply` 가 아무것도 증명하지 못한다. 그래서 base 시점 파일
내용을 읽어 교체를 적용한 뒤 `difflib` 로 unified diff 를 만든다 — 실제로 적용
가능한 patch 다.

## 세 변형

- `perfect_pipeline` — 기대 교체를 전부 수행한다(지표 만점).
- `partial_pipeline` — 파일당 첫 교체만 수행한다(coverage·accuracy 가 내려간다).
- `empty_pipeline` — 빈 diff 를 돌려준다(지표 0 부근).

이 stub 은 fixture 의 정답을 그대로 베끼므로 **검색·생성 품질을 측정하지 않는다**.
측정하는 것은 runner 의 조립과 채점이 맞는가 하나다.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Optional, Sequence

from app.evaluation.case import ExpectedReplacement
from app.evaluation.replay.answer_diff import normalize_path
from app.evaluation.replay.fixture import ReplayFixture
from app.evaluation.replay.runner import PipelineOutput, ReplayContext, ReplayPipeline

DIFF_CONTEXT_LINES = 3


# ---------------------------------------------------------------------------
# diff 생성
# ---------------------------------------------------------------------------


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _ensure_trailing_newline(text: str) -> str:
    """마지막 줄 개행을 맞춘다.

    `difflib` 은 "\\ No newline at end of file" 표기를 만들지 않으므로, 개행이 없는
    파일을 그대로 diff 하면 `git apply` 가 거부한다. stub 이 다루는 fixture 코드는
    전부 개행으로 끝나며, 그렇지 않은 파일은 개행을 붙인 형태로 다룬다.
    """
    if not text or text.endswith("\n"):
        return text
    return text + "\n"


def _file_diff(relative_path: str, before_text: str, after_text: str) -> str:
    """한 파일의 unified diff — `git apply -p1` 이 그대로 받는 형식(`a/`·`b/` 접두)."""
    lines = difflib.unified_diff(
        _ensure_trailing_newline(before_text).splitlines(keepends=True),
        _ensure_trailing_newline(after_text).splitlines(keepends=True),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
        n=DIFF_CONTEXT_LINES,
    )
    return "".join(lines)


def _group_by_path(
    replacements: Sequence[ExpectedReplacement],
) -> "dict[str, list[ExpectedReplacement]]":
    grouped: dict = {}
    for item in replacements:
        grouped.setdefault(normalize_path(item.path), []).append(item)
    return grouped


def build_diff(
    worktree: Path,
    replacements: Sequence[ExpectedReplacement],
    *,
    limit: Optional[int] = None,
) -> str:
    """worktree 의 실제 내용에 교체를 적용한 unified diff 를 만든다.

    `limit` 는 케이스 전체에서 수행할 교체 수 상한이다(`None` 이면 전부, `0` 이면 없음).
    파일당이 아니라 **전체 기준**인 이유: mock case2 처럼 파일 하나에 교체가 하나씩
    있는 fixture 에서 파일당 상한은 아무것도 줄이지 못해 `partial` 이 `perfect` 와
    같아진다.

    교체는 단순 부분문자열 치환이다 — `match_mode="normalized_text"` 기대값이라도
    같은 방식으로 시도하고, 원문에 없으면 그 항목은 건너뛴다(초안이 놓친 것으로
    잡히므로 지표에 정직하게 반영된다).
    """
    selected = list(replacements) if limit is None else list(replacements)[:limit]

    sections: list = []
    for relative_path, items in _group_by_path(selected).items():
        target = Path(worktree) / relative_path
        original = _read(target)
        if original is None:
            continue
        updated = original
        for item in items:
            if item.before and item.before in updated:
                updated = updated.replace(item.before, item.after)
        if updated == original:
            continue
        sections.append(_file_diff(relative_path, original, updated))
    return "".join(sections)


# ---------------------------------------------------------------------------
# 파이프라인 변형
# ---------------------------------------------------------------------------


def _fixtures_by_case(fixtures: Sequence[ReplayFixture]) -> dict:
    return {fixture.case_id: fixture for fixture in fixtures}


def _make_pipeline(
    fixtures: Sequence[ReplayFixture], limit: Optional[int]
) -> ReplayPipeline:
    """fixture 를 case_id 로 찾아 초안을 만드는 stub 을 돌려준다.

    `ReplayContext` 에는 scope 가 없다 — 실제 파이프라인은 법령 텍스트와 코드만 보고
    답을 내야 하기 때문이다. stub 은 정답을 알아야 하므로 fixture 목록을 **클로저로**
    들고 있는다.
    """
    known = _fixtures_by_case(fixtures)

    def pipeline(context: ReplayContext) -> PipelineOutput:
        fixture = known.get(context.case_id)
        if fixture is None:
            return PipelineOutput(diff_text="", retrieved_paths=())
        diff_text = build_diff(
            context.worktree,
            fixture.scope.expected_replacements,
            limit=limit,
        )
        return PipelineOutput(
            diff_text=diff_text,
            # 검색 단계 대용 — 정답 경로를 순위 순으로 돌려준다(Recall@K 가 1.0 이 된다).
            retrieved_paths=tuple(
                normalize_path(path) for path in fixture.scope.relevant_paths
            ),
        )

    return pipeline


def perfect_pipeline(fixtures: Sequence[ReplayFixture]) -> ReplayPipeline:
    """기대 교체를 전부 수행하는 stub."""
    return _make_pipeline(fixtures, None)


def partial_pipeline(fixtures: Sequence[ReplayFixture]) -> ReplayPipeline:
    """첫 기대 교체 하나만 수행하는 stub — 기대 교체가 여럿인 케이스에서 지표가 내려간다."""
    return _make_pipeline(fixtures, 1)


def empty_pipeline(fixtures: Sequence[ReplayFixture] = ()) -> ReplayPipeline:
    """아무것도 만들지 않는 stub — 초안 없음이 지표 0 으로 이어지는지 본다."""

    def pipeline(context: ReplayContext) -> PipelineOutput:
        return PipelineOutput(diff_text="", retrieved_paths=())

    return pipeline


STUB_PIPELINES: dict = {
    "perfect": perfect_pipeline,
    "partial": partial_pipeline,
    "empty": empty_pipeline,
}
"""CLI `--stub` 이름 → 파이프라인 팩토리."""


def build_stub_pipeline(
    name: str, fixtures: Sequence[ReplayFixture]
) -> ReplayPipeline:
    """이름으로 stub 파이프라인을 만든다 — 알 수 없는 이름은 `KeyError`."""
    return STUB_PIPELINES[name](fixtures)
