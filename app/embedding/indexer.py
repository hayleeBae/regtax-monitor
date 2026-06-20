"""
로컬 임베딩 인덱서.

하이브리드에서 유일하게 '코드를 직접 만지는' 부분이며, 항상 로컬/사내에서 돈다.
- M1/CPU에서 동작. GPU 불필요.
- 코드도 벡터도 외부로 나가지 않는다.
"""
import re
from pathlib import Path

import chromadb

from config import settings


class CodeIndexer:
    def __init__(self, persist_dir: str = "./chroma_data"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection("code")
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(settings.embedding_model)
        return self._model

    def index(self, adapter) -> int:
        """CodebaseAdapter의 파일을 청킹 후 임베딩하여 벡터DB에 저장. 청크 수 반환."""
        count = 0
        for path in adapter.list_files():
            text = adapter.read_file(path)
            chunks = self._chunk(path, text)
            for i, chunk in enumerate(chunks):
                chunk_id = f"{path}::{i}"
                emb = self.model.encode(chunk).tolist()
                self.collection.upsert(
                    ids=[chunk_id],
                    embeddings=[emb],
                    documents=[chunk],
                    metadatas=[{"path": path, "chunk": i}],
                )
                count += 1
        return count

    def search(self, query: str, k: int = 5):
        from app.codebase.base import CodeHit

        q = self.model.encode(query).tolist()
        n = min(k, self.collection.count())
        if n == 0:
            return []
        res = self.collection.query(query_embeddings=[q], n_results=n)
        hits = []
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            hits.append(
                CodeHit(
                    path=meta["path"],
                    symbol=_extract_symbol(meta["path"], doc),
                    snippet=doc,
                    score=round(1 - dist, 4),
                )
            )
        return hits

    def _chunk(self, path: str, text: str) -> list[str]:
        suffix = Path(path).suffix.lower()
        if suffix == ".java":
            return _chunk_java(text)
        if suffix == ".sql":
            return _chunk_sql(text)
        return [text]  # XML 등 나머지는 파일 단위


# ── 청킹 헬퍼 ─────────────────────────────────────────────

_JAVA_METHOD = re.compile(
    r"(?:(?:public|private|protected|static|final|synchronized|abstract)\s+)+"
    r"[\w<>\[\]]+\s+\w+\s*\([^)]*\)\s*(?:throws\s+[\w\s,]+)?\s*\{",
    re.MULTILINE,
)


def _chunk_java(text: str) -> list[str]:
    """Java 파일을 메서드 단위로 분리. 메서드가 없으면 파일 전체 반환."""
    matches = list(_JAVA_METHOD.finditer(text))
    if not matches:
        return [text]

    chunks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # 중괄호 균형 맞춰 메서드 본문 끝 찾기
        body_start = text.index("{", start)
        depth = 0
        pos = body_start
        while pos < end:
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
                if depth == 0:
                    end = pos + 1
                    break
            pos += 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

    return chunks if chunks else [text]


def _chunk_sql(text: str) -> list[str]:
    """SQL 파일을 세미콜론 단위 문장으로 분리."""
    stmts = [s.strip() for s in text.split(";") if s.strip()]
    return stmts if stmts else [text]


def _extract_symbol(path: str, snippet: str) -> str:
    """스니펫에서 메서드명/테이블명 등 식별자 추출."""
    suffix = Path(path).suffix.lower()
    if suffix == ".java":
        m = re.search(r"\b(\w+)\s*\(", snippet)
        return m.group(1) if m else ""
    if suffix == ".sql":
        m = re.search(r"(?:TABLE|INTO)\s+(\w+)", snippet, re.IGNORECASE)
        return m.group(1) if m else ""
    return ""
