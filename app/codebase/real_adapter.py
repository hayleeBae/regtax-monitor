import subprocess
import tempfile
from pathlib import Path

from app.codebase.base import CodebaseAdapter, CodeHit
from config import settings


class RealCodebaseAdapter(CodebaseAdapter):
    """
    회사 실제 repo 대상 구현체.
    REPO_ROOT 환경변수로 경로를 지정한다.
    apply_patch()는 git apply로 워킹트리에 직접 반영한다.
    """

    SOURCE_EXTS = {".java", ".sql", ".xml", ".py", ".kt", ".ts", ".tsx", ".js"}

    def __init__(self, repo_root: str, indexer=None):
        self.root = Path(repo_root).resolve()
        self.indexer = indexer

    def list_files(self) -> list[str]:
        index_paths = [
            p.strip() for p in settings.repo_index_paths.split(",") if p.strip()
        ]
        roots = (
            [self.root / p for p in index_paths] if index_paths else [self.root]
        )
        files = []
        for root in roots:
            for p in root.rglob("*"):
                if p.is_file() and p.suffix in self.SOURCE_EXTS and ".git" not in p.parts:
                    files.append(str(p.relative_to(self.root)))
        return files

    def read_file(self, path: str) -> str:
        full_path = self.root / path
        for enc in ("utf-8", "cp949"):
            try:
                return full_path.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
        return full_path.read_text(encoding="utf-8", errors="replace")

    def search(self, query: str, k: int = 5) -> list[CodeHit]:
        if self.indexer is None:
            raise RuntimeError("indexer가 주입되지 않았습니다.")
        return self.indexer.search(query, k=k)

    def find_usages(self, class_name: str, max_results: int = 5) -> list[str]:
        """class_name을 import하거나 직접 참조하는 파일 경로 목록."""
        results = []
        for path in self.list_files():
            if Path(path).suffix not in {".java", ".kt", ".py"}:
                continue
            try:
                text = self.read_file(path)
                if class_name in text:
                    results.append(path)
                    if len(results) >= max_results:
                        break
            except Exception:
                continue
        return results

    def apply_patch(self, proposal_id: int, diff: str) -> str:
        """
        승인된 patch를 git apply로 워킹트리에 반영한다.
        --check로 먼저 검증 후 실제 적용.
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False, encoding="utf-8"
        ) as f:
            f.write(diff)
            patch_path = f.name

        try:
            check = subprocess.run(
                ["git", "apply", "--check", patch_path],
                cwd=str(self.root),
                capture_output=True,
                text=True,
            )
            if check.returncode != 0:
                raise RuntimeError(f"git apply --check 실패:\n{check.stderr.strip()}")

            apply = subprocess.run(
                ["git", "apply", patch_path],
                cwd=str(self.root),
                capture_output=True,
                text=True,
            )
            if apply.returncode != 0:
                raise RuntimeError(f"git apply 실패:\n{apply.stderr.strip()}")
        finally:
            Path(patch_path).unlink(missing_ok=True)

        return f"proposal_{proposal_id} 적용 완료 (repo: {self.root})"
