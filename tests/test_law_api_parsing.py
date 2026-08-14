"""법제처 응답 파싱(app.collector.law_api._parse_law_detail) 단위 테스트.

스펙 계약: COLLECTION_SEMANTICS_SPEC.md §2·§5, ADR-014.
HTTP 호출 없이 응답 dict fixture로만 검증한다 — 네트워크·OC 키 불필요.
"""

from app.collector.law_api import LawApiClient, _parse_law_detail
from app.domain.changes.amendment import parse_amendment


# ── 1) 정상 개정문·제개정이유 → 4필드 파생 ────────────────────────

def test_parse_law_detail_four_fields():
    law = {
        "개정문": {
            "개정문내용": ['제55조제1항 중 "1천200만원"을 "1천400만원"으로 한다.']
        },
        "제개정이유": {
            "제개정이유내용": ["종합소득 기본세율 최저구간 상한을 상향."]
        },
    }
    result = _parse_law_detail(law)

    assert result["amendment_text"] == '제55조제1항 중 "1천200만원"을 "1천400만원"으로 한다.'
    assert result["reason_text"] == "종합소득 기본세율 최저구간 상한을 상향."
    assert result["amendment_parsed"] is True
    # 파생된 before는 개정문 원문과 달라야 한다 (원문 보존 + 파싱 분리).
    assert result["before_text"] != result["amendment_text"]
    assert "1천200만원" in result["before_text"]
    assert "1천400만원" in result["after_text"]
    assert result["article_no"] == "제55조제1항"


# ── 2) 리스트-안-리스트 응답 변형 ─────────────────────────────────

def test_parse_law_detail_nested_list():
    law = {
        "개정문": {
            "개정문내용": [[
                '제189조제1항 중 "100분의 6"을 "100분의 7"로 한다.',
                "부칙 <제0000호>",
            ]]
        },
        "제개정이유": {"제개정이유내용": [["원천징수 세율 조정."]]},
    }
    result = _parse_law_detail(law)

    assert "100분의 6" in result["amendment_text"]
    assert "부칙" in result["amendment_text"]  # 두 줄이 개행으로 join
    assert result["reason_text"] == "원천징수 세율 조정."
    assert result["amendment_parsed"] is True
    assert "100분의 6" in result["before_text"]
    assert "100분의 7" in result["after_text"]


# ── 3) 비정형 개정문 (파서 미인식) → 폴백 ─────────────────────────

def test_parse_law_detail_unrecognized_falls_back():
    law = {
        "개정문": {"개정문내용": ["이 법은 공포한 날부터 시행한다."]},
        "제개정이유": {"제개정이유내용": ["시행일 규정."]},
    }
    result = _parse_law_detail(law)

    assert result["amendment_parsed"] is False
    assert result["before_text"] == ""
    assert result["after_text"] == result["amendment_text"]
    assert result["amendment_text"] == "이 법은 공포한 날부터 시행한다."


# ── 4) 개정문 자체가 없음 → 4필드 모두 빈 문자열 ──────────────────

def test_parse_law_detail_empty():
    result = _parse_law_detail({})

    assert result["amendment_text"] == ""
    assert result["reason_text"] == ""
    assert result["before_text"] == ""
    assert result["after_text"] == ""
    assert result["amendment_parsed"] is False
    assert result["article_no"] == ""


# ── 5) mock 데이터 계약 — 실 문형을 흉내 낸다 ─────────────────────

def test_mock_results_contract():
    results = LawApiClient(oc=None)._mock_results("20260101")
    assert results
    for item in results:
        assert item["amendment_text"], f"amendment_text 없음: {item['law_id']}"
        assert "reason_text" in item
        # mock의 개정문이 실 P1 문형이라 파서가 1건 이상 인식해야 한다.
        edits = parse_amendment(item["amendment_text"])
        assert edits, f"파싱 0건: {item['amendment_text']}"
