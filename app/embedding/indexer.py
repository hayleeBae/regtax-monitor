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
        self._term_dict = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(settings.embedding_model)
        return self._model

    @property
    def term_dict(self) -> dict:
        """컬럼코드 → 한글명 사전 (repo_root 비면 빈 사전 → enrich no-op)."""
        if self._term_dict is None:
            from app.embedding.term_dict import load
            self._term_dict = load(settings.repo_root)
        return self._term_dict

    def _enrich(self, chunk: str) -> str:
        """암호 컬럼코드(a0121 등)에 한글명 헤더를 붙여 임베딩 검색이 가능하게 한다."""
        from app.embedding.term_dict import build_header

        header = build_header(chunk, self.term_dict)
        return f"{header}\n{chunk}" if header else chunk

    def index(self, adapter) -> int:
        """CodebaseAdapter의 파일을 청킹 후 임베딩하여 벡터DB에 저장. 청크 수 반환."""
        files = adapter.list_files()
        total = len(files)
        count = 0
        for file_idx, path in enumerate(files, 1):
            print(f"  [{file_idx}/{total}] {path}", flush=True)
            text = adapter.read_file(path)
            chunks = self._chunk(path, text)
            for i, chunk in enumerate(chunks):
                chunk_id = f"{path}::{i}"
                doc = self._enrich(chunk)
                emb = self.model.encode(doc).tolist()
                self.collection.upsert(
                    ids=[chunk_id],
                    embeddings=[emb],
                    documents=[doc],
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
        if suffix == ".py":
            return _chunk_python(text)
        if suffix == ".kt":
            return _chunk_kotlin(text)
        if suffix == ".xml":
            return _chunk_xml(text)
        return [text]  # TS, JS 등 나머지는 파일 단위


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


_PY_DEF = re.compile(r"^(?:def |class )", re.MULTILINE)


def _chunk_python(text: str) -> list[str]:
    """Python 파일을 함수/클래스 단위로 분리."""
    matches = list(_PY_DEF.finditer(text))
    if not matches:
        return [text]
    chunks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
    return chunks if chunks else [text]


_KOTLIN_FUN = re.compile(
    r"(?:(?:public|private|protected|internal|override|suspend|inline|open|abstract)\s+)*"
    r"fun\s+\w+\s*[\(<]",
    re.MULTILINE,
)


def _chunk_kotlin(text: str) -> list[str]:
    """Kotlin 파일을 fun 단위로 분리. 매치 없으면 파일 전체 반환."""
    matches = list(_KOTLIN_FUN.finditer(text))
    if not matches:
        return [text]
    chunks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body_start = text.find("{", start)
        if body_start == -1 or body_start >= end:
            chunks.append(text[start:end].strip())
            continue
        depth, pos = 0, body_start
        while pos < len(text):
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


_XML_STMT = re.compile(
    r"<(select|insert|update|delete|resultMap)\b([^>]*)>([\s\S]*?)</\1>",
    re.IGNORECASE,
)
_XML_ID = re.compile(r'\bid\s*=\s*["\']([^"\']+)["\']')


def _chunk_xml(text: str) -> list[str]:
    """SQL mapper XML을 쿼리/resultMap 단위로 분리.
    id 속성을 청크 앞에 붙여 임베딩이 쿼리 식별자를 인식하게 한다."""
    chunks = []
    for m in _XML_STMT.finditer(text):
        tag, attrs, body = m.group(1), m.group(2), m.group(3)
        id_match = _XML_ID.search(attrs)
        label = f"[{tag} id={id_match.group(1)}]\n" if id_match else f"[{tag}]\n"
        chunk = (label + m.group(0)).strip()
        if chunk:
            chunks.append(chunk)
    return chunks if chunks else [text]


def _extract_symbol(path: str, snippet: str) -> str:
    """스니펫에서 메서드명/함수명/테이블명 등 식별자 추출."""
    suffix = Path(path).suffix.lower()
    if suffix in {".java", ".kt"}:
        m = re.search(r"\b(\w+)\s*\(", snippet)
        return m.group(1) if m else ""
    if suffix == ".py":
        m = re.search(r"^(?:def|class)\s+(\w+)", snippet, re.MULTILINE)
        return m.group(1) if m else ""
    if suffix == ".sql":
        m = re.search(r"(?:TABLE|INTO)\s+(\w+)", snippet, re.IGNORECASE)
        return m.group(1) if m else ""
    return ""
