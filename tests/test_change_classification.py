"""Issue #0007 규칙 우선 hybrid 분류 테스트."""

from app.domain.changes.classification import (
    ClassificationSource,
    HybridChangeClassifier,
    RuleChangeClassifier,
)
from app.domain.changes.normalization import ChangeNormalizer
from app.domain.common.enums import ChangeType


class _FakeLlm:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def classify_change(self, before: str, after: str, normalized: dict) -> dict:
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def _change(before: str, after: str):
    return ChangeNormalizer().normalize(before, after)


def test_clear_money_change_uses_rule_without_llm() -> None:
    llm = _FakeLlm()
    result = HybridChangeClassifier(llm).classify(_change("15만원", "25만원"))

    assert result.primary_type is ChangeType.VALUE_CHANGE
    assert result.source is ClassificationSource.RULE
    assert result.confidence >= 0.9
    assert llm.calls == 0
    assert result.reason and result.signals


def test_clear_rate_and_date_are_classified_by_rule() -> None:
    classifier = RuleChangeClassifier()
    rate = classifier.classify(_change("세율 6%", "세율 8%"))
    date = classifier.classify(_change("2025년 1월 1일", "2026년 1월 1일"))

    assert rate.primary_type is ChangeType.RATE_CHANGE
    assert date.primary_type is ChangeType.DATE_CHANGE


def test_condition_change_uses_llm_fallback_and_validates_enum() -> None:
    llm = _FakeLlm(
        {
            "primary_type": "condition_change",
            "secondary_types": ["value_change"],
            "confidence": 0.86,
            "reason": "금액과 적용 조건이 함께 변경됨",
            "signals": [{"type": "comparison", "evidence": "이하에서 미만"}],
        }
    )
    result = HybridChangeClassifier(llm).classify(
        _change("7천만원 이하", "8천만원 미만")
    )

    assert llm.calls == 1
    assert result.primary_type is ChangeType.CONDITION_CHANGE
    assert result.secondary_types == (ChangeType.VALUE_CHANGE,)
    assert result.source is ClassificationSource.HYBRID


def test_llm_failure_falls_back_to_rule_with_lower_confidence() -> None:
    llm = _FakeLlm(error=TimeoutError("slow"))
    result = HybridChangeClassifier(llm).classify(
        _change("만 18세 이상", "만 19세 미만")
    )

    assert result.primary_type is ChangeType.CONDITION_CHANGE
    assert result.source is ClassificationSource.FALLBACK
    assert result.confidence < 0.8
    assert "LLM fallback 실패" in result.reason


def test_malformed_llm_result_falls_back_safely() -> None:
    result = HybridChangeClassifier(_FakeLlm({"primary_type": "invented"})).classify(
        _change("제1항을 삭제한다", "제2항을 신설한다")
    )

    assert result.primary_type is ChangeType.STRUCTURAL_CHANGE
    assert result.source is ClassificationSource.FALLBACK


def test_identical_text_is_no_code_impact_without_llm() -> None:
    llm = _FakeLlm()
    result = HybridChangeClassifier(llm).classify(_change("문구", "문구"))

    assert result.primary_type is ChangeType.NO_CODE_IMPACT
    assert llm.calls == 0
