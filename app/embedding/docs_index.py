"""참고 문서 인덱스 — 『개정세법 해설』 등 배경지식으로 analyze를 보강한다.

국세청 해설서는 공식 API가 없고 연 1회 PDF 발간이라 자동 수집 이득이 없다.
담당자가 화면(또는 docs/ 폴더)에 PDF를 올리면:
  PDF/텍스트 → 문단 청킹 → bge-m3 임베딩 → ChromaDB "tax_docs" 컬렉션
코드 인덱스("code" 컬렉션)와 분리해 서로 오염되지 않는다.
analyze 시 해당 개정과 유사한 해설 청크가 [참고 자료] 컨텍스트로 주입된다.
"""
from pathlib import Path

import chromadb

from config import settings

ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}
_CHUNK_MAX = 900     # 청크 최대 길이 (bge-m3 입력 여유)
_CHUNK_MIN = 40      # 이보다 짧은 조각(머리글·쪽번호 등)은 버림


class DocsIndexer:
    def __init__(self, persist_dir: str = "./chroma_data", docs_dir: str | None = None):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection("tax_docs")
        self.docs_dir = Path(docs_dir or settings.docs_dir)
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(settings.embedding_model)
        return self._model

    def index_file(self, path: Path) -> int:
        """문서 하나를 (재)인덱싱. 같은 파일명 기존 청크는 교체된다. 청크 수 반환."""
        text = extract_text(path)
        chunks = chunk_text(text)
        try:
            self.collection.delete(where={"source": path.name})
        except Exception:
            pass
        for i, chunk in enumerate(chunks):
            emb = self.model.encode(chunk).tolist()
            self.collection.upsert(
                ids=[f"{path.name}::{i}"],
                embeddings=[emb],
                documents=[chunk],
                metadatas=[{"source": path.name, "chunk": i}],
            )
        return len(chunks)

    def index_all(self) -> dict[str, int]:
        """docs_dir의 지원 문서 전체 인덱싱. {파일명: 청크수} 반환."""
        if not self.docs_dir.is_dir():
            return {}
        result: dict[str, int] = {}
        for path in sorted(self.docs_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES:
                result[path.name] = self.index_file(path)
        return result

    def list_sources(self) -> list[dict]:
        """인덱싱된 문서 목록: [{name, chunks}]."""
        got = self.collection.get(include=["metadatas"])
        counts: dict[str, int] = {}
        for meta in got.get("metadatas") or []:
            src = meta.get("source", "?")
            counts[src] = counts.get(src, 0) + 1
        return [{"name": k, "chunks": v} for k, v in sorted(counts.items())]

    def delete_source(self, name: str) -> None:
        """문서의 청크와 원본 파일을 함께 제거."""
        self.collection.delete(where={"source": name})
        target = self.docs_dir / Path(name).name
        if target.is_file():
            target.unlink()

    def search(self, query: str, k: int = 2) -> list[dict]:
        """관련 문서 발췌 검색: [{source, snippet, score}]."""
        n = min(k, self.collection.count())
        if n == 0:
            return []
        q = self.model.encode(query).tolist()
        res = self.collection.query(query_embeddings=[q], n_results=n)
        return [
            {"source": meta["source"], "snippet": doc, "score": round(1 - dist, 4)}
            for doc, meta, dist in zip(
                res["documents"][0], res["metadatas"][0], res["distances"][0]
            )
        ]


def extract_text(path: Path) -> str:
    """PDF는 pypdf로, 텍스트류는 그대로 읽는다."""
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="replace")


def chunk_text(text: str) -> list[str]:
    """빈 줄 기준 문단을 _CHUNK_MAX까지 병합. 짧은 조각(쪽번호 등)은 버린다."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        candidate = f"{buf}\n\n{p}" if buf else p
        if len(candidate) <= _CHUNK_MAX:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
        # 문단 하나가 상한을 넘으면 상한 단위로 강제 분할
        while len(p) > _CHUNK_MAX:
            chunks.append(p[:_CHUNK_MAX])
            p = p[_CHUNK_MAX:]
        buf = p
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) >= _CHUNK_MIN]
