# Step 1: replay-real-pipeline

> **ADR-012 보강 (2026-08-11 사람 개입):** 초안 생성이 `app/evaluation/` 계층 가드
> (#0004, `app.llm` import 금지)와 충돌해 최초 실행이 blocked 됐다. 해결: 초안 생성을
> `app/application/replay_draft.py::build_replay_draft_fn`으로 빼고 `real_pipeline`은
> `draft_fn`을 **주입받는다**. 아래 지시 중 "LLM을 직접 부른다"는 부분은 이 구조로
> 대체됐다 — `build_real_pipeline(*, draft_fn, index_root, top_k, indexer_factory,
> adapter_factory)`이며 `llm_factory`는 없다.

## 읽어야 할 파일

- `/CLAUDE.md` (CRITICAL — 코드 반출 금지, 승인 게이트, seam 규칙, 테스트에서 무거운 의존성 금지)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙 — **look-ahead 금지**, 인덱스 격리)
- `/docs/architecture/ADR.md` (**ADR-012** 전문. ADR-011·ADR-003도 함께)
- `/app/evaluation/replay/runner.py` (`ReplayContext`, `PipelineOutput`, `ReplayPipeline` — 구현할 계약)
- `/app/evaluation/replay/stub_pipeline.py` (**본보기** — 같은 계약의 결정적 구현)
- `/app/evaluation/replay/index_cache.py` (Step 0 — `replay_index_key`, `prepare_index`)
- `/app/retrieval/orchestrator.py`, `/app/retrieval/providers.py` (`RagProvider`/`DictionaryProvider`/`ConstantProvider` 생성자)
- `/app/main.py` 1009~1180행 (`apply` 엔드포인트 — 스니펫 구성과 `propose_and_build` 호출 방식의 본보기. **DB 조회 부분은 따라하지 않는다**)
- `/app/llm/common.py` (`propose_and_build` 시그니처), `/app/llm/__init__.py` (`get_llm_client`)

`apply` 엔드포인트를 읽되, 거기서 `Mapping` 행을 조회하는 부분은 **replay가 따라하면 안 되는 지점**이다. 이유는 ADR-012에 있다.

## 작업

`app/evaluation/replay/real_pipeline.py`를 신규 생성한다. `ReplayPipeline` 계약(`ReplayContext` → `PipelineOutput`)을 실제 인덱싱·검색·초안 생성으로 구현한다.

```python
def build_real_pipeline(
    *,
    index_root: Path | None = None,
    top_k: int = 5,
    llm_factory=None,        # 기본 get_llm_client — 테스트가 가짜를 주입한다
    indexer_factory=None,    # 기본 CodeIndexer
    adapter_factory=None,    # 기본 RealCodebaseAdapter
) -> ReplayPipeline: ...
```

### 절차

1. `replay_index_key(ctx.repo_id, ctx.base_commit, settings.embedding_model)` → `prepare_index(ctx.worktree, key)` (Step 0 재사용)
2. **worktree를 repo_root로 하는** 어댑터 구성
3. orchestrator 구성 후 `RetrievalQuery(text=..., article_id=ctx.case_id 관련 값, change_type=...)` 실행
4. 상위 `top_k` 후보의 파일을 `adapter.read_file`로 읽어 스니펫 구성
5. `propose_and_build(llm, law_diff=..., code_snippets=snippets, read_file=adapter.read_file)` → `diff_text`
6. `PipelineOutput(diff_text=..., retrieved_paths=<순위 순 경로 tuple>)` 반환

### 반드시 지킬 규칙

**look-ahead 차단 (ADR-012 — 이 step의 존재 이유)**

- `VerifiedMappingProvider`를 **구성하지 마라.**
- orchestrator에 `reranker`를 주입하지 말고 `RetrievalConfig(rerank_enabled=False)`로 명시적으로 끈다.
- DB 세션·`Mapping`·`MappingDecision`을 import 하지 마라.
- 이유: 과거 개정을 재현하면서 그 개정으로 만들어진 오늘의 검증 자산을 입력으로 쓰면 정답을 보고 정답을 맞히는 것이 되어 지표가 무의미해진다.

**provider의 repo_root는 worktree다**

- `DictionaryProvider(repo_root=str(ctx.worktree))`, `ConstantProvider(repo_root=str(ctx.worktree))`.
- `settings.repo_root`(오늘의 repo)를 넘기면 과거 시점 재현이 깨진다.

**law_diff 구성**

- `ctx.law`(`LawInput`)의 `law_name`·`article`·`before_text`·`after_text`로 만든다. `apply`의 구성 방식을 참고하되 DB 컬럼이 아니라 `ctx.law`에서 가져온다.

**실패 처리**

- LLM 미기동·타임아웃·응답 파싱 실패는 예외를 그대로 올린다 — runner가 `failure_kind="pipeline_failed"`로 케이스를 격리한다(#0018 계약). 여기서 삼키고 빈 diff를 반환하지 마라. 이유: 빈 diff는 "초안이 아무것도 못 찾음"이라는 **유효한 측정 결과**와 구분되지 않는다.

**로그·예외에 경로를 담지 마라**

- 회사 절대경로가 리포트·로그로 새지 않게 한다. 상대 경로나 케이스 id만 쓴다.

## 테스트

`tests/test_replay_real_pipeline.py` 신규. **임베딩·ChromaDB·LLM을 절대 트리거하지 마라** — 전부 가짜 팩토리로 주입한다.

- 파이프라인이 `PipelineOutput`을 돌려주고 `retrieved_paths`가 순위 순인지.
- **`VerifiedMappingProvider`가 구성되지 않는지** — orchestrator에 넘어간 provider 목록을 캡처해 확인.
- **`rerank_enabled=False`로 넘어가는지** — `RetrievalConfig` 캡처.
- provider들의 `repo_root`가 `ctx.worktree`인지(가짜 팩토리로 인자 캡처).
- 인덱스 캐시 적중 시 재인덱싱하지 않는지.
- LLM이 예외를 던지면 그대로 전파되는지(삼키지 않는지).
- 소스에 DB 관련 import가 없는지(`grep`으로도 확인 가능하나 테스트로 고정하면 더 좋다).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
grep -n "VerifiedMappingProvider\|MappingDecision\|from app.db\|reranking" app/evaluation/replay/real_pipeline.py
```

## 검증 절차

1. 위 AC를 실행한다. 두 번째 커맨드 출력이 **비어 있어야** 한다(주석 언급도 최소화).
2. 체크리스트:
   - look-ahead 차단 3종(verified provider 미구성 / rerank off / DB 미import)이 지켜졌는가?
   - provider repo_root가 worktree인가?
   - 테스트가 임베딩·LLM을 띄우지 않는가?
   - `runner.py`를 수정하지 않았는가?
3. `phases/issue-0022/index.json`의 step 1 갱신.

## 금지사항

- `VerifiedMappingProvider`·rerank·결정 이력을 쓰지 마라. 이유: look-ahead 유출로 지표가 부풀려지고 `#0019·#0020` 판단이 틀어진다(ADR-012).
- DB 세션·`Mapping` 행을 조회하지 마라. 이유: replay의 입력은 "법령 변경 + 그 시점 코드"뿐이다. 저장된 매핑은 입력이 아니다.
- `settings.repo_root`를 provider·adapter에 넘기지 마라. 이유: 과거 시점이 아니라 오늘 코드를 보게 되어 replay가 무의미해진다.
- LLM 실패를 삼키고 빈 diff를 반환하지 마라. 이유: "실패"와 "찾은 게 없음"이 구분되지 않아 지표가 거짓이 된다.
- `runner.py`를 수정하지 마라. 이유: seam 계약은 이미 확정됐고, 파이프라인이 계약에 맞춰야 한다.
- 운영 `chroma_data`를 쓰지 마라. 이유: Step 0의 캐시 경로만 사용한다.
- 테스트에서 실제 임베딩·LLM을 호출하지 마라. 이유: CLAUDE.md 규칙.
- 기존 테스트를 깨뜨리지 마라.
