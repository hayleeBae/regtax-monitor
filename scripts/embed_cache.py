"""크래시-안전 임베딩 캐시 빌더 (저사양·재부팅 잦은 PC용).

chroma의 HNSW 세그먼트는 불결한 종료에 손상되기 쉽다(sqlite는 무결해도 벡터가
날아감). 그래서 임베딩을 chroma가 아니라 **자체 sqlite(WAL)에 파일마다 커밋**해
크래시-안전하게 쌓는다. 청킹·enrich·모델은 실제 파이프라인(CodeIndexer)을 그대로
재사용하므로 결과 동등성이 유지된다.

- 재시작 가능: 이미 캐시된 path는 건너뛴다.
- recall 측정은 이 캐시를 직접 읽어 코사인 유사도로 수행한다(scripts/recall_eval.py).

사용:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python scripts/embed_cache.py
"""
from __future__ import annotations

import sqlite3
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.codebase.real_adapter import RealCodebaseAdapter  # noqa: E402
from app.embedding.indexer import CodeIndexer  # noqa: E402
from config import settings  # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "embed_cache.db"


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(str(CACHE))
    db.execute("PRAGMA journal_mode=WAL")          # 크래시 안전
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS emb("
        "path TEXT, chunk INTEGER, doc TEXT, dim INTEGER, vec BLOB, "
        "PRIMARY KEY(path, chunk))"
    )
    db.commit()
    return db


def _pack(v) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def main() -> int:
    t0 = time.time()
    db = _connect()
    done = {r[0] for r in db.execute("SELECT DISTINCT path FROM emb").fetchall()}

    indexer = CodeIndexer()  # 빈 chroma는 생성되지만 사용하지 않음 (청킹/enrich/model만 재사용)
    adapter = RealCodebaseAdapter(settings.repo_root, indexer=indexer)
    all_files = adapter.list_files()
    remaining = [f for f in all_files if f not in done]

    # 처리 순서 우선화: recall 정답 파일이 몰린 nexacro/pay(xfdl) → 나머지 pay →
    # 그 외. 전체 완료 전에도 pay 도메인 기준 예비 recall을 낼 수 있게 앞당긴다.
    # (최종 전체 캐시 내용은 순서와 무관하게 동일 — 재현성 유지.)
    def _prio(p: str) -> int:
        q = p.replace("\\", "/")
        if "nexacro/solution/pay" in q:
            return 0
        if "/pay/" in q or q.startswith("src/hr/pay"):
            return 1
        return 2
    remaining.sort(key=_prio)

    print(f"전체 {len(all_files)} / 완료 {len(done)} / 남음 {len(remaining)} — embed 시작", flush=True)

    # 여러 파일 청크를 모아 큰 배치로 인코딩(CPU 멀티코어 처리량↑). 파일 경계에서만
    # flush/commit 하므로 크래시 시에도 파일 단위로 온전(재개 skip=DISTINCT path 유지).
    BATCH = 96
    count = 0
    buf_docs: list[str] = []                 # 인코딩 대기 문서
    buf_owner: list[tuple[str, int]] = []    # 각 문서의 (path, chunk_idx)
    pending_files = 0

    def flush() -> int:
        nonlocal buf_docs, buf_owner, pending_files
        if not buf_docs:
            return 0
        vecs = indexer.model.encode(buf_docs, batch_size=32)
        for (p, ci), doc, vec in zip(buf_owner, buf_docs, vecs):
            v = vec.tolist()
            db.execute(
                "INSERT OR REPLACE INTO emb(path, chunk, doc, dim, vec) VALUES (?,?,?,?,?)",
                (p, ci, doc, len(v), _pack(v)),
            )
        db.commit()  # 버퍼에 담긴 파일들은 모두 완결 → 커밋
        n = len(buf_docs)
        buf_docs, buf_owner, pending_files = [], [], 0
        return n

    for i, path in enumerate(remaining, 1):
        print(f"  [{i}/{len(remaining)}] (전체 {len(done)+i}/{len(all_files)}) {path}", flush=True)
        try:
            chunks = indexer._chunk(path, adapter.read_file(path))
            for ci, ch in enumerate(chunks):
                buf_docs.append(indexer._enrich(ch))
                buf_owner.append((path, ci))
            pending_files += 1
            # 배치가 차면 flush(파일 경계에서만 — 현재 파일 청크까지 모두 담은 뒤)
            if len(buf_docs) >= BATCH:
                count += flush()
        except Exception as e:
            print(f"    ! 스킵: {path} — {type(e).__name__}: {e}", flush=True)
    count += flush()  # 잔여

    total = db.execute("SELECT COUNT(*) FROM emb").fetchone()[0]
    db.close()
    print(f"EMBED_CACHE_DONE 신규청크={count} 총청크={total} 분={((time.time()-t0)/60):.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
