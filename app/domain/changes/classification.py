"""규칙 우선, LLM fallback 법령 변경 분류."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from app.domain.changes.normalization import NormalizedChange
from app.domain.common.enums import ChangeType
from app.domain.common.serialization import to_jsonable


RULE_CLASSIFIER_VERSION = "rule-classifier-v1"
HYBRID_CLASSIFIER_VERSION = "hybrid-classifier-v1"


class ClassificationSource(str, Enum):
    RULE = "rule"
    LLM = "llm"
    HYBRID = "hybrid"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class ClassificationSignal:
    type: str
    evidence: str


@dataclass(frozen=True)
class ChangeClassification:
    primary_type: ChangeType
    secondary_types: tuple[ChangeType, ...]
    confidence: float
    source: ClassificationSource
    reason: str
    signals: tuple[ClassificationSignal, ...]
    ambiguous: bool
    normalizer_version: str
    classifier_version: str
    llm_model: str | None = None
    prompt_version: str | None = None


class ChangeClassificationClient(Protocol):
    def classify_change(self, before: str, after: str, normalized: dict) -> dict: ...


class RuleChangeClassifier:
    version = RULE_CLASSIFIER_VERSION

    def classify(self, change: NormalizedChange) -> ChangeClassification:
        if change.before_text == change.after_text:
            return self._result(change, ChangeType.NO_CODE_IMPACT, 0.99, "개정 전후 문구가 동일함", "identical", "동일 문구")
        if change.structural_signals:
            evidence = ", ".join(change.structural_signals)
            return self._result(change, ChangeType.STRUCTURAL_CHANGE, 0.72, "조문 구조 변경 신호가 있음", "structural", evidence, ambiguous=True)
        if change.comparison_signals and (change.money_changes or change.age_changes or change.duration_changes):
            secondary = (ChangeType.VALUE_CHANGE,) if change.money_changes else ()
            return self._result(change, ChangeType.CONDITION_CHANGE, 0.78, "값과 비교 조건이 함께 변경됨", "comparison", ", ".join(change.comparison_signals), secondary, True)
        if change.rate_changes:
            return self._result(change, ChangeType.RATE_CHANGE, 0.96, "정규화된 비율 값이 변경됨", "rate", _delta_evidence(change.rate_changes))
        if change.date_changes:
            return self._result(change, ChangeType.DATE_CHANGE, 0.95, "정규화된 날짜가 변경됨", "date", _delta_evidence(change.date_changes))
        if change.money_changes:
            return self._result(change, ChangeType.VALUE_CHANGE, 0.96, "정규화된 금액이 변경됨", "money", _delta_evidence(change.money_changes))
        if change.age_changes or change.duration_changes:
            values = change.age_changes or change.duration_changes
            return self._result(change, ChangeType.VALUE_CHANGE, 0.88, "기간 또는 연령 값이 변경됨", "value", _delta_evidence(values))
        return self._result(change, ChangeType.UNKNOWN, 0.2, "명확한 정규화 신호가 없음", "unknown", "수치·구조 신호 없음", ambiguous=True)

    def _result(self, change, primary, confidence, reason, signal_type, evidence, secondary=(), ambiguous=False):
        return ChangeClassification(primary, secondary, confidence, ClassificationSource.RULE, reason, (ClassificationSignal(signal_type, evidence),), ambiguous, change.normalizer_version, self.version)


class HybridChangeClassifier:
    version = HYBRID_CLASSIFIER_VERSION

    def __init__(self, llm: ChangeClassificationClient, rule: RuleChangeClassifier | None = None) -> None:
        self.llm = llm
        self.rule = rule or RuleChangeClassifier()

    def classify(self, change: NormalizedChange) -> ChangeClassification:
        rule_result = self.rule.classify(change)
        if not rule_result.ambiguous and (
            rule_result.confidence >= 0.9
            or (rule_result.confidence >= 0.8 and rule_result.primary_type in {ChangeType.VALUE_CHANGE, ChangeType.RATE_CHANGE, ChangeType.DATE_CHANGE})
        ):
            return replace(rule_result, classifier_version=self.version)
        try:
            raw = self.llm.classify_change(change.before_text, change.after_text, to_jsonable(change))
            return self._from_llm(raw, change, rule_result)
        except (KeyError, TypeError, ValueError, RuntimeError, TimeoutError) as exc:
            return replace(rule_result, confidence=min(rule_result.confidence, 0.79), source=ClassificationSource.FALLBACK, reason=f"{rule_result.reason}; LLM fallback 실패: {exc}", classifier_version=self.version)

    def _from_llm(self, raw: dict, change: NormalizedChange, rule_result: ChangeClassification) -> ChangeClassification:
        primary = ChangeType(raw["primary_type"])
        if (
            rule_result.primary_type is ChangeType.STRUCTURAL_CHANGE
            and primary is not ChangeType.STRUCTURAL_CHANGE
        ):
            return replace(
                rule_result,
                confidence=max(rule_result.confidence, 0.8),
                source=ClassificationSource.HYBRID,
                reason=f"{rule_result.reason}; 구조 변경 안전 우선",
                classifier_version=self.version,
            )
        confidence = float(raw["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        secondary = tuple(ChangeType(value) for value in raw.get("secondary_types", ()))
        signals = tuple(ClassificationSignal(str(item["type"]), str(item["evidence"])) for item in raw["signals"])
        if not signals or not str(raw["reason"]).strip():
            raise ValueError("reason and signals are required")
        source = ClassificationSource.HYBRID if primary == rule_result.primary_type or rule_result.primary_type in secondary else ClassificationSource.LLM
        return ChangeClassification(primary, secondary, confidence, source, str(raw["reason"]), signals, False, change.normalizer_version, self.version)


def _delta_evidence(deltas) -> str:
    parts = []
    for delta in deltas:
        before = delta.before.raw if delta.before else "없음"
        after = delta.after.raw if delta.after else "없음"
        parts.append(f"{before} → {after}")
    return ", ".join(parts)
