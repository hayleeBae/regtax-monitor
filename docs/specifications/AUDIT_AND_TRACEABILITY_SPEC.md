# Audit and Traceability Specification

- 문서 상태: Implementation Ready
- 관련 Issue: `#0013`, `#0014`
- 버전: `audit-spec-v1`

## 1. 목적

법령 분석부터 patch 승인까지의 판단을 `run_id` 기준으로 추적하고, 어떤 입력·코드 버전·모델·프롬프트·검색 근거로 결과가 만들어졌는지 재구성한다.

## 2. 원칙

- append-only
- 민감정보 최소 저장
- 큰 payload는 artifact 분리
- hash 무결성 검증
- run과 event 분리
- audit 실패를 조용히 무시하지 않음
- replay는 원본 repository를 수정하지 않음

## 3. ExecutionRun

`RunType`: collect, analyze, classify, map, apply, golden, approve, reject, evaluation, historical_replay.

`RunStatus`: created, running, completed, partial, failed, cancelled.

필드:

- run_id unique
- parent_run_id
- run_type/status
- law_change_id/proposal_id/evaluation_run_id
- source_hash
- repository alias와 commit
- settings_hash
- llm backend/model
- embedding model
- prompt versions
- started/completed
- error category/message

절대 repository 경로는 API에 노출하지 않는다.

## 4. AuditEvent

```python
@dataclass(frozen=True)
class AuditEventRecord:
    run_id: str
    sequence_no: int
    event_type: AuditEventType
    occurred_at: datetime
    payload: Mapping[str, Any]
    artifact_refs: tuple[ArtifactReference, ...]
```

이벤트: RUN_CREATED/STARTED, NORMALIZATION_COMPLETED, ANALYSIS_REQUESTED/COMPLETED, CLASSIFICATION_COMPLETED, RETRIEVAL_PROVIDER_COMPLETED, RETRIEVAL_COMPLETED, POLICY_DECIDED, EDIT_REQUESTED/COMPLETED, ANCHOR_VALIDATION_FAILED, RETRY_REQUESTED/COMPLETED, PATCH_BUILT, PATCH_VALIDATION_COMPLETED, GOLDEN_STARTED/COMPLETED, PROPOSAL_CREATED/APPROVED/REJECTED, RUN_COMPLETED/FAILED.

payload는 구조화 JSON이며 코드 본문 전체를 넣지 않는다.

## 5. Artifact Store

```python
class ArtifactStore(Protocol):
    def put_bytes(self, run_id: str, artifact_type: str, content: bytes, suffix: str) -> ArtifactReference: ...
    def read(self, ref: ArtifactReference) -> bytes: ...
    def verify(self, ref: ArtifactReference) -> bool: ...
```

기본 `LocalArtifactStore`.

```text
data/audit/<run-id>/
├── manifest.json
├── analysis-input.json
├── analysis-output.json
├── retrieval.json
├── edit-input.json
├── edit-output.txt
├── proposal.patch
└── golden.log
```

`data/audit/`는 gitignore.

## 6. ArtifactReference

artifact id/type, relative path, sha256, size, media type, created_at, contains_code, redacted를 저장한다. 절대경로는 저장하지 않는다.

## 7. Atomic Write

임시 파일 작성 → hash → atomic rename → DB reference 저장. 실패 시 임시 파일 정리. orphan cleanup 정책을 둔다.

## 8. 민감정보

저장 금지: API key, token, cookie, password, `.env`, 인증 header, 개인 식별정보.

기본 false:

```env
AUDIT_STORE_PROMPT_INPUT=false
AUDIT_STORE_CODE_SNIPPETS=false
AUDIT_STORE_LLM_RAW_OUTPUT=true
```

raw output이 코드를 echo할 수 있으므로 `contains_code=true` 처리한다.

## 9. Sanitizer

key whitelist 우선. api_key/token/authorization/password/secret/cookie/oc_key를 제거한다. 문자열 내부 secret 형태도 마스킹하되 정규식만 믿지 않는다.

## 10. Settings Hash

feature flags, top-k, score/classifier version, threshold, model, context, temperature, retry, golden command identifier를 canonical JSON으로 hash한다. secret, 절대경로, timestamp는 제외한다.

## 11. Prompt Version

사람이 관리하는 version과 prompt 본문 hash를 함께 저장한다. 본문이 바뀌었는데 version이 같으면 테스트 또는 시작 경고를 낸다.

## 12. RunRecorder

```python
class RunRecorder:
    def start_run(...)->RunContext: ...
    def record(...)->None: ...
    def complete(...)->None: ...
    def fail(...)->None: ...
```

application service가 호출하며 domain 객체는 recorder를 직접 알지 않는다.

## 13. 실패 정책

- 분석/검색 audit 일부 실패: 원 결과 반환 + `audit_incomplete=true`
- 승인/거절 audit 실패: 상태 변경과 이벤트 기록의 transaction 일관성 필요
- audit artifact 복제 실패는 기존 Proposal patch를 무효화하지 않으나 경고

## 14. Replay

### Inspection Replay

저장 artifact로 당시 흐름을 재구성하며 LLM을 호출하지 않는다.

### Execution Replay

동일 입력과 설정으로 새 run을 실행한다. 원 run의 child run으로 연결하고 모델 차이로 결과가 달라질 수 있음을 기록한다.

manifest에는 source/commit/artifact/model/prompt/settings와 replayability를 포함한다.

## 15. API

```http
GET /runs/{run_id}
GET /runs/{run_id}/events
GET /runs/{run_id}/artifacts
POST /runs/{run_id}/replay
```

초기 UI에서는 코드 포함 artifact 다운로드를 제한할 수 있다.

## 16. DB index

run_id unique, law_change_id, started_at, `(run_id, sequence_no)` unique, event_type, created_at.

## 17. 테스트

run lifecycle, event sequence, append-only, sanitizer, stable settings hash, prompt hash, atomic write, traversal, tamper detection, audit partial, approve transaction, inspection replay, child execution replay.

## 18. 수용 기준

- analyze/map/apply에 run_id
- 이벤트 조회
- retry/golden 기록
- 모델/prompt/commit 기록
- secret 미저장 테스트
- artifact hash
- inspection replay
- 원본 repo 무변경

## 19. Claude Code 요청문

```text
Issue #0013과 #0014를 구현하라.

Run과 AuditEvent는 append-only다.
큰 payload는 LocalArtifactStore에 저장하고 DB에는 reference/hash만 둔다.
절대경로와 secret을 노출하지 않는다.

기존 route를 전면 리팩터링하지 말고 최소 application wrapper로 주요 이벤트를 연결한다.
approve/reject는 상태 변경과 audit event 일관성을 보장한다.
Inspection replay와 Execution replay를 구분하고 실제 repo에 patch를 적용하지 않는다.
```
