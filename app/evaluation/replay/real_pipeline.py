"""replay 실제 파이프라인 — HISTORICAL_REPLAY_SPEC §4·§6, ADR-012 (Issue #0022).

`#0018` 이 만든 `ReplayPipeline` seam(`ReplayContext` → `PipelineOutput`)에 실제
인덱싱·검색·초안 생성을 끼운다. stub 이 fixture 의 정답을 베껴 조립 경로만 검증한다면,
이쪽은 **법령 변경과 그 시점 코드만 보고** 초안을 만든다 — replay 가 재려는 것이 바로
그것이다.

## 검증 자산을 입력으로 쓰지 않는다 (ADR-012 — 이 모듈의 존재 이유)

검증 매핑 provider 를 구성하지 않고, reranker 도 주입하지 않으며
`RetrievalConfig(rerank_enabled=False)` 로 rerank 단계를 명시적으로 끈다. DB 계층은
아예 거치지 않는다(세션·매핑 행·결정 이력 import 없음).

이유: 검증 매핑과 결정 이력은 **그 개정을 처리하면서 사람이 만든 사후 자산**이다.
과거 시점을 재현하면서 그것을 입력으로 주면 정답을 보고 정답을 맞히는 것이 되어
지표가 부풀려지고, 그 숫자로 `#0019·#0020` 의 효과를 판단하면 방향이 틀어진다.
따라서 replay 가 재는 것은 "verified 자산 없는 순수 검색·생성 성능"이다.

## 보는 코드는 언제나 worktree 다

어댑터와 사전·상수 provider 의 `repo_root` 는 전부 `context.worktree` 다.
`settings.repo_root`(오늘의 코드)를 한 군데라도 넘기면 과거 시점 재현이 아니게 된다.
인덱스도 `index_cache.prepare_index` 의 `evaluation/replay_index/<key>/` 만 쓴다 —
운영 `chroma_data/` 는 읽지도 쓰지도 않는다.

### 남아 있는 look-ahead (기록만 하고 고치지 않는다)

용어 사전·상수 인벤토리는 프로젝트 루트의 전역 캐시 파일(`term_dict_cache.json` 등)을
공유하며 repo 별로 나뉘어 있지 않다. 캐시가 이미 있으면 그 내용은 "오늘의 repo" 에서
수확된 것일 수 있다. `refresh_cache=True` 로 강제 재수확하는 선택지는 **더 나쁘다** —
과거 시점에서 수확한 사전을 전역 캐시에 덮어써 운영 검색까지 과거 코드로 오염시킨다.
`index_cache` 가 `CodeIndexer.term_dict` 에 대해 남긴 판단과 같다: 컬럼코드→한글명
매핑은 법령 개정과 무관하게 거의 변하지 않아 지표를 뒤집을 크기가 아니고, 시그니처를
바꾸는 일은 운영 인덱싱 경로에 회귀 위험을 만든다(CLAUDE.md — 동작 보존 우선).

## 미해결 — 계층 가드와의 충돌 (사람 판단 필요)

`tests/test_evaluation.py::test_evaluation_layer_has_no_forbidden_imports`(#0004)는
`app/evaluation/` 아래 모든 파일에서 `app.llm` import 를 문자열로 금지한다. ADR-012 는
이 모듈이 `propose_and_build` 로 초안을 만들도록 정했으므로 둘이 정면으로 부딪힌다.
여기서는 **가드를 우회하지 않았다** — `importlib` 이나 `from app import llm` 같은
문자열 회피는 의존을 숨길 뿐 없애지 못한다. 대신 두 곳의 import 를 전부 함수 안으로
내려 `import app.evaluation...` 만으로는 추론 백엔드가 딸려 오지 않게 했다(운영
`apply` 엔드포인트와 같은 방식). 가드를 ADR-012 에 맞춰 좁힐지, 초안 생성 진입점을
`app/evaluation/` 밖으로 뺄지는 사람이 정한다.

## 실패는 삼키지 않는다

추론 백엔드 미기동·타임아웃·응답 파싱 실패는 예외를 그대로 올린다. runner 가
`failure_kind="pipeline_failed"` 로 그 케이스만 격리한다(스펙 §9). 여기서 잡아 빈 diff 를
돌려주면 "파이프라인이 죽었다"와 "초안이 아무것도 찾지 못했다"가 리포트에서 같은
모습이 되어 지표가 거짓이 된다.

또한 운영 `apply` 엔드포인트와 달리 빈 diff 에 안내 주석을 채우거나 경고 머리말을
붙이지 않는다. 그런 텍스트는 patch 가 아니면서 `git apply --check` 를 타므로 지표 §7 의
"diff 없음(None)"이 "apply 실패(False)"로 둔갑한다. 경고는 로그로만 남긴다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from app.domain.changes.classification import RuleChangeClassifier
from app.domain.changes.normalization import ChangeNormalizer
from app.evaluation.case import LawInput
from app.evaluation.replay.answer_diff import normalize_path
from app.evaluation.replay.index_cache import prepare_index, replay_index_key
from app.evaluation.replay.runner import PipelineOutput, ReplayContext, ReplayPipeline
from app.retrieval.orchestrator import (
    RetrievalConfig,
    RetrievalOrchestrator,
    RetrievalQuery,
)
from app.retrieval.providers import ConstantProvider, DictionaryProvider, RagProvider
from config import settings

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
"""검색 상위 몇 건을 초안 컨텍스트로 넘길지 — 운영 `apply` 경로와 같은 값."""


# ---------------------------------------------------------------------------
# 기본 팩토리 (지연 import — ADR-011·ADR-012)
# ---------------------------------------------------------------------------


def _default_llm_factory() -> Any:
    """기본 LLM — `get_llm_client()` (설정된 백엔드).

    함수 안에서 import 하는 이유는 `index_cache` 와 같다: 이 모듈을 import 하는 것만으로
    추론 백엔드 구현이 딸려 오지 않게 한다.
    """
    from app.llm import get_llm_client

    return get_llm_client()


def _default_adapter_factory(repo_root: str, indexer: Any) -> Any:
    """기본 어댑터 — `RealCodebaseAdapter(repo_root=<worktree>, indexer=...)`.

    코드베이스 접근은 `CodebaseAdapter` seam 을 통해서만 한다(CLAUDE.md). 빌드 산출물
    제외 목록(`EXCLUDED_DIRS`)도 그대로 따라온다.
    """
    from app.codebase.real_adapter import RealCodebaseAdapter

    return RealCodebaseAdapter(repo_root=repo_root, indexer=indexer)


# ---------------------------------------------------------------------------
# 입력 구성
# ---------------------------------------------------------------------------


def _article_id(context: ReplayContext) -> str:
    """검색 문맥용 조문 식별자.

    운영 경로는 `law_id:article_no`(DB 값)를 쓰지만 replay 에는 DB 가 없다. case_id 와
    조문 번호로 케이스 안에서 안정적인 값을 만든다 — 경로는 들어가지 않는다(ADR-010).
    """
    article = (context.law.article or "").strip()
    return f"{context.case_id}:{article}" if article else context.case_id


def _change_type(law: LawInput) -> str:
    """개정 유형 — 운영과 같은 규칙 분류기로 **법령 텍스트에서** 도출한다.

    저장된 `LawChange.change_type` 을 읽어 오는 대신 여기서 분류하는 이유: 그 값은 DB
    에 있고, replay 의 입력은 "법령 변경 + 그 시점 코드"뿐이다. 분류기는 정규화된
    before/after 만 보는 규칙 기반이라 사후 정보가 섞이지 않는다.
    """
    normalized = ChangeNormalizer().normalize(law.before_text or "", law.after_text or "")
    return RuleChangeClassifier().classify(normalized).primary_type.value


def _query_text(law: LawInput) -> str:
    """검색 질의 — 운영 `apply` 와 같은 구성(법령명·조문·개정 전후).

    AI 요약·영향 분석은 넣지 않는다. replay 에는 그 필드가 없고, 있다 해도 사후에
    생성된 텍스트다.
    """
    return " ".join(
        part
        for part in (law.law_name, law.article, law.before_text, law.after_text)
        if part
    )


def law_diff_text(law: LawInput) -> str:
    """초안 프롬프트에 넣는 법령 변경 본문 — `ctx.law` 만으로 구성한다."""
    header = " ".join(part for part in (law.law_name, law.article) if part)
    return (
        f"[법령] {header}\n\n"
        f"[개정 전]\n{law.before_text or '(내용 없음)'}\n\n"
        f"[개정 후]\n{law.after_text or '(내용 없음)'}"
    )


# ---------------------------------------------------------------------------
# 검색·스니펫
# ---------------------------------------------------------------------------


def _ranked_paths(candidates: Iterable[Any]) -> tuple:
    """후보를 순위 순 경로로 바꾼다 — 같은 파일의 중복 후보는 첫 등장만 남긴다.

    지표(Recall@K·MRR)는 파일 단위라 같은 파일이 심볼만 달리해 여러 번 실리면 상위 K
    자리를 한 파일이 차지해 재현율이 실제보다 낮게 보인다. 정규화는 runner·리포트와
    같은 `normalize_path` 를 쓴다.
    """
    paths = [normalize_path(candidate.location.path) for candidate in candidates]
    return tuple(dict.fromkeys(path for path in paths if path))


def _build_snippets(adapter: Any, paths: Sequence[str]) -> list:
    """상위 후보 파일을 worktree 에서 읽어 초안 컨텍스트를 만든다.

    읽기는 `CodebaseAdapter.read_file` 로만 한다(CLAUDE.md seam 규칙). 한 파일을 읽지
    못했다고 케이스를 실패시키지 않는다 — 나머지 후보로 초안을 시도하는 편이 낫고,
    "찾았지만 읽지 못함"은 결과 diff 에 그대로 드러난다.
    """
    snippets: list = []
    for path in paths:
        try:
            snippets.append(f"// {path}\n{adapter.read_file(path)}")
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            logger.warning("replay 후보 파일을 읽지 못했습니다 (건너뜁니다).")
    return snippets


def _retrieve(adapter: Any, context: ReplayContext, top_k: int) -> Any:
    """RAG·용어 사전·상수 세 provider 로만 검색한다.

    **검증 매핑 provider 는 목록에 없고 reranker 도 넘기지 않는다**(ADR-012).
    `rerank_enabled=False` 를 함께 지정하는 것은 중복이 아니라 의도 표시다 — 나중에
    reranker 를 주입하는 변경이 들어와도 replay 는 rerank 없이 돈다.
    """
    providers = (
        RagProvider(lambda text, k: adapter.search(text, k=k)),
        DictionaryProvider(repo_root=str(context.worktree)),
        ConstantProvider(repo_root=str(context.worktree)),
    )
    orchestrator = RetrievalOrchestrator(providers)
    query = RetrievalQuery(
        text=_query_text(context.law),
        repository_commit=context.base_commit,
        top_k_per_provider=top_k,
        article_id=_article_id(context),
        change_type=_change_type(context.law),
    )
    return orchestrator.retrieve(
        query, RetrievalConfig(final_top_k=top_k, rerank_enabled=False)
    )


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------


def build_real_pipeline(
    *,
    index_root: Optional[Path] = None,
    top_k: int = DEFAULT_TOP_K,
    llm_factory: Optional[Callable[..., Any]] = None,
    indexer_factory: Optional[Callable[..., Any]] = None,
    adapter_factory: Optional[Callable[..., Any]] = None,
) -> ReplayPipeline:
    """실제 인덱싱·검색·초안 생성을 하는 `ReplayPipeline` 을 만든다.

    팩토리를 전부 열어 두는 이유는 `index_cache` 와 같다 — 테스트가 임베딩·ChromaDB·
    추론 백엔드를 띄우지 않고 조립과 look-ahead 차단만 검증할 수 있어야 한다
    (CLAUDE.md). `index_root` 는 캐시 루트 override 로, 테스트가 `tmp_path` 를 넘긴다.

    한 케이스의 순서: 인덱스 준비(캐시 재사용) → worktree 어댑터 → 검색 →
    상위 후보 스니펫 → `propose_and_build` → `PipelineOutput`.
    """
    make_llm = llm_factory or _default_llm_factory
    make_adapter = adapter_factory or _default_adapter_factory

    def pipeline(context: ReplayContext) -> PipelineOutput:
        key = replay_index_key(
            context.repo_id, context.base_commit, settings.embedding_model
        )
        indexer, reused = prepare_index(
            context.worktree,
            key,
            root=index_root,
            indexer_factory=indexer_factory,
            # 캐시 미스 때 인덱싱할 어댑터도 같은 팩토리로 만든다 — 인덱싱 대상과
            # 검색 대상이 갈라지지 않게 한다.
            adapter_factory=make_adapter,
        )
        logger.info(
            "replay 인덱스 준비 완료 (case=%s, cache_hit=%s)", context.case_id, reused
        )

        adapter = make_adapter(repo_root=str(context.worktree), indexer=indexer)
        response = _retrieve(adapter, context, top_k)
        if response.warnings:
            # 경고 **본문**은 남기지 않는다 — provider 예외 메시지에 worktree 절대경로가
            # 실려 로그로 새어 나갈 수 있다(ADR-010). 개수만 센다.
            logger.warning(
                "replay 검색 경고 %d건 (case=%s)",
                len(response.warnings),
                context.case_id,
            )

        paths = _ranked_paths(response.candidates)
        snippets = _build_snippets(adapter, paths)
        logger.info(
            "replay 검색 완료 (case=%s, candidates=%d, snippets=%d)",
            context.case_id,
            len(paths),
            len(snippets),
        )

        # 프롬프트·앵커 편집 → unified diff 변환은 백엔드 공용 로직 한 곳에만 있다
        # (CLAUDE.md). 운영 `apply` 와 같은 자리에서 같은 방식으로 지연 import 한다.
        from app.llm.common import propose_and_build

        # 예외를 잡지 않는다 — 실패는 runner 의 `pipeline_failed` 로 올라가야 한다.
        diff_text, warnings, applied, _raw = propose_and_build(
            make_llm(),
            law_diff=law_diff_text(context.law),
            code_snippets=snippets,
            read_file=adapter.read_file,
        )
        logger.info(
            "replay 초안 생성 완료 (case=%s, edits_applied=%d, warnings=%d)",
            context.case_id,
            applied,
            len(warnings),
        )
        return PipelineOutput(diff_text=diff_text, retrieved_paths=paths)

    return pipeline
