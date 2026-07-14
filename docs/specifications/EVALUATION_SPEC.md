# Evaluation Framework Specification

- 문서 상태: Implementation Ready
- 관련 Issue: `#0004 Evaluation Dataset & Metrics`, `#0005 Evaluation Runner & Report`
- 상위 문서: `ARCHITECTURE_V2.md`, `IMPLEMENTATION_ROADMAP.md`
- 버전: `evaluation-spec-v1`

## 1. 목적

regtax-monitor의 분류·검색·patch 생성 성능을 동일한 데이터와 설정으로 반복 측정한다. 개별 성공 사례가 아니라 다음을 수치로 설명할 수 있어야 한다.

- 변경 유형 분류 정확도
- 관련 코드 Recall@K와 MRR
- RAG·용어 사전·상수 매칭의 기여
- 앵커 1차 및 재시도 성공률
- `git apply` 성공률
- 골든 테스트 통과율
- 불필요 파일 수정률
- 단계별 지연시간과 재시도 횟수

## 2. 설계 원칙

1. 평가 실행은 운영 DB와 운영 Chroma index를 사용하지 않는다.
2. core fixture는 네트워크와 LLM 없이도 실행 가능해야 한다.
3. 하나의 case 실패가 전체 평가를 중단하지 않는다.
4. 정답, 입력, 모델, prompt, repository commit, 설정을 결과와 함께 기록한다.
5. 자유형 요약의 문체 점수보다 구조화된 사실과 코드 결과를 우선 평가한다.
6. 실제 회사 코드는 공개 fixture와 분리한다.

## 3. 디렉토리

```text
app/evaluation/
├── case.py
├── loader.py
├── result.py
├── runner.py
├── experiments.py
├── report.py
├── environment.py
├── errors.py
└── metrics/
    ├── classification.py
    ├── retrieval.py
    ├── patch.py
    └── runtime.py

evaluation/
├── datasets/
├── fixtures/
└── results/              # gitignore
```

## 4. 평가 케이스

```python
@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    title: str
    domain: str
    tags: tuple[str, ...]
    law: LawInput
    expected: ExpectedOutcome
    repository: RepositoryFixture
    execution: ExecutionExpectation
    metadata: CaseMetadata
```

### YAML 예시

```yaml
schema_version: "1"
case_id: "tax_child_credit_value_001"
title: "자녀세액공제 금액 변경"
domain: "tax"
tags: ["value_change", "java", "test_update"]

law:
  law_name: "소득세법"
  tier: "law"
  article: "제59조의2"
  before_text: "자녀 1명당 연 15만원을 공제한다."
  after_text: "자녀 1명당 연 25만원을 공제한다."
  effective_date: "2026-01-01"

expected:
  change_type: "value_change"
  automation_decision: "draft_allowed"
  retrieval:
    relevant_files:
      - "src/main/java/com/example/tax/IncomeTaxService.java"
      - "src/test/java/com/example/tax/IncomeTaxServiceTest.java"
    primary_files:
      - "src/main/java/com/example/tax/IncomeTaxService.java"
    relevant_symbols:
      - "IncomeTaxService.calculateChildTaxCredit"
  patch:
    expected_replacements:
      - path: "src/main/java/com/example/tax/IncomeTaxService.java"
        before: "150000L"
        after: "250000L"
        match_mode: "normalized_text"
    forbidden_files:
      - "src/main/java/com/example/tax/LegacyIncomeTaxService.java"
    require_git_apply: true
    require_golden_pass: true

repository:
  fixture_type: "directory"
  path: "evaluation/fixtures/repositories/child_credit"
  base_commit: null
  answer_commit: null
  golden_command: "python3 tests/golden_income_tax.py"

execution:
  evaluate_classification: true
  evaluate_retrieval: true
  evaluate_patch: true
  top_k: [1, 3, 5, 10]
  timeout_seconds: 600

metadata:
  source: "synthetic"
  reviewed: true
```

## 5. 데이터 검증

`DatasetLoader`는 실행 전에 다음을 검사한다.

- schema version
- `case_id` 중복
- fixture 경로와 예상 파일 존재
- relevant/forbidden 중복
- patch 평가 시 expected replacement 존재
- historical case의 commit 유효성
- repository root 밖 경로 참조
- enum과 timeout 범위

검증 실패 시 실행하지 않고 `DatasetValidationError`를 반환한다.

## 6. 분류 지표

- Accuracy
- 유형별 Precision, Recall, F1
- Macro F1
- 평균 confidence
- low-confidence rate
- high-confidence wrong rate

`high-confidence wrong`의 기본 기준은 confidence `>= 0.80`이다.

## 7. 검색 지표

### Case Hit@K

상위 K개에 정답 파일이 하나 이상 존재하는지 측정한다.

### File Recall@K

```text
상위 K개에서 발견된 정답 파일 수 / 전체 정답 파일 수
```

### MRR

```text
MRR = mean(1 / 첫 정답 rank)
```

검색되지 않으면 0이다.

### Precision@K

```text
상위 K개 중 정답 파일 수 / 실제 반환 후보 수
```

### 추가 지표

- Primary file hit@1/3/5
- provider별 정답 기여
- candidate coverage
- provider 실패 건수
- 평균 검색시간

## 8. patch 지표

- Anchor first-pass success
- Anchor retry success
- `git apply --check` 성공률
- 실제 `git apply` 성공률
- expected replacement accuracy
- relevant patch file coverage
- unnecessary file rate
- golden test pass rate

골든 상태는 `passed`, `failed`, `apply_failed`, `timeout`, `skipped`, `error`로 구분한다.

## 9. 분석 결과 평가

자유형 요약 전체를 LLM judge로 평가하지 않는다. 다음 구조화 사실만 검사한다.

```yaml
analysis:
  required_facts:
    - {type: old_value, value: "150000"}
    - {type: new_value, value: "250000"}
    - {type: impact_keyword, value: "자녀세액공제"}
  forbidden_claims:
    - "시행일이 2025년이다"
```

숫자 정규화, 필수 키워드, 필드 존재, 금지 주장 포함 여부를 검사한다.

## 10. 결과 모델

```python
@dataclass
class CaseResult:
    case_id: str
    status: CaseStatus
    experiment_id: str
    duration_ms: int
    classification: ClassificationResult | None
    retrieval: RetrievalResult | None
    patch: PatchResult | None
    errors: list[EvaluationError]
    artifacts: list[ArtifactReference]
```

상태: `passed`, `partial`, `failed`, `skipped`, `error`.

## 11. Experiment 계약

```python
class EvaluationExperiment(Protocol):
    experiment_id: str
    def prepare(self, context: EvaluationContext) -> None: ...
    def run_case(self, case: EvaluationCase, context: EvaluationContext) -> CaseResult: ...
    def close(self) -> None: ...
```

초기 구현:

- `FixtureBaselineExperiment`
- `RetrievalExperiment`
- `PipelineExperiment`

## 12. 실행 격리

- DB: in-memory SQLite 또는 임시 파일
- Chroma: 임시 persist directory 또는 읽기 전용 snapshot
- repository: fixture 복사본 또는 임시 worktree
- 법제처 API: fixture로 대체
- 운영 `regtax.db`, `chroma_data/`, 실제 working tree 수정 금지

## 13. CLI

```bash
python -m app.evaluation.runner \
  --dataset evaluation/datasets/core.yaml \
  --experiment fixture_baseline \
  --result-dir evaluation/results \
  --run-name local-baseline \
  --fail-fast false
```

옵션: `--case-id`, `--tag`, `--timeout`, `--max-workers`, `--config`, `--dry-run`.

## 14. 결과 파일

```text
evaluation/results/<run-id>/
├── manifest.json
├── config_snapshot.json
├── summary.json
├── cases.jsonl
├── report.md
├── failures.md
└── artifacts/
```

`manifest.json`에는 dataset hash, experiment, git commit, Python/platform, 모델, prompt version, 실행시간과 결과 hash를 저장한다.

## 15. 보고서

`report.md` 순서:

1. 실행 정보
2. 데이터셋 구성
3. 전체 지표
4. 유형별 분류 지표
5. Recall@K, MRR
6. provider 기여
7. patch/golden 결과
8. latency
9. 실패 분포
10. 실패 case 목록

모든 비율은 분모를 함께 표기한다. 예: `Recall@5 90.0% (18/20)`.

## 16. 최소 core dataset

| 유형 | 건수 |
|---|---:|
| VALUE_CHANGE | 5 |
| RATE_CHANGE | 3 |
| DATE_CHANGE | 2 |
| CONDITION_CHANGE | 4 |
| TABLE_CHANGE | 2 |
| NEW_FIELD | 1 |
| STRUCTURAL_CHANGE | 2 |
| NO_CODE_IMPACT | 1 |

## 17. 오류 코드

- `dataset_invalid`
- `fixture_not_found`
- `prepare_failed`
- `classification_failed`
- `retrieval_failed`
- `patch_failed`
- `golden_failed`
- `timeout`
- `internal_error`

오류 하나가 다음 case 실행을 막지 않는다.

## 18. 테스트

### 단위

schema, duplicate id, invalid path, Recall@K, MRR, Macro F1, duplicate candidate, empty prediction, replacement accuracy, unnecessary file rate, summary aggregation.

### 통합

fixture 3건 실행, case 오류 격리, 결과 파일과 hash, dry-run, filtering, timeout.

## 19. 보안

- 결과 폴더 gitignore
- 코드 snippet 저장 기본 false
- `.env` 및 secret 저장 금지
- command 실행은 기존 golden 정책 재사용
- private dataset은 저장소 외부 경로 허용

## 20. 수용 기준

### Issue #0004

- 스키마와 loader
- 최소 10개 fixture
- classification/retrieval/patch metric
- 네트워크 없이 테스트

### Issue #0005

- CLI runner
- 실패 격리
- manifest/summary/cases/report/failures
- fixture baseline 실행
- `verify.sh quick/full` 통과

## 21. Claude Code 요청문

```text
Issue #0004와 #0005를 순서대로 구현하라.

읽을 문서:
CLAUDE.md, ARCHITECTURE.md, ADR.md, ARCHITECTURE_V2.md,
IMPLEMENTATION_ROADMAP.md, EVALUATION_SPEC.md.

#0004에서는 평가 모델, dataset loader, metric만 구현하고
LLM, ChromaDB, FastAPI route를 연결하지 않는다.

#0005에서는 fixture baseline runner와 JSON/JSONL/Markdown report를 구현한다.
운영 DB와 운영 chroma_data를 사용하지 않는다.
하나의 case 오류가 전체 run을 중단하지 않게 한다.
기존 API는 변경하지 않는다.

완료 후 변경 파일, 데이터셋 예시, 지표 산식,
실행 결과, 테스트 결과, 남은 위험을 보고하라.
```
