# regtax-monitor — 법령 변경 모니터링 & 코드 반영 시스템 (세법·인사)

연말정산은 매년 세법이 개정되고, 그때마다 eHR의 연말정산 로직(공제 한도·공제율·적용
요건)을 수정해야 한다. 근로기준법·최저임금 고시 등 인사시스템에 적용되는 법령 전반도
마찬가지다. 이 시스템은 그 사이클을 지원한다:

1. **수집·감지** — 법제처 API로 소관 법령의 변경을 자동 수집하여 담당자에게 알리고,
2. **분석·매핑** — 변경 조문을 요약하고 관련 코드 위치에 매핑하고,
3. **초안 생성** — 담당자가 "반영"을 누르면 git apply 가능한 patch 초안을 만든다.

> **왜 만들었나**: 연말정산 실무에서 가장 비싼 실패는 "잘못 고친 것"이 아니라
> **"개정을 놓친 것"**이다. 초안 품질과 별개로, 수집→요약→매핑 파이프라인만으로도
> 이 리스크를 줄인다. 실제로 2026년 세법 5종은 **법률 개정 0건, 시행령·시행규칙
> 개정 10건**이었다 — 법률만 지켜보는 방식으로는 올해 변경을 전부 놓쳤다.

---

## 핵심 설계 원칙과 근거

상세한 결정 기록은 [docs/ADR.md](docs/ADR.md) 참조. 요약:

### 1. 코드는 외부로 나가지 않는다 (완전 로컬, ADR-001)

임베딩·검색·생성 전부 로컬에서 수행한다. 초기 설계는 "로컬 임베딩 + Claude API
생성" 하이브리드였지만, RAG로 좁힌 스니펫이라도 외부 전송에는 보안 검토가 필요했다.
생성까지 로컬 추론 서버로 옮기면 **검토 자체가 불필요**해진다 — 사내 이식의 최대
장벽을 설계로 제거한 것.

| 단계 | 실행 위치 | 코드 외부 반출 |
|---|---|---|
| 임베딩 (bge-m3) | 로컬 | ✗ |
| 벡터 저장·검색 (ChromaDB) | 로컬 `chroma_data/` | ✗ |
| patch 생성 — **local 백엔드 (기본)** | 로컬 추론 서버 | ✗ |
| patch 생성 — claude 백엔드 (옵션) | 외부 API | RAG로 좁힌 스니펫만 |

Claude 하이브리드 구현은 `app/llm/claude_client.py`로 보존되어 있어
`LLM_BACKEND=claude`로 언제든 되돌릴 수 있다 (비실시간 워크로드라 Anthropic
Batch API 50% 할인 적용 가능).

### 2. 사람 승인 게이트 — 자동 적용은 영구 제외 (ADR-002)

AI는 초안 생성까지만 하고, 승인 시에만 patch 파일이 출력된다. 로컬 소형 모델은
환각(제공되지 않은 파일의 편집을 지어냄)이 실제로 관측되었지만, diff 변환기가
걸러내고 승인 게이트가 최후 방어선이 되어 **잘못된 코드가 자동 적용되는 일은
구조적으로 없다**. 모델 성능에 기대지 않고 구조로 안전을 확보하는 선택.

### 3. 환경에 따라 바뀌는 지점은 두 개의 이음새(seam)뿐 (ADR-003)

- `app/llm/` — `LlmClient` (로컬 모델 ↔ Claude API, `get_llm_client()`가 선택)
- `app/codebase/` — `CodebaseAdapter` (mock repo ↔ 실제 eHR repo, `REPO_ROOT`로 선택)

집 개발(mock, API 키 없음)과 회사 운영(실 repo, SSL 프록시)의 차이가 **코드 분기
없이 `.env` 설정만으로** 흡수된다. 프롬프트·파싱·diff 변환은 `app/llm/common.py`에
공유되어 백엔드를 바꿔도 편집 형식과 승인 플로우는 변하지 않는다.

### 4. 확률적 매칭의 천장은 3중 부트스트랩 + 검증 자산으로 넘는다 (ADR-005)

임베딩 유사도만으로는 eHR 레거시의 암호 컬럼명(`a0121`, `n0200`)과 법령 용어를
연결할 수 없다. 그래서 매핑을 세 갈래로 부트스트랩한다:

| 방법 | 원리 | 커버하는 것 |
|---|---|---|
| RAG 퍼지 검색 | bge-m3 벡터 유사도 | 일반적인 의미 매칭 |
| 용어 사전 정확매칭 | SQL/VO 주석에서 `{코드: 한글명}` 자동 수확 | 암호 컬럼명 (`AS n0200 -- 자녀세액공제`) |
| 상수 값매칭 | 코드의 수치 리터럴 인벤토리 × 개정문 금액 파싱 | "15만원→25만원" 같은 수치 개정 |

셋 다 첫 부트스트랩용이고, **진짜 자산은 담당자 검증(verified) Mapping의 누적**이다.
연말정산 세법은 매년 같은 자리(소득세법 제50~59조 부근)가 바뀌므로, 한 시즌만
검증을 투자하면 이듬해부터 추측 없이 직행한다.

### 5. 골든 테스트 — 환각의 구조적 차단 (ADR-006)

초안 diff를 repo **스크래치 사본**에 적용하고 `GOLDEN_TEST_CMD`(국세청 모의계산
기대값 대조 등)를 실행한다. 실제 repo는 절대 건드리지 않는다. 개정이 계산 결과를
바꾸면 patch에 기대값 갱신까지 포함되어야 통과하고, 그 갱신 자체도 승인 게이트에서
검토된다. 실패한 초안도 승인은 가능하다(사람의 결정) — 다만 경고가 명시된다.

### 6. 수집 대상은 도메인 레지스트리로 관리 (ADR-007)

`domains.json`이 도메인(tax/hr)별 수집 법령·고시 검색어를 정의한다. 연말정산(세법
5종)을 넘어 노동·인사 법령 11종으로 확장했고, 수집 건마다 `domain`이 태깅되어
도메인별 담당자 라우팅의 기반이 된다. 법령은 3계층(법률/시행령/시행규칙)을
수집한다 — 실제 수치·요건은 시행령에만 있는 경우가 많고("대통령령으로 정하는 금액"),
최저임금처럼 **법령이 아니라 고시로 바뀌는 수치**는 행정규칙 수집으로 잡는다.

### 7. 기반 기술 선택 근거

- **ChromaDB**: SQLite처럼 파일 기반 임베디드 — 별도 서버 없이 pip 설치만으로 동작. 사내 이식 장벽 최소화 (ADR-004)
- **bge-m3**: 한국어 강점 + CPU/M1에서 GPU 없이 동작
- **OpenAI 호환 로컬 추론**: 특정 런타임 비종속 — Ollama/vLLM/llama.cpp/LM Studio를 `LOCAL_LLM_BASE_URL`만 바꿔 교체
- **청킹**: Java는 메서드, SQL은 문장, XML은 쿼리/resultMap 단위 — 매핑 결과가 "파일"이 아니라 "수정 지점"을 가리키도록

---

## 동작 방식

```
법제처 API → collect (LawChange 저장, domain/tier 태깅, 고시는 PDF 첨부 자동 추출)
  → analyze (LLM 요약·영향 분석 + 해설서 RAG 컨텍스트 주입)
  → map     (RAG + 사전 정확매칭 + 상수 값매칭 → Mapping, 담당자 verify로 자산화)
  → apply   (LLM 앵커 편집 → unified diff → 골든 테스트 자동 검증 → Proposal)
  → approve/reject (사람 승인 게이트 → patch 파일 출력)
```

- **인덱싱**: 서버 첫 기동 시 `chroma_data/`가 비어 있으면 자동 인덱싱된다 (CPU라
  수십 분). 코드/사전 변경을 검색에 반영하려면 `rm -rf chroma_data/` 후 재기동.
  생성 모델 교체는 재인덱싱 불필요 — `EMBEDDING_MODEL`을 바꿀 때만 재구축.
- **앵커 실패 피드백 루프**: 모델이 SEARCH 앵커를 원본과 다르게 복사하면, 가장
  비슷한 실제 원본 발췌(±25줄)를 보여주며 최대 2회 자동 재작성시킨다. 파일 전체를
  보내지 않고 로컬 소형 모델의 복사 실수를 보정한다.

### 실동작 검증 (2026-07, M1 · qwen3:8b · CPU 추론)

| 단계 | 소요 | 결과 |
|---|---|---|
| analyze (요약·영향 분석) | 약 1.5분 | JSON 파싱 정상, 요약 정확 |
| propose → unified diff | 약 4.5분 | `git apply` 가능한 diff 생성 |

가상 개정(자녀세액공제 15만원→25만원)과 노동법 사례(최저임금 시간급 10,030→10,320원)
모두 정확한 코드 위치로 매칭됐다. 비실시간 워크플로(담당자 승인 대기)라 수 분의
생성 시간은 실용 범위.

---

## 프로젝트 구조

```
regtax-monitor/
├── config.py              모든 설정의 단일 진입점 (.env 로드, pydantic-settings)
├── domains.json           수집 도메인 레지스트리 (tax/hr — 법령명·고시 검색어)
├── run.py                 uvicorn 런처 (:8000)
├── static/index.html      대시보드 UI (단일 파일, 바닐라 JS)
├── mock_repo/             집 개발용 mock eHR (Java/SQL/XML + 골든 테스트)
├── data/uploads/          해설서 업로드 폴더 (gitignore)
├── app/
│   ├── main.py            FastAPI 엔트리 + 전체 API 라우트
│   ├── golden.py          골든 테스트 (스크래치 사본에 diff 적용·검증)
│   ├── db/                SQLAlchemy 모델 (LawChange/Mapping/PatchProposal …) + SQLite
│   ├── llm/               [이음새 1] LlmClient — local_client(기본)/claude_client + common(공유 프롬프트·파싱)
│   ├── codebase/          [이음새 2] CodebaseAdapter — mock_adapter/real_adapter
│   ├── embedding/         indexer(코드 RAG) · docs_index(해설서 RAG) · term_dict(용어 사전) · const_inventory(상수 값매칭)
│   └── collector/         law_api(법제처 API — 법령 3계층·행정규칙·PDF 첨부) + registry(도메인 로더)
├── tests/                 앱 테스트 (pytest)
├── scripts/               개발 하네스 (verify.sh, execute.py, trace.py)
└── docs/                  PRD · ARCHITECTURE · ADR · UI_GUIDE · OBSERVABILITY
```

레이어 규칙 등 상세: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## 시작하기

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # 개발 도구까지: -r requirements-dev.txt 추가

cp .env.example .env                     # LAW_API_OC 입력 (없으면 mock 법령 데이터로 동작)

# 로컬 추론 서버 (기본 백엔드) — 컨텍스트 창을 반드시 함께 지정할 것 (아래 주의사항)
brew install ollama
ollama pull qwen3:8b
OLLAMA_CONTEXT_LENGTH=16384 ollama serve

python run.py                            # http://127.0.0.1:8000 (첫 기동 시 자동 인덱싱)
```

### ⚠ 컨텍스트 창 주의사항 (실패 사례에서 얻은 것)

- Ollama의 OpenAI 호환 레이어(`/v1`)는 요청의 `options.num_ctx`를 **무시한다**
  (0.31.1 확인). 컨텍스트 창은 서버 기동 시 `OLLAMA_CONTEXT_LENGTH`로 설정해야 한다.
- 기본 4096으로 기동하면 RAG 프롬프트가 잘려(context shift) **오해석·할루시네이션**의
  원인이 된다. serve 로그에 "context shift"가 보이면 프롬프트 유실 신호다.
- 앱은 호출마다 프롬프트 근사 토큰을 로그로 출력하고, `LOCAL_LLM_NUM_CTX`(기본
  16384)를 넘으면 경고한다 — 서버의 `OLLAMA_CONTEXT_LENGTH`와 같은 값으로 맞출 것.
- 근거 기록: `.harness/failures/F-20260712-0001`(컨텍스트 유실),
  `F-20260712-0002`(출력 토큰 상한 절단).

---

## 활용 방법

### 웹 UI 워크플로 (대시보드: http://127.0.0.1:8000)

1. **수집** 버튼 → 도메인별 법령·고시 변경이 목록에 쌓인다 (도메인 필터 지원)
2. 변경 건 선택 → **분석** → LLM 요약·영향 분석 확인
3. **매핑** → 관련 코드 위치 목록 (rag_hits / dict_matches / const_matches 출처 표기)
   → 맞는 매핑은 **검증** 체크 (다음 시즌부터 이 매핑으로 직행)
4. **반영** → patch 초안 생성 + 골든 테스트 자동 실행
5. 초안 검토 → **승인**(patch 파일 출력) 또는 **거절**(재작업)
6. **📚 해설서 관리** → 국세청 『개정세법 해설』 PDF 업로드 (즉시 인덱싱되어
   analyze 컨텍스트로 자동 주입)

### API로 직접 (자동화·스크립팅)

```
POST  /collect                     도메인별 수집 (법령 3계층 + 행정규칙)
GET   /changes?domain=hr           변경 목록 (도메인 필터)
POST  /changes/{id}/analyze        LLM 분석·요약
POST  /changes/{id}/map            매핑 부트스트랩 (RAG+사전+상수)
PATCH /mappings/{id}/verify        담당자 매핑 검증 ★ 자산화의 핵심
POST  /changes/{id}/apply          patch 초안 생성 + 골든 테스트
POST  /proposals/{id}/golden       골든 테스트 재실행
POST  /proposals/{id}/approve      승인 → patch 파일 출력
POST  /proposals/{id}/reject       거절
GET/POST/DELETE /refdocs…          해설서 목록/업로드/삭제
```

매핑 검증을 건너뛰어도 플로우는 진행된다(RAG confidence 기반 자동 선택). 주기 수집은
`SCHEDULER_ENABLED=true`(기본 24시간 간격).

### 어디까지 기대할 수 있나 — 개정 유형별 효용

| 개정 유형 | 예 | 효용 |
|---|---|---|
| **수치 개정** | 공제 한도·세율표 변경 (15만원→25만원) | ◎ git apply 가능한 초안까지 자동. 매년 개정의 상당수 |
| **요건 개정** | 적용 대상·소득 기준 변경 | △ 초안 품질은 낮으나 "어느 파일·어느 메서드" 매핑 가치 유지 |
| **구조 개정** | 공제 항목 신설·계산 체계 변경 | ✗ 자동 초안 무리. 감지 + 영향 범위 알림까지가 가치 |

**"탐지·알림 + 수치 개정의 초안 자동화"가 실질 효용 범위**이고, 복잡한 개정에는
보조 도구다. 이 격차는 모델 크기가 아니라 구조(매핑 자산 축적, 컨텍스트 확장,
장기적으로 파라미터 테이블화)로 줄인다.

### 골든 테스트 설정

`.env`의 `GOLDEN_TEST_CMD`에 스크래치 repo 루트에서 실행할 검증 명령을 지정한다
(exit 0=통과, 비우면 검증 생략).

- mock 개발: `python3 tests/golden_income_tax.py` (세율표 XML→산출세액→기대값 대조)
- 회사: 사내 빌드/테스트 명령 (예: `mvn -q test -Dtest=YearEndGoldenTest`)

### 모델 업그레이드 / 교체

```bash
ollama pull qwen3:14b        # .env에서 LOCAL_LLM_MODEL=qwen3:14b 변경 후 재시작 — 그게 전부
```

- 재인덱싱 불필요 (생성 모델과 임베딩은 무관)
- 속도가 아쉬우면 분석 단계만 `LOCAL_LLM_MODEL_CHEAP=qwen3:4b`로 분리
- 사내 vLLM 등은 `LOCAL_LLM_BASE_URL`만 해당 서버로 지정
- Claude API 복귀: `LLM_BACKEND=claude` + `ANTHROPIC_API_KEY`

---

## 환경 이식

git에는 **코드만** 들어 있다. `.env`, `.venv/`, `chroma_data/`, Ollama 모델은 모두
gitignore 대상이라 머신마다 준비한다:

| 항목 | git으로 옮겨지나 | 회사에서 할 일 |
|---|---|---|
| 코드 전체 | ✓ | pull만 하면 됨 |
| `.env` | ✗ | `.env.example` 복사 후 `LAW_API_OC`, `REPO_ROOT` 등 입력 |
| `.venv/` | ✗ | venv 재생성 + pip install |
| Ollama + 모델 | ✗ | 사내망이 ollama.com을 막으면 `~/.ollama/models/` 폴더를 통째로 복사해도 동작. 사내 vLLM이 있으면 `LOCAL_LLM_BASE_URL`만 지정 |
| `chroma_data/` | ✗ | `REPO_ROOT` 지정 후 첫 기동 시 자동 인덱싱 (수십 분) |

- SSL 프록시 환경: `HF_HUB_DISABLE_SSL=true` + pip `--trusted-host`. 법제처 API는
  truststore가 OS 인증서 저장소를 신뢰해 프록시 CA 문제 없이 동작. **코드에
  `verify=False` 하드코딩 금지.**
- 법제처 OC 키는 **target별 신청제** — 행정규칙 목록/본문은 별도 신청 필요
  (미신청 시 `/collect` 응답의 `admrul_warning`으로 안내).
- 이식 후 첫 시즌에 담당자 매핑 검증(`PATCH /mappings/{id}/verify`)을 반드시 실행
  — 이것이 이듬해부터의 정확도를 결정한다.

---

## 개발

```bash
bash scripts/verify.sh quick     # lint (ruff) — 수 초
bash scripts/verify.sh full      # quick + pytest (tests/ + scripts/)
bash scripts/verify.sh security  # 시크릿 스캔 + pip-audit
```

- 개발 규칙·CRITICAL 제약: [CLAUDE.md](CLAUDE.md) / 설계 결정: [docs/ADR.md](docs/ADR.md)
- 실패 이력은 `.harness/failures/`에 구조화 기록된다 (원인 검증 후 resolved 처리,
  삭제 금지 — [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md))
- 개발 보조로 [korean-law-mcp](https://github.com/chrisryugj/korean-law-mcp)(MIT)를
  Claude Code에 로컬 등록해 법령·고시 조회에 활용. 프로덕션 수집은 법제처 API 직접
  호출(외부 서버 미경유)이며, admrul/eflaw/thdCmp 파싱은 이 저장소의 검증된 호출
  방식을 참조 구현으로 포팅한 것.

## 로드맵

레버리지 큰 순서. 즉시 구현 가능했던 것은 완료됐고, 남은 것은 운영·장기 과제:

| # | 항목 | 상태 |
|---|---|---|
| 1 | 조문→코드 매핑 자산 축적 | 🔄 운영 과제 — 매 시즌 검증 반복 (구조는 완성) |
| 2 | 상수 인벤토리 값매칭 (세법+노동법 수치) | ✅ |
| 3 | 골든 테스트 검증 | ✅ |
| 4 | 앵커 실패 피드백 루프 | ✅ |
| 5 | 컨텍스트 소스 확장 — 시행령·시행규칙 ✅ / 행정규칙(고시) ✅ / 해설서 RAG ✅ / 과거 개정 커밋 few-shot ⬜(회사 repo 이력 필요) | 🔶 |
| 6 | 모델 업그레이드 | ⏸ 보류 — 구조 개선(1·5)이 우선, 필요 시 `LOCAL_LLM_MODEL`만 교체 |
| 7 | 세법 파라미터 테이블화 — 개정을 "코드 수정"이 아니라 "파라미터 행 추가"로 | 📅 장기 (eHR 리팩토링 수반). 상수 인벤토리(2)의 수확 결과가 곧 리팩토링 대상 목록 |

수집기에는 확장 유틸리티 2종이 파이프라인 미연결 상태로 포함되어 있다:
`search_effective`(시행일 기준 "곧 시행될 개정" 조회), `fetch_three_tier`(법률→시행령
→시행규칙 위임조문 공식 매핑 — 소득세법 188건 실검증).
