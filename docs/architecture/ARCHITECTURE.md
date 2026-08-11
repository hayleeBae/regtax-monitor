# 아키텍처

## 디렉토리 구조 (현재 구조 — 2026-07 기준)
```
app/
├── main.py              # FastAPI 앱 + 전체 API 라우트 (단일 파일, static/index.html 서빙)
├── golden.py            # 골든 테스트 — 초안 diff를 스크래치 사본에 적용·검증
├── collector/           # 법령 수집
│   ├── law_api.py       #   법제처 API 클라이언트 (법령 3계층 + 행정규칙 + PDF 첨부 추출)
│   └── registry.py      #   도메인 레지스트리 로더 (domains.json)
├── llm/                 # [seam 1] LLM 백엔드 교체 지점
│   ├── base.py          #   LlmClient 인터페이스
│   ├── __init__.py      #   get_llm_client() 팩토리 (LLM_BACKEND 설정으로 선택)
│   ├── local_client.py  #   LocalClient — OpenAI 호환 로컬 추론 서버 (기본)
│   ├── claude_client.py #   Anthropic API (LLM_BACKEND=claude, 지연 import)
│   └── common.py        #   양 백엔드 공유: 프롬프트, JSON 추출, 앵커 편집→unified diff
├── codebase/            # [seam 2] 대상 코드베이스 교체 지점
│   ├── base.py          #   CodebaseAdapter 인터페이스
│   ├── mock_adapter.py  #   mock_repo/ (REPO_ROOT 미설정 시)
│   └── real_adapter.py  #   실제 eHR repo (REPO_ROOT 설정 시)
├── embedding/           # 로컬 RAG
│   ├── indexer.py       #   코드 청킹(Java/SQL/XML) + bge-m3 → chroma_data/
│   ├── docs_index.py    #   참고 문서(해설서) 별도 컬렉션(tax_docs) 인덱싱
│   ├── term_dict.py     #   암호 컬럼코드↔한글명 사전 (주석에서 자동 수확)
│   ├── const_inventory.py #  법령 수치 리터럴 인벤토리 (값 매칭)
│   └── symbol_index.py  #   (#0019) Java/MyBatis/SQL 심볼·관계 그래프 추출 (adapter 경유, symbol_index_cache.json gitignore) — #0020 CodeGraphProvider가 소비
├── domain/mappings/    # 매핑 검증 결정 순수 도메인 (Issue #0015~#0016)
│   ├── decisions.py     #   MappingDecisionType/reason enum, MappingDecisionRecord, resolve_state(), check_stale()
│   └── reranking.py     #   (#0016) DecisionContext, classify_reuse, rerank_delta, RERANK_VERSION — 문맥 게이팅 검색 재정렬
├── mappings/            # 매핑 결정 영속화
│   ├── repository.py    #   mapping_decision append+list only (수정 미제공, audit 패턴)
│   └── reranking_lookup.py #  (#0016) MappingDecision⨝Mapping → location별 DecisionContext 빌드 (DB 접근)
├── evaluation/          # 평가·측정 (데이터셋, 지표, ablation)
│   ├── case.py          #   EvaluationCase 스키마 + loader.py (검색·분류·patch 평가용)
│   ├── retrieval_benchmark.py # provider 조합·rerank on/off ablation
│   ├── decision_fixtures.py   # (#0016) ablation용 파일 기반 결정 이력
│   └── replay/          #   과거 개정 replay — 선언 계층(#0017)과 실행 계층(#0018) 분리
│       ├── fixture.py   #     [선언] ReplayFixture/ReplayScope/PrivacyMode (순수 계약)
│       ├── loader.py    #     [선언] YAML 로더 — path XOR path_env, revision 문자 제한, golden_command allowlist
│       ├── git_cmd.py   #     [실행] (#0018) git allowlist wrapper — shell=False + timeout, 서브커맨드 검사
│       ├── worktree.py  #     [실행] (#0018) repo 경로 해석(path_env) + dirty/commit 사전검증 + 임시 detached worktree 컨텍스트 (finally cleanup)
│       ├── answer_diff.py #   [실행] (#0018) answer commit 변경 추출(commit 대 commit, worktree 불필요) → scope 필터로 정답 집합 + fixture 기대교체 대조
│       ├── golden_exec.py #   [실행] (#0018) fixture golden_command 실행 — shell=False + cwd=worktree 고정 + 인자 검증(절대경로·`..`·대상 재지정 옵션 거부, allowlist 는 loader 재사용), 타임아웃은 예외 대신 GoldenResult(status=error)
│       ├── runner.py    #     [실행] (#0018) 스펙 §4 조립 — 사전검증 → 임시 worktree → ReplayPipeline seam 호출(임베딩·추론 백엔드 미import) → 스크래치 apply/골든 → answer 비교 → finally cleanup. 케이스 실패는 failure_kind 로 격리하고 계속 진행(§9), privacy 는 fixture 중 가장 엄격한 모드. CLI 진입점(--fixtures/--output-dir/--privacy-mode/--stub)
│       ├── stub_pipeline.py # [실행] (#0018) 결정적 stub 파이프라인(perfect/partial/empty) — worktree 실제 내용을 읽어 적용 가능한 unified diff 생성, 로컬·테스트 검증 전용
│       ├── real_pipeline.py # [실행] (#0022) 실제 파이프라인 — worktree를 RealCodebaseAdapter로 인덱싱(evaluation/replay_index/<key> 캐시, 운영 chroma_data 무접근) → RAG·사전·상수 검색 → propose_and_build 초안. verified 매핑·rerank 제외(look-ahead 유출 차단), DB 미경유. CLI에서만 지연 import
│       └── report.py    #     [실행] (#0018) 스펙 §7 지표 산출(순수 계산) + privacy_mode 게이팅 저장 (allowed_artifacts 소비 지점, replay_report.json/md + environment.json)
└── db/
    ├── database.py      #   SQLAlchemy 엔진/세션 (SQLite regtax.db) + init_db()/_migrate() (legacy verified backfill)
    └── models.py        #   LawChange / Mapping / Proposal / ExecutionRun / AuditEvent / MappingDecision 등

config.py                # pydantic-settings Settings (.env) — 모든 설정의 단일 진입점
domains.json             # 수집 도메인 레지스트리 (tax/hr)
run.py                   # uvicorn 런처 (:8000)
static/index.html        # 대시보드 UI (단일 파일, 바닐라 JS)
mock_repo/               # 집 개발용 가짜 eHR (Java/SQL/XML + 골든 테스트)
evaluation/
├── datasets/            # 평가 데이터셋 (core.yaml + company_private 템플릿)
├── fixtures/
│   ├── repositories/    #   mock 코드 fixture
│   ├── decisions/       #   (#0016) rerank ablation용 결정 이력
│   └── replay_sources/  #   (#0017) replay mock repo의 base/answer 파일 트리 — 커밋 대상
├── private/             # 회사 실데이터 (데이터셋 + replay fixture) — gitignore
└── results/             # 벤치마크 산출물 — gitignore
tests/                   # 앱 테스트 (pytest)
scripts/                 # 하네스 (verify.sh, execute.py, trace.py + 자체 테스트)
```

## 레이어 규칙
- `main.py`(라우트) → `collector`/`llm`/`codebase`/`embedding`/`db` 를 호출한다. 역방향 금지.
- LLM 호출은 반드시 `get_llm_client()`가 반환한 `LlmClient`를 통해서만 — 라우트나 다른 모듈에서 추론 서버/Anthropic에 직접 HTTP 호출 금지.
- 대상 코드베이스 파일 접근은 반드시 `CodebaseAdapter`를 통해서만.
- 백엔드 공통 로직(프롬프트·파싱·diff 변환)은 `llm/common.py`에만 — 클라이언트별 복제 금지.
- 설정 접근은 `config.settings` 단일 인스턴스로만.
- `app/domain/` 은 순수 계약 — FastAPI, SQLAlchemy, 외부 LLM SDK를 import하지 않는다. 영속화는 `app/audit/`·`app/mappings/` repository가 담당한다.
- 검증 이력 등 append-only 저장소는 수정/삭제 API를 제공하지 않는다(repository에서 강제).
- `app/evaluation/`은 DB·FastAPI 없이 실행 가능해야 한다 — 평가·ablation은 서버 기동과 무관하게 재현 가능해야 하기 때문이다.
- `app/evaluation/replay/`는 **선언 계층과 실행 계층을 파일 단위로 가른다**. `fixture.py`·`loader.py`(선언)는 git 실행·파일 쓰기·환경변수 읽기를 하지 않는다 — 실제 repo 절대경로는 YAML이 아니라 환경변수로만 들어오고, 그 해석은 실행 계층 몫이다. `git_cmd.py`·`runner.py`·`report.py`(실행)만 git과 파일시스템을 다룬다.
- replay의 git 호출은 전부 `git_cmd.py` wrapper를 통과한다 — 서브커맨드 allowlist·`shell=False`·timeout이 wrapper 안에서 강제된다. 다른 모듈에서 git을 직접 실행하지 않는다.
- replay 산출물 저장은 `report.py` 한 곳에서만 하고 `allowed_artifacts(privacy_mode)`로 게이팅한다 — 저장 지점이 흩어지면 privacy 모드가 무의미해진다.
- replay 파이프라인은 **과거 시점에 존재하지 않던 정보를 입력으로 쓰지 않는다**(look-ahead 금지). 검증 매핑·결정 이력·rerank는 그 개정을 처리하며 만들어진 사후 자산이므로 replay 경로에서 제외한다 — 쓰면 정답을 보고 정답을 맞히는 것이 되어 지표가 무의미해진다(ADR-012).
- replay 인덱스는 `evaluation/replay_index/<key>/`에만 만들고 운영 `chroma_data/`를 읽거나 쓰지 않는다. 이 디렉토리에는 대상 코드의 임베딩이 담기므로 반출 금지·gitignore 대상이다.

## 데이터 흐름
```
법제처 API → collect(LawChange 저장, domain/tier 태깅)
  → analyze(LLM 요약 + 해설서 RAG 컨텍스트)
  → map(RAG + 사전 정확매칭 + 상수 값매칭 → orchestrator merge
         → (#0016) 검증 이력 rerank: 문맥 게이팅 boost/penalty (절단 전)
         → 정렬 → final_top_k 절단 → rank 부여 → Mapping, 담당자 verify로 정확도 축적)
  → apply(LLM 앵커 편집 → unified diff → 골든 테스트 자동 검증 → Proposal)
  → approve/reject(사람 승인 게이트 → patch 파일 출력)
```

## 외부 연동
- **법제처 국가법령정보 공동활용 API** (open.law.go.kr) — 법령 목록/본문(law/eflaw), 행정규칙(admrul), 위임조문(thdCmp). OC 키는 target별 신청제. 행정규칙 본문의 `ID` 파라미터는 행정규칙일련번호(행정규칙ID는 `LID`).
- **로컬 추론 서버** (기본) — OpenAI 호환 `chat/completions` (Ollama/vLLM/llama.cpp/LM Studio, `LOCAL_LLM_BASE_URL`).
- **Anthropic API** (옵션) — `LLM_BACKEND=claude` 시에만. RAG로 좁힌 스니펫만 전송.
