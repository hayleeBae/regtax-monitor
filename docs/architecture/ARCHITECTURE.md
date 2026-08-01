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
│   └── const_inventory.py #  법령 수치 리터럴 인벤토리 (값 매칭)
├── domain/mappings/    # 매핑 검증 결정 순수 도메인 (Issue #0015~#0016)
│   ├── decisions.py     #   MappingDecisionType/reason enum, MappingDecisionRecord, resolve_state(), check_stale()
│   └── reranking.py     #   (#0016) DecisionContext, classify_reuse, rerank_delta, RERANK_VERSION — 문맥 게이팅 검색 재정렬
├── mappings/            # 매핑 결정 영속화
│   ├── repository.py    #   mapping_decision append+list only (수정 미제공, audit 패턴)
│   └── reranking_lookup.py #  (#0016) MappingDecision⨝Mapping → location별 DecisionContext 빌드 (DB 접근)
└── db/
    ├── database.py      #   SQLAlchemy 엔진/세션 (SQLite regtax.db) + init_db()/_migrate() (legacy verified backfill)
    └── models.py        #   LawChange / Mapping / Proposal / ExecutionRun / AuditEvent / MappingDecision 등

config.py                # pydantic-settings Settings (.env) — 모든 설정의 단일 진입점
domains.json             # 수집 도메인 레지스트리 (tax/hr)
run.py                   # uvicorn 런처 (:8000)
static/index.html        # 대시보드 UI (단일 파일, 바닐라 JS)
mock_repo/               # 집 개발용 가짜 eHR (Java/SQL/XML + 골든 테스트)
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

## 데이터 흐름
```
법제처 API → collect(LawChange 저장, domain/tier 태깅)
  → analyze(LLM 요약 + 해설서 RAG 컨텍스트)
  → map(RAG + 사전 정확매칭 + 상수 값매칭 → orchestrator merge·rank
         → (#0016) 검증 이력 rerank: 문맥 게이팅 boost/penalty 재정렬 → Mapping, 담당자 verify로 정확도 축적)
  → apply(LLM 앵커 편집 → unified diff → 골든 테스트 자동 검증 → Proposal)
  → approve/reject(사람 승인 게이트 → patch 파일 출력)
```

## 외부 연동
- **법제처 국가법령정보 공동활용 API** (open.law.go.kr) — 법령 목록/본문(law/eflaw), 행정규칙(admrul), 위임조문(thdCmp). OC 키는 target별 신청제. 행정규칙 본문의 `ID` 파라미터는 행정규칙일련번호(행정규칙ID는 `LID`).
- **로컬 추론 서버** (기본) — OpenAI 호환 `chat/completions` (Ollama/vLLM/llama.cpp/LM Studio, `LOCAL_LLM_BASE_URL`).
- **Anthropic API** (옵션) — `LLM_BACKEND=claude` 시에만. RAG로 좁힌 스니펫만 전송.
