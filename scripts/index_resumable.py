"""재시작 가능한 전체 인덱싱 러너 (저사양 PC·장시간 인덱싱용).

`chroma_data`에 이미 인덱싱된 파일을 건너뛰고 남은 파일만 처리한다. 인덱서가
`PersistentClient` + `upsert`(id=`{path}::{i}`)라 증분 저장·멱등이므로, 중간에
PC가 꺼져도 다시 실행하면 그 지점부터 이어진다.

사용:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python scripts/index_resumable.py

주의: 인덱싱 대상은 .env(REPO_ROOT, REPO_INDEX_PATHS)로 결정된다. 범위를 바꾸면
캐시가 구 범위를 섞으므로 chroma_data를 지우고 처음부터 돌린다(COMPANY_VALIDATION §3-1).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.codebase.real_adapter import RealCodebaseAdapter  # noqa: E402
from app.embedding.indexer import CodeIndexer  # noqa: E402
from config import settings  # noqa: E402


def _indexed_paths(collection) -> set[str]:
    """컬렉션에 이미 존재하는 파일 경로 집합(메타데이터 기준)."""
    done: set[str] = set()
    got = collection.get(include=["metadatas"])
    for meta in got.get("metadatas") or []:
        if meta and "path" in meta:
            done.add(meta["path"])
    return done


def main() -> int:
    t0 = time.time()
    indexer = CodeIndexer()  # persist_dir="./chroma_data" (코드 고정)
    adapter = RealCodebaseAdapter(settings.repo_root, indexer=indexer)

    all_files = adapter.list_files()
    done = _indexed_paths(indexer.collection)
    # 마지막으로 처리되던 파일이 중간에 끊겼을 수 있으므로, 이미-완료 목록에서
    # 파일 순서상 '마지막 1개'는 다시 처리한다(멱등이라 안전, 누락 chunk 보정).
    remaining = [f for f in all_files if f not in done]
    if done and all_files:
        last_done = [f for f in all_files if f in done]
        if last_done:
            redo = last_done[-1]
            if redo not in remaining:
                remaining.insert(0, redo)

    total = len(all_files)
    print(f"전체 {total} / 완료 {len(done)} / 남음 {len(remaining)} — resume 시작", flush=True)

    count = 0
    for idx, path in enumerate(remaining, 1):
        print(f"  [{idx}/{len(remaining)}] (전체 {len(done)+idx}/{total}) {path}", flush=True)
        try:
            text = adapter.read_file(path)
            chunks = indexer._chunk(path, text)
            for i, chunk in enumerate(chunks):
                doc = indexer._enrich(chunk)
                emb = indexer.model.encode(doc).tolist()
                indexer.collection.upsert(
                    ids=[f"{path}::{i}"],
                    embeddings=[emb],
                    documents=[doc],
                    metadatas=[{"path": path, "chunk": i}],
                )
                count += 1
        except Exception as e:  # 한 파일 실패가 전체를 멈추지 않게
            print(f"    ! 실패 스킵: {path} — {type(e).__name__}: {e}", flush=True)

    print(
        f"RESUMABLE_INDEX_DONE 신규청크={count} "
        f"컬렉션총청크={indexer.collection.count()} 분={((time.time()-t0)/60):.1f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
