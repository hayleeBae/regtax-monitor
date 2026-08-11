from pathlib import Path

from app.codebase.base import CodebaseAdapter, CodeHit

PATCHES_DIR = "mock_repo/patches"


class MockCodebaseAdapter(CodebaseAdapter):
    """
    집 개발용: 로컬 mock repo 디렉토리를 대상으로 동작.
    (회사 시스템 구조를 흉내낸 Java/SQL/XML 샘플 — Phase 3에서 작성)
    회사에서는 RealCodebaseAdapter를 같은 인터페이스로 구현해 교체.
    """

    SOURCE_EXTS = {".java", ".sql", ".xml"}

    def __init__(self, repo_root: str, indexer=None):
        self.root = Path(repo_root)
        self.indexer = indexer  # embedding.indexer.CodeIndexer 주입

    def list_files(self) -> list[str]:
        """확장자 필터 + 빌드 산출물 디렉토리 제외 (`EXCLUDED_DIRS`).

        제외는 RealCodebaseAdapter 와 같은 규칙이다 — adapter 만 경유하는 소비자
        (`symbol_index` 등)가 어느 어댑터에서든 산출물 심볼을 보지 않아야 한다.
        `mock_repo/` 에는 해당 디렉토리가 없어 기존 동작은 그대로다.
        """
        files = []
        for p in self.root.rglob("*"):
            if not p.is_file() or p.suffix not in self.SOURCE_EXTS:
                continue
            relative = p.relative_to(self.root)
            if self._is_excluded(relative):
                continue
            files.append(str(relative))
        return files

    def read_file(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def search(self, query: str, k: int = 5) -> list[CodeHit]:
        if self.indexer is None:
            raise RuntimeError("indexer가 주입되지 않았습니다. (Phase 3에서 연결)")
        return self.indexer.search(query, k=k)

    def repository_revision(self) -> str | None:
        return "fixture:mock_repo-v1"

    def apply_patch(self, proposal_id: int, diff: str) -> str:
        """
        승인된 patch를 mock_repo/patches/ 에 파일로 저장한다.
        실제 repo 연동 시 git apply 또는 PR 생성으로 교체.
        """
        out_dir = Path(PATCHES_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"proposal_{proposal_id}.patch"
        out_path.write_text(diff, encoding="utf-8")
        return str(out_path)
