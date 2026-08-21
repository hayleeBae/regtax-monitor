"""라이브 검색 recall 측정 도구 (범용, eHR 데이터 비포함).

기존 `chroma_data` 인덱스에 대해 fixture의 각 케이스를 검색하고,
정답 파일이 top-K에 드는지로 recall/hit@K/MRR을 계산한다.

- fixture 스키마: {cases: [{id, query, answers: [relpath...]}]}
  (fixture 자체는 eHR 경로를 담을 수 있으므로 gitignore 위치에 둔다 — 이 스크립트는 경로를 모른다.)
- 출력 리포트는 경로 원문 없이 케이스 id·rank·hit만 남기도록 --redacted 지원.

사용:
  HF_HUB_OFFLINE=1 python scripts/recall_eval.py --fixtures evaluation/private/recall_fixtures.yaml \
      --k 10 --out evaluation/private/recall_report.md [--redacted]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embedding.indexer import CodeIndexer  # noqa: E402


def _rank_of(hits, answers: set[str]) -> int | None:
    """정답 파일이 처음 등장하는 순위(1-base). 없으면 None.
    경로 비교는 basename과 posix 상대경로 양쪽을 허용(인덱스 경로 표기 차 흡수)."""
    ans_base = {Path(a).name for a in answers}
    ans_posix = {a.replace("\\", "/") for a in answers}
    for i, h in enumerate(hits, start=1):
        hp = str(h.path).replace("\\", "/")
        if hp in ans_posix or Path(hp).name in ans_base:
            return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--persist-dir", default="./chroma_data")
    ap.add_argument("--out", default="")
    ap.add_argument("--redacted", action="store_true",
                    help="리포트에 경로 원문을 남기지 않는다(케이스 id·rank만).")
    args = ap.parse_args()

    cases = yaml.safe_load(Path(args.fixtures).read_text(encoding="utf-8"))["cases"]
    indexer = CodeIndexer(persist_dir=args.persist_dir)

    rows = []
    ranks = []
    for c in cases:
        hits = indexer.search(c["query"], k=args.k)
        rank = _rank_of(hits, set(c["answers"]))
        ranks.append(rank)
        top = [] if args.redacted else [f"{Path(h.path).name}#{h.score:.3f}" for h in hits[:3]]
        rows.append((c["id"], rank, top))

    n = len(cases)
    def hit_at(kk: int) -> float:
        return sum(1 for r in ranks if r is not None and r <= kk) / n
    mrr = sum((1.0 / r) for r in ranks if r) / n

    lines = [
        f"# 검색 recall 리포트 (라이브 인덱스, K={args.k})",
        "",
        f"- 케이스 수: {n}",
        f"- Hit@1: {hit_at(1):.2f}  Hit@3: {hit_at(3):.2f}  Hit@5: {hit_at(5):.2f}  Hit@{args.k}: {hit_at(args.k):.2f}",
        f"- MRR: {mrr:.3f}",
        "",
        "| case | 정답 rank | top-3" + ("" if args.redacted else " (file#score)") + " |",
        "|---|---|---|",
    ]
    for cid, rank, top in rows:
        rk = str(rank) if rank else "miss"
        lines.append(f"| {cid} | {rk} | {', '.join(top) if top else '(redacted)'} |")
    report = "\n".join(lines)

    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
