# regtax-monitor Architecture V2

- 문서 상태: Draft for Implementation
- 대상 버전: v2
- 기준 저장소: `hayleeBae/regtax-monitor`
- 기준일: 2026-07-14
- 상위 원칙: 기존 `docs/ARCHITECTURE.md`, `docs/ADR.md`, `CLAUDE.md`의 제약을 유지한다.
- 목적: 현재의 법령 수집·분석·매핑·patch 초안 생성 파이프라인을 **측정 가능하고, 변경 유형에 따라 통제되며, 재현 가능한 기업형 AI 변경관리 시스템**으로 확장한다.

---

## 1. 문서 목적

현재 시스템은 다음 핵심 기능을 이미 제공한다.

1. 법제처 API 기반 법령·시행령·시행규칙·행정규칙 수집
2. 로컬 LLM 기반 변경 요약 및 영향 분석
3. 코드 RAG, 용어 사전, 상수 인벤토리 기반 관련 코드 매핑
4. 앵커 기반 코드 편집안 생성
5. 서버 측 unified diff 생성
6. 스크래치 저장소 골든 테스트
7. 사람 승인 후 patch 파일 출력

V2의 목적은 기능을 무작정 늘리는 것이 아니다. 다음 네 가지 질문에 시스템이 답할 수 있도록 만드는 것이다.

- 이 결과가 얼마나 정확한가?
- 왜 이 처리 경로가 선택됐는가?
- 동일 조건에서 다시 실행할 수 있는가?
- 자동화해도 되는 변경과 사람이 판단해야 하는 변경을 어떻게 구분하는가?

---

## 2. V2 설계 목표

### 2.1 기능 목표

- 법령 변경을 정형화된 변경 유형으로 분류한다.
- 변경 유형에 따라 검색·생성·검증 전략을 다르게 적용한다.
- 검색 전략별 성능을 같은 데이터셋으로 비교한다.
- 과거 법령 개정과 실제 코드 변경을 재현 평가할 수 있다.
- 승인·거절 결과를 검증 자산으로 축적한다.
- 모든 실행을 `run_id` 기준으로 추적하고 재현할 수 있다.
- 향후 코드 관계 그래프를 추가할 수 있는 확장 지점을 마련한다.

### 2.2 품질 목표

- 기존 `collect → analyze → map → apply → approve/reject` 흐름을 깨뜨리지 않는다.
- 기존 API와 DB 데이터의 하위 호환성을 유지한다.
- LLM이 실패해도 기존 승인 게이트와 골든 테스트가 우회되지 않는다.
- 평가 모드는 운영 데이터와 분리한다.
- 동일 입력·동일 코드 commit·동일 모델 설정의 실행 근거를 저장한다.
- 모든 신규 기능은 feature flag 또는 명시적 설정으로 비활성화할 수 있어야 한다.

### 2.3 비목표

V2 범위에서는 다음을 구현하지 않는다.

- AI가 실제 업무 저장소에 자동 commit 또는 push
- 승인 없는 patch 자동 적용
- LLM fine-tuning
- Neo4j 등 별도 그래프 DB 필수 도입
- 세법 계산 로직의 전면 파라미터 테이블화
- 운영 조직의 권한·인증 체계 전면 개편
- 완전 자동화된 법률적 판단

---

## 3. 유지해야 하는 기존 아키텍처 원칙

### 3.1 두 개의 환경 seam 유지

환경 차이를 흡수하는 교체 지점은 기존과 동일하게 유지한다.

- `app/llm/`: `LlmClient`
- `app/codebase/`: `CodebaseAdapter`

새 기능이 필요하더라도 운영환경별 분기를 다른 계층에 직접 추가하지 않는다.

### 3.2 사람 승인 게이트 유지

`Proposal`은 초안이다. 다음 원칙은 V2에서도 변경하지 않는다.

- AI 결과는 자동 적용하지 않는다.
- 승인 시 patch 파일을 출력한다.
- 골든 테스트 실패 상태에서도 승인 여부는 사람이 결정한다.
- 실패·경고 정보는 숨기지 않는다.

### 3.3 결정론적 검증 우선

LLM은 의미 판단과 코드 편집안 생성에 사용한다. 다음은 일반 코드가 담당한다.

- 입력 스키마 검증
- 파일 경로 확인
- SEARCH 앵커 일치 여부
- unified diff 생성
- git apply 가능 여부
- 테스트 실행
- 지표 계산
- 로그 해시 생성

### 3.4 설정의 단일 진입점

모든 신규 설정은 `config.settings`를 통해 접근한다. 모듈에서 직접 환경변수를 읽지 않는다.

---

## 4. V2 전체 아키텍처

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                           Law Collection                                 │
│ 법제처 API / domains.json / 3-tier law / administrative rules           │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                      Change Normalization                                │
│ 조문 diff / 숫자·비율·날짜 정규화 / 문서 해시 / source metadata         │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    Change Classification                                │
│ Rule Classifier → LLM Fallback → Confidence → Automation Policy         │
└───────────────┬──────────────────────┬───────────────────────┬───────────┘
                │                      │                       │
                ▼                      ▼                       ▼
      VALUE/RATE/DATE          CONDITION/TABLE         STRUCTURAL/UNKNOWN
      exact-first route         hybrid route            analysis-only route
                │                      │                       │
                └──────────────┬───────┴───────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                      Retrieval Orchestrator                              │
│ verified mapping / RAG / term dictionary / constants / optional graph   │
│ 후보 병합 → 중복 제거 → 점수 정규화 → 근거 기록 → top-k                │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     Risk & Automation Policy                             │
│ 변경 유형 + 분류 신뢰도 + 검색 근거 + 과거 검증 + 영향 범위            │
│ → DRAFT_ALLOWED / ANALYSIS_ONLY / MANUAL_REVIEW_REQUIRED                 │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                    ┌───────────┴────────────┐
                    │                        │
                    ▼                        ▼
          Analysis / Mapping only       LLM Code Editor
                                             │
                                             ▼
                                   Anchor Validator & Retry
                                             │
                                             ▼
                                       Patch Builder
                                             │
                                             ▼
                                    Scratch Golden Test
                                             │
                                             ▼
                                       Human Review
                                             │
                                             ▼
                                     Patch File Output

모든 단계:
Run Context → Audit Event → Metrics → Artifact Store
```

---

## 5. 핵심 컴포넌트

## 5.1 ChangeNormalizer

### 책임

법령 변경 원문을 분류·검색·평가에 사용할 수 있는 표준 구조로 변환한다.

### 입력

- `before_text`
- `after_text`
- 법령명
- 조문 식별자
- `domain`
- `tier`
- 시행일 및 공포일
- 원문 출처

### 출력 예시

```json
{
  "before_text": "자녀 1명당 연 15만원",
  "after_text": "자녀 1명당 연 25만원",
  "normalized_diff": {
    "removed_values": [
      {"raw": "15만원", "kind": "money", "value": 150000}
    ],
    "added_values": [
      {"raw": "25만원", "kind": "money", "value": 250000}
    ],
    "dates": [],
    "ratios": []
  },
  "source_hash": "sha256:...",
  "normalizer_version": "v1"
}
```

### 설계 원칙

- 기존 `const_inventory.py`의 수치 정규화 기능을 재사용한다.
- 동일 정규화 로직을 분류기와 검색기가 각각 구현하지 않는다.
- 원문과 정규화 결과를 모두 보존한다.

---

## 5.2 ChangeClassifier

### 변경 유형

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

### 분류 단계

1. 규칙 기반 분류
2. 규칙 결과가 모호할 경우 LLM fallback
3. 두 결과를 결합하여 최종 유형과 신뢰도 산출
4. 분류 근거 저장
5. 자동화 정책 결정에 전달

### 규칙 기반 예시

| 조건 | 기본 유형 |
|---|---|
| 동일 문맥에서 금액 값만 변경 | `VALUE_CHANGE` |
| 퍼센트·분수·세율 변경 | `RATE_CHANGE` |
| 시행일·기한·연령 기준일 변경 | `DATE_CHANGE` |
| “이상/이하/초과/미만/해당하는 경우” 조건 변경 | `CONDITION_CHANGE` |
| 구간별 금액·세율 표의 복수 행 변경 | `TABLE_CHANGE` |
| 새 공제 항목·새 제출 항목 명시 | `NEW_FIELD` |
| 계산 체계·적용 주체·법률 구조 변경 | `STRUCTURAL_CHANGE` |

### LLM 출력 스키마

```json
{
  "change_type": "condition_change",
  "confidence": 0.78,
  "reason": "소득 기준의 비교 연산과 적용 대상이 변경됨",
  "signals": [
    "기존 '7천만원 이하'가 '8천만원 이하'로 변경",
    "단순 상수 변경이지만 대상 조건문에 영향을 줄 가능성이 있음"
  ],
  "recommended_policy": "manual_review_required"
}
```

### 실패 정책

- JSON 파싱 실패: 기존 공통 JSON 복구 로직 사용
- LLM 호출 실패: 규칙 결과가 있으면 사용, 없으면 `UNKNOWN`
- 신뢰도 기준 미달: `MANUAL_REVIEW_REQUIRED`
- 분류 실패가 `apply` 차단 원인이 되더라도 `analyze`와 `map`은 가능해야 한다.

---

## 5.3 RetrievalOrchestrator

### 목적

현재 분산된 검색 결과를 하나의 표준 후보 모델로 통합한다.

### 검색 소스

1. `verified_mapping`
2. `rag`
3. `term_dictionary`
4. `constant_match`
5. `code_graph` — V2 후반 선택 기능
6. `historical_commit` — 과거 사례 사용 시

### 후보 모델

```json
{
  "path": "src/main/java/IncomeTaxService.java",
  "symbol": "calculateChildTaxCredit",
  "line_start": 120,
  "line_end": 160,
  "sources": [
    {
      "type": "constant_match",
      "raw_score": 1.0,
      "normalized_score": 1.0,
      "evidence": ["150000L"]
    },
    {
      "type": "rag",
      "raw_score": 0.82,
      "normalized_score": 0.76,
      "evidence": ["자녀세액공제 계산"]
    }
  ],
  "verified": false,
  "final_score": 0.91,
  "rank": 1
}
```

### 병합 규칙

- 경로와 심볼 또는 청크 위치가 같은 후보는 하나로 병합한다.
- 소스별 원점수는 덮어쓰지 않고 보존한다.
- 최종 점수 산식은 버전 관리한다.
- `verified_mapping`은 무조건 정답으로 간주하지 않고, 현재 commit에서 경로·앵커가 유효한지 검사한다.
- 오래된 검증 매핑은 `stale`로 표시한다.

### 초기 점수 예시

```text
final_score =
    0.35 * verified_score
  + 0.25 * constant_score
  + 0.20 * dictionary_score
  + 0.15 * rag_score
  + 0.05 * graph_score
```

이 값은 기본값일 뿐이며 평가 결과를 통해 변경한다. 하드코딩하지 않고 설정 또는 버전 객체로 관리한다.

---

## 5.4 AutomationPolicyEngine

### 목적

“관련 코드를 찾았다”와 “patch 초안을 만들어도 안전하다”를 분리한다.

### 정책 결과

```python
class AutomationDecision(str, Enum):
    DRAFT_ALLOWED = "draft_allowed"
    ANALYSIS_ONLY = "analysis_only"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
```

### 기본 정책

| 변경 유형 | 기본 결정 |
|---|---|
| `VALUE_CHANGE` | 높은 근거가 있으면 `DRAFT_ALLOWED` |
| `RATE_CHANGE` | 높은 근거가 있으면 `DRAFT_ALLOWED` |
| `DATE_CHANGE` | 설정/상수 위치 확인 시 `DRAFT_ALLOWED` |
| `CONDITION_CHANGE` | `MANUAL_REVIEW_REQUIRED` |
| `TABLE_CHANGE` | 복수 파일·테스트가 확인되면 제한적으로 허용 |
| `NEW_FIELD` | `MANUAL_REVIEW_REQUIRED` |
| `STRUCTURAL_CHANGE` | `ANALYSIS_ONLY` |
| `NO_CODE_IMPACT` | `ANALYSIS_ONLY` |
| `UNKNOWN` | `MANUAL_REVIEW_REQUIRED` |

### `DRAFT_ALLOWED` 최소 조건 예시

- 분류 신뢰도 `>= 0.80`
- 검색 top-1 최종 점수 `>= 0.80`
- 서로 다른 검색 소스 2개 이상의 근거 또는 유효한 verified mapping
- 실제 파일 존재
- 검색 대상 commit이 기록됨
- 구조 변경 신호 없음

### 강제 차단 조건

- `STRUCTURAL_CHANGE`
- 파일 경로가 repository root 밖을 가리킴
- 후보가 모두 stale
- 검색 후보 간 모듈 충돌이 큼
- 원문이 지나치게 짧거나 diff가 없음
- 실행에 필요한 코드 commit을 확인할 수 없음

---

## 5.5 Evaluation Framework

### 평가 단위

하나의 평가 케이스는 다음을 포함한다.

```yaml
case_id: tax_child_credit_2024
domain: tax
law:
  name: 소득세법
  article: 제59조의2
  before_text: 자녀 1명당 연 15만원
  after_text: 자녀 1명당 연 25만원
expected:
  change_type: value_change
  files:
    - src/main/java/IncomeTaxService.java
    - src/test/java/IncomeTaxServiceTest.java
  symbols:
    - calculateChildTaxCredit
  replacements:
    - before: "150000"
      after: "250000"
repository:
  fixture: mock_repo
  base_commit: null
  answer_commit: null
policy:
  expected_decision: draft_allowed
```

### 핵심 지표

#### 분류

- Accuracy
- Macro F1
- 유형별 Precision/Recall
- Low-confidence rate

#### 검색

- Recall@1
- Recall@5
- MRR
- File precision
- Candidate coverage
- Latency

#### 편집 및 patch

- 1차 앵커 적용 성공률
- 재시도 후 앵커 적용 성공률
- `git apply` 성공률
- 예상 파일 수정 재현율
- 불필요한 파일 수정률
- golden test pass rate

#### 운영 효율

- 단계별 latency
- LLM 호출 횟수
- retry 횟수
- 입력·출력 근사 token
- 사람 검토시간 — 수동 기록 또는 별도 이벤트

### 평가 실행 모드

```bash
python -m evaluation.runner \
  --dataset evaluation/datasets/core.jsonl \
  --experiment hybrid_all \
  --output evaluation/results/run-20260714
```

### 운영 DB 분리

평가 실행은 기본적으로 운영 `regtax.db`에 쓰지 않는다.

- 별도 임시 DB
- 별도 Chroma collection 또는 별도 persist directory
- 평가 artifact 전용 출력 폴더
- 외부 법제처 API 호출 금지 또는 fixture 고정

---

## 5.6 Experiment Runner

### 실험 조합

| ID | verified | RAG | dictionary | constants | graph |
|---|---:|---:|---:|---:|---:|
| `rag_only` | 0 | 1 | 0 | 0 | 0 |
| `rag_dict` | 0 | 1 | 1 | 0 | 0 |
| `rag_const` | 0 | 1 | 0 | 1 | 0 |
| `hybrid_all` | 0 | 1 | 1 | 1 | 0 |
| `verified_hybrid` | 1 | 1 | 1 | 1 | 0 |
| `graph_hybrid` | 1 | 1 | 1 | 1 | 1 |

### 비교 원칙

- 같은 데이터셋
- 같은 embedding model
- 같은 index snapshot
- 같은 repository commit
- 같은 top-k
- 같은 score normalization
- 생성 모델 평가는 retrieval 평가와 분리 가능

### 결과 산출물

- `summary.json`
- `cases.jsonl`
- `report.md`
- `failures.md`
- `config_snapshot.json`

---

## 5.7 Historical Replay

### 목적

과거 법령 개정 시점의 코드에서 시스템을 실행하고 실제 수정 commit과 비교한다.

### 처리 흐름

```text
historical case
→ base commit checkout to temporary worktree
→ index build or snapshot load
→ analyze/classify/map/apply
→ generated patch
→ answer commit diff extraction
→ semantic and structural comparison
→ report
```

### 정답의 정의

실제 commit 전체를 기계적으로 정답으로 사용하지 않는다. 한 commit에 다른 업무 변경이 섞일 수 있으므로 평가 케이스 작성자가 관련 파일과 변경 범위를 명시한다.

### 비교 기준

- 수정 대상 파일의 교집합
- before/after 값 일치
- 조건식의 의미 일치
- 테스트 통과
- 불필요 변경
- 실제 commit과 줄 단위 유사도 — 참고 지표로만 사용

### 보안

회사 repo commit을 평가 데이터로 사용할 경우 원문 코드를 공개 저장소에 포함하지 않는다.

- 경로·해시·익명화 메타데이터만 공개 가능
- 실제 fixture는 사내 저장소 또는 로컬 경로로 관리
- 평가 결과에도 코드 본문 출력 여부를 설정으로 통제

---

## 5.8 Verified Mapping Feedback

### 현재 문제

`verified=True`만으로는 다음 정보를 표현하기 어렵다.

- 누가 확인했는가?
- 어느 commit에서 유효했는가?
- 왜 승인했는가?
- 이후 코드 이동으로 stale해졌는가?
- 잘못된 후보를 왜 거절했는가?

### V2 모델

```text
Mapping
└── MappingDecision (append-only)
    ├── decision: verified / rejected / stale
    ├── reason_code
    ├── reason_text
    ├── repository_commit
    ├── path_hash
    ├── actor
    └── created_at
```

### 거절 사유 코드

- `wrong_module`
- `legacy_code`
- `false_positive_term`
- `same_value_unrelated`
- `generated_code`
- `test_only`
- `stale_path`
- `insufficient_context`
- `other`

### 재사용 정책

- 같은 법령 조문 및 유사 변경 유형의 verified mapping을 우선한다.
- commit 또는 파일 해시가 달라졌다면 경로·심볼 유효성을 재검증한다.
- 거절 이력은 동일 근거가 반복될 때 점수 감점에 사용한다.
- 초기 버전에서는 학습 모델이 아니라 규칙 기반 재정렬만 수행한다.

---

## 5.9 Audit & Traceability

### Run 모델

모든 주요 실행은 `run_id`를 가진다.

```json
{
  "run_id": "run_01J...",
  "run_type": "production",
  "change_id": 123,
  "repository_commit": "abc123",
  "source_hash": "sha256:...",
  "embedding_model": "BAAI/bge-m3",
  "llm_backend": "local",
  "llm_model": "qwen3:8b",
  "prompt_versions": {
    "analysis": "analysis-v2",
    "classification": "classification-v1",
    "edit": "edit-v2"
  },
  "settings_hash": "sha256:...",
  "started_at": "...",
  "completed_at": "...",
  "status": "completed"
}
```

### AuditEvent

단계별 append-only 이벤트를 저장한다.

```text
RUN_CREATED
NORMALIZATION_COMPLETED
CLASSIFICATION_COMPLETED
RETRIEVAL_COMPLETED
POLICY_DECIDED
LLM_REQUESTED
LLM_RESPONDED
ANCHOR_VALIDATION_FAILED
RETRY_REQUESTED
PATCH_BUILT
GOLDEN_TEST_COMPLETED
PROPOSAL_APPROVED
PROPOSAL_REJECTED
RUN_FAILED
```

### 저장 원칙

- 민감 코드 본문 전체를 DB에 중복 저장하지 않는다.
- 필요 시 artifact 파일로 저장하고 DB에는 경로·해시만 기록한다.
- 프롬프트는 템플릿 버전과 입력 artifact를 분리한다.
- 비밀키와 인증정보는 절대 기록하지 않는다.
- 로그는 append-only를 원칙으로 한다.
- 재현 불가능한 항목은 명시적으로 `unavailable`로 기록한다.

---

## 5.10 Code Graph Extension Point

### 목적

V2 초기 필수 기능은 아니지만, 관련 파일 집합 검색을 위해 확장 지점을 정의한다.

### 최소 관계

- `CALLS`
- `IMPLEMENTS`
- `MAPS_TO`
- `READS_FIELD`
- `WRITES_FIELD`
- `TESTED_BY`
- `USES_CONSTANT`

### 저장 방식

초기에는 JSON 또는 SQLite 테이블로 충분하다.

```text
CodeSymbol
CodeRelation
```

별도 그래프 DB는 데이터 규모와 조회 요구가 확인된 뒤 ADR로 결정한다.

### 검색 통합

그래프 결과는 독립적인 정답이 아니라 retrieval candidate의 추가 근거로 사용한다.

---

## 6. 제안 디렉토리 구조

```text
app/
├── application/
│   ├── analysis_service.py
│   ├── mapping_service.py
│   ├── proposal_service.py
│   └── evaluation_service.py
├── domain/
│   ├── changes/
│   │   ├── models.py
│   │   ├── classification.py
│   │   └── policy.py
│   ├── retrieval/
│   │   ├── candidate.py
│   │   ├── scoring.py
│   │   └── experiment.py
│   ├── audit/
│   │   └── events.py
│   └── evaluation/
│       ├── case.py
│       └── metrics.py
├── classification/
│   ├── rule_classifier.py
│   ├── llm_classifier.py
│   └── classifier.py
├── retrieval/
│   ├── orchestrator.py
│   ├── verified_provider.py
│   ├── rag_provider.py
│   ├── dictionary_provider.py
│   ├── constant_provider.py
│   └── graph_provider.py
├── audit/
│   ├── recorder.py
│   ├── artifacts.py
│   └── replay.py
├── evaluation/
│   ├── loader.py
│   ├── runner.py
│   ├── experiments.py
│   ├── metrics/
│   └── report.py
└── ...

evaluation/
├── datasets/
├── fixtures/
├── results/
└── README.md
```

### 구조 적용 원칙

현재 프로젝트가 단일 `main.py` 중심이므로 한 번에 전체 구조를 이전하지 않는다.

1. 신규 기능은 신규 패키지로 분리한다.
2. 기존 라우트는 application service를 호출하도록 점진적으로 이동한다.
3. collector/llm/codebase/embedding/db는 기존 public interface를 유지한다.
4. 대규모 디렉토리 이동은 별도 issue와 ADR 없이는 수행하지 않는다.

---

## 7. 데이터 모델 변경안

## 7.1 신규 테이블

### `execution_runs`

- `id`
- `run_id` unique
- `run_type`
- `law_change_id`
- `repository_commit`
- `source_hash`
- `settings_hash`
- `llm_backend`
- `llm_model`
- `embedding_model`
- `status`
- `started_at`
- `completed_at`

### `audit_events`

- `id`
- `run_id`
- `sequence_no`
- `event_type`
- `payload_json`
- `artifact_path`
- `artifact_hash`
- `created_at`

### `change_classifications`

- `id`
- `law_change_id`
- `run_id`
- `change_type`
- `confidence`
- `classifier_source`
- `reason`
- `signals_json`
- `classifier_version`
- `created_at`

### `mapping_decisions`

- `id`
- `mapping_id`
- `decision`
- `reason_code`
- `reason_text`
- `repository_commit`
- `path_hash`
- `actor`
- `created_at`

### `evaluation_runs`

- `id`
- `run_id`
- `dataset_id`
- `experiment_id`
- `config_json`
- `summary_json`
- `artifact_path`
- `created_at`

## 7.2 마이그레이션 원칙

현재 DB가 SQLite와 SQLAlchemy create-all 방식이라면, 테이블 추가는 가능하나 기존 컬럼 변경은 안전하지 않다.

- 신규 테이블 추가를 우선한다.
- 기존 `Mapping.verified`는 즉시 삭제하지 않는다.
- `verified` 변경 API는 내부적으로 `MappingDecision`도 함께 기록하도록 호환 계층을 둔다.
- 데이터 마이그레이션 스크립트를 별도로 제공한다.
- Alembic 도입은 별도 ADR로 결정한다.

---

## 8. API 변경안

## 8.1 기존 API 유지

기존 API는 유지한다.

- `POST /collect`
- `POST /changes/{id}/analyze`
- `POST /changes/{id}/map`
- `POST /changes/{id}/apply`
- `PATCH /mappings/{id}/verify`
- `POST /proposals/{id}/approve`
- `POST /proposals/{id}/reject`

## 8.2 신규 API

### 분류

```http
POST /changes/{id}/classify
GET  /changes/{id}/classification
```

### 실행 추적

```http
GET /runs/{run_id}
GET /runs/{run_id}/events
POST /runs/{run_id}/replay
```

`replay`는 동일 artifact가 존재하는 범위에서만 허용하며 실제 repo에 변경을 적용하지 않는다.

### 매핑 의사결정

```http
POST /mappings/{id}/decisions
GET  /mappings/{id}/decisions
```

기존 verify API는 하위 호환 래퍼로 유지한다.

### 평가

```http
POST /evaluations
GET  /evaluations/{run_id}
GET  /evaluations/{run_id}/report
```

초기 구현에서는 평가를 CLI 전용으로 시작하고 API는 후속 issue로 미룰 수 있다.

---

## 9. 설정 변경안

```env
# Classification
CHANGE_CLASSIFICATION_ENABLED=true
CHANGE_CLASSIFICATION_LLM_FALLBACK=true
CHANGE_CLASSIFICATION_MIN_CONFIDENCE=0.80

# Policy
AUTOMATION_POLICY_ENABLED=true
AUTOMATION_DRAFT_MIN_SCORE=0.80
AUTOMATION_REQUIRE_MULTI_SOURCE=true

# Retrieval
RETRIEVAL_VERIFIED_ENABLED=true
RETRIEVAL_RAG_ENABLED=true
RETRIEVAL_DICTIONARY_ENABLED=true
RETRIEVAL_CONSTANT_ENABLED=true
RETRIEVAL_GRAPH_ENABLED=false
RETRIEVAL_TOP_K=10
RETRIEVAL_SCORING_VERSION=v1

# Audit
AUDIT_ENABLED=true
AUDIT_ARTIFACT_DIR=data/audit
AUDIT_STORE_LLM_RAW_OUTPUT=true
AUDIT_STORE_CODE_SNIPPETS=false

# Evaluation
EVALUATION_DATA_DIR=evaluation/datasets
EVALUATION_RESULT_DIR=evaluation/results
EVALUATION_ISOLATED_DB=true
```

설정명은 구현 시 기존 naming convention과 충돌 여부를 확인한다.

---

## 10. 주요 시퀀스

## 10.1 분석·분류·매핑

```text
User/API
  → AnalysisService
  → RunRecorder.create()
  → ChangeNormalizer.normalize()
  → LlmClient.analyze()
  → ChangeClassifier.classify()
  → RetrievalOrchestrator.retrieve()
  → AutomationPolicyEngine.decide()
  → DB save
  → RunRecorder.complete()
```

## 10.2 patch 생성

```text
User/API
  → ProposalService
  → load latest classification
  → load retrieval candidates
  → policy check
  → if ANALYSIS_ONLY: reject draft generation with structured reason
  → LlmClient.generate_edits()
  → AnchorValidator
  → RetryController
  → PatchBuilder
  → GoldenTest
  → Proposal save
  → Audit events save
```

## 10.3 평가

```text
CLI
  → DatasetLoader
  → for each experiment
      → isolated fixture
      → run classification/retrieval/proposal
      → MetricsCollector
  → ExperimentComparator
  → ReportWriter
```

---

## 11. 오류 처리

### 오류 분류

- `INPUT_ERROR`
- `SOURCE_UNAVAILABLE`
- `CLASSIFICATION_ERROR`
- `RETRIEVAL_ERROR`
- `POLICY_BLOCKED`
- `LLM_ERROR`
- `ANCHOR_ERROR`
- `PATCH_ERROR`
- `GOLDEN_TEST_ERROR`
- `AUDIT_ERROR`
- `EVALUATION_ERROR`

### 원칙

- 오류를 일반 문자열 하나로 저장하지 않는다.
- 사용자에게 보여줄 메시지와 내부 원인을 분리한다.
- audit 저장 실패가 원래 업무 결과를 조용히 유실시키지 않게 한다.
- 민감정보는 오류 payload에서 제거한다.
- 재시도 가능한 오류 여부를 표시한다.

---

## 12. 보안 및 개인정보

- 기본 LLM backend는 로컬을 유지한다.
- 평가 artifact에 실제 코드가 포함될 수 있으므로 기본 gitignore 대상이다.
- raw LLM 출력 저장 여부를 설정으로 제어한다.
- 외부 Claude backend 사용 시 run metadata에 외부 전송 여부를 표시한다.
- 코드 경로가 repository root를 벗어나지 않도록 resolve 후 검증한다.
- prompt injection을 방지하기 위해 법령·코드 입력은 명령이 아니라 데이터 구간으로 명확히 구분한다.
- audit payload에 API key, token, cookie, `.env` 값 저장을 금지한다.

---

## 13. 관측성과 성능

### 필수 로그

- `run_id`
- 단계명
- 처리 시간
- 모델명
- prompt version
- 입력 근사 token
- 출력 token 또는 근사치
- 후보 수
- retry 수
- policy result
- golden result

### 성능 기준

초기 목표값은 기준선이며 평가 후 조정한다.

- 규칙 분류: 평균 100ms 이내
- retrieval orchestration: 기존 map 대비 20% 이상 악화하지 않음
- audit 저장: 단계별 50ms 이내 목표
- 평가 1건 실패가 전체 평가 실행을 중단하지 않음
- CPU 환경에서 생성 latency는 품질·재현성보다 후순위

---

## 14. 테스트 전략

### 단위 테스트

- 수치·비율·날짜 정규화
- 규칙 기반 분류
- 분류 fallback
- 후보 병합 및 점수 계산
- policy decision
- metric 계산
- audit event 순서
- mapping stale 판정

### 통합 테스트

- mock law change → classify → retrieve → policy
- draft allowed case → patch → golden pass
- structural change → analysis only
- stale verified mapping → fallback retrieval
- LLM JSON 실패 → 복구 또는 UNKNOWN
- audit artifact hash 검증

### 회귀 테스트

- 기존 API 응답 및 상태 흐름
- 기존 mock 자녀세액공제 사례
- 기존 최저임금 사례
- 기존 승인/거절
- 기존 `verify.sh quick/full/security`

### 평가 fixture

최소 20개로 시작한다.

- value 5
- rate 3
- date 2
- condition 4
- table 2
- new field 1
- structural 2
- no-code-impact 1

---

## 15. 수용 기준

V2 핵심 설계 완료의 수용 기준은 다음과 같다.

1. 기존 전체 테스트가 통과한다.
2. 변경 유형 분류가 DB 또는 artifact에 저장된다.
3. 정책 엔진이 구조 변경의 patch 자동 생성을 차단할 수 있다.
4. retrieval 후보가 소스별 근거와 점수를 포함한다.
5. 동일 데이터셋으로 최소 4개 retrieval 실험을 실행할 수 있다.
6. Recall@1, Recall@5, MRR 보고서가 생성된다.
7. patch 적용·재시도·golden 결과 지표가 생성된다.
8. `run_id`로 분석부터 승인까지 이벤트를 조회할 수 있다.
9. 기존 `Mapping.verified` 흐름이 깨지지 않는다.
10. 실제 저장소에 자동 변경을 가하는 경로가 추가되지 않는다.

---

## 16. 구현 원칙

- 하나의 issue에서 아키텍처 재편과 기능 개발을 동시에 하지 않는다.
- 각 issue는 독립적으로 revert 가능해야 한다.
- 신규 도메인 객체와 서비스는 기존 모듈을 직접 역참조하지 않는다.
- 공통 LLM 프롬프트·파싱 규칙은 기존 `app/llm/common.py` 중복을 만들지 않는다.
- 신규 LLM 호출은 `LlmClient` 인터페이스를 확장하거나 명확한 application service를 거친다.
- 테스트 없는 점수 산식 변경을 금지한다.
- prompt version을 변경하면 평가 결과의 이전 버전과 구분한다.
- 자동화 정책을 완화하는 변경은 ADR과 평가 결과를 요구한다.

---

## 17. 후속 상세 명세

이 문서는 전체 구조를 정의한다. 실제 구현 전에 다음 상세 명세를 순서대로 작성한다.

1. `EVALUATION_SPEC.md`
2. `CHANGE_CLASSIFICATION_SPEC.md`
3. `RETRIEVAL_EXPERIMENT_SPEC.md`
4. `AUDIT_AND_TRACEABILITY_SPEC.md`
5. `VERIFIED_MAPPING_SPEC.md`
6. `HISTORICAL_REPLAY_SPEC.md`
7. `CODE_GRAPH_SPEC.md`

각 상세 명세는 데이터 스키마, 클래스·함수 계약, 오류 코드, 테스트 케이스, 수용 기준을 포함해야 한다.
