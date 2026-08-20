# Step 2: gate-exclusion

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/specifications/CODE_GRAPH_SPEC.md` (§10 자동화 게이트 — CODE_GRAPH는 독립 근거가 아니다)
- `/docs/architecture/ADR.md` (ADR-017, ADR-011 자동화 정책)
- `/app/policy/automation.py` (`AutomationPolicyEngine.decide` — `min_independent_sources` 판정부. `sources = {evidence.source for evidence in top.evidences}` 라인)
- `/app/domain/common/enums.py` (`RetrievalSource.CODE_GRAPH`)
- `/app/retrieval/graph_expand.py`, `/app/retrieval/orchestrator.py` (Step 0·1 — CODE_GRAPH evidence가 후보에 붙는 방식)

Step 1이 후보에 CODE_GRAPH evidence를 어떻게 붙이는지 확인하고 이어서 작업하라.

## 작업

자동화 정책의 독립 근거 카운트에서 CODE_GRAPH를 제외한다.

`app/policy/automation.py`의 `decide`:
- 독립 source 수를 셀 때 사용하는 source 집합에서 `RetrievalSource.CODE_GRAPH`를 **빼고** 센다:
  ```python
  independent = {e.source for e in top.evidences if e.source is not RetrievalSource.CODE_GRAPH}
  if len(independent) < self.thresholds.min_independent_sources and not valid_verified:
      reasons.append(BlockReason("retrieval_evidence_insufficient", "독립 검색 근거가 부족함"))
  ```
- 즉 CODE_GRAPH는 단독으로도, 다른 seed 근거에 얹혀서도 draft 허용의 두 번째 독립 근거가 되지 못한다.
- 다른 판정(retrieval_score_low 등)의 로직은 바꾸지 않는다 — 최상위 evidence 점수 계산 등 기존 동작 보존.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

추가 테스트:
- top 후보의 evidence가 {RAG, CODE_GRAPH}뿐이면 독립 source 1개로 간주 → `retrieval_evidence_insufficient` 발생(draft 미허용)
- {RAG, CONSTANT_MATCH}면 종전대로 2 source 충족(회귀)
- CODE_GRAPH 단독이면 미충족
- 기존 automation 테스트 전부 통과(회귀)

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크: 스펙 §10 일치, 다른 block 사유 로직 불변.
3. `phases/issue-0020/index.json`의 step 2 업데이트:
   - 성공 → `"completed"` + `summary`(수정 라인, 제외 규칙, 회귀 확인)
   - 3회 실패 → `"error"` + `error_message`
   - 개입 필요 → `"blocked"` + `blocked_reason` 후 중단

## 금지사항

- CODE_GRAPH를 독립 source로 세지 마라. 이유: 그래프는 seed에 인과 종속(스펙 §10) — 잘못된 이웃이 draft를 유발하면 "잘못 고침" 위험.
- `min_independent_sources` 임계값 자체나 verified_mapping 예외 로직을 바꾸지 마라. 이유: 이 step 범위는 CODE_GRAPH 제외뿐.
- 다른 block 사유(score_low, candidate_missing 등) 판정을 건드리지 마라. 이유: 동작 보존.
- 기존 테스트를 깨뜨리지 마라.
