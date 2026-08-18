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

`.env`에 `REPO_ROOT`, `REPO_INDEX_PATHS`, local LLM 설정을 입력한다.
`.env`는 커밋하지 않는다.

eHR 권장 화이트리스트 (도메인: 급여·연말정산/연차/4대보험 + SQL + Nexacro 화면):

```bash
REPO_INDEX_PATHS=src/hr/pay,src/hr/tim/annl,src/hr/ins,src/hr/sta/pay,src/hr/sqlmap,web/nexacro/solution/pay,web/nexacro/solution/tim,web/nexacro/solution/ins
```

`REPO_INDEX_PATHS`는 1차 방어다 — `web/eHR/`(컴파일 산출물)·`nexacro14lib/`(벤더
런타임)·`UbiService/`(리포팅 엔진·로그) 같은 경로별 산출물은 `EXCLUDED_DIRS`
블랙리스트로는 잡히지 않으므로 화이트리스트로 범위를 좁혀 원천 차단한다. 범위는
운영자가 조정 가능하나, **화이트리스트 없이 eHR을 인덱싱하지 않는다**.

> ⚠️ **보안 경고 (스펙 §6).** `build.xml`에 평문 자격증명이 존재한다(2026-08-14
> 실측). 화이트리스트가 repo 루트 파일을 배제해 차단하지만, `REPO_INDEX_PATHS`를
> 비우고(전체 인덱싱) 돌리면 자격증명이 인덱스·LLM 컨텍스트로 노출된다. 조치 권고
> (eHR 소유자 몫): 자격증명 외부화(`build.properties` 분리 + 형상 제외), 사내
> 정보보호팀(security@pantechcni.com) 통보. 실제 IP·계정·비밀번호 값은 이 문서를
> 포함해 어디에도 옮겨 적지 않는다.

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

## 3. 실제 eHR 인덱싱

기존 mock `chroma_data`를 실제 index로 재사용하지 않는다. 삭제가 부담되면 회사
환경 전용 persist directory를 사용한다. 서버 자동 인덱싱 로그에서 파일 수, chunk
수, 소요시간, 오류를 기록한다.

### 3-1. 인덱싱 범위 변경 시 캐시 재생성

`REPO_INDEX_PATHS`(스캔 범위)를 바꾸면 프로젝트 루트의 전역 캐시가 구 범위를 그대로
반영하므로, 아래 캐시와 벡터 인덱스를 삭제한 뒤 서버를 재기동해 새 범위로 재생성한다.

```bash
rm -f term_dict_cache.json term_loc_cache.json const_inventory_cache.json symbol_index_cache.json
rm -rf chroma_data/
python run.py   # 첫 기동 시 새 범위로 자동 재인덱싱 (CPU, 수십 분)
```

이 `*_cache.json`들은 eHR 내부 파생물이라 gitignore 대상 — 커밋·반출 금지다.

### 3-2. eHR 적합화 검증 (Issue #0024)

인덱싱 후 아래 세 항목을 확인한다.

- **xfdl 인덱싱**: `.xfdl` Script의 청크가 검색에 잡히는지 스모크 확인한다. `/index`
  이후 대시보드 검색에서 도메인 라벨(예: "직무발명") 또는 xfdl에 하드코딩된 한도
  상수로 조회해 상위 후보에 `web/nexacro/solution/**/*.xfdl` 파일이 나오는지 본다
  (건수·순위만 private 메모에 기록, 경로 원문은 가린다).
- **인코딩(CP949 한글 주석)**: 최신 연말정산 SQL `PayRefCom_2026.xml`(CP949)의 한글
  주석이 용어 사전에 깨지지 않고 수확되는지 확인한다. `term_dict_cache.json` 재생성
  후 해당 컬럼 코드(a0121 등)의 한글 라벨이 정상 판독되는지 본다.
- **인코딩(선언·실제 불일치)**: XML 선언이 EUC-KR인데 실제 UTF-8인 파일
  (`TimTimm.xml`/`TimVac.xml`)이 폴백 없이 무손실 판독되는지 함께 확인한다.

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

## 4-2. 과거 개정 replay (Issue #0017·#0018·#0022)

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

### 실행 1단계 — 배관·fixture 일관성 확인 (stub)

```bash
export EHR_REPO_ROOT=/실제/eHR/repo/경로
python -m app.evaluation.replay.runner \
  --fixtures evaluation/private/replay_company.yaml \
  --output-dir evaluation/results/replay-company-stub \
  --pipeline stub --stub perfect
```

먼저 stub 으로 한 번 돌려 **배관을 확인**한다(worktree 생성·answer diff 추출·정리까지).
이때 `expected_replacement_accuracy` 가 1.0이면 fixture가 answer commit과 일관된다는
뜻이고, 낮으면 **fixture가 틀린 것**이므로 먼저 고친다 — fixture가 틀린 채로 실제
파이프라인을 돌리면 결과 전체가 무의미하다. stub은 임베딩·추론을 쓰지 않아 몇 초 안에
끝난다.

### 실행 2단계 — 실제 파이프라인 (Issue #0022)

```bash
python -m app.evaluation.replay.runner \
  --fixtures evaluation/private/replay_company.yaml \
  --output-dir evaluation/results/replay-company \
  --pipeline real
```

`--pipeline real` 은 base 시점 worktree를 인덱싱해 검색하고 초안을 생성한다. 확인할 것:

- **첫 실행은 인덱싱 때문에 수십 분** 걸린다. 인덱스는 `evaluation/replay_index/<key>/`에
  캐시되며, key는 (repo id, base commit, 임베딩 모델, 인덱서 버전)이다 — 같은 base
  commit으로 다시 돌리면 두 번째부터 **캐시가 적중해 인덱싱을 건너뛴다**. 로그의
  `replay 인덱스 캐시 적중` / `replay 인덱스를 새로 만듭니다` 로 어느 쪽인지 확인한다.
  base commit이 다른 케이스를 추가하면 그만큼 첫 실행 비용이 늘어난다.
- 캐시는 자동으로 지워지지 않는다. 재인덱싱이 필요하면 해당 디렉토리를 직접 삭제한다.
  (`evaluation/replay_index/`는 gitignore 대상 — 대상 코드의 임베딩이 담겨 **커밋·반출
  금지**다.)
- Ollama가 떠 있어야 한다(`OLLAMA_CONTEXT_LENGTH=16384 ollama serve`). 미기동이면 해당
  케이스가 `failure_kind=pipeline_failed` 로 격리되고 나머지 케이스는 계속 실행된다.
- 운영 `chroma_data/`는 읽지도 쓰지도 않는다 — 개발 서버 인덱스와 섞이지 않는다.
- 인덱스 캐시 루트를 다른 디스크에 두려면 `--index-root /다른/경로` 를 준다.

**`LLM_BACKEND=claude` 이면 대상 코드 스니펫이 외부(Anthropic API)로 전송된다.** replay는
케이스를 연속 실행하므로 이 경우 CLI가 경고를 내고 `--allow-external-llm` 없이는 실행을
거부한다. **회사 환경에서는 `LLM_BACKEND=local` 을 쓴다** — 플래그를 붙여 돌리는 것은
반출 판단이 끝난 경우에만 한다.

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
첫 실행 인덱싱 소요시간 (case별):
두 번째 실행 인덱스 캐시 적중: 예/아니오
LLM_BACKEND (local 권장):
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
replay 인덱싱 소요시간 / 캐시 적중 여부:
replay 원본 무변경·worktree 누수 여부:
초안 생성 절단 재현 여부 (F-20260712-0002):
requirements.txt 보안 하한 설치 성공 여부 (SSL 제약):
provider 오류:
기타 경고(context shift, timeout, SSL):
```

API 키, 사내 URL, 사용자명, 실제 코드, 실제 파일 경로는 전달하지 않는다.
