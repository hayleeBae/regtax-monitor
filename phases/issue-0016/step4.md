# Step 4: rerank-ablation

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 테스트에서 무거운 의존성(임베딩 로드·인덱싱·LLM)을 트리거하지 않는다)
- `/docs/architecture/ADR.md` (**ADR-009** — "ablation으로 전후 성능을 수치 입증")
- `/docs/specifications/RETRIEVAL_EXPERIMENT_SPEC.md` (**§14 리포트 항목, §15 verified reranking, §18 수용 기준**)
- `/docs/specifications/EVALUATION_SPEC.md` (데이터셋 스키마)
- `/docs/roadmap/IMPLEMENTATION_ROADMAP.md` Issue #0016 절 (수용 기준: "ablation report로 전후 비교")
- `/app/evaluation/retrieval_benchmark.py` (수정 대상 — `default_experiments`, `run_orchestrator_cases`, `main`)
- `/app/domain/mappings/reranking.py` (Step 0), `/app/mappings/reranking_lookup.py` (Step 3)
- `/app/retrieval/orchestrator.py` (Step 2 — `rerank_enabled`, reranker 주입)
- `/evaluation/datasets/core.yaml` (기존 평가 케이스 — `law.article`, `expected.change_type`, `expected.retrieval.relevant_files` 구조)
- `/tests/test_retrieval_benchmark.py` (**실험 목록을 정확일치로 단언하는 테스트가 있다 — 함께 갱신해야 한다**)

## 작업

rerank on/off를 같은 조건에서 비교하는 ablation을 만든다. 이 step이 없으면 #0016의 수용 기준 "ablation report로 전후 비교"가 미충족으로 남는다.

### 1) 실험 변형 추가 (`app/evaluation/retrieval_benchmark.py`)

`RetrievalExperimentConfig`에 `rerank_enabled: bool = False`를 추가하고, `default_experiments()` 끝에 두 변형을 추가한다:

```python
RetrievalExperimentConfig("verified_rerank_off", (모든 소스), rerank_enabled=False),
RetrievalExperimentConfig("verified_rerank_on",  (모든 소스), rerank_enabled=True),
```

- 두 변형은 **provider 조합·top_k·정렬 조건이 완전히 동일**해야 한다. 이유: 차이의 원인이 rerank 하나로 특정되지 않으면 ablation이 아니다.
- 기존 5개 실험(`rag_only`~`verified_hybrid`)의 id·순서·소스 조합은 **바꾸지 마라.** 이유: 기존 벤치마크 결과와의 비교 가능성이 깨진다.
- `run_orchestrator_cases()`가 `RetrievalConfig(..., rerank_enabled=experiment.rerank_enabled)`를 넘기고, 케이스의 조문·변경유형을 `RetrievalQuery(article_id=..., change_type=...)`로 전달하도록 한다. 케이스에서 `article_id`는 `EvaluationCase`의 law 정보로 구성하고, `change_type`은 `case.expected.change_type`을 쓴다(V2 어휘라 Step 0 게이팅이 정상 동작한다).
- `environment.json`에 각 실험의 `rerank_enabled`와 `RERANK_VERSION`이 남도록 한다(스펙 §14 재현성).

### 2) 결정 이력 fixture

ablation이 의미를 가지려면 검증·거절 이력이 있어야 한다. 현재 벤치마크 CLI는 `VerifiedMappingProvider(lambda _query: ())`로 **검증 데이터를 전혀 넣지 않아** rerank on/off 차이가 0으로 나온다.

- `evaluation/fixtures/decisions/core_decisions.yaml`(경로·파일명은 재량) 신규 — `evaluation/datasets/core.yaml`의 케이스에 대응하는 mock 결정 이력을 기술한다. 최소한 아래 세 종류를 포함하라:
  - **정답 파일에 대한 exact verified 이력** (boost가 정답을 끌어올리는지)
  - **오답 파일에 대한 exact rejected 이력** (penalty가 오답을 끌어내리는지)
  - **다른 조문(article_id)에 대한 verified 이력** (문맥 게이팅이 무관 boost를 막는지 — §11)
  - 가능하면 stale 이력 1건 (stale이 강제 정답 처리되지 않는지 — 수용 기준)
- fixture를 `DecisionContext`로 바꾸는 **파일 기반 `CandidateReranker` 구현**을 만든다(`app/evaluation/` 하위, DB 불필요). 이유: 벤치마크는 DB 없이 실행되며, `SqlAlchemyDecisionContextLookup`을 그대로 쓰면 벤치마크가 SQLite에 묶인다.
- fixture 로더는 `app/domain/mappings/reranking.py`의 `DecisionContext`를 그대로 만들어야 한다 — **평행 구조를 새로 정의하지 마라.**

### 3) CLI 배선 (`main()`)

- `--decisions <path>` 옵션을 추가한다(선택). 주면 파일 기반 reranker를 orchestrator에 주입하고, 없으면 `reranker=None`.
- 옵션이 없을 때 기존 동작과 결과가 동일해야 한다.

### 4) 리포트

`comparison.md`/`comparison.json`은 기존 형식을 그대로 쓰되, `verified_rerank_off` vs `verified_rerank_on` 행이 나란히 나오면 충분하다. 별도 리포트 포맷을 새로 만들지 마라.

**리포트 결과를 해석해 `docs/` 아래에 요약 문서를 남길 필요는 없다** — 이 step의 산출물은 리포트를 생성할 수 있는 코드와 fixture이며, 실제 수치 해석은 사람 판단 영역이다. 다만 step summary에 로컬 실행 시 관측된 on/off 지표 차이를 반드시 기록하라.

## 테스트

`tests/test_retrieval_benchmark.py`를 갱신·추가한다:

- 기존 `test_default_experiments_have_fixed_provider_combinations`의 실험 id 목록 단언을 새 목록으로 갱신한다.
- `verified_rerank_off`와 `verified_rerank_on`의 `enabled_sources`·`top_k`가 동일하고 `rerank_enabled`만 다른지 단언한다(공정 비교 고정).
- fixture 로더 테스트: yaml → `DecisionContext` 변환, 없는 파일·빈 파일 처리.
- 파일 기반 reranker가 `contexts_for`에서 `candidate.dedup_key` 키로 반환하는지.
- **`hybrid_all`이나 `verified_rerank_on`이 항상 더 높아야 한다는 단언은 절대 넣지 마라** — 스펙 §14가 명시적으로 금지한다. 저하도 그대로 보고되어야 한다.

테스트에서 임베딩 모델 로드·ChromaDB 인덱싱·LLM 호출을 트리거하지 마라(CLAUDE.md). 벤치마크 runner는 가짜 케이스 실행 함수로 검증한다.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

로컬 ablation 실행(전용 persist-dir 사용, 실제 인덱싱이 필요하면 `--build-index`):

```bash
source .venv/bin/activate && python -m app.evaluation.retrieval_benchmark --dataset evaluation/datasets/core.yaml --result-dir evaluation/results --run-name rerank-ablation-0016 --persist-dir ./evaluation/chroma_mock_tax --decisions evaluation/fixtures/decisions/core_decisions.yaml
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 두 번째 커맨드는 `comparison.md`에 `verified_rerank_off`/`verified_rerank_on` 두 행이 생성되면 성공이다.
2. 아키텍처 체크리스트를 확인한다:
   - 두 변형의 조건이 rerank 외에 동일한가?
   - fixture 로더가 `DecisionContext`를 재사용하는가(평행 정의 없음)?
   - 벤치마크 경로가 SQLAlchemy 세션을 요구하지 않는가?
   - 기본 chroma 디렉토리(`./chroma_data`)를 오염시키지 않았는가? (전용 `--persist-dir` 사용)
   - "성능 향상" 단언 테스트가 없는가(스펙 §14 금지)?
3. 결과에 따라 `phases/issue-0016/index.json`의 step 4를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "추가한 실험 변형·fixture 경로·CLI 옵션 + 관측된 on/off 지표 차이(R@1/MRR)"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- rerank on이 항상 우수하다고 단언하는 테스트를 만들지 마라. 이유: RETRIEVAL_EXPERIMENT_SPEC §14가 명시적으로 금지하며, 성능 저하를 숨기면 측정 체계 자체가 무의미해진다.
- fixture 수치를 정답 파일에만 유리하게 짜서 차이를 인위적으로 부풀리지 마라. 이유: 무관 조문 boost 차단·stale 미강제도 함께 검증해야 수용 기준을 채운다.
- 기존 실험 id(`rag_only`, `rag_dict`, `rag_const`, `hybrid_all`, `verified_hybrid`)의 이름·소스 조합을 바꾸지 마라. 이유: 과거 벤치마크 결과와 비교 불가가 된다.
- 벤치마크 경로에 DB 세션·SQLAlchemy 의존을 넣지 마라. 이유: ablation은 DB 없이 재현 가능해야 한다.
- 테스트에서 임베딩 모델 로드·ChromaDB 인덱싱·LLM 호출을 트리거하지 마라. 이유: CLAUDE.md 규칙이며 CI 시간이 수십 분으로 폭발한다.
- 기본 `chroma_data/`를 벤치마크로 덮어쓰지 마라. 이유: 개발 서버 인덱스가 오염되면 재인덱싱에 수십 분이 든다.
- 기존 테스트를 깨뜨리지 마라.
