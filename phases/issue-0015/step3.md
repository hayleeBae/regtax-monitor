# Step 3: api-endpoints

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 승인 게이트 우회 금지, 코드 반출 금지)
- `/docs/architecture/ADR.md` (ADR-008 — compat cache 동기 갱신)
- `/docs/specifications/VERIFIED_MAPPING_SPEC.md` (§5 기존 API 호환, §6 신규 API)
- `/app/main.py` 의 다음 부분:
  - `verify_mapping` (현재 `PATCH /mappings/{mapping_id}/verify`, 약 827행)
  - `get_mappings` (약 796행 — verified 필터 사용처)
  - `changes/{change_id}/apply` (약 846행 — verified 필터 사용처, 회귀 주의)
  - 상단 import와 `get_session` 의존성 주입 방식
- `/app/mappings/repository.py` (Step 2 — `SqlAlchemyMappingDecisionRepository`)
- `/app/domain/mappings/decisions.py` (Step 0 — enum, `allowed_reason_codes`, `MappingDecisionRecord`)
- `/app/db/models.py` (Step 1 — `MappingDecision`, `Mapping`)

## 작업

### 1) 기존 `PATCH /mappings/{mapping_id}/verify` 확장 (하위호환 유지)

시그니처(`mapping_id`, `verified: bool = True`, optional `actor`, optional `reason_code`)와 route URL, 응답 형태는 **바꾸지 않는다**(기존 응답 키 유지). 내부 동작만 확장:

- `verified=True` → `MappingDecisionType.VERIFIED` 이벤트 append.
- `verified=False` → `MappingDecisionType.REVOKED` 이벤트 append (되돌리기 의미. REJECTED가 아니다).
- **같은 트랜잭션**에서 `Mapping.verified`를 `resolve_state(...) == VERIFIED` 결과로 갱신한 뒤 commit. 이벤트 append와 cache 갱신이 한 트랜잭션이어야 한다(스펙 §5).
- `actor` 미지정 시 `"owner"`.

### 2) 신규 엔드포인트 (스펙 §6)

```
POST /mappings/{mapping_id}/decisions   # body: decision, reason_code?, reason_text?, actor?
GET  /mappings/{mapping_id}/decisions   # 이력 목록
GET  /mappings/{mapping_id}/state        # 현재 상태 + 최근 이유 + 검증 commit
```

- `POST /decisions`: `decision`은 `MappingDecisionType` allowlist 검증, `reason_code`는 `allowed_reason_codes(decision)`로 검증(불일치 시 422/400). 매핑 없으면 404. append 후 compat cache(`Mapping.verified`)를 같은 트랜잭션에서 재계산·갱신.
- `GET /decisions`: repository `list_for_mapping` 결과를 직렬화. **코드 본문은 응답에 포함하지 않는다**(해시만 저장돼 있으므로 자연히 없음).
- `GET /state`: `current_state`(MappingDecisionType | None) + 최근 이벤트의 reason_code/reason_text/repository_commit.
- 매핑 조회는 기존처럼 `db.get(Mapping, mapping_id)` 사용, 없으면 404.

### 3) 테스트

`tests/test_mapping_decision_api.py` 신규 작성 (FastAPI `TestClient`, 다른 API 테스트 스타일 참고, 무거운 의존성 트리거 금지):
- **회귀**: `PATCH /verify?verified=true` → 200, 응답 키(mapping_id/path/symbol/verified) 유지, `Mapping.verified==True`, decision 1건(VERIFIED).
- `PATCH /verify?verified=false` → `Mapping.verified==False`, decision(REVOKED) 추가.
- `POST /decisions` REJECTED + 유효 reason_code → 201/200, `GET /decisions`에 반영, `GET /state`가 REJECTED.
- `POST /decisions`에 타입-불일치 reason_code → 4xx.
- 존재하지 않는 mapping_id → 404.
- `apply`가 여전히 verified 매핑을 사용하는지 최소 1개 회귀 확인(가능하면).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 기존 `PATCH /verify` route URL·응답 키가 보존됐는가?
   - 이벤트 append와 `Mapping.verified` 갱신이 같은 트랜잭션인가?
   - reason_code allowlist 검증이 있는가? 응답에 코드 본문이 없는가?
   - CLAUDE.md CRITICAL(승인 게이트 우회·코드 반출) 위반이 없는가?
3. 결과에 따라 `phases/issue-0015/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "신규 3개 엔드포인트 + verify 확장 + compat cache 동기 갱신 요약"`
   - 실패(3회) → `"status": "error"`, `"error_message"`
   - 개입 필요 → `"status": "blocked"`, `"blocked_reason"`

## 금지사항

- `PATCH /verify`의 route URL·응답 키를 바꾸지 마라. 이유: 기존 UI/클라이언트 하위호환(스펙 §5).
- `Mapping.verified` cache 갱신을 이벤트 append와 다른 트랜잭션으로 분리하지 마라. 이유: 부분 실패 시 cache 불일치로 apply가 오작동한다.
- `reason_text`나 응답에 대상 코드 본문을 넣지 마라. 이유: 코드 반출 금지(CLAUDE.md CRITICAL) — 해시만 저장한다.
- patch 자동 적용 등 승인 게이트 우회 경로를 만들지 마라.
- 기존 테스트를 깨뜨리지 마라.
