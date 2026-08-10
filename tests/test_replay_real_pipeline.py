"""Issue #0022 replay 실제 파이프라인 테스트 — HISTORICAL_REPLAY_SPEC §4·§6, ADR-012.

고정하는 것은 네 가지다.

1. **look-ahead 차단** — 검증 매핑 provider 가 구성되지 않고, reranker 가 주입되지 않고,
   `rerank_enabled=False` 로 넘어가고, 소스에 DB import 가 없는가. 이 파이프라인의
   존재 이유이자, 깨져도 테스트 없이는 눈에 띄지 않는 유일한 항목이다(지표만 조용히
   올라간다).
2. **보는 코드가 worktree 인가** — 어댑터·사전·상수 provider 의 `repo_root` 가 전부
   `context.worktree` 인가. 오늘의 repo 를 보면 과거 시점 재현이 아니다.
3. **조립** — 검색 순위대로 `retrieved_paths` 가 나오고 그 파일들이 초안 컨텍스트로
   들어가는가, 인덱스 캐시 적중 시 재인덱싱하지 않는가.
4. **실패를 삼키지 않는가** — 추론 백엔드 실패가 그대로 올라가는가(runner 가
   `pipeline_failed` 로 격리한다).

임베딩·ChromaDB·추론 백엔드를 띄우지 않는다(CLAUDE.md). 인덱서·어댑터·LLM 은 전부 가짜
팩토리로 주입하고, 사전·상수 provider 도 대역으로 바꾼다 — 진짜 provider 는 프로젝트
루트의 전역 캐시 파일(`term_dict_cache.json` 등)을 읽고 쓰므로 테스트가 개발 환경의
캐시를 건드리게 된다. 캐시 루트는 항상 `tmp_path` 다.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.codebase.base import CodeHit
from app.domain.common.enums import RetrievalSource
from app.evaluation.case import LawInput
from app.evaluation.replay import real_pipeline
from app.evaluation.replay.index_cache import replay_index_key
from app.evaluation.replay.runner import PipelineOutput, ReplayContext
from app.retrieval.orchestrator import ProviderResult, RetrievalConfig
from config import settings

CASE_ID = "historical_tax_2024_child_credit"
REPO_ID = f"{CASE_ID}:0123456789abcdef"
BASE_COMMIT = "abc123def456"

LAW = LawInput(
    law_name="소득세법",
    tier="law",
    before_text="자녀세액공제 금액은 1명당 연 15만원으로 한다.",
    after_text="자녀세액공제 금액은 1명당 연 25만원으로 한다.",
    article="제59조의2",
)

FILES = {
    "src/TaxCalculator.java": "class TaxCalculator {\n    long credit = 150000L;\n}\n",
    "src/mapper/tax.xml": "<select>SELECT n0200 FROM tax</select>\n",
    "src/vo/TaxVo.java": "class TaxVo {\n    Long n0200;\n}\n",
}


# ---------------------------------------------------------------------------
# 대역 (무거운 의존성 대체)
# ---------------------------------------------------------------------------


class FakeCollection:
    def __init__(self, count: int):
        self._count = count

    def count(self) -> int:
        return self._count


class FakeIndexer:
    """`CodeIndexer` 대역 — `collection.count()` 와 `index(adapter)` 만 흉내 낸다."""

    def __init__(self, persist_dir: str, doc_count: int = 0):
        self.persist_dir = persist_dir
        self.collection = FakeCollection(doc_count)
        self.indexed: list = []

    def index(self, adapter) -> int:
        self.indexed.append(adapter)
        return len(FILES)


class FakeAdapter:
    """`RealCodebaseAdapter` 대역 — 검색은 미리 준 hit 를, 읽기는 `FILES` 를 돌려준다."""

    def __init__(self, repo_root: str, indexer, hits=()):
        self.repo_root = repo_root
        self.indexer = indexer
        self.hits = list(hits)
        self.searched: list = []
        self.read: list = []

    def search(self, query: str, k: int = 5):
        self.searched.append((query, k))
        return self.hits[:k]

    def read_file(self, path: str) -> str:
        self.read.append(path)
        try:
            return FILES[path]
        except KeyError:
            raise FileNotFoundError(path)


class FakeLlm:
    """편집 블록을 돌려주는 LLM 대역 — 진짜 `propose_and_build` 를 그대로 태운다."""

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


class NullProvider:
    """사전·상수 provider 대역 — 생성 인자만 기록하고 후보를 내지 않는다."""

    instances: list = []

    def __init__(self, repo_root: str, refresh_cache: bool = False):
        self.repo_root = repo_root
        self.refresh_cache = refresh_cache
        type(self).instances.append(self)

    def retrieve(self, query) -> ProviderResult:
        return ProviderResult(self.source, (), (), 0)


class NullDictionaryProvider(NullProvider):
    source = RetrievalSource.TERM_DICTIONARY
    version = "fake-dictionary"
    instances: list = []


class NullConstantProvider(NullProvider):
    source = RetrievalSource.CONSTANT_MATCH
    version = "fake-constant"
    instances: list = []


EDIT_RESPONSE = (
    "@@@FILE: src/TaxCalculator.java\n"
    "@@@SEARCH\n    long credit = 150000L;\n"
    "@@@REPLACE\n    long credit = 250000L;\n"
    "@@@END\n"
)


# ---------------------------------------------------------------------------
# 조립 헬퍼
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _null_side_providers(monkeypatch):
    """사전·상수 provider 를 대역으로 바꾼다 — 전역 캐시 파일 접근을 막는다."""
    NullDictionaryProvider.instances = []
    NullConstantProvider.instances = []
    monkeypatch.setattr(real_pipeline, "DictionaryProvider", NullDictionaryProvider)
    monkeypatch.setattr(real_pipeline, "ConstantProvider", NullConstantProvider)


def make_context(tmp_path: Path) -> ReplayContext:
    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)
    return ReplayContext(
        case_id=CASE_ID,
        worktree=worktree,
        repo_id=REPO_ID,
        base_commit=BASE_COMMIT,
        law=LAW,
        timeout_seconds=60,
    )


def make_factories(hits=(), doc_count: int = 0):
    """(indexer_factory, adapter_factory, 기록) 한 벌."""
    made: dict = {"indexers": [], "adapters": []}

    def indexer_factory(persist_dir: str):
        indexer = FakeIndexer(persist_dir, doc_count=doc_count)
        made["indexers"].append(indexer)
        return indexer

    def adapter_factory(repo_root: str, indexer):
        adapter = FakeAdapter(repo_root, indexer, hits=hits)
        made["adapters"].append(adapter)
        return adapter

    return indexer_factory, adapter_factory, made


def default_hits():
    return (
        CodeHit("src/TaxCalculator.java", "credit", "long credit = 150000L;", 0.9),
        CodeHit("src/mapper/tax.xml", "n0200", "SELECT n0200", 0.8),
        # 같은 파일의 다른 심볼 — 경로 단위 지표에서 한 자리만 차지해야 한다.
        CodeHit("src/TaxCalculator.java", "calc", "long calc()", 0.7),
        CodeHit("src/vo/TaxVo.java", "n0200", "Long n0200;", 0.6),
    )


def run_pipeline(tmp_path: Path, *, llm=None, hits=None, doc_count=0, top_k=5):
    indexer_factory, adapter_factory, made = make_factories(
        hits=default_hits() if hits is None else hits, doc_count=doc_count
    )
    llm = llm or FakeLlm(EDIT_RESPONSE)
    pipeline = real_pipeline.build_real_pipeline(
        index_root=tmp_path / "index",
        top_k=top_k,
        llm_factory=lambda: llm,
        indexer_factory=indexer_factory,
        adapter_factory=adapter_factory,
    )
    context = make_context(tmp_path)
    return pipeline(context), made, llm, context


# ---------------------------------------------------------------------------
# 1) look-ahead 차단 (ADR-012)
# ---------------------------------------------------------------------------


class CaptureOrchestrator:
    """`RetrievalOrchestrator` 대역 — 넘어온 provider·reranker·config 를 붙잡는다."""

    captured: dict = {}

    def __init__(self, providers, reranker=None):
        type(self).captured["providers"] = tuple(providers)
        type(self).captured["reranker"] = reranker

    def retrieve(self, query, config=None):
        type(self).captured["query"] = query
        type(self).captured["config"] = config
        from app.retrieval.orchestrator import RetrievalResponse

        return RetrievalResponse((), {}, "v", query.query_hash, None, (), 0)


def capture_retrieval(tmp_path: Path, monkeypatch, top_k: int = 5) -> dict:
    CaptureOrchestrator.captured = {}
    monkeypatch.setattr(real_pipeline, "RetrievalOrchestrator", CaptureOrchestrator)
    run_pipeline(tmp_path, top_k=top_k)
    return CaptureOrchestrator.captured


def test_verified_mapping_provider_is_not_constructed(tmp_path, monkeypatch):
    """검증 매핑은 그 개정을 처리하며 만들어진 사후 자산이라 입력이 될 수 없다."""
    captured = capture_retrieval(tmp_path, monkeypatch)

    sources = {provider.source for provider in captured["providers"]}
    assert RetrievalSource.VERIFIED_MAPPING not in sources
    assert sources == {
        RetrievalSource.RAG,
        RetrievalSource.TERM_DICTIONARY,
        RetrievalSource.CONSTANT_MATCH,
    }


def test_reranker_is_not_injected_and_rerank_is_disabled(tmp_path, monkeypatch):
    """결정 이력 rerank 도 사후 자산이다 — 주입하지 않고, 설정으로도 꺼 둔다."""
    captured = capture_retrieval(tmp_path, monkeypatch)

    assert captured["reranker"] is None
    assert captured["config"].rerank_enabled is False


def test_retrieval_config_uses_requested_top_k(tmp_path, monkeypatch):
    captured = capture_retrieval(tmp_path, monkeypatch, top_k=3)

    assert captured["config"].final_top_k == 3
    assert captured["query"].top_k_per_provider == 3
    # 운영 기본값과 달라지지 않았는지 — rerank 만 끄고 나머지는 그대로다.
    assert captured["config"].weights == RetrievalConfig().weights


def test_query_context_comes_from_law_input(tmp_path, monkeypatch):
    """조문 식별자·개정 유형은 DB 가 아니라 `ctx.law` 에서 나온다."""
    captured = capture_retrieval(tmp_path, monkeypatch)
    query = captured["query"]

    assert query.article_id == f"{CASE_ID}:제59조의2"
    assert query.change_type == "value_change"
    assert LAW.law_name in query.text
    assert LAW.after_text in query.text
    assert query.repository_commit == BASE_COMMIT


def test_source_has_no_database_imports():
    """DB 계층을 통과하지 않는다 — import 자체가 없어야 한다(ADR-012)."""
    source = Path(real_pipeline.__file__).read_text(encoding="utf-8")

    for token in (
        "app.db",
        "sqlalchemy",
        "Session",
        "VerifiedMappingProvider",
        "MappingDecision",
        "get_session",
    ):
        assert token not in source, f"replay 파이프라인에 {token} 이 등장한다"


def test_provider_and_adapter_repo_root_is_worktree(tmp_path):
    """오늘의 repo(`settings.repo_root`)가 아니라 과거 시점 worktree 를 본다."""
    _, made, _, context = run_pipeline(tmp_path)

    expected = str(context.worktree)
    # 인덱싱용·검색용 두 어댑터가 만들어지며 둘 다 worktree 를 봐야 한다.
    assert made["adapters"]
    assert {adapter.repo_root for adapter in made["adapters"]} == {expected}
    assert NullDictionaryProvider.instances[0].repo_root == expected
    assert NullConstantProvider.instances[0].repo_root == expected
    assert expected != settings.repo_root


# ---------------------------------------------------------------------------
# 2) 조립 — 검색 순위·스니펫·초안
# ---------------------------------------------------------------------------


def test_returns_pipeline_output_with_ranked_paths(tmp_path):
    output, _made, _llm, _context = run_pipeline(tmp_path)

    assert isinstance(output, PipelineOutput)
    # 점수 순 + 같은 파일 중복 제거.
    assert output.retrieved_paths == (
        "src/TaxCalculator.java",
        "src/mapper/tax.xml",
        "src/vo/TaxVo.java",
    )


def test_snippets_and_law_diff_are_built_from_context(tmp_path):
    output, _made, llm, _context = run_pipeline(tmp_path)

    call = llm.calls[0]
    assert [snippet.splitlines()[0] for snippet in call["code_snippets"]] == [
        f"// {path}" for path in output.retrieved_paths
    ]
    assert LAW.law_name in call["law_diff"]
    assert LAW.before_text in call["law_diff"]
    assert LAW.after_text in call["law_diff"]


def test_diff_text_comes_from_anchor_edits(tmp_path):
    output, _made, _llm, _context = run_pipeline(tmp_path)

    assert "--- a/src/TaxCalculator.java" in output.diff_text
    assert "-    long credit = 150000L;" in output.diff_text
    assert "+    long credit = 250000L;" in output.diff_text


def test_empty_draft_stays_empty(tmp_path):
    """초안이 비면 빈 문자열 그대로다 — 안내 주석을 채우면 `git apply` 지표가 뒤집힌다."""
    output, _made, _llm, _context = run_pipeline(tmp_path, llm=FakeLlm("편집 없음"))

    assert output.diff_text == ""
    assert output.retrieved_paths


def test_unreadable_candidate_is_skipped(tmp_path):
    """후보 파일 하나를 못 읽어도 나머지로 초안을 시도한다."""
    hits = (
        CodeHit("src/TaxCalculator.java", "credit", "long credit = 150000L;", 0.9),
        CodeHit("src/gone.java", "x", "", 0.5),
    )
    output, _made, llm, _context = run_pipeline(tmp_path, hits=hits)

    assert "src/gone.java" in output.retrieved_paths
    assert [snippet.splitlines()[0] for snippet in llm.calls[0]["code_snippets"]] == [
        "// src/TaxCalculator.java"
    ]


# ---------------------------------------------------------------------------
# 3) 인덱스 캐시 (스펙 §6)
# ---------------------------------------------------------------------------


def test_cache_miss_indexes_the_worktree(tmp_path):
    _output, made, _llm, context = run_pipeline(tmp_path, doc_count=0)

    indexer = made["indexers"][0]
    assert len(indexer.indexed) == 1
    assert indexer.indexed[0].repo_root == str(context.worktree)
    # 운영 벡터DB 가 아니라 replay 캐시 루트에 쓴다.
    assert indexer.persist_dir.startswith(str(tmp_path / "index"))


def test_cache_hit_skips_reindexing(tmp_path):
    key = replay_index_key(REPO_ID, BASE_COMMIT, settings.embedding_model)
    (tmp_path / "index" / key).mkdir(parents=True)

    _output, made, _llm, _context = run_pipeline(tmp_path, doc_count=7)

    assert made["indexers"][0].indexed == []


# ---------------------------------------------------------------------------
# 4) 실패 전파 (스펙 §9)
# ---------------------------------------------------------------------------


def test_llm_failure_propagates(tmp_path):
    """runner 가 `pipeline_failed` 로 격리해야 한다 — 여기서 빈 diff 로 뭉개지 않는다."""
    failing = FakeLlm(error=RuntimeError("추론 백엔드에 연결할 수 없습니다"))

    with pytest.raises(RuntimeError):
        run_pipeline(tmp_path, llm=failing)


def test_search_failure_propagates(tmp_path):
    """모든 provider 가 실패하면 orchestrator 의 `RetrievalError` 가 그대로 올라간다."""

    class BrokenAdapter(FakeAdapter):
        def search(self, query: str, k: int = 5):
            raise RuntimeError("index unavailable")

    def adapter_factory(repo_root: str, indexer):
        return BrokenAdapter(repo_root, indexer)

    def indexer_factory(persist_dir: str):
        return FakeIndexer(persist_dir, doc_count=1)

    pipeline = real_pipeline.build_real_pipeline(
        index_root=tmp_path / "index",
        llm_factory=lambda: FakeLlm(EDIT_RESPONSE),
        indexer_factory=indexer_factory,
        adapter_factory=adapter_factory,
    )
    # 사전·상수 provider 대역은 성공하므로 전체 실패는 아니다 — 검색은 계속되고
    # 후보만 비어야 한다(스펙 §9 의 provider 격리).
    output = pipeline(make_context(tmp_path))
    assert output.retrieved_paths == ()


def test_pipeline_is_reusable_across_cases(tmp_path):
    """같은 파이프라인 객체로 여러 케이스를 돌려도 상태가 섞이지 않는다."""
    indexer_factory, adapter_factory, made = make_factories(hits=default_hits())
    pipeline = real_pipeline.build_real_pipeline(
        index_root=tmp_path / "index",
        llm_factory=lambda: FakeLlm(EDIT_RESPONSE),
        indexer_factory=indexer_factory,
        adapter_factory=adapter_factory,
    )
    first = pipeline(make_context(tmp_path))
    second = pipeline(make_context(tmp_path))

    assert first.retrieved_paths == second.retrieved_paths
    assert len(made["adapters"]) >= 2


def test_law_diff_text_has_no_paths():
    """프롬프트 본문에 경로가 실리지 않는다 — 법령 텍스트만 들어간다(ADR-010)."""
    text = real_pipeline.law_diff_text(LAW)

    assert "/" not in text.replace("[개정 전]", "").replace("[개정 후]", "")
    assert text.startswith("[법령] 소득세법 제59조의2")


def test_index_preparation_does_not_touch_operational_store(tmp_path):
    """운영 `chroma_data` 경로를 만들지도 쓰지도 않는다."""
    before = time.time()
    run_pipeline(tmp_path)

    operational = Path("./chroma_data")
    assert not (
        operational.exists() and operational.stat().st_mtime > before
    ), "운영 벡터DB 디렉토리가 replay 실행으로 변경되었다"
