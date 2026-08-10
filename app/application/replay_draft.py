"""replay 초안 생성 — 추론 백엔드 의존을 evaluation 밖에 둔다 (ADR-012 보강, Issue #0022).

`app/evaluation/` 아래에서는 생성 스택(추론 백엔드) import 가 계층 가드로 금지된다
(`tests/test_evaluation.py::test_evaluation_layer_has_no_forbidden_imports`, #0004) —
evaluation 은 **측정** 계층이므로 생성 스택에 의존하지 않는다는 규칙이다.

그래서 replay 실제 파이프라인(`app/evaluation/replay/real_pipeline.py`)은 초안 생성을
직접 하지 않고 이 함수를 **주입받는다**. 생성 스택 의존은 여기에만 있다.
`app/application/` 은 이미 `MappingService`·`ProposalService` 가 orchestrator·생성
백엔드를 조합하는 자리라 성격이 맞는다.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

ReplayDraftFn = Callable[[str, Sequence[str], Callable[[str], str]], str]
"""replay 초안 함수 계약 — (law_diff, code_snippets, read_file) → diff_text."""


def build_replay_draft_fn(llm: Optional[Any] = None) -> ReplayDraftFn:
    """법령 diff + 코드 스니펫으로 unified diff 초안을 만드는 함수를 돌려준다.

    `llm` 을 주지 않으면 설정된 백엔드(`get_llm_client()`)를 쓴다. 회사에서
    `LLM_BACKEND=claude` 면 스니펫이 외부로 나가므로 CLI 가 그 분기에서 명시적 opt-in 을
    요구한다(#0022 step 2). 이 함수 자체는 백엔드를 가리지 않는다.

    프롬프트·앵커 편집 → unified diff 변환은 백엔드 공용 로직 한 곳(`propose_and_build`)에만
    있다(CLAUDE.md). 운영 `apply` 엔드포인트와 같은 자리에서 같은 방식으로 지연 import 한다.
    """

    def draft(
        law_diff: str,
        code_snippets: Sequence[str],
        read_file: Callable[[str], str],
    ) -> str:
        from app.llm import get_llm_client
        from app.llm.common import propose_and_build

        client = llm if llm is not None else get_llm_client()
        diff_text, _warnings, _applied, _raw = propose_and_build(
            client,
            law_diff=law_diff,
            code_snippets=list(code_snippets),
            read_file=read_file,
        )
        return diff_text

    return draft
