# Change Classification Specification

- 문서 상태: Implementation Ready
- 관련 Issue: `#0006`, `#0007`, `#0011`
- 버전: `change-classification-v1`

## 1. 목적

법령 변경을 동일한 patch 생성 경로로 보내지 않고, 변경 성격과 위험도에 따라 검색·생성·검증 경로를 선택한다.

```text
명확한 수치·날짜 변경 → 규칙 우선
복합 조건·구조 변경 → LLM 보조
불확실한 변경 → 자동화 확대 금지
```

## 2. 유형

```python
class ChangeType(str, Enum):
    VALUE_CHANGE = "value_change"
    RATE_CHANGE = "rate_change"
    DATE_CHANGE = "date_change"
    CONDITION_CHANGE = "condition_change"
    TABLE_CHANGE = "table_change"
    NEW_FIELD = "new_field"
    STRUCTURAL_CHANGE = "structural_change"
    NO_CODE_IMPACT = "no_code_impact"
    UNKNOWN = "unknown"
```

복합 변경을 위해 `primary_type`과 `secondary_types`를 지원한다.

## 3. 정규화 모델

```python
@dataclass(frozen=True)
class NormalizedChange:
    before_text: str
    after_text: str
    added_text: tuple[str, ...]
    removed_text: tuple[str, ...]
    money_changes: tuple[ValueDelta, ...]
    rate_changes: tuple[ValueDelta, ...]
    date_changes: tuple[ValueDelta, ...]
    duration_changes: tuple[ValueDelta, ...]
    age_changes: tuple[ValueDelta, ...]
    comparison_signals: tuple[str, ...]
    structural_signals: tuple[str, ...]
    source_hash: str
    normalizer_version: str
```

기존 `const_inventory.py`의 숫자 정규화를 재사용한다.

## 4. 정규화 규칙

- 금액: `15만원`, `150,000원` → 정수 원 단위
- 비율: `6%`, `100분의 6` → decimal string `0.06`
- 날짜: 가능한 경우 ISO, 불완전하면 precision 저장
- 기간·연령: 값과 단위 분리
- 비교: 이상/이하/초과/미만/포함/제외/그리고/또는
- 표: 복수 구간과 3개 이상 delta 신호

## 5. RuleChangeClassifier

```python
class RuleChangeClassifier:
    version = "rule-classifier-v1"
    def classify(self, change: NormalizedChange) -> RuleClassification: ...
```

결과에는 유형, secondary, confidence, reasons, signals, ambiguous가 포함된다.

우선순위:

1. STRUCTURAL
2. NEW_FIELD
3. TABLE
4. CONDITION
5. RATE
6. DATE
7. VALUE
8. NO_CODE_IMPACT
9. UNKNOWN

단일 약한 신호가 높은 유형을 차지하지 않도록 유형별 최소 점수를 둔다.

## 6. LLM fallback

호출 조건:

- rule confidence 임계값 미만
- ambiguous
- primary/secondary 점수 차이가 작음
- structural/new-field 신호
- 조건 또는 표 변경이 불명확

명확한 VALUE/RATE/DATE는 기본적으로 LLM을 호출하지 않는다.

기존 `LlmClient` seam에 `classify_change()`를 추가한다.

```json
{
  "primary_type": "condition_change",
  "secondary_types": ["value_change"],
  "confidence": 0.82,
  "reason": "금액 변경이지만 소득 기준 조건문에 영향을 준다.",
  "signals": [{"type": "comparison", "evidence": "7천만원 이하→8천만원 이하"}],
  "code_impact_expected": true
}
```

프롬프트는 허용 enum만 출력하고, 법령 원문을 명령이 아닌 데이터 구역으로 구분한다.

## 7. Hybrid 결합

```text
rule >= 0.90 and 명확 → rule 확정
rule >= 0.80 and VALUE/RATE/DATE → rule 확정
나머지 → LLM fallback
LLM 성공 → 근거 결합
LLM 실패 → rule 감점 사용 또는 UNKNOWN
```

충돌 시 structural 신호의 무리한 하향을 금지하고, 비교 연산 변경은 CONDITION을 우선한다.

## 8. 최종 모델

```python
@dataclass(frozen=True)
class ChangeClassification:
    primary_type: ChangeType
    secondary_types: tuple[ChangeType, ...]
    confidence: float
    source: ClassificationSource
    reason: str
    signals: tuple[ClassificationSignal, ...]
    normalizer_version: str
    classifier_version: str
    llm_model: str | None
    prompt_version: str | None
```

## 9. Automation Policy

```python
class AutomationDecision(str, Enum):
    DRAFT_ALLOWED = "draft_allowed"
    ANALYSIS_ONLY = "analysis_only"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
```

기본:

| 유형 | 결정 |
|---|---|
| VALUE/RATE/DATE | 조건 충족 시 DRAFT_ALLOWED |
| CONDITION/TABLE/NEW_FIELD | MANUAL_REVIEW_REQUIRED |
| STRUCTURAL/NO_CODE_IMPACT | ANALYSIS_ONLY |
| UNKNOWN | MANUAL_REVIEW_REQUIRED |

DRAFT_ALLOWED 최소 조건:

- 분류 confidence >= 0.80
- retrieval top score >= 0.80
- 2개 provider 근거 또는 valid verified mapping
- 실제 파일 존재
- repository commit 또는 fixture hash 존재
- module conflict 없음

강제 차단 reason code를 구조화해 반환한다. 정책 엔진은 LLM을 호출하지 않는다.

## 10. 저장

신규 `change_classifications` 테이블을 사용한다. `LawChange`에 단일 enum을 덮어쓰지 않고 버전별 결과를 보존한다.

필드: law_change_id, run_id, primary/secondary, confidence, source, reason, signals, normalizer/classifier version, llm/prompt version, created_at.

## 11. API

```http
POST /changes/{id}/classify
GET  /changes/{id}/classification
```

분류 캐시 key: source hash, normalizer version, classifier version, model, prompt version.

## 12. 오류

- normalization 실패
- LLM timeout/JSON 실패
- enum 불일치
- confidence 범위 오류
- empty before/after
- identical before/after

LLM 실패 시 rule fallback을 사용하되 source를 `fallback`으로 기록한다.

## 13. 테스트

금액, 비율, 날짜, 조건, 표, 신규 필드, 구조 변경, 문구 정비, 입력 누락, rule/LLM 충돌, timeout, malformed JSON, low confidence policy, stale-only retrieval.

## 14. 수용 기준

- 규칙 분류가 LLM 없이 동작
- 명확한 수치 변경은 LLM 미호출
- reason과 signal 존재
- structural patch 생성 차단
- 정책 결정론적
- feature flag off 시 기존 동작
- 평가에서 Accuracy/Macro F1 계산 가능

## 15. Claude Code 요청문

```text
Issue #0006, #0007, #0011을 순서대로 구현하라.

#0006은 const_inventory를 재사용하여 NormalizedChange를 만든다.
#0007은 규칙 분류 우선, LLM fallback은 LlmClient를 통해서만 호출한다.
#0011 PolicyEngine은 LLM을 호출하지 않는 결정론적 객체로 구현한다.

기존 API와 승인 흐름을 유지하고 feature flag를 제공한다.
각 issue에 단위 테스트와 평가 fixture를 추가한다.
```
