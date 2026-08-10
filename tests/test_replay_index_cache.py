"""Issue #0022 replay 인덱스 캐시 테스트 — HISTORICAL_REPLAY_SPEC §6·§11, ADR-012.

고정하는 것은 세 가지다.

1. **키 결정성과 분리** — 스펙 §6 의 네 구성요소 중 하나만 달라져도 다른 인덱스가
   되는가. 특히 인덱서 버전: 청킹 규칙이 바뀐 뒤 옛 인덱스를 재사용하면 측정이
   조용히 틀린다.
2. **경로 봉쇄** — 인덱스가 `evaluation/replay_index/` 밖(특히 운영 벡터DB 디렉토리)을
   가리킬 수 없는가.
3. **캐시 판단과 대상** — 적중 시 인덱싱을 건너뛰는가, 어댑터가 오늘의 repo 가 아니라
   worktree 로 만들어지는가.

임베딩 모델·ChromaDB·LLM 을 띄우지 않는다(CLAUDE.md). 인덱서와 어댑터는 전부 가짜
팩토리로 주입하고, 캐시 루트는 항상 `tmp_path` 다 — 프로젝트 기본 루트를 쓰면 테스트가
실제 캐시 디렉토리를 만든다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.evaluation.replay.index_cache import (
    REPLAY_INDEX_ROOT,
    REPLAY_INDEXER_VERSION,
    ReplayIndexKeyError,
    prepare_index,
    replay_index_dir,
    replay_index_key,
)

REPO_ID = "historical_tax_2024_child_credit:0123456789abcdef"
BASE_COMMIT = "abc123def456"
MODEL = "BAAI/bge-m3"


# ---------------------------------------------------------------------------
# 가짜 인덱서·어댑터 (무거운 의존성 대체)
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
        return 1


class FakeAdapter:
    def __init__(self, repo_root: str, indexer):
        self.repo_root = repo_root
        self.indexer = indexer


def make_factories(doc_count: int = 0):
    """(indexer_factory, adapter_factory, 기록) — 생성 인자를 캡처한다."""
    calls: dict = {"indexers": [], "adapters": []}

    def indexer_factory(persist_dir: str):
        indexer = FakeIndexer(persist_dir, doc_count=doc_count)
        calls["indexers"].append(indexer)
        return indexer

    def adapter_factory(repo_root: str, indexer):
        adapter = FakeAdapter(repo_root, indexer)
        calls["adapters"].append(adapter)
        return adapter

    return indexer_factory, adapter_factory, calls


# ---------------------------------------------------------------------------
# 캐시 키 (스펙 §6)
# ---------------------------------------------------------------------------


def test_key_is_deterministic():
    first = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)
    second = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)
    assert first == second


def test_key_changes_with_base_commit():
    assert replay_index_key(REPO_ID, BASE_COMMIT, MODEL) != replay_index_key(
        REPO_ID, "fedcba987654", MODEL
    )


def test_key_changes_with_embedding_model():
    assert replay_index_key(REPO_ID, BASE_COMMIT, MODEL) != replay_index_key(
        REPO_ID, BASE_COMMIT, "intfloat/multilingual-e5-large"
    )


def test_key_changes_with_indexer_version():
    """청킹 규칙이 바뀌면 기존 인덱스를 재사용하면 안 된다."""
    assert replay_index_key(REPO_ID, BASE_COMMIT, MODEL) != replay_index_key(
        REPO_ID, BASE_COMMIT, MODEL, indexer_version="replay-index-v2"
    )


def test_key_changes_with_repo_id():
    assert replay_index_key(REPO_ID, BASE_COMMIT, MODEL) != replay_index_key(
        "other_case:fedcba9876543210", BASE_COMMIT, MODEL
    )


def test_key_boundaries_are_unambiguous():
    """구성요소 경계가 뭉개지지 않는지 — 이어 붙이기만 하면 두 조합이 같은 키가 된다."""
    assert replay_index_key("ab", "c", MODEL) != replay_index_key("a", "bc", MODEL)


def test_key_is_hash_shaped_and_carries_no_path():
    """키에 절대경로 조각이 섞이면 캐시 디렉토리 이름으로 회사 경로가 샌다(ADR-010)."""
    key = replay_index_key(
        f"{REPO_ID}", BASE_COMMIT, MODEL, indexer_version=REPLAY_INDEXER_VERSION
    )
    assert len(key) == 16
    assert all(char in "0123456789abcdef" for char in key)
    assert "/" not in key
    assert "historical_tax" not in key


def test_key_rejects_empty_component():
    with pytest.raises(ReplayIndexKeyError):
        replay_index_key(REPO_ID, "", MODEL)


# ---------------------------------------------------------------------------
# 인덱스 디렉토리 — 루트 봉쇄
# ---------------------------------------------------------------------------


def test_dir_is_under_root(tmp_path: Path):
    key = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)
    target = replay_index_dir(key, tmp_path)
    assert target.parent == tmp_path
    assert tmp_path in target.resolve().parents


@pytest.mark.parametrize(
    "key",
    ["../../etc", "..", ".", "a/b", "/abs", "sub/../..", "", "key with space"],
)
def test_dir_rejects_keys_that_escape_root(tmp_path: Path, key: str):
    with pytest.raises(ReplayIndexKeyError):
        replay_index_dir(key, tmp_path)


def test_default_root_is_replay_index_root(tmp_path: Path):
    """기본 루트는 운영 벡터DB 가 아니라 `evaluation/replay_index/` 다 (ADR-012)."""
    key = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)
    target = replay_index_dir(key)
    assert REPLAY_INDEX_ROOT.as_posix() in target.as_posix()
    assert "chroma_data" not in target.as_posix()


def test_prepare_index_never_returns_operational_chroma_path(tmp_path: Path):
    """운영 인덱스를 덮어쓰지 않는다 — 재인덱싱 수십 분 + 개발 서버 검색 오염."""
    indexer_factory, adapter_factory, calls = make_factories()
    key = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)

    prepare_index(
        tmp_path / "worktree",
        key,
        root=tmp_path / "cache",
        indexer_factory=indexer_factory,
        adapter_factory=adapter_factory,
    )

    persist_dir = calls["indexers"][0].persist_dir
    assert "chroma_data" not in persist_dir
    assert str(tmp_path / "cache") in persist_dir


# ---------------------------------------------------------------------------
# 캐시 판단 (스펙 §6)
# ---------------------------------------------------------------------------


def test_prepare_index_indexes_when_cache_is_empty(tmp_path: Path):
    indexer_factory, adapter_factory, calls = make_factories(doc_count=0)
    key = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)

    indexer, reused = prepare_index(
        tmp_path / "worktree",
        key,
        root=tmp_path / "cache",
        indexer_factory=indexer_factory,
        adapter_factory=adapter_factory,
    )

    assert reused is False
    assert len(indexer.indexed) == 1
    assert indexer.indexed[0] is calls["adapters"][0]


def test_prepare_index_reuses_populated_cache(tmp_path: Path):
    """디렉토리가 있고 문서가 있으면 인덱싱을 건너뛴다."""
    root = tmp_path / "cache"
    key = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)
    (root / key).mkdir(parents=True)

    indexer_factory, adapter_factory, calls = make_factories(doc_count=42)
    indexer, reused = prepare_index(
        tmp_path / "worktree",
        key,
        root=root,
        indexer_factory=indexer_factory,
        adapter_factory=adapter_factory,
    )

    assert reused is True
    assert indexer.indexed == []
    assert calls["adapters"] == []


def test_prepare_index_reindexes_when_directory_exists_but_is_empty(tmp_path: Path):
    """중단된 첫 인덱싱의 잔해 — 빈 인덱스를 재사용하면 케이스가 통째로 0점이 된다."""
    root = tmp_path / "cache"
    key = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)
    (root / key).mkdir(parents=True)

    indexer_factory, adapter_factory, _ = make_factories(doc_count=0)
    indexer, reused = prepare_index(
        tmp_path / "worktree",
        key,
        root=root,
        indexer_factory=indexer_factory,
        adapter_factory=adapter_factory,
    )

    assert reused is False
    assert len(indexer.indexed) == 1


def test_prepare_index_second_run_reuses_first(tmp_path: Path):
    """같은 키로 두 번 — 두 번째는 캐시 적중이다(회사 실행이 완주하는 전제)."""
    root = tmp_path / "cache"
    key = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)

    empty_factory, adapter_factory, _ = make_factories(doc_count=0)
    _, first_reused = prepare_index(
        tmp_path / "worktree",
        key,
        root=root,
        indexer_factory=empty_factory,
        adapter_factory=adapter_factory,
    )

    filled_factory, adapter_factory2, calls = make_factories(doc_count=7)
    _, second_reused = prepare_index(
        tmp_path / "worktree",
        key,
        root=root,
        indexer_factory=filled_factory,
        adapter_factory=adapter_factory2,
    )

    assert first_reused is False
    assert second_reused is True
    assert calls["adapters"] == []


# ---------------------------------------------------------------------------
# 인덱싱 대상 — worktree (ADR-012)
# ---------------------------------------------------------------------------


def test_adapter_is_built_with_worktree_as_repo_root(tmp_path: Path):
    """오늘의 repo 를 인덱싱하면 replay 자체가 무의미하다."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    indexer_factory, adapter_factory, calls = make_factories()
    key = replay_index_key(REPO_ID, BASE_COMMIT, MODEL)

    indexer, _ = prepare_index(
        worktree,
        key,
        root=tmp_path / "cache",
        indexer_factory=indexer_factory,
        adapter_factory=adapter_factory,
    )

    adapter = calls["adapters"][0]
    assert adapter.repo_root == str(worktree)
    assert adapter.indexer is indexer


def test_prepare_index_rejects_unsafe_key(tmp_path: Path):
    indexer_factory, adapter_factory, calls = make_factories()
    with pytest.raises(ReplayIndexKeyError):
        prepare_index(
            tmp_path / "worktree",
            "../../chroma_data",
            root=tmp_path / "cache",
            indexer_factory=indexer_factory,
            adapter_factory=adapter_factory,
        )
    assert calls["indexers"] == []
