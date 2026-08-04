# Step 3: replay-metrics

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — **코드는 외부로 나가지 않는다**)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙 — "replay 산출물 저장은 `report.py` 한 곳에서만 하고 `allowed_artifacts(privacy_mode)`로 게이팅한다")
- `/docs/architecture/ADR.md` (**ADR-011**)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§7 지표 9종, §8 privacy 3모드, §12 수용 기준**)
- `/app/evaluation/replay/fixture.py` (#0017 — `PrivacyMode`, `ArtifactKind`, **`allowed_artifacts()`**. 이 step이 그 함수를 소비하는 유일한 지점이다)
- `/app/evaluation/replay/answer_diff.py` (Step 2 — `AnswerDiff`, `ChangedFile`, `ReplacementCheck`)
- `/app/evaluation/metrics.py` (기존 평가 지표 — **재사용 가능한 것이 있는지 먼저 확인하라**)
- `/app/evaluation/retrieval_benchmark.py` (리포트 파일 출력 스타일 — `comparison.json`/`comparison.md`/`environment.json` 구성과 결정적 직렬화 `_compact_json`)

`app/evaluation/metrics.py`를 먼저 읽고 Recall 계산 등 이미 있는 함수를 재사용하라. 같은 지표를 두 번 구현하지 마라.

## 작업

`app/evaluation/replay/report.py`를 신규 생성한다. **지표 계산**과 **privacy 게이팅된 저장** 두 가지가 이 모듈의 책임이다.

### 1) 입력과 지표 (스펙 §7)

```python
@dataclass(frozen=True)
class ReplayOutcome:
    """한 케이스 실행의 원자료 — runner(Step 5)가 채운다."""

    case_id: str
    answer: AnswerDiff
    replacement_checks: tuple[ReplacementCheck, ...]
    generated_files: tuple[str, ...]          # 생성된 초안이 건드린 파일
    generated_replacements: tuple[...]        # 초안이 수행한 before→after (구조는 재량)
    retrieved_paths: tuple[str, ...]          # 검색 상위 후보 경로 (순위 순)
    git_apply_ok: bool | None                 # apply --check 결과 (미실행 시 None)
    golden_status: str | None                 # passed | failed | error | skipped
    duration_ms: int
```

산출할 지표(§7 전부):

- `relevant_path_recall_at_k` — `retrieved_paths` 상위 K에 `answer.in_scope` 경로가 얼마나 들어왔는지. K는 `(1, 3, 5, 10)`.
- `primary_rank` — in-scope 정답 중 가장 높은 순위(없으면 None).
- `expected_replacement_accuracy` — fixture `expected_replacements` 중 초안이 실제로 수행한 비율.
- `file_coverage` — `answer.in_scope` 파일 중 초안이 건드린 비율.
- `unnecessary_file_rate` — 초안이 건드린 파일 중 in-scope도 excluded도 아닌 비율.
- `changed_file_jaccard` — 초안 파일 집합 ∩ in-scope 집합 / 합집합.
- `git_apply`, `golden_result` — 그대로 기록.
- `normalized_diff_similarity` — **참고값으로만 산출한다.**

**핵심 규칙**: `normalized_diff_similarity`는 **합격/불합격 판정에 쓰지 마라.** 스펙 §7이 "동일 결과를 다른 구현으로 만들 수 있으므로 필수 합격 기준이 아니다"라고 명시했다. 리포트에 값은 남기되 판정 로직에서 참조하지 않는다.

지표 계산 함수는 순수 함수로 만들어라 — 파일시스템·git에 접근하지 않는다.

### 2) privacy 게이팅 (스펙 §8) — 이 step의 보안 핵심

```python
def write_report(
    outcomes: Sequence[ReplayOutcome],
    output_dir: Path,
    privacy_mode: PrivacyMode,
    environment: Mapping[str, object] | None = None,
) -> Path: ...
```

- 저장 직전에 `allowed_artifacts(privacy_mode)`(#0017)를 호출하고, **허용된 `ArtifactKind`만 payload에 넣는다.**
- 매핑:
  - `ArtifactKind.FILE_PATH` 미허용 → 파일 경로를 쓰지 않는다. 경로가 필요한 자리에는 **경로 해시**(`ArtifactKind.HASH`)를 넣는다.
  - `ArtifactKind.DIFF_BODY` 미허용 → diff 본문 라인을 쓰지 않는다.
  - `ArtifactKind.DIFF_STRUCTURE` 미허용 → hunk 헤더·파일별 ±줄수도 쓰지 않는다(집계 카운트만).
  - `ArtifactKind.GOLDEN_OUTPUT` 미허용 → 골든 **상태 문자열만** 남기고 출력 본문은 버린다.
  - `ArtifactKind.CODE_SNIPPET` 미허용 → `before`/`after` 문자열을 쓰지 않는다. 일치 여부 boolean만 남긴다.
- **금지 방식**: 전체 payload를 만든 뒤 지우는(블랙리스트) 방식으로 구현하지 마라. 허용 집합을 보고 **넣을 것만 넣는**(화이트리스트) 방식으로 구성하라. 이유: #0017이 화이트리스트를 택한 이유가 "새 항목 추가 시 누락되면 저장이 막힐 뿐 유출되지 않는다"이고, 블랙리스트는 반대로 동작한다.
- 출력은 `retrieval_benchmark.py`처럼 `replay_report.json` + 사람이 읽는 `replay_report.md` + `environment.json` 구성으로 한다. 직렬화는 결정적으로(정렬된 키, 고정 구분자).

### 3) ARCHITECTURE 갱신

`report.py`는 이미 트리에 있다. 갱신이 필요하면 설명 문구만 다듬고, 레이어 규칙 문장은 수정하지 마라.

## 테스트

`tests/test_replay_report.py` 신규 작성. git·파일시스템 의존을 최소화하고 `tmp_path`를 쓴다.

- 지표 계산: 손으로 계산 가능한 작은 입력으로 recall@k, file_coverage, unnecessary_file_rate, jaccard, replacement accuracy 각각의 값을 단언.
- `primary_rank`가 없을 때 None인지.
- **privacy 게이팅 (핵심, 각 모드마다)**:
  - `metadata_only`로 쓴 리포트 파일을 **문자열로 읽어**, in-scope 파일 경로·diff 본문·골든 출력·before/after 문자열이 **하나도 나타나지 않는지** 단언하라. 실제 파일 내용을 검사해야 한다 — 자료구조만 보면 놓친다.
  - `redacted`: 경로와 파일별 ±줄수는 있고, diff 본문·코드 스니펫·골든 출력은 없는지.
  - `full`: 전부 있는지.
- `normalized_diff_similarity`가 판정에 쓰이지 않는지 — 이 값만 0으로 만든 입력에서도 나머지 판정 결과가 변하지 않는지 단언.
- 같은 입력 두 번 → 바이트 단위 동일 파일(결정성).
- 빈 `outcomes` → 예외 없이 빈 리포트.

무거운 의존성(임베딩·LLM·ChromaDB·DB)을 트리거하지 마라.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `allowed_artifacts()`를 실제로 호출하는가(#0017 함수를 재구현하지 않았는가)?
   - 게이팅이 화이트리스트 방식인가(payload를 만든 뒤 지우는 방식이 아닌가)?
   - `normalized_diff_similarity`가 판정 로직에서 참조되지 않는가?
   - 지표 계산 함수가 파일시스템·git에 접근하지 않는가?
   - `app/evaluation/metrics.py`에 이미 있는 계산을 중복 구현하지 않았는가?
3. 결과에 따라 `phases/issue-0018/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, ReplayOutcome 구조, 산출 지표 목록, 모드별 게이팅 규칙, 출력 파일 이름 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- `normalized_diff_similarity`를 합격 판정에 쓰지 마라. 이유: 스펙 §7이 명시적으로 금지한다 — 같은 결과를 다른 구현으로 만들 수 있어 낮은 유사도가 실패를 뜻하지 않는다.
- privacy 게이팅을 "전부 만들고 지우기"로 구현하지 마라. 이유: 항목을 놓치면 그대로 유출된다. 허용 집합만 보고 넣는 화이트리스트여야 누락이 안전한 방향으로 실패한다.
- `allowed_artifacts()`의 판정을 이 모듈에 복제하지 마라. 이유: 규칙이 두 곳이 되면 #0017의 표와 어긋난다.
- 리포트 저장을 다른 모듈에서도 하도록 열어두지 마라. 이유: ARCHITECTURE 레이어 규칙 — 저장 지점이 흩어지면 privacy 모드가 무의미해진다.
- 지표 계산 함수에서 git·파일을 읽지 마라. 이유: 순수 함수라야 테스트가 결정적이고 회사 환경 없이 검증된다.
- 초안 생성·worktree·golden 실행을 이 step에서 하지 마라. 이유: Step 4·5의 범위다.
- 기존 테스트를 깨뜨리지 마라.
