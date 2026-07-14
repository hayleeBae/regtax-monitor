# regtax-monitor V2 Implementation Roadmap

- 문서 상태: Draft for Issue Planning
- 기준 문서: `docs/ARCHITECTURE_V2.md`
- 목적: Claude Code가 한 번에 과도한 범위를 수정하지 않도록 V2 구현을 독립적인 issue로 분리한다.
- 중요 원칙: **측정 체계를 먼저 만들고, 그 다음 성능을 개선한다.**

---

## 1. 전체 우선순위

```text
Foundation
  ↓
Evaluation Baseline
  ↓
Change Classification
  ↓
Retrieval Orchestration & Ablation
  ↓
Automation Policy
  ↓
Audit & Replay
  ↓
Verified Mapping Feedback
  ↓
Historical Replay
  ↓
Code Graph
```

이 순서를 지키는 이유는 다음과 같다.

1. 평가 도구 없이 기능을 개선하면 성능 향상을 입증할 수 없다.
2. 변경 유형을 먼저 알아야 유형별 검색·생성 정책을 적용할 수 있다.
3. 검색 결과가 표준화돼야 정책 엔진과 감사 로그가 안정적으로 동작한다.
4. 과거 commit 재현과 코드 그래프는 실제 저장소 데이터가 필요하므로 후순위다.

---

## 2. Issue 목록

| Issue | 제목 | 목표 | 의존성 |
|---|---|---|---|
| #0003 | V2 Foundation Contracts | 공통 타입·버전·run context 기반 | 없음 |
| #0004 | Evaluation Dataset & Metrics | 평가 데이터와 지표 계산 기반 | #0003 |
| #0005 | Evaluation Runner & Report | 실험 실행과 보고서 출력 | #0004 |
| #0006 | Change Normalization | 법령 diff 정규화 | #0003 |
| #0007 | Hybrid Change Classification | 규칙 + LLM fallback 분류 | #0006 |
| #0008 | Retrieval Candidate Contract | 검색 후보 통합 모델 | #0003 |
| #0009 | Retrieval Orchestrator | RAG·사전·상수·검증 매핑 병합 | #0008 |
| #0010 | Retrieval Ablation Benchmark | 검색 전략 비교 실험 | #0005, #0009 |
| #0011 | Automation Policy Engine | 유형·근거 기반 draft 허용 정책 | #0007, #0009 |
| #0012 | Pipeline Service Integration | 기존 route에 분류·정책 연결 | #0011 |
| #0013 | Execution Run & Audit Events | run_id와 append-only 이벤트 | #0003 |
| #0014 | Artifact Store & Replay Metadata | 프롬프트·결과 해시와 재현 정보 | #0013 |
| #0015 | Mapping Decision History | 승인·거절·stale 이력 | #0013 |
| #0016 | Verified Mapping Reranking | 검증·거절 이력 검색 반영 | #0015, #0009 |
| #0017 | Historical Replay Fixtures | 과거 commit 평가 포맷 | #0005 |
| #0018 | Historical Replay Runner | base/answer commit 비교 실행 | #0017, #0014 |
| #0019 | Code Symbol Index | 코드 심볼과 최소 관계 추출 | #0008 |
| #0020 | Graph-assisted Retrieval | 코드 관계를 후보 점수에 반영 | #0019, #0010 |
| #0021 | V2 Documentation & Release Gate | 문서 동기화·최종 회귀 검증 | 전체 |

---

# 3. Issue 상세 기획

## Issue #0003 — V2 Foundation Contracts

### 목표

V2 전 기능이 공유할 공통 타입과 버전 관리 구조를 추가한다. 아직 기존 파이프라인 동작은 변경하지 않는다.

### 구현 범위

- `RunType`
- `RunStatus`
- `ChangeType`
- `AutomationDecision`
- `RetrievalSource`
- `ErrorCategory`
- `VersionedComponent`
- `RunContext`
- 시간·ID 생성 유틸리티
- JSON serialization 규칙

### 권장 경로

```text
app/domain/
├── common/
│   ├── enums.py
│   ├── version.py
│   └── errors.py
└── runs/
    └── context.py
```

### 제약

- FastAPI와 SQLAlchemy에 의존하지 않는 순수 Python 타입이어야 한다.
- Domain 계층에서 `app.main`, DB session, LLM client를 import하지 않는다.
- 기존 enum과 중복될 경우 신규 생성 전에 재사용 가능성을 검토한다.

### 테스트

- enum 직렬화
- 잘못된 version 값
- run_id 유일성
- RunContext immutable 여부 또는 변경 정책

### 수용 기준

- 기존 테스트 100% 통과
- 신규 타입에 단위 테스트 존재
- 앱 동작에는 변화 없음
- `docs/ARCHITECTURE_V2.md`에 정의된 이름과 일치

### Claude Code 지시문 핵심

```text
기존 기능을 리팩터링하지 말고 V2 공통 계약만 추가하라.
새 domain 모듈은 FastAPI, SQLAlchemy, LLM SDK를 import하지 않아야 한다.
```

---

## Issue #0004 — Evaluation Dataset & Metrics

### 목표

LLM 또는 실제 서비스 실행 없이도 평가 데이터셋을 읽고 retrieval 지표를 계산할 수 있는 순수 평가 코어를 구현한다.

### 구현 범위

- JSONL/YAML 평가 케이스 스키마
- dataset loader
- schema validation
- Recall@K
- MRR
- file precision
- exact replacement accuracy
- patch file coverage
- metric 결과 모델

### 데이터셋 필수 필드

```yaml
case_id:
domain:
law:
expected:
repository:
policy:
```

### 권장 경로

```text
app/evaluation/
├── case.py
├── loader.py
├── result.py
└── metrics/
    ├── retrieval.py
    ├── patch.py
    └── classification.py

evaluation/datasets/
└── core.yaml
```

### 비범위

- 실제 LLM 호출
- ChromaDB 실행
- API
- Markdown 보고서

### 테스트

- 올바른 dataset load
- 필수 필드 누락
- 빈 정답 목록
- 정답이 rank 1, rank 5, 미검색인 경우
- 중복 후보
- MRR 계산
- 불필요 파일 수정률

### 수용 기준

- 최소 10개 mock 평가 케이스
- 모든 지표가 결정론적으로 계산됨
- 외부 네트워크·LLM 없이 테스트 가능

---

## Issue #0005 — Evaluation Runner & Report

### 목표

평가 케이스를 실행하고 결과를 JSON·JSONL·Markdown으로 출력한다.

### 구현 범위

- experiment interface
- case runner
- 실패 격리
- 결과 디렉토리 생성
- config snapshot
- `summary.json`
- `cases.jsonl`
- `report.md`
- `failures.md`
- CLI entrypoint

### CLI 예시

```bash
python -m app.evaluation.runner \
  --dataset evaluation/datasets/core.yaml \
  --experiment fixture_baseline \
  --output evaluation/results
```

### 실패 정책

- 하나의 case 실패가 전체 run을 중단하지 않는다.
- 실패 case는 상태와 오류 분류를 기록한다.
- summary에는 성공·실패·skip 건수를 표시한다.

### 수용 기준

- fixture baseline 실험이 실행됨
- 동일 입력에서 동일 보고서 수치
- timestamp를 제외한 결과 비교가 가능함
- 결과 폴더는 gitignore 처리

---

## Issue #0006 — Change Normalization

### 목표

법령 변경문에서 금액·비율·날짜·기간·연령 등 분류 및 검색 신호를 일관되게 추출한다.

### 구현 범위

- `NormalizedChange`
- money normalization
- ratio normalization
- date normalization
- duration/age normalization
- before/after added/removed signals
- source hash
- normalizer version

### 재사용

`app/embedding/const_inventory.py`의 기존 로직을 우선 재사용한다. 중복 정규식 생성을 금지한다.

### 수용 기준

- `15만원 → 25만원`
- `100분의 6 → 100분의 8`
- `2026년 1월 1일`
- `만 19세`
- `3개월`
예제가 모두 정형화된다.

---

## Issue #0007 — Hybrid Change Classification

### 목표

규칙 기반 분류를 우선하고, 모호한 경우에만 LLM을 호출한다.

### 구현 범위

- `RuleChangeClassifier`
- `LlmChangeClassifier`
- `HybridChangeClassifier`
- 분류 prompt
- JSON schema
- confidence
- reason/signals
- classifier version
- DB 저장 또는 우선 artifact 저장

### LLM 인터페이스

기존 `LlmClient`를 우회하여 HTTP 호출하지 않는다. 필요한 경우 인터페이스에 `classify_change()`를 추가하고 local/claude 구현을 모두 맞춘다.

### fallback

```text
rule confidence >= threshold → rule result
rule ambiguous + LLM success → combined result
LLM failure + rule candidate → rule result with lower confidence
both fail → UNKNOWN
```

### 테스트

- 명확한 value/rate/date
- 조건 변경
- 구조 변경
- LLM JSON 파싱 실패
- LLM timeout
- UNKNOWN fallback

### 수용 기준

- 최소 8개 유형 지원
- 분류 결과에 근거 존재
- LLM이 없어도 기본 분류 가능

---

## Issue #0008 — Retrieval Candidate Contract

### 목표

기존 RAG·사전·상수·검증 매핑 결과를 공통 후보 모델로 표현한다.

### 구현 범위

- `RetrievalCandidate`
- `RetrievalEvidence`
- `CandidateLocation`
- score normalization interface
- deduplication key
- stale flag
- serialization

### 제약

- 기존 검색 구현을 아직 변경하지 않는다.
- source-specific raw payload를 보존할 수 있어야 한다.
- 외부에 노출할 안전한 evidence와 내부 debug evidence를 구분한다.

### 수용 기준

- 세 검색 결과를 동일 JSON 구조로 변환 가능
- 병합 전 원점수가 유지됨
- 중복 후보 키 테스트 존재

---

## Issue #0009 — Retrieval Orchestrator

### 목표

모든 검색 provider를 호출하고 후보를 병합·정렬한다.

### 구현 범위

- provider protocol
- verified provider
- rag provider adapter
- dictionary provider adapter
- constant provider adapter
- orchestrator
- score version
- feature flags
- top-k

### 정책

- provider 하나의 실패는 가능한 경우 전체 검색을 중단하지 않는다.
- 실패 provider는 결과 metadata와 audit에 기록한다.
- 모든 provider가 실패하면 명확한 `RETRIEVAL_ERROR`.

### 수용 기준

- 기존 map API가 신규 orchestrator를 통해 같은 수준의 결과를 반환
- 각 후보에 source evidence 존재
- 기능 flag로 provider별 on/off 가능
- 기존 `rag_hits/dict_matches/const_matches` 표시를 유지하거나 호환 변환 제공

---

## Issue #0010 — Retrieval Ablation Benchmark

### 목표

검색 결합의 효과를 수치로 비교한다.

### 실험

- `rag_only`
- `rag_dict`
- `rag_const`
- `hybrid_all`
- `verified_hybrid`

### 고정 조건

- dataset
- embedding model
- repo fixture
- top-k
- scoring version
- index snapshot

### 결과

- Recall@1
- Recall@5
- MRR
- file precision
- latency
- provider failure count

### 수용 기준

- 한 명령으로 전체 실험 실행
- 비교 Markdown 표 생성
- case별 순위 차이 확인 가능
- 결과에 환경 snapshot 포함

---

## Issue #0011 — Automation Policy Engine

### 목표

patch 생성 가능 여부를 변경 유형과 검색 근거를 기준으로 결정한다.

### 구현 범위

- policy input/output
- 기본 rule set
- configurable thresholds
- structured block reason
- policy version

### 차단 사례

- structural
- unknown low confidence
- stale-only mapping
- no repository commit
- file not found
- source conflict

### 수용 기준

- policy가 LLM을 호출하지 않음
- 동일 입력은 동일 결정
- 차단 사유가 UI/API에서 표시 가능
- 정책 차단이 analyze/map을 막지 않음

---

## Issue #0012 — Pipeline Service Integration

### 목표

기존 `main.py` 라우트의 직접 orchestration 일부를 application service로 이동하고 분류·정책을 실제 흐름에 연결한다.

### 구현 범위

- `AnalysisService`
- `MappingService`
- `ProposalService`
- 기존 route thin adapter
- classify/map/apply 연결
- 호환 응답

### 중요 제약

- 전면 리팩터링 금지
- route URL 변경 금지
- 기존 DB 모델 삭제 금지
- `apply`에서 policy를 우회하는 별도 경로 금지

### 수용 기준

- 기존 API 회귀 테스트 통과
- structural case는 patch 생성 대신 구조화된 차단 응답
- value case는 기존 patch 생성 흐름 유지

---

## Issue #0013 — Execution Run & Audit Events

### 목표

모든 주요 실행에 `run_id`를 부여하고 단계별 이벤트를 저장한다.

### 구현 범위

- `ExecutionRun`
- `AuditEvent`
- recorder
- event sequence
- DB models
- repository
- API 조회

### 저장 범위

- 모델·프롬프트 버전
- code commit
- source/settings hash
- latency
- status
- 오류 category

### 수용 기준

- analyze/map/apply 각각 run 생성
- run_id로 이벤트 순서 조회
- 비밀정보가 payload에 없음
- audit 실패 처리 테스트

---

## Issue #0014 — Artifact Store & Replay Metadata

### 목표

DB에 넣기 어려운 큰 실행 결과를 파일 artifact로 저장하고 해시로 검증한다.

### 구현 범위

- artifact store interface
- local filesystem implementation
- atomic write
- SHA-256
- prompt input/output artifact
- patch/test log artifact
- replay manifest

### 기본 정책

- 코드 snippet 저장 기본값 false
- raw LLM output 저장 설정 가능
- artifact directory gitignore
- 경로 traversal 차단

### 수용 기준

- artifact 변조 시 해시 불일치 탐지
- atomic write 테스트
- run manifest 생성

---

## Issue #0015 — Mapping Decision History

### 목표

단일 `verified` boolean을 append-only 의사결정 이력으로 보완한다.

### 구현 범위

- `MappingDecision`
- verify/reject/stale
- reason codes
- actor
- commit/path hash
- 기존 verify API 호환

### 수용 기준

- 기존 verify 동작 유지
- 결정 이력 조회 가능
- 결정 수정 대신 새 이벤트 추가
- stale 판정 가능

---

## Issue #0016 — Verified Mapping Reranking

### 목표

검증·거절 이력을 retrieval 점수에 반영한다.

### 구현 범위

- verified boost
- rejected penalty
- stale validation
- same article/change-type matching
- scoring version update

### 수용 기준

- 유효한 verified 후보가 상단으로 이동
- stale mapping은 강제 정답 처리되지 않음
- 거절 이력으로 무관 후보 점수 감소
- ablation report로 전후 비교

---

## Issue #0017 — Historical Replay Fixtures

### 목표

과거 개정과 실제 commit을 평가 케이스로 기술할 수 있는 포맷을 만든다.

### 구현 범위

- base commit
- answer commit
- relevant paths
- expected replacements
- optional golden command
- privacy mode

### 수용 기준

- mock git repo 기반 fixture 3개
- unrelated changes가 섞인 answer commit 사례 포함
- 실제 코드 본문 비공개 모드 지원

---

## Issue #0018 — Historical Replay Runner

### 목표

임시 worktree에서 과거 시점의 코드를 재현하고 생성 결과를 실제 수정과 비교한다.

### 구현 범위

- git worktree/clone abstraction
- base checkout
- answer diff extraction
- generated patch comparison
- cleanup
- timeout

### 안전 제약

- 원본 repo 변경 금지
- force/reset 금지
- 모든 작업은 임시 디렉토리
- command allowlist

### 수용 기준

- fixture 3개 실행
- 실패 후 임시 디렉토리 정리
- file coverage와 replacement accuracy 보고
- 실제 answer commit 전체 일치만을 성공 기준으로 삼지 않음

---

## Issue #0019 — Code Symbol Index

### 목표

별도 그래프 DB 없이 Java·XML·SQL의 최소 심볼과 관계를 추출한다.

### 우선 지원

- Java method/class
- MyBatis statement id
- mapper namespace
- test method
- constant usage

### 비범위

- 완전한 Java compiler 수준 분석
- 모든 동적 호출 해석
- Neo4j

### 수용 기준

- mock repo 심볼 추출
- Service ↔ Mapper ↔ Test 일부 연결
- 실패 파일이 전체 인덱싱을 중단하지 않음

---

## Issue #0020 — Graph-assisted Retrieval

### 목표

코드 관계를 retrieval 근거로 추가한다.

### 구현 범위

- graph provider
- neighbor expansion
- graph score
- evidence
- experiment flag

### 수용 기준

- 관계 파일이 top-k에 추가될 수 있음
- graph 단독으로 draft 허용하지 않음
- ablation 결과 생성

---

## Issue #0021 — V2 Documentation & Release Gate

### 목표

구현·문서·평가 결과를 동기화하고 V2 완료 여부를 검증한다.

### 체크리스트

- architecture updated
- ADR additions
- API docs
- DB schema
- evaluation report
- security scan
- migration guide
- rollback guide
- README demo flow
- status update

### 최종 수용 기준

```bash
bash scripts/verify.sh quick
bash scripts/verify.sh full
bash scripts/verify.sh security
```

통과 또는 문서화된 예외만 존재해야 한다.

---

# 4. Phase별 완료 정의

## Phase A — 측정 기반

포함 issue: `#0003~#0005`

완료 시 할 수 있어야 하는 것:

- 평가 데이터셋 정의
- 정답 후보와 검색 결과 비교
- Recall@K와 MRR 계산
- Markdown 보고서 생성

## Phase B — 변경 이해

포함 issue: `#0006~#0007`

완료 시 할 수 있어야 하는 것:

- 법령 diff 정규화
- 변경 유형 분류
- 분류 근거·신뢰도 기록

## Phase C — 검색 성능 증명

포함 issue: `#0008~#0010`

완료 시 할 수 있어야 하는 것:

- 검색 소스 통합
- 조합별 성능 비교
- 어떤 설계가 성능을 올렸는지 수치로 설명

## Phase D — 안전한 자동화 경계

포함 issue: `#0011~#0012`

완료 시 할 수 있어야 하는 것:

- 수치 변경은 patch 초안 허용
- 구조 변경은 분석만 제공
- 정책 차단 사유 표시

## Phase E — 기업형 추적성

포함 issue: `#0013~#0016`

완료 시 할 수 있어야 하는 것:

- 결과 생성 근거 추적
- 승인·거절 이력 축적
- 검증 매핑 재사용

## Phase F — 실제 과거 사례 검증

포함 issue: `#0017~#0018`

완료 시 할 수 있어야 하는 것:

- 과거 코드 시점에서 파이프라인 재실행
- 실제 commit과 수정 범위 비교
- 포트폴리오용 객관적 결과 확보

## Phase G — 연관 파일 검색

포함 issue: `#0019~#0020`

완료 시 할 수 있어야 하는 것:

- Service·Mapper·Test 관계 후보 확장
- 검색 누락 감소 여부 측정

---

# 5. Claude Code 공통 작업 지침

각 issue를 Claude Code에 전달할 때 아래 공통 지침을 포함한다.

```text
1. 작업 전 CLAUDE.md, docs/ARCHITECTURE.md, docs/ADR.md,
   docs/ARCHITECTURE_V2.md를 읽는다.

2. 이번 issue 범위 밖의 리팩터링을 하지 않는다.

3. 기존 두 seam인 LlmClient와 CodebaseAdapter를 우회하지 않는다.

4. Domain 계층은 FastAPI, SQLAlchemy, 외부 LLM SDK에 의존하지 않는다.

5. 실제 repo에 patch를 자동 적용하는 경로를 만들지 않는다.

6. 기존 API의 하위 호환성을 유지한다.

7. 신규 기능에는 단위 테스트와 필요한 통합 테스트를 추가한다.

8. 작업 후 다음 명령을 실행하고 결과를 기록한다.
   - bash scripts/verify.sh quick
   - bash scripts/verify.sh full
   - 필요 시 bash scripts/verify.sh security

9. 완료 응답에는 아래를 포함한다.
   - 변경 파일 목록
   - 핵심 설계 선택
   - 테스트 결과
   - 남은 위험
   - 문서 수정 내역

10. 명세와 실제 구조가 충돌하면 임의로 대규모 변경하지 말고,
    충돌 내용과 최소 수정안을 먼저 보고한다.
```

---

# 6. 첫 개발 요청 권장 순서

설계 문서를 저장소에 반영한 후 Claude Code에 처음 전달할 작업은 `Issue #0003`이다.

다음 요청문을 사용할 수 있다.

```text
Issue #0003 — V2 Foundation Contracts를 구현하시오.

반드시 먼저 다음 문서를 읽고 제약을 지켜라.
- CLAUDE.md
- docs/ARCHITECTURE.md
- docs/ADR.md
- docs/ARCHITECTURE_V2.md
- docs/IMPLEMENTATION_ROADMAP.md

목표:
V2 기능이 공유할 순수 domain 계약을 추가한다.
이번 작업에서는 기존 파이프라인, API, DB 동작을 변경하지 않는다.

구현 범위:
- RunType
- RunStatus
- ChangeType
- AutomationDecision
- RetrievalSource
- ErrorCategory
- VersionedComponent
- RunContext
- run_id 생성과 직렬화 테스트

제약:
- Domain 모듈은 FastAPI, SQLAlchemy, 외부 LLM SDK를 import하지 않는다.
- 기존 enum과 중복 여부를 먼저 확인한다.
- 범위 밖 리팩터링 금지.
- 기존 테스트를 모두 유지한다.

수용 기준:
- 신규 타입 단위 테스트
- run_id 유일성 테스트
- JSON 직렬화 테스트
- bash scripts/verify.sh quick 통과
- bash scripts/verify.sh full 통과

완료 후 변경 파일, 테스트 결과, 설계 충돌 여부를 보고하라.
```

---

# 7. 설계 검토 체크포인트

각 Phase가 끝날 때 다음을 확인한다.

- 기능이 실제 성능 지표와 연결되는가?
- 모델을 더 믿는 방향이 아니라 실패를 통제하는 방향인가?
- 기존 승인 게이트를 약화하지 않았는가?
- 실제 코드가 외부로 나가는 경로가 추가되지 않았는가?
- 결과가 `run_id`와 version으로 설명 가능한가?
- 동일 결과를 다시 만들기 위한 정보가 충분한가?
- 운영 DB와 평가 데이터가 분리됐는가?
- Claude Code가 범위를 넘겨 구조 전체를 흔들지 않았는가?
