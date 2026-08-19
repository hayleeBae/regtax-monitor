# Step 1: routing-decision

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/specifications/DB_DATA_ROUTING_SPEC.md` (§5 라우팅, §6 안내 산출물, §7 NO_CODE_IMPACT 구분)
- `/docs/architecture/ADR.md` (ADR-016)
- `/app/domain/common/enums.py` (`AutomationDecision`, `ChangeType` — 확장 대상)
- `/app/policy/automation.py` (`AutomationDecision` 소비, 정책 결정)
- `/app/application/services.py` (`ProposalService.propose`, `ProposalResult` — 확장 대상)
- `/app/collector/registry.py` (Step 0 산출물: `DbItem`, `DbDataRegistry`)

Step 0에서 만든 `DbItem`/`DbDataRegistry`를 꼼꼼히 읽고 이어서 작업하라.

## 작업

레지스트리 매칭을 **정책/제안 레이어의 라우팅 결정**으로 배선한다.

1. `app/domain/common/enums.py`: `AutomationDecision`에 `DB_UPDATE_GUIDANCE = "db_update_guidance"` 추가.

2. `DbUpdateGuidance` dataclass 추가 (위치: `app/application/services.py` 또는 `app/policy/`에 적절히. frozen):
   ```python
   @dataclass(frozen=True)
   class DbUpdateGuidance:
       item_label: str
       law_name: str
       article: str
       before: str        # ChangeNormalizer 파생값(없으면 "")
       after: str
       guidance: str
   ```

3. `ProposalService.propose`를 확장해 DB 라우팅을 우선 처리한다:
   ```python
   def propose(self, policy_input, generator, db_match: DbItem | None = None,
               guidance: DbUpdateGuidance | None = None) -> ProposalResult:
       # db_match 가 있으면: generator 를 호출하지 않고
       #   ProposalResult(blocked=True, decision=DB_UPDATE_GUIDANCE, proposal=<guidance dict>) 반환.
       # db_match 가 없으면: 기존 동작 그대로(policy.decide → generator).
   ```
   - **핵심 규칙**: db_match 시 `generator()`(= LLM patch 생성)를 절대 호출하지 않는다.
   - `db_match=None`이면 기존 경로와 100% 동일해야 한다(회귀).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

추가 테스트:
- db_match 주입 시 `propose`가 `DB_UPDATE_GUIDANCE` 결정을 반환하고 **generator가 호출되지 않음**(mock generator 호출 횟수 0 검증)
- db_match=None 시 기존 draft_allowed/blocked 경로 회귀
- `DbUpdateGuidance`가 안내 dict로 직렬화될 때 DB 스키마 원문이 포함되지 않음(라벨만)

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크: ADR-016/스펙 §5~§7 일치, 승인 게이트 원칙 유지.
3. `phases/issue-0025/index.json`의 step 1 업데이트:
   - 성공 → `"completed"` + `summary`(추가된 enum 값, `propose` 새 시그니처, DbUpdateGuidance 위치 명시)
   - 3회 실패 → `"error"` + `error_message`
   - 개입 필요 → `"blocked"` + `blocked_reason` 후 중단

## 금지사항

- db_match가 있을 때 `generator()`(LLM 생성)를 호출하지 마라. 이유: 코드 patch 무의미 항목에 출력 토큰을 쓰고 승인 게이트 취지를 흐린다(스펙 §5).
- 기존 `propose(policy_input, generator)` 호출부의 동작을 바꾸지 마라(신규 인자는 기본값 None). 이유: 동작 보존.
- `DbUpdateGuidance`나 로그에 실제 DB 스키마 원문을 넣지 마라. 이유: org 보안 규칙.
- 자동 DB 갱신 경로를 만들지 마라. 이유: CLAUDE.md CRITICAL(사람 승인 게이트).
- 기존 테스트를 깨뜨리지 마라.
