"""
④ 마이크로 벤치마크: enrichment 효과 격리 측정.

전체 재인덱싱(CPU에서 수십 분) 대신, '코드만 있고 한글이 없는' VO 청크라는
최악의 케이스를 raw vs enriched로 직접 임베딩해 쿼리와의 코사인 유사도를 비교한다.
이게 RAG가 실패하던 바로 그 케이스다.
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from config import settings

if settings.hf_hub_disable_ssl:
    import truststore
    truststore.inject_into_ssl()

import numpy as np
from sentence_transformers import SentenceTransformer

from app.embedding.term_dict import load, build_header

table = load(settings.repo_root)
model = SentenceTransformer(settings.embedding_model)


def emb(text: str):
    v = model.encode(text)
    return v / (np.linalg.norm(v) + 1e-9)


def cos(a, b):
    return float(np.dot(a, b))

# (법령 쿼리, 정답 코드, 오답(무관) 코드) — 정답/오답 모두 '한글 주석 없는' VO 줄로 구성
CASES = [
    ("만 8세 이상 자녀세액공제 공제액 인상",      "b0181", "d0110"),
    ("고향사랑기부금 세액공제 한도 확대",          "n0161", "a0120"),
    ("신용카드 등 대중교통 사용분 소득공제",       "l0160", "n0161"),
    ("기본공제 배우자 공제 요건 변경",            "a0120", "l0160"),
    ("연금보험료공제 국민연금 보험료",            "d0110", "b0181"),
]


def raw_chunk(code: str) -> str:
    # RAG가 실패하던 실제 형태: 코드만, 한글 없음
    return f"private Long {code} = 0L;"


print(f"사전 코드 수: {len(table)}\n")
print(f"{'쿼리':<34} {'정답코드':>7} {'raw':>8} {'enriched':>9} {'Δ':>7} {'오답enr':>8}")
print("-" * 82)

wins = 0
for query, gold, bad in CASES:
    q = emb(query)
    raw = raw_chunk(gold)
    enr = f"{build_header(raw, table)}\n{raw}"
    bad_enr = f"{build_header(raw_chunk(bad), table)}\n{raw_chunk(bad)}"

    s_raw = cos(q, emb(raw))
    s_enr = cos(q, emb(enr))
    s_bad = cos(q, emb(bad_enr))  # 무관 코드 enriched — 정답보다 낮아야 specificity 확인

    delta = s_enr - s_raw
    flag = "✓" if (s_enr > s_raw and s_enr > s_bad) else "✗"
    if flag == "✓":
        wins += 1
    print(f"{query[:33]:<34} {gold:>7} {s_raw:>8.3f} {s_enr:>9.3f} {delta:>+7.3f} {s_bad:>8.3f} {flag}")

print("-" * 82)
print(f"\n정답 코드의 enriched 헤더 (검증용):")
for _, gold, _ in CASES:
    print(f"  {gold}: {build_header(raw_chunk(gold), table)}")
print(f"\n결과: {wins}/{len(CASES)} 케이스에서 "
      f"enriched가 raw보다 높고 + 무관코드보다 높음(specificity OK)")
