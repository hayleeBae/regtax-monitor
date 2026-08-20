# Step 3: graph-ablation

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `/CLAUDE.md` (테스트에서 무거운 의존성(임베딩·LLM·ChromaDB) 직접 트리거 금지)
- `/docs/specifications/CODE_GRAPH_SPEC.md` (§12 평가/ablation)
- `/docs/specifications/RETRIEVAL_EXPERIMENT_SPEC.md` (기존 ablation 규약)
- `/docs/architecture/ADR.md` (ADR-017, ADR-010)
- `/app/evaluation/retrieval_benchmark.py` (기존 ablation 구조 — 확장 대상)
- `/app/retrieval/orchestrator.py` (Step 1: `graph_enabled` 토글)
- `/app/retrieval/graph_expand.py` (Step 0)

기존 `retrieval_benchmark`가 실험을 어떻게 구성·비교·리포트하는지 읽고 이어서 작업하라.

## 작업

graph on/off ablation을 추가한다.

`app/evaluation/retrieval_benchmark.py`:
- `graph_enabled` False(기준선) vs True(graph_hybrid) 두 조건을 같은 평가셋에 대해 돌리고 비교한다.
- 지표: 관련 파일 **Recall@5**, **test file recall**, **unnecessary file rate**, **candidate count**, **latency**(스펙 §12).
- 리포트에 두 조건 수치를 나란히 출력한다. **그래프가 불필요후보율을 높이거나 recall 이득이 없으면 그대로 보고**한다(그래프가 늘 이득은 아니다 — 숨기지 않는다).
- 현 평가셋은 합성 mock이므로, 리포트에 "합성 mock 기준 — 실 eHR 수치는 9월 W1 실 fixture 이후 재측정" 한 줄을 남긴다.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

추가 검증(테스트 또는 재현 가능한 실행):
- ablation 실행이 graph off/on 두 조건 지표를 산출하고 리포트에 함께 남김(무거운 의존성 없이 소형 fixture로 검증 — 임베딩/LLM 직접 로드 금지, 필요한 협력자는 스텁)
- off 조건 지표가 그래프 도입 전 기준선과 일치(회귀)

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크: RETRIEVAL_EXPERIMENT_SPEC 규약 준수, 무거운 의존성 미트리거.
3. `phases/issue-0020/index.json`의 step 3 업데이트:
   - 성공 → `"completed"` + `summary`(추가한 ablation 조건, 지표, 리포트 위치)
   - 3회 실패 → `"error"` + `error_message`
   - 개입 필요 → `"blocked"` + `blocked_reason` 후 중단

## 금지사항

- 테스트에서 임베딩/LLM/ChromaDB를 직접 로드하지 마라. 이유: CLAUDE.md — 무거운 의존성은 스텁/소형 fixture로 격리.
- 그래프 이득이 없거나 음(-)인 결과를 감추거나 지표를 유리하게 왜곡하지 마라. 이유: ablation의 목적은 정직한 전후 비교(재현율 우선 원칙).
- orchestrator/graph_expand/automation 로직을 바꾸지 마라. 이유: 이 step은 평가만. 로직 변경은 앞 step에서 끝났다.
- 기존 테스트를 깨뜨리지 마라.
