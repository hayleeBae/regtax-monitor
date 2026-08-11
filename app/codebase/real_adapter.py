import re
import subprocess
from pathlib import Path

from app.codebase.base import CodebaseAdapter, CodeHit
from config import settings

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_unified(diff: str) -> list[dict]:
    """unified diff를 {path, hunks:[(orig_start, orig_len, body_lines)]} 목록으로 파싱."""
    files: list[dict] = []
    cur = None
    lines = diff.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            plus = lines[i + 1] if i + 1 < len(lines) else ""
            path = plus[6:] if plus.startswith("+++ b/") else plus[4:]
            cur = {"path": path, "hunks": []}
            files.append(cur)
            i += 2
            continue
        m = _HUNK_RE.match(line)
        if m and cur is not None:
            start, length = int(m.group(1)), int(m.group(2) or 1)
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("@@ ") and not lines[i].startswith("--- "):
                body.append(lines[i])
                i += 1
            cur["hunks"].append((start, length, body))
            continue
        i += 1
    return files


def _apply_hunks(orig: list[str], hunks: list, path: str) -> list[str]:
    """원본 줄 리스트에 hunk를 아래에서 위로 적용. 컨텍스트 불일치 시 예외."""
    result = orig[:]
    for start, length, body in sorted(hunks, key=lambda h: h[0], reverse=True):
        at = start - 1
        old_seg = [b[1:] for b in body if b[:1] in (" ", "-")]
        new_seg = [b[1:] for b in body if b[:1] in (" ", "+")]
        if result[at:at + length] != old_seg:
            raise RuntimeError(
                f"{path}: 컨텍스트 불일치(줄 {start}) — 초안 생성 이후 파일이 바뀌었을 수 있습니다."
            )
        result[at:at + length] = new_seg
    return result


class RealCodebaseAdapter(CodebaseAdapter):
    """
    회사 실제 repo 대상 구현체.
    REPO_ROOT 환경변수로 경로를 지정한다.
    apply_patch()는 git apply로 워킹트리에 직접 반영한다.
    """

    SOURCE_EXTS = {".java", ".sql", ".xml", ".py", ".kt", ".ts", ".tsx", ".js"}

    # EXCLUDED_DIRS(빌드 산출물 제외 목록)는 CodebaseAdapter 에서 상속한다 —
    # 어댑터 공통 규칙이라 base 에 있고, `RealCodebaseAdapter.EXCLUDED_DIRS` 로
    # 참조하는 기존 코드·문서는 그대로 동작한다.

    def __init__(self, repo_root: str, indexer=None):
        self.root = Path(repo_root).resolve()
        self.indexer = indexer

    def _is_indexable(self, path: Path) -> bool:
        """인덱싱 대상 파일인지 — 확장자 + 제외 디렉토리 판정."""
        if not path.is_file() or path.suffix not in self.SOURCE_EXTS:
            return False
        return not self._is_excluded(path)

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
                if self._is_indexable(p):
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

    def repository_revision(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.root, check=True,
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

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

    @staticmethod
    def _strip_preamble(diff: str) -> str:
        """diff 앞에 붙은 주석·경고(#) 머리말을 제거해 git apply가 인식하게 한다."""
        lines = diff.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("--- ") or line.startswith("diff --git"):
                return "".join(lines[i:])
        return diff

    def apply_patch(self, proposal_id: int, diff: str) -> str:
        """
        승인된 unified diff를 파이썬으로 직접 적용한다.
        git apply 대신 파일별 원본 인코딩(UTF-8/CP949)·줄바꿈(CRLF/LF)을 보존해
        eHR 레거시(EUC-KR·CRLF 혼재)에서도 안전하게 반영한다.
        """
        files = _parse_unified(self._strip_preamble(diff))
        if not files:
            raise RuntimeError("적용할 변경(hunk)이 없습니다. (주석만 있는 초안)")

        changed: list[str] = []
        for f in files:
            full = self.root / f["path"]
            raw = full.read_bytes()
            try:
                enc, text = "utf-8", raw.decode("utf-8")
            except UnicodeDecodeError:
                enc, text = "cp949", raw.decode("cp949")
            newline = "\r\n" if b"\r\n" in raw else "\n"

            new_lines = _apply_hunks(text.splitlines(), f["hunks"], f["path"])
            out = newline.join(new_lines)
            if raw.endswith(b"\n"):
                out += newline
            full.write_bytes(out.encode(enc))
            changed.append(f["path"])

        return f"proposal_{proposal_id} 적용 완료 — {', '.join(changed)} (repo: {self.root})"
