# Company Mac Validation Runbook

실제 경로·코드·검색 결과는 이 문서나 Git에 기록하지 않는다. 아래 결과를 공유할
때도 경로와 코드 원문은 가리고 건수·상태·순위만 전달한다.

## 1. 최신화와 기본 검증

```bash
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python --version
bash scripts/verify.sh full
bash scripts/verify.sh security
```

## 2. 환경 설정

`.env`에 `REPO_ROOT`, 필요한 경우 `REPO_INDEX_PATHS`, local LLM 설정을 입력한다.
`.env`는 커밋하지 않는다.

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

## 3. 실제 eHR 인덱싱

기존 mock `chroma_data`를 실제 index로 재사용하지 않는다. 삭제가 부담되면 회사
환경 전용 persist directory를 사용한다. 서버 자동 인덱싱 로그에서 파일 수, chunk
수, 소요시간, 오류를 기록한다.

## 4. 검색 확인

법령 사례 1~3건에 대해 상위 5개 파일을 확인하고 다음만 별도 private 메모에 기록한다.

```text
case id:
변경 유형:
상위 결과 순위:
정답 파일 rank:
맞음 / 일부 맞음 / 틀림:
provider 오류:
```

## 4-1. 검증 이력 rerank 발동 확인 (Issue #0016)

#0016 rerank는 검증 매핑의 `(path, symbol)`이 검색 후보의 `(path, symbol)`과
**정확히 일치**할 때만 적용된다(`CandidateLocation.dedup_key` 기준). 그런데 후보의
symbol은 검색원마다 다르다 — RAG는 청크 심볼, 용어 사전은 컬럼 코드, 상수 매칭은
값 문자열. 실제 `mapping` 테이블에 어떤 symbol이 쌓이느냐에 따라 rerank가 한 번도
발동하지 않을 수 있다. 집 mock의 ablation fixture는 파일 단위로 느슨하게 매칭해서
이 격차가 드러나지 않으므로, 회사에서만 확인 가능하다.

검증 매핑이 쌓인 뒤 사례 3~5건에 대해 아래를 기록한다(실제 경로·symbol 값은 적지
않고 일치 여부만).

```text
검증 매핑 수:
rerank 발동 건수 (응답 rerank_version 존재 + 순위 변동):
symbol 일치로 매칭된 건수 / symbol 불일치로 미매칭된 건수:
정답 파일 rank 변화 (rerank off → on):
stale 이력 후보의 rank 변화:
```

`stale 이력 후보의 rank 변화`는 별도 판단 대기 항목이다 — 현재 stale은 boost 제거에
더해 -0.50 penalty를 받아 이력이 전혀 없는 후보보다 아래로 내려간다(스펙 §10 문언
그대로). 정답이 부당하게 밀리는 사례가 관측되면 penalty를 중립화(boost 제거만)하는
방향으로 스펙 §10 수치 개정을 검토한다.

## 4-2. 과거 개정 replay (Issue #0017·#0018)

과거 개정 1~3건을 골라 **그때 실제로 고친 commit**과 우리 초안을 비교한다. 이 절이
회사에서만 가능한 가장 중요한 확인이다 — 집에서는 합성 fixture로 비교 로직만
검증했고, 실제 개정에서 초안이 어디까지 맞히는지는 여기서 처음 측정된다.

### 준비 — fixture 작성

```bash
mkdir -p evaluation/private
cp evaluation/datasets/company_replay.template.yaml evaluation/private/replay_company.yaml
```

`replay_company.yaml`을 채울 때 지킬 것:

- `path_env: "EHR_REPO_ROOT"` 를 그대로 둔다. **절대경로를 YAML에 적지 않는다** — 적으면
  fixture 파일 자체가 반출 위험물이 된다.
- `base_commit` / `answer_commit` 은 실제 SHA로 바꾼다(개정 직전 / 실제로 고친 commit).
- `scope.relevant_paths` 는 **사람이 판단해 지정**한다. answer commit에 딸려 들어간
  리팩토링·문서·무관 수정은 `excluded_paths` 로 뺀다 — 그것까지 맞히라고 요구하면
  지표가 영원히 낮게 나와 의미를 잃는다(스펙 §2).
- `privacy_mode: "metadata_only"` 를 유지한다. 이 값이 리포트에 코드·경로가 남지 않게
  하는 유일한 방어선이다.
- `golden_command` 는 필요할 때만 넣는다. 허용 실행파일은 `mvn`/`gradle`/`./gradlew`/`pytest`
  뿐이고, 절대경로 인자와 `-f`/`--file`/`-p`/`--rootdir`/`-b`/`--settings` 계열은 거부된다.

원본 repo가 **깨끗한 상태**여야 실행된다(커밋 안 된 변경이 있으면 시작 자체가 거부된다).
실행은 임시 detached worktree에서만 이루어지고 원본 작업 트리는 건드리지 않는다.

### 실행

```bash
export EHR_REPO_ROOT=/실제/eHR/repo/경로
python -m app.evaluation.replay.runner --fixtures evaluation/private/replay_company.yaml --output-dir evaluation/results/replay-company --stub perfect
```

먼저 `--stub perfect` 로 한 번 돌려 **배관을 확인**한다(worktree 생성·answer diff 추출·
정리까지). 이때 `expected_replacement_accuracy` 가 1.0이면 fixture가 answer commit과
일관된다는 뜻이고, 낮으면 **fixture가 틀린 것**이므로 먼저 고친다 — fixture가 틀린 채로
실제 파이프라인을 돌리면 결과 전체가 무의미하다.

배관이 확인되면 실제 파이프라인을 주입해 돌린다(주입 방법은 `runner.py` 의 `ReplayPipeline`
시그니처 참조 — `ReplayContext` 를 받아 `PipelineOutput(diff_text, retrieved_paths)` 을
돌려주는 callable이면 된다).

### 실행 후 확인

```bash
git status --short        # 원본 repo에 변경이 없어야 한다
git worktree list         # 임시 worktree가 남아 있지 않아야 한다
```

둘 중 하나라도 어긋나면 **그 자체가 보고할 결함**이다. 남은 worktree는
`git worktree prune` 으로 회수한다.

### 기록할 것

경로·코드는 적지 않는다. `evaluation/results/replay-company/replay_report.md` 는
`metadata_only` 라 그대로 봐도 되지만, 아래 요약만 옮기면 충분하다.

```text
replay 케이스 수:
fixture 일관성(expected_replacement_accuracy, stub perfect 기준):
file_coverage:
expected_replacement_accuracy (실제 파이프라인):
unnecessary_file_rate:
git_apply 성공/실패:
golden 결과 (passed/failed/error/skipped):
failure_kind 발생 건수와 종류:
원본 repo 무변경 확인: 예/아니오
worktree 누수 여부: 있음/없음
1건당 소요시간:
```

`failure_kind` 는 `commit_not_found` / `worktree_failed` / `pipeline_failed` /
`answer_diff_failed` / `cleanup_failed` 중 하나로 나온다 — 고정 어휘라 그대로 적어도
사내 정보가 새지 않는다.

## 5. Private dataset

```bash
mkdir -p evaluation/private
cp evaluation/datasets/company_private.template.yaml evaluation/private/company.yaml
```

`company.yaml`에는 확인된 정답만 넣는다. `reviewed: true`는 담당자가 실제 관련
파일을 확인한 경우에만 사용한다. `evaluation/private/`, `evaluation/results/`,
`chroma_data/`, 각종 `*_cache.json`은 커밋하지 않는다.

## 6. 전달할 결과

```text
git pull: 성공/실패
Python: 3.10.x 여부
verify full/security: 성공/실패
Ollama 모델과 context length:
인덱싱 파일 수 / chunk 수 / 소요시간:
검색 사례 수:
정답 rank 요약:
rerank 발동 건수 / symbol 미매칭 건수:
replay 케이스 수 / file_coverage / replacement accuracy:
replay 원본 무변경·worktree 누수 여부:
초안 생성 절단 재현 여부 (F-20260712-0002):
requirements.txt 보안 하한 설치 성공 여부 (SSL 제약):
provider 오류:
기타 경고(context shift, timeout, SSL):
```

API 키, 사내 URL, 사용자명, 실제 코드, 실제 파일 경로는 전달하지 않는다.
