"""라이브 검색 recall 측정 도구 (임베딩 캐시 기반, chroma 비의존).

`scripts/embed_cache.py`가 만든 크래시-안전 임베딩 캐시(embed_cache.db)를 읽어,
각 fixture 케이스의 쿼리를 코사인 유사도로 검색하고 정답 파일이 top-K에 드는지로
recall/hit@K/MRR을 계산한다. chroma의 HNSW 취약점을 우회한다.

- 파일 단위 순위 = 그 파일 소속 청크의 최고 순위.
- fixture 스키마: {cases: [{id, query, answers: [relpath...]}]} (gitignore 위치).
- 리포트는 --redacted로 경로 원문 없이(케이스 id·rank만) 출력 가능.

사용:
  HF_HUB_OFFLINE=1 python scripts/recall_eval.py --fixtures evaluation/private/recall_fixtures.yaml \
      --k 10 --out evaluation/private/recall_report.md --redacted
"""
from __future__ import annotations

import argparse
import sqlite3
import struct
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402


def _load_cache(db_path: str):
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT path, dim, vec FROM emb").fetchall()
    db.close()
    if not rows:
        raise SystemExit(f"임베딩 캐시가 비어 있습니다: {db_path}")
    paths = [r[0] for r in rows]
    dim = rows[0][1]
    mat = np.empty((len(rows), dim), dtype=np.float32)
    for i, (_p, d, blob) in enumerate(rows):
        mat[i] = struct.unpack(f"{d}f", blob)
    # 코사인용 정규화
    mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    return paths, mat


def _file_ranking(scores: np.ndarray, paths: list[str]) -> list[str]:
    """청크 점수 내림차순을 파일 단위 순위로 접는다(파일 최초 등장 순)."""
    order = np.argsort(-scores)
    seen: dict[str, None] = {}
    for idx in order:
        p = paths[idx].replace("\\", "/")
        if p not in seen:
            seen[p] = None
    return list(seen.keys())


def _rank_of(ranked_files: list[str], answers: set[str]) -> int | None:
    ans_posix = {a.replace("\\", "/") for a in answers}
    ans_base = {Path(a).name for a in answers}
    for i, p in enumerate(ranked_files, start=1):
        if p in ans_posix or Path(p).name in ans_base:
            return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--cache", default="embed_cache.db")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--out", default="")
    ap.add_argument("--redacted", action="store_true")
    args = ap.parse_args()

    cases = yaml.safe_load(Path(args.fixtures).read_text(encoding="utf-8"))["cases"]
    paths, mat = _load_cache(args.cache)

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(settings.embedding_model)

    rows, ranks = [], []
    for c in cases:
        q = np.asarray(model.encode(c["query"]), dtype=np.float32)
        q /= (np.linalg.norm(q) + 1e-12)
        scores = mat @ q
        ranked = _file_ranking(scores, paths)
        rank = _rank_of(ranked, set(c["answers"]))
        ranks.append(rank)
        top = [] if args.redacted else [Path(p).name for p in ranked[:3]]
        rows.append((c["id"], rank, top))

    n = len(cases)
    def hit_at(kk: int) -> float:
        return sum(1 for r in ranks if r is not None and r <= kk) / n
    mrr = sum((1.0 / r) for r in ranks if r) / n

    lines = [
        f"# 검색 recall 리포트 (임베딩 캐시, 파일 {len({p.replace(chr(92),'/') for p in paths})}개, K={args.k})",
        "",
        f"- 케이스 수: {n}",
        f"- Hit@1: {hit_at(1):.2f}  Hit@3: {hit_at(3):.2f}  Hit@5: {hit_at(5):.2f}  Hit@{args.k}: {hit_at(args.k):.2f}",
        f"- MRR: {mrr:.3f}",
        "",
        "| case | 정답 rank | top-3" + ("" if args.redacted else " (basename)") + " |",
        "|---|---|---|",
    ]
    for cid, rank, top in rows:
        lines.append(f"| {cid} | {rank if rank else 'miss'} | {', '.join(top) if top else '(redacted)'} |")
    report = "\n".join(lines)

    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
