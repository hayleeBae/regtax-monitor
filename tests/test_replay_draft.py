"""Issue #0022 replay 초안 생성 — ADR-012 보강.

초안 생성(`propose_and_build` 를 태우는 부분)은 계층 가드 때문에 `app/evaluation/`
밖(`app/application/replay_draft.py`)으로 빠졌다. 이 파일이 그 동작을 고정한다:
법령 diff + 코드 스니펫 + read_file → 앵커 편집 → unified diff.

추론 백엔드는 가짜(`FakeLlm`)로 주입한다 — `build_replay_draft_fn(llm=...)`. 실제
네트워크·모델은 띄우지 않는다(CLAUDE.md).
"""

from __future__ import annotations

from app.application.replay_draft import build_replay_draft_fn

FILES = {
    "src/TaxCalculator.java": "class TaxCalculator {\n    long credit = 150000L;\n}\n",
}

EDIT_RESPONSE = (
    "@@@FILE: src/TaxCalculator.java\n"
    "@@@SEARCH\n    long credit = 150000L;\n"
    "@@@REPLACE\n    long credit = 250000L;\n"
    "@@@END\n"
)

LAW_DIFF = "[법령] 소득세법 제59조의2\n\n[개정 전]\n15만원\n\n[개정 후]\n25만원"


class FakeLlm:
    """편집 블록을 돌려주는 추론 백엔드 대역 — 진짜 `propose_and_build` 를 태운다."""

    def __init__(self, response: str = "", error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list = []

    def propose_edits(self, law_diff: str, code_snippets: list) -> str:
        self.calls.append({"law_diff": law_diff, "code_snippets": list(code_snippets)})
        if self.error is not None:
            raise self.error
        return self.response

    def complete(self, prompt: str, max_tokens: int = 2048) -> str:  # pragma: no cover
        return ""


def _read_file(path: str) -> str:
    return FILES[path]


def _snippets():
    return [f"// src/TaxCalculator.java\n{FILES['src/TaxCalculator.java']}"]


def test_returns_unified_diff_from_anchor_edits():
    draft = build_replay_draft_fn(llm=FakeLlm(EDIT_RESPONSE))
    diff = draft(LAW_DIFF, _snippets(), _read_file)

    assert "--- a/src/TaxCalculator.java" in diff
    assert "-    long credit = 150000L;" in diff
    assert "+    long credit = 250000L;" in diff


def test_law_diff_and_snippets_reach_the_backend():
    llm = FakeLlm(EDIT_RESPONSE)
    draft = build_replay_draft_fn(llm=llm)
    draft(LAW_DIFF, _snippets(), _read_file)

    call = llm.calls[0]
    assert call["law_diff"] == LAW_DIFF
    assert call["code_snippets"] == _snippets()


def test_empty_edits_produce_empty_diff():
    draft = build_replay_draft_fn(llm=FakeLlm("편집 없음"))
    diff = draft(LAW_DIFF, _snippets(), _read_file)

    assert diff == ""


def test_backend_failure_propagates():
    """추론 백엔드 실패는 삼키지 않는다 — replay runner 가 pipeline_failed 로 격리한다."""
    draft = build_replay_draft_fn(
        llm=FakeLlm(error=RuntimeError("추론 백엔드에 연결할 수 없습니다"))
    )

    try:
        draft(LAW_DIFF, _snippets(), _read_file)
    except RuntimeError:
        pass
    else:
        raise AssertionError("추론 백엔드 예외가 전파되어야 한다")
