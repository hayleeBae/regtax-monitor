# Step 2: apply-integration

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/specifications/DB_DATA_ROUTING_SPEC.md` (§5 라우팅, §6 안내, §11 AC)
- `/docs/architecture/ADR.md` (ADR-016)
- `/app/main.py` (`/changes/{change_id}/apply` 라우트 — 확장 대상. 정책 게이트·`ProposalService().propose` 호출부)
- `/app/collector/registry.py` (Step 0: `DbDataRegistry`, `load_domains`)
- `/app/application/services.py` (Step 1: `propose` 새 시그니처, `DbUpdateGuidance`)
- `/app/domain/changes/normalization.py` (`ChangeNormalizer` — 전후값 파생)

Step 0·1의 산출물을 읽고 이어서 작업하라.

## 작업

`/apply` 라우트에 DB 라우팅을 통합한다.

1. `app/main.py`의 `apply()` 라우트:
   - `load_domains()`로 레지스트리를 만들고 `DbDataRegistry.match(row.law_id, row.article_no)` 확인.
   - **매칭 시**:
     - `ChangeNormalizer`로 before/after 값 델타를 파생(기존 코드 재사용, 없으면 "").
     - `DbUpdateGuidance` 구성 → `ProposalService().propose(policy_input, generator, db_match=..., guidance=...)` 호출(Step 1 시그니처).
     - 응답에 `decision="db_update_guidance"`, `item_label`, `law_name`, `article`, `before`, `after`, `guidance` 포함.
     - audit 이벤트 기록(기존 `POLICY_DECIDED` 흐름 재사용, decision 값만 db_update_guidance).
     - **매핑 조회·LLM patch 생성 경로에 진입하지 않는다.**
   - **미매칭 시**: 기존 로직 그대로.
   - 매칭 판정은 매핑(Mapping) 유무보다 **먼저** 하라 — 매칭 건은 "사용 가능한 매핑 없음" 422로 빠지면 안 된다.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

추가 검증(테스트로 작성 — 실제 서버·LLM 호출 없이):
- `db_items`에 테스트용 항목이 있는 도메인/변경에 대해 `apply()`가 `db_update_guidance` 응답을 반환하고, LLM 클라이언트/generator가 호출되지 않음(mock으로 검증)
- 미매칭 변경은 기존 apply 동작(정책 게이트/blocked/draft) 회귀
- 매칭 건이 매핑 부재로 422가 되지 않음

> 주의: 이 step 테스트는 무거운 의존성(임베딩·LLM·ChromaDB)을 직접 트리거하지 말 것(CLAUDE.md). 레지스트리 매칭·라우팅 분기만 단위 테스트로 격리해 검증하라. 필요한 협력자는 mock/스텁으로 주입한다.

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크: 스펙 §5·§11, 승인 게이트 유지, ADR-016 일치.
3. `phases/issue-0025/index.json`의 step 2 업데이트:
   - 성공 → `"completed"` + `summary`(수정한 라우트, 응답 스키마, 매칭 우선순위)
   - 3회 실패 → `"error"` + `error_message`
   - 개입 필요 → `"blocked"` + `blocked_reason` 후 중단

## 금지사항

- 매칭 건에서 매핑 조회·LLM patch 생성 경로에 진입하지 마라. 이유: 스펙 §5(코드 draft 미진입).
- 실제 DB 스키마 원문·자격증명을 응답·로그·테스트에 넣지 마라. 이유: org 보안 규칙.
- 테스트에서 임베딩/LLM/ChromaDB를 직접 로드하지 마라. 이유: CLAUDE.md(무거운 의존성 테스트 금지) — mock 사용.
- 미매칭 경로의 기존 동작을 바꾸지 마라. 이유: 동작 보존.
- 기존 테스트를 깨뜨리지 마라.
