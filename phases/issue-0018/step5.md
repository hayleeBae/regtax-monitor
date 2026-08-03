# Step 5: replay-runner

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 실제 repo 무변경, 승인 게이트 우회 금지, 테스트에서 무거운 의존성 금지)
- `/docs/architecture/ARCHITECTURE.md` (`app/evaluation/replay/` 계층 구조와 레이어 규칙 3줄)
- `/docs/architecture/ADR.md` (**ADR-011** — 파이프라인 seam 결정과 근거)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§4 전체 절차 1~11, §6 index cache, §9 실패 구분, §11 테스트, §12 수용 기준**)
- `/docs/roadmap/IMPLEMENTATION_ROADMAP.md` Issue #0018 절 (수용 기준)
- 이전 step 산출물 전부:
  - `/app/evaluation/replay/git_cmd.py` (Step 0)
  - `/app/evaluation/replay/worktree.py` (Step 1)
  - `/app/evaluation/replay/answer_diff.py` (Step 2)
  - `/app/evaluation/replay/report.py` (Step 3)
  - `/app/evaluation/replay/golden_exec.py` (Step 4)
- `/app/evaluation/replay/loader.py`, `/evaluation/fixtures/replay/mock_cases.yaml` (#0017)
- `/app/evaluation/retrieval_benchmark.py` (**CLI 스타일 본보기** — argparse 구성, `main(argv) -> int`, 결과 디렉토리 처리)

이전 step들이 만든 함수 시그니처를 정확히 확인한 뒤 조립하라. **추측하지 마라.**

## 작업

`app/evaluation/replay/runner.py`를 신규 생성한다. 앞선 5개 모듈을 조립해 스펙 §4의 절차를 수행한다.

### 1) 파이프라인 seam (ADR-011)

runner는 초안 생성 파이프라인을 **구현하지 않고 주입받는다.**

```python
@dataclass(frozen=True)
class ReplayContext:
    """파이프라인이 받는 실행 문맥."""

    case_id: str
    worktree: Path        # 과거 시점 코드가 있는 임시 디렉토리
    repo_id: str          # index cache 키 재료 (스펙 §6) — 경로가 아니라 안정적 식별자
    base_commit: str
    law: LawInput
    timeout_seconds: int


@dataclass(frozen=True)
class PipelineOutput:
    """파이프라인이 돌려주는 것."""

    diff_text: str                  # 생성된 초안 (unified diff)
    retrieved_paths: tuple[str, ...]  # 검색 상위 후보 경로 (순위 순)


ReplayPipeline = Callable[[ReplayContext], PipelineOutput]
```

- `repo_id`는 **절대경로가 아니어야 한다.** 회사 경로가 캐시 키·리포트로 새지 않도록 안정적 식별자(예: fixture의 `case_id` 또는 repo 경로 해시)를 쓴다.
- 스펙 §6의 index cache는 주입되는 파이프라인의 책임이다. runner는 키 재료(`repo_id`, `base_commit`)를 넘기는 데까지만 한다.
- **runner는 임베딩·ChromaDB·LLM을 import 하지 마라.** seam을 두는 이유가 사라진다.

### 2) 케이스 실행 (스펙 §4)

```python
def run_case(
    fixture: ReplayFixture,
    pipeline: ReplayPipeline,
    project_root: Path,
) -> ReplayOutcome: ...
```

절차:

1. `worktree.resolve_repo_path` → `assert_clean_worktree` → commit 존재 확인
2. `worktree.replay_worktree(...)` 컨텍스트 진입 (base 시점)
3. `pipeline(ReplayContext(...))` 호출 → `PipelineOutput`
4. 생성 diff를 **worktree 안에서** `git apply --check`로 검증 (`git_cmd` 경유). 결과를 `git_apply_ok`에 기록
5. `--check`가 통과하면 worktree에 `apply` (골든 테스트를 위해 필요). **원본 repo에는 절대 적용하지 않는다**
6. `golden_exec.run_golden(fixture.execution.golden_command, worktree, timeout)` 
7. `answer_diff.extract_answer_diff` + `check_expected_replacements` (원본 repo에서 commit 대 commit — worktree 불필요)
8. `ReplayOutcome` 구성 후 반환
9. 컨텍스트 종료 시 cleanup (Step 1이 `finally`로 보장)

핵심 규칙:

- **한 케이스의 실패가 다른 케이스를 중단시키면 안 된다.** 예외는 케이스 단위로 잡아 `ReplayOutcome`에 실패 유형을 기록하고 다음 케이스로 넘어간다(스펙 §9 — commit 없음 / worktree 실패 / index 실패 / LLM unavailable / golden timeout / cleanup 실패를 구분).
- 생성된 diff를 **원본 repo에 적용하는 코드를 만들지 마라.** CLAUDE.md CRITICAL(자동 적용 금지)이며, apply는 worktree 안에서만 한다.
- 초안을 승인하거나 patch 파일을 사용자 repo에 쓰는 기능을 넣지 마라 — replay는 **측정 도구**다.

### 3) 전체 실행 + CLI

```python
def run_fixtures(
    fixtures: Sequence[ReplayFixture],
    pipeline: ReplayPipeline,
    project_root: Path,
    output_dir: Path,
    privacy_mode: PrivacyMode | None = None,
) -> Path: ...


def main(argv: Sequence[str] | None = None) -> int: ...
```

- `privacy_mode`가 None이면 **fixture별 `execution.privacy_mode`를 쓴다.** 여러 fixture가 섞이면 **가장 엄격한 모드**를 리포트 전체에 적용하라(하나라도 `metadata_only`면 전체 `metadata_only`). 이유: 한 파일에 모드가 섞이면 느슨한 쪽 기준으로 새어나간다.
- CLI 옵션: `--fixtures <path>`(기본 `evaluation/fixtures/replay/mock_cases.yaml`), `--output-dir`, `--privacy-mode`(선택 override).
- 기본 파이프라인은 **주입되지 않으면 오류**로 끝낸다. CLI에서 실제 LLM 파이프라인을 기본값으로 붙이지 마라 — 이 CLI는 회사에서 파이프라인을 주입해 쓰는 진입점이고, 집에서는 stub으로 검증한다.
- 출력 디렉토리는 `evaluation/results/` 하위를 기본으로 한다(이미 gitignore).

### 4) 결정적 stub 파이프라인

`app/evaluation/replay/stub_pipeline.py`(경로 재량) — 테스트와 로컬 검증용. LLM·임베딩 없이 결정적으로 동작한다.

최소 3종:

- `perfect_pipeline` — fixture의 `expected_replacements`를 그대로 적용하는 diff를 만든다(지표가 만점에 가깝게 나와야 한다).
- `partial_pipeline` — 기대 교체 중 일부만 수행한다.
- `empty_pipeline` — 빈 diff를 돌려준다(지표가 0에 가깝게 나와야 한다).

이 stub들은 **worktree의 실제 파일 내용을 읽어** diff를 구성해야 한다(그래야 `git apply --check`가 실제로 통과/실패한다). 임의 문자열을 diff처럼 꾸며내지 마라.

### 5) ARCHITECTURE 갱신

`runner.py`는 이미 트리에 있다. `stub_pipeline.py`를 추가하고 설명을 다듬어라. 레이어 규칙 문장은 수정하지 마라.

## 테스트

`tests/test_replay_runner.py` 신규 작성. `scripts/build_replay_repos.py`로 `tmp_path`에 mock repo를 빌드하고, git이 없으면 skip.

로드맵 수용 기준과 스펙 §12를 그대로 덮어라:

- **fixture 3개 실행**: mock 3건을 `perfect_pipeline`으로 돌려 리포트 파일이 생기는지.
- **원본 무변경 검증**: 실행 전후 원본 repo의 `status --porcelain`·`rev-parse HEAD`·`git worktree list`가 동일한지.
- **실패 후 임시 디렉토리 정리**: 파이프라인이 예외를 던지는 케이스에서도 worktree·임시 디렉토리가 남지 않고, **나머지 케이스는 계속 실행**되는지.
- **file coverage / replacement accuracy 보고**: `perfect` vs `partial` vs `empty`에서 지표가 기대 방향으로 달라지는지(정확한 값 단언).
- **answer commit 전체 일치를 성공 기준으로 삼지 않음**: case3(unrelated noise)에서 초안이 문서를 안 건드려도 `file_coverage`가 1.0이 되는지 — 문서는 excluded이므로 정답에 포함되지 않는다.
- privacy: 섞인 모드에서 가장 엄격한 모드가 적용되는지.
- `git apply --check` 실패 케이스(깨진 diff)에서 `git_apply_ok=False`로 기록되고 예외가 아닌지.
- 원본 repo에 diff가 적용되지 않았는지(실행 후 원본 파일 내용 확인).

**테스트에서 실제 LLM·임베딩·ChromaDB를 트리거하지 마라** — stub 파이프라인만 쓴다(CLAUDE.md).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
source .venv/bin/activate && python3 -m app.evaluation.replay.runner --fixtures evaluation/fixtures/replay/mock_cases.yaml --output-dir evaluation/results/replay-smoke --stub perfect && ls evaluation/results/replay-smoke
```

```bash
git worktree list && git status --short
```

## 검증 절차

1. 위 AC 커맨드를 순서대로 실행한다. 두 번째가 리포트 파일을 출력하고, 세 번째에서 **worktree 누수와 작업 트리 변경이 없어야** 한다.
   - (`--stub` 옵션 이름·형태는 재량이나, CLI로 stub을 골라 로컬 검증이 가능해야 한다.)
2. 아키텍처 체크리스트를 확인한다:
   - runner가 임베딩·ChromaDB·LLM을 import하지 않는가? (`grep -n "chroma\|sentence_transformers\|anthropic\|llm" app/evaluation/replay/runner.py` 가 비어야 한다)
   - 생성 diff를 원본 repo에 적용하는 코드가 없는가?
   - 케이스 실패가 전체를 중단시키지 않는가?
   - 리포트 저장이 `report.py`를 통해서만 이루어지는가?
   - git 호출이 전부 `git_cmd` 경유인가?
3. 결과에 따라 `phases/issue-0018/index.json`의 step 5를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, seam 시그니처, CLI 옵션, stub 3종, mock 3건 실행 시 관측된 지표 값"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- 생성된 diff를 원본 repo에 적용하지 마라. 이유: CLAUDE.md CRITICAL — 자동 적용은 승인 게이트 우회다. apply는 임시 worktree 안에서만 한다.
- 초안 승인·patch 파일 출력 기능을 넣지 마라. 이유: replay는 측정 도구이며, 승인 경로는 사람이 대시보드에서 수행한다.
- runner에서 임베딩·ChromaDB·LLM을 import하지 마라. 이유: 파이프라인을 seam으로 둔 이유(ADR-011)가 사라지고, 집 환경 테스트가 무거워진다.
- CLI 기본 파이프라인으로 실제 LLM 파이프라인을 붙이지 마라. 이유: 실수로 무거운 실행이 도는 것을 막는다. 명시적으로 주입해야 한다.
- 한 케이스의 예외가 전체 실행을 중단하게 두지 마라. 이유: 스펙 §9가 실패 유형 구분과 계속 진행을 전제하며, 나머지 fixture 결과를 잃는다.
- 여러 privacy 모드가 섞였을 때 느슨한 쪽을 택하지 마라. 이유: 한 리포트 파일에 모드가 섞이면 엄격한 fixture의 코드가 느슨한 기준으로 저장된다.
- 운영 `chroma_data/`를 건드리거나 인덱싱하지 마라. 이유: 재인덱싱에 수십 분이 들고, 인덱스 관리는 주입되는 파이프라인의 책임이다(스펙 §6 — 운영 index를 덮어쓰지 않는다).
- `subprocess`를 직접 호출하지 마라. 이유: git은 `git_cmd`, 골든은 `golden_exec`가 각각 안전장치를 갖고 있다.
- 기존 테스트를 깨뜨리지 마라.
