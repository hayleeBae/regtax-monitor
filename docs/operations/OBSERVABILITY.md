# 관측성 스펙 — 실행 흔적(Traces)과 실패 레코드(Failures)

> 이 문서는 하네스의 1·2단계 관측성 구현 스펙이다. 3단계(옵티마이저 층)는 `OPTIMIZER_DESIGN.md` 참조.
>
> 설계 원칙: **모든 필드는 "나중에 기계(옵티마이저 에이전트)가 읽는다"를 전제로 구조화한다.**
> 자유 텍스트는 사람용 보조 필드(summary, description)에만 허용하고, 판정·분류·참조는 반드시 enum/ID로 남긴다.

---

## 1. 디렉토리 구조

```
.harness/
├── taxonomy.yml                 # 분류 코드 정의 — 사람이 관리, 보호 파일 (에이전트 수정 불가)
├── current_run                  # 진행 중인 run_id (execute.py가 관리, gitignore)
├── traces/                      # 실행 흔적 (append-only, gitignore — 로컬 전용)
│   └── {run_id}/
│       ├── run.json             # run 메타데이터 (시작 시 생성, 종료 시 outcome 갱신)
│       └── events.jsonl         # 이벤트 스트림 (한 줄 = 한 이벤트)
└── failures/                    # 실패 레코드 (append-only, git 추적 — 팀 자산)
    ├── index.jsonl              # 실패 인덱스 (grep 진입점)
    └── {failure_id}/
        ├── failure.json         # 실패 상세 레코드
        └── artifacts/           # 에러 로그 원문, diff 등 증거물
```

규칙 세 가지. 첫째, `traces/`와 `failures/`는 append-only다 — 재실행 시 기존 run을 수정하지 않고 새 run_id를 발급한다. 둘째, 실패는 해결되어도 삭제하지 않고 `failure.json`의 `status`만 갱신한다. 셋째, `traces/`는 로컬 전용(gitignore)이고 `failures/`와 `taxonomy.yml`은 버전 관리에 포함한다 — 실패 데이터는 팀이 공유해야 가치가 있다.

## 2. 기존 구조와의 관계

이 스펙은 기존 `phases/` 산출물을 대체하지 않고 보완한다.

| 기존 | 역할 | 관측성 레이어 | 역할 |
|---|---|---|---|
| `phases/{task}/index.json` | step 상태의 현재값 | `traces/{run}/events.jsonl` | 상태 변화의 **이력** (시도별, 시간순) |
| `step{n}-output.json` | Claude 호출의 raw 출력 | `run.json` + attempt 이벤트 | run 단위 메타데이터와 판정 요약 |
| `ESCALATION.md` | 사람이 읽는 중단 리포트 | `failures/{id}/failure.json` | 기계가 읽는 구조화 실패 데이터 |

ESCALATION.md와 failure 레코드는 항상 쌍으로 생성된다 (execute.py `_write_escalation`). 사람은 ESCALATION.md를 보고 조치하고, 조치 후 failure.json의 `verified_cause`를 채운다.

## 3. ID 규약

| ID | 형식 | 예시 | 발급 주체 |
|---|---|---|---|
| `run_id` | `{YYYYMMDD}-{HHmmss}-{4자리 hex}` | `20260711-103015-a3f2` | trace.py `start_run` |
| `event_id` | `{run_id}/{seq}` (run 내 0부터 증가) | `20260711-103015-a3f2/12` | trace.py `log` |
| `failure_id` | `F-{YYYYMMDD}-{4자리 연번}` | `F-20260711-0003` | trace.py `record_failure` |

## 4. `run.json` 스키마

```json
{
  "run_id": "20260711-103015-a3f2",
  "started_at": "2026-07-11T10:30:15+0900",
  "finished_at": "2026-07-11T11:02:44+0900",
  "task": "PACE / auth-layer",
  "stage_entry": "implement",
  "phase_dir": "1-auth-layer",
  "total_steps": 5,
  "harness_version": "1.1.0",
  "git": { "branch": "feat-auth-layer", "base_commit": "abc1234" },
  "final_commit": "def5678",
  "outcome": "success",
  "total_cost_usd": 1.4200,
  "failure_refs": ["F-20260711-0003"]
}
```

`outcome` enum: `success` | `partial` | `failed` | `blocked` | `halted` | `aborted`

`harness_version`(scripts/trace.py의 `HARNESS_VERSION` 상수)은 하네스 구성 — 스크립트, CLAUDE.md 지침, 검증 규칙 — 을 바꿀 때마다 올린다. "하네스 버전 X에서 실패율이 변했다"를 판정하는 조인 키이므로 빼먹으면 3단계에서 효과 측정이 불가능해진다.

## 5. 이벤트 스키마 (`events.jsonl`)

```json
{"ts":"2026-07-11T10:31:02+0900","event_id":"20260711-103015-a3f2/12","run_id":"20260711-103015-a3f2","stage":"implement","seq":12,"type":"attempt","actor":"harness","summary":"step 2 (api-layer) attempt 1: error","step":2,"attempt":1,"status":"fail","payload":{"elapsed_s":482,"step_status":"error"}}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `ts` | ISO8601 | 이벤트 시각 (KST) |
| `event_id` / `run_id` / `seq` | — | §3 규약 |
| `stage` | enum | `design` `implement` `test` `secscan` `release` |
| `type` | enum | 아래 표 |
| `actor` | enum | `harness`(execute.py) `agent` `subagent` `human` `ci` |
| `summary` | string | 한 줄 요약 (grep 대상) |
| `step` / `attempt` | int? | 해당되는 경우만 |
| `status` | enum? | `ok` `fail` `skip` — 판정이 있는 이벤트만 |
| `payload` | object? | 타입별 상세. **대용량 원문 금지** — artifacts 경로 참조 |
| `refs` | object? | `failure_id` 등 상호 참조 |

이벤트 타입과 기록 주체:

| type | 기록 주체 | 시점 |
|---|---|---|
| `run_start` / `run_end` | execute.py 자동 | run 시작/종료 |
| `step_start` / `step_end` | execute.py 자동 | step 시작 / 완료·실패·차단 |
| `attempt` | execute.py 자동 | Claude 호출 1회 종료마다 (경과, 판정) |
| `guard` | execute.py 자동 | 보호 파일 수정 감지, 예산 초과 |
| `escalation` | execute.py 자동 | failure 레코드 생성 시 |
| `decision` | **에이전트** (CLI) | 복수 대안 중 기술적 선택을 했을 때 |
| `validation` | **에이전트** (CLI) | AC 외 추가 검증을 수행했을 때 |
| `note` / `human_input` / `tool_call` / `tool_result` | 필요시 | 자유 |

에이전트 기록 규칙 두 가지가 특히 중요하다. **decision 이벤트를 아끼지 말 것** — "A 대신 B를 택했다"는 분기는 실패 인과 분석의 핵심 재료다 (`payload`에 `options`, `chosen`, `reason_code` 권장). **validation 이벤트는 판정 기준을 함께 기록할 것** — "통과"가 아니라 "어떤 기준을 통과했나"가 남아야 한다.

```bash
# 에이전트/사람 수동 기록 CLI
python3 scripts/trace.py event --type decision --summary "iBatis 유지 (기존 매퍼 재사용)" \
    --payload '{"options":["ibatis","jpa"],"chosen":"ibatis","reason_code":"consistency"}'
python3 scripts/trace.py event --type validation --summary "원본 SP 결과와 diff 0건" --status ok

# 대화형 단계(/design, /secscan)에서 run을 열고 닫기
python3 scripts/trace.py start --stage design --task "권한 모델 설계"
python3 scripts/trace.py end --outcome success
```

## 6. 실패 레코드 스키마

### 6.1 `failures/index.jsonl` (grep 진입점)

```json
{"failure_id":"F-20260711-0003","ts":"2026-07-11T10:31:02+0900","run_id":"20260711-103015-a3f2","stage":"implement","category":"validation_failure","cause_code":"unknown","status":"open","summary":"[3회 재시도 실패 (error)] auth-layer step 2 (api-layer)"}
```

### 6.2 `failures/{failure_id}/failure.json` (상세)

```json
{
  "failure_id": "F-20260711-0003",
  "ts": "2026-07-11T10:31:02+0900",
  "run_id": "20260711-103015-a3f2",
  "stage": "implement",
  "step": 2,
  "trace_refs": ["20260711-103015-a3f2/12"],
  "symptom": {
    "category": "validation_failure",
    "signal": "AuthzFilterTest.testRoleScope AssertionError ...",
    "artifact": "artifacts/escalation-detail.md"
  },
  "verified_cause": {
    "cause_code": "assumption_stale_api",
    "description": "SP 결과셋 컬럼명이 EMP_NO가 아니라 EMPNO. 유사 쿼리 패턴에서 검증 없이 유추함.",
    "verified_by": "human",
    "causal_chain": ["20260711-103015-a3f2/8", "20260711-103015-a3f2/12"]
  },
  "resolution": {
    "action": "fixed",
    "description": "컬럼명 수정. DB 스키마 실측 확인을 step 주의사항에 추가 제안.",
    "commit": "def5678"
  },
  "status": "resolved",
  "recurrence_of": null
}
```

**`symptom`과 `verified_cause`의 분리가 이 스키마의 심장이다.** 겉보기 증상(타임아웃)과 검증된 원인(무한 재시도 vs 느린 쿼리)은 다르며, 이 구분이 무너지면 실패 데이터 전체가 노이즈가 된다. 그래서 execute.py는 symptom만 자동으로 채우고, verified_cause는 항상 `unknown`/`unverified` 초안으로 남긴다 — 사람이 원인을 확인한 뒤에만 채운다.

필드 규칙:

- `verified_by` enum: `human` | `agent` | `test` | `unverified`. **미검증 원인을 검증된 것처럼 기록하는 것이 최악이다** — 3단계 채굴기는 unverified를 분석에서 제외하므로, 정직한 unverified는 무해하지만 거짓 verified는 오염이다.
- `causal_chain`: 이벤트 event_id를 시간순으로 나열해 "어디서 씨앗이 심겼고 어디서 터졌는가"를 잇는다.
- `recurrence_of`: 과거 failure_id 참조. **재발 실패는 하네스 결함의 가장 강한 신호**이며 3단계 채굴의 1순위 대상이다.
- `status` enum: `open` | `resolved` | `wont_fix` | `recurring`
- `category`/`cause_code`는 `.harness/taxonomy.yml`에 정의된 값만 사용. 새 코드가 필요하면 taxonomy에 먼저 추가한다 (taxonomy는 보호 파일 — 에이전트 수정 불가).

## 7. 조회 예시

```bash
# 실패 이력 테이블로 훑기
cat .harness/failures/index.jsonl | jq -r '[.ts,.failure_id,.category,.cause_code,.status,.summary]|@tsv'

# 특정 원인 코드의 재발 여부 (3건 이상이면 3단계 채굴 후보)
grep '"cause_code":"assumption_stale_api"' .harness/failures/index.jsonl | wc -l

# 미검증 실패 목록 (사람이 채워야 할 숙제)
grep '"cause_code":"unknown"' .harness/failures/index.jsonl

# 특정 run의 이벤트 흐름
cat .harness/traces/20260711-103015-a3f2/events.jsonl | jq -r '[.seq,.type,.status//"-",.summary]|@tsv'

# 실패 판정 이벤트만
grep '"status":"fail"' .harness/traces/*/events.jsonl

# step별 소요 시간
grep '"type":"attempt"' .harness/traces/{run_id}/events.jsonl | jq -r '[.step,.attempt,.payload.elapsed_s]|@tsv'

# run별 결과와 비용
cat .harness/traces/*/run.json | jq -r '[.run_id,.outcome,.total_cost_usd//0,.harness_version]|@tsv'
```

## 8. 운영 수칙 요약

1. execute.py 실행만으로 traces/failures가 자동 축적된다 — 별도 조작 불필요.
2. 실패 조치 후 `verified_cause` 채우기가 사람의 유일한 의무. 미확인이면 `unverified`.
3. 실패 레코드는 삭제 금지, status 갱신만.
4. 하네스 구성(스크립트/지침/검증 규칙) 변경 시 `scripts/trace.py`의 `HARNESS_VERSION`을 올린다.
5. 축적 목표: `OPTIMIZER_DESIGN.md` §7 착수 조건 (검증 실패 30건, 동일 원인 클러스터 2개).
