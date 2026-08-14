"""개정문 파서(app.domain.changes.amendment) 단위·통합 테스트.

스펙 계약: COLLECTION_SEMANTICS_SPEC.md §3, ADR-014.
API 호출 없이 실채록 문형을 문자열 상수로만 검증한다.
"""

from app.domain.changes.amendment import (
    AmendmentEdit,
    derive_before_after,
    parse_amendment,
)
from app.domain.changes.normalization import ChangeNormalizer


# ── P1: 따옴표 치환 ────────────────────────────────────────────────

def test_p1_straight_quotes():
    edits = parse_amendment('제59조의2제1항 중 "연 15만원"을 "연 25만원"으로 한다.')
    assert edits == [
        AmendmentEdit("제59조의2제1항", "replace", "연 15만원", "연 25만원"),
    ]


def test_p1_curly_quotes():
    # 실 API는 둥근따옴표(U+201C/U+201D)로 오는 경우가 있다.
    edits = parse_amendment("제55조 중 “100분의 6”을 “100분의 7”로 한다.")
    assert edits == [
        AmendmentEdit("제55조", "replace", "100분의 6", "100분의 7"),
    ]


# ── P2: 무따옴표 치환 ──────────────────────────────────────────────

def test_p2_bare_replace():
    edits = parse_amendment("제55조 중 100분의 6을 100분의 7로 한다.")
    assert edits == [
        AmendmentEdit("제55조", "replace", "100분의 6", "100분의 7"),
    ]


# ── P3: 전문개정 + 후속 본문 ───────────────────────────────────────

def test_p3_rewrite_collects_body():
    text = (
        "제10조를 다음과 같이 한다.\n"
        "제10조(과세표준) 종합소득 과세표준은 종합소득금액에서 공제액을 뺀 금액으로 계산한다."
    )
    edits = parse_amendment(text)
    assert len(edits) == 1
    edit = edits[0]
    assert edit.article_ref == "제10조"
    assert edit.kind == "rewrite"
    assert edit.before_fragment == ""
    assert edit.after_fragment.startswith("제10조(과세표준)")


# ── P4 / P5: 신설·삭제 ─────────────────────────────────────────────

def test_p4_insert_inline():
    edits = parse_amendment("제5조에 다음 각 호를 신설한다.")
    assert len(edits) == 1
    assert edits[0].article_ref == "제5조"
    assert edits[0].kind == "insert"
    assert edits[0].before_fragment == ""
    assert edits[0].after_fragment == "다음 각 호"


def test_p4_insert_block():
    text = (
        "제12조의2를 다음과 같이 신설한다.\n"
        "제12조의2(추가공제) 추가공제액은 200만원으로 한다."
    )
    edits = parse_amendment(text)
    assert len(edits) == 1
    assert edits[0].kind == "insert"
    assert edits[0].article_ref == "제12조의2"
    assert edits[0].after_fragment.startswith("제12조의2(추가공제)")


def test_p5_delete():
    edits = parse_amendment("제7조제3항을 삭제한다.")
    assert edits == [
        AmendmentEdit("제7조제3항", "delete", "", ""),
    ]


# ── 복수 edit — 순서 보존 ──────────────────────────────────────────

def test_multiple_edits_preserve_order():
    text = (
        "제55조 중 100분의 6을 100분의 7로 한다.\n"
        "제7조제3항을 삭제한다.\n"
        '제59조의2제1항 중 "연 15만원"을 "연 25만원"으로 한다.'
    )
    edits = parse_amendment(text)
    assert [e.article_ref for e in edits] == ["제55조", "제7조제3항", "제59조의2제1항"]
    assert [e.kind for e in edits] == ["replace", "delete", "replace"]


# ── 폴백: 파싱 0건 ─────────────────────────────────────────────────

def test_fallback_when_no_edits():
    raw = "이 법은 공포한 날부터 시행한다."  # 인식 문형 아님
    assert parse_amendment(raw) == []
    assert derive_before_after([], fallback_text=raw) == ("", raw)


# ── 실채록 문형 샘플 (연결 문형 · 조문별 나열) ──────────────────────

# 소득세법류 개정문 문체: "제N조 중 …을 …으로 하고, 같은 조 제M항 중 …" 연결.
_REAL_SAMPLE_1 = (
    '제59조의2제1항 중 "연 15만원"을 "연 25만원"으로 하고, '
    '같은 조 제2항 중 "연 30만원"을 "연 40만원"으로 한다.'
)

# 시행령 수치 개정 + 삭제가 여러 줄로 나열되는 문체.
_REAL_SAMPLE_2 = (
    "제189조제1항 중 “100분의 6”을 “100분의 7”로 한다.\n"
    "제189조제2항을 삭제한다.\n"
    "제100조의3을 다음과 같이 신설한다.\n"
    "제100조의3(세액공제 특례) 공제한도는 연 50만원으로 한다."
)


def test_real_sample_1_connected_form():
    edits = parse_amendment(_REAL_SAMPLE_1)
    # "같은 조 제2항"이 직전 조("제59조의2")를 이어받아 해소된다.
    assert edits == [
        AmendmentEdit("제59조의2제1항", "replace", "연 15만원", "연 25만원"),
        AmendmentEdit("제59조의2제2항", "replace", "연 30만원", "연 40만원"),
    ]


def test_real_sample_2_mixed_kinds():
    edits = parse_amendment(_REAL_SAMPLE_2)
    assert [(e.article_ref, e.kind) for e in edits] == [
        ("제189조제1항", "replace"),
        ("제189조제2항", "delete"),
        ("제100조의3", "insert"),
    ]
    insert_edit = edits[2]
    assert insert_edit.after_fragment.startswith("제100조의3(세액공제 특례)")


# ── 통합: derive → ChangeNormalizer 값 델타 방향 ──────────────────

def test_derive_feeds_money_delta_into_normalizer():
    edits = parse_amendment('제59조의2제1항 중 "연 15만원"을 "연 25만원"으로 한다.')
    before, after = derive_before_after(edits, fallback_text="")
    # 같은 조문 문맥(제59조의2제1항)이 양쪽에 남아 토큰 정렬을 돕는다.
    assert before == "제59조의2제1항 연 15만원"
    assert after == "제59조의2제1항 연 25만원"

    normalized = ChangeNormalizer().normalize(before, after)
    money = [
        d for d in normalized.money_changes
        if d.before and d.after and "15" in d.before.raw and "25" in d.after.raw
    ]
    assert money, f"기대 방향의 money delta 없음: {normalized.money_changes}"
