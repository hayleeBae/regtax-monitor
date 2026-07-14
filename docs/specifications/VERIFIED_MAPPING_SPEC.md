# Verified Mapping Feedback Specification

- 문서 상태: Implementation Ready
- 관련 Issue: `#0015`, `#0016`
- 버전: `verified-mapping-v1`

## 1. 목적

`Mapping.verified` boolean을 유지하면서 승인·거절·stale 이력과 코드 버전을 축적하고 retrieval 재정렬에 활용한다.

## 2. 모델

```python
class MappingDecisionType(str, Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    STALE = "stale"
    REVOKED = "revoked"
```

필드: mapping_id, decision, reason_code/text, repository_commit, path_hash, symbol_hash, actor, created_at.

모든 결정은 append-only다.

## 3. Reason Code

Verified: confirmed_by_owner, matched_historical_change, golden_test_confirmed, exact_constant_confirmed, domain_mapping_confirmed, other.

Rejected: wrong_module, legacy_code, false_positive_term, same_value_unrelated, generated_code, test_only, duplicate_candidate, insufficient_context, other.

Stale: file_missing, symbol_missing, content_changed, module_moved, repository_replaced.

## 4. 상태 계산

이력을 시간순으로 읽어 최신 상태를 계산한다. `VERIFIED → STALE → VERIFIED`는 최종 VERIFIED다. REVOKED는 이전 verified를 취소하지만 후보를 삭제하지 않는다.

## 5. 기존 API 호환

`PATCH /mappings/{id}/verify`:

- true → VERIFIED event
- false → REVOKED 또는 명시적 REJECTED

`Mapping.verified`는 최신 상태 compatibility cache로 유지하며 event insert와 같은 transaction에서 갱신한다.

## 6. 신규 API

```http
POST /mappings/{id}/decisions
GET  /mappings/{id}/decisions
GET  /mappings/{id}/state
```

## 7. 유효성 정보

검증 당시 repository commit, relative path, file hash, symbol snippet hash를 저장한다. 코드 본문은 decision 테이블에 저장하지 않는다.

## 8. Stale Validator

파일 존재, symbol/anchor, file hash, commit 관계를 검사한다. hash가 달라도 symbol이 유효하면 `modified_but_valid`로 처리 가능하다. validator 실패가 자동 stale event를 만들지는 않는다.

## 9. 재사용 기준

1. same stable law/article + same change type
2. same article + compatible type
3. same law + domain term
4. domain only

문맥이 다르면 boost하지 않는다.

## 10. Reranking

예시:

- valid exact verified +0.35
- compatible verified +0.20
- golden-confirmed +0.05
- historical match +0.05
- rejected exact -0.30
- repeated rejection 최대 -0.50
- stale boost 제거 + penalty
- legacy -0.15

수치는 retrieval scoring version에 포함한다.

## 11. 반복 거절

candidate path/symbol + domain + law/article + reason + change type 문맥이 같을 때만 강한 penalty를 적용한다. 다른 법령에서의 거절을 영구 차단으로 쓰지 않는다.

## 12. UI

현재 상태, 검증 commit, stale warning, 마지막 이유, 이력 보기를 제공한다. 거절 시 reason code와 optional text를 받는다.

## 13. Migration

신규 table 생성 → 기존 verified=true에 legacy migration event → 기존 컬럼 유지. migration은 idempotent해야 한다.

## 14. 테스트

verified/reject/revoke, resolver, transaction rollback, migration idempotency, stale missing, modified but valid, boost, repeated rejection, unrelated rejection, legacy API.

## 15. 수용 기준

- append-only history
- 기존 verify API
- cache 일치
- stale 검증
- retrieval evidence에 결정 근거
- verified_hybrid 비교
- actor/reason 추적

## 16. Claude Code 요청문

```text
Issue #0015와 #0016을 구현하라.

Mapping.verified를 삭제하지 말고 compatibility cache로 유지한다.
결정은 MappingDecision append-only event로 기록한다.
기존 verified 데이터를 idempotent migration한다.

Reranking은 같은 법령/조문/유형 문맥에서만 적용한다.
verified를 무조건 1위로 강제하지 않고 stale을 검증한다.
변경 전후 retrieval ablation 결과를 생성한다.
```
