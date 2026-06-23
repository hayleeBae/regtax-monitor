"""
④ 검증: 한글명 헤더 주입(enrichment) 전/후 RAG 검색 품질 비교.

흐름:
  1) 현재 인덱스(주입 전)로 baseline 검색
  2) 재인덱싱 (enrich 적용)
  3) 동일 쿼리 재검색
  4) before/after 비교 출력
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from config import settings

if settings.hf_hub_disable_ssl:
    import truststore
    truststore.inject_into_ssl()

from app.codebase.real_adapter import RealCodebaseAdapter
from app.codebase.mock_adapter import MockCodebaseAdapter
from app.embedding.indexer import CodeIndexer

MOCK_REPO_ROOT = "mock_repo"

# (법령 변경 텍스트, 기대 컬럼코드들)
QUERIES = [
    ("만 8세 이상 자녀에 대한 자녀세액공제 공제액 인상", ["b0181", "n0200", "n0201"]),
    ("고향사랑기부금 세액공제 한도 확대", ["n0161"]),
    ("신용카드 등 사용금액 소득공제 대중교통 사용분", ["l0160", "l0200"]),
    ("기본공제 배우자 공제 요건 변경", ["a0120", "a0121"]),
    ("연금보험료공제 국민연금 보험료", ["d0110"]),
]
K = 5


def make_adapter(indexer):
    if settings.repo_root:
        return RealCodebaseAdapter(repo_root=settings.repo_root, indexer=indexer)
    return MockCodebaseAdapter(repo_root=MOCK_REPO_ROOT, indexer=indexer)


def probe(indexer):
    """각 쿼리에 대해 (최상위 점수, 기대코드 첫 적중 순위, 적중 코드) 반환."""
    out = []
    for query, expected in QUERIES:
        hits = indexer.search(query, k=K)
        top_score = hits[0].score if hits else None
        hit_rank, hit_code = None, None
        for rank, h in enumerate(hits, 1):
            found = [c for c in expected if c in h.snippet]
            if found:
                hit_rank, hit_code = rank, found[0]
                break
        out.append((query, expected, top_score, hit_rank, hit_code,
                    [(h.path.split("/")[-1], h.score) for h in hits]))
    return out


def show(title, results):
    print(f"\n{'='*70}\n{title}\n{'='*70}")
    for query, expected, top, rank, code, hits in results:
        mark = f"순위 {rank} (코드 {code})" if rank else "✗ 적중 없음"
        print(f"\nＱ {query}")
        print(f"   기대코드 {expected} → {mark}   top_score={top}")
        for name, sc in hits:
            print(f"     - {sc:>7.4f}  {name}")


idx = CodeIndexer()
print(f"현재 인덱스 청크 수: {idx.collection.count()}")
print(f"repo_root: {settings.repo_root!r}  /  사전 코드 수: {len(idx.term_dict)}")

before = probe(idx)
show("BEFORE — 한글명 헤더 주입 전 (기존 인덱스)", before)

print("\n\n>>> 재인덱싱 시작 (enrich 적용)... 시간이 걸립니다.\n")
adapter = make_adapter(idx)
count = idx.index(adapter)
print(f">>> 재인덱싱 완료: {count}개 청크\n")

after = probe(idx)
show("AFTER — 한글명 헤더 주입 후", after)

# 요약
print(f"\n{'='*70}\n요약 (기대코드 적중 순위, 낮을수록 좋음 / ✗=miss)\n{'='*70}")
print(f"{'쿼리':<40} {'BEFORE':>8} {'AFTER':>8}")
for (q, _, _, rb, _, _), (_, _, _, ra, _, _) in zip(before, after):
    sb = str(rb) if rb else "✗"
    sa = str(ra) if ra else "✗"
    print(f"{q[:38]:<40} {sb:>8} {sa:>8}")
