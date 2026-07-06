# 국세 법령 변경 모니터링 & 코드 반영 시스템

국세청 소관 법령의 변경을 자동 수집·감지하여 담당자에게 알리고,
담당자가 "반영"을 누르면 AI가 매핑된 코드 위치를 분석해 **수정안 초안**을
생성하는 시스템. (기본: 완전 로컬 — 로컬 임베딩 + 로컬 추론)

## 핵심 원칙

- **코드는 외부로 나가지 않는다.** 임베딩·검색·생성 모두 로컬에서 수행한다
  (기본 `LLM_BACKEND=local`). `LLM_BACKEND=claude`로 바꾸면 생성 단계만
  RAG로 좁혀진 스니펫을 Claude API로 보내는 하이브리드로 동작한다.
- **두 개의 이음새(seam)** 만 환경에 따라 바뀐다:
  - `app/llm/`      — `LlmClient` (로컬 모델 ↔ API 교체 지점, `get_llm_client()`)
  - `app/codebase/` — `CodebaseAdapter` (mock repo ↔ 실제 repo 교체 지점)
- **사람 승인 게이트.** AI는 초안만 생성, 자동 적용 금지.

## LLM 백엔드

| 백엔드 | 설정 | 생성 모델 | 코드 반출 |
|---|---|---|---|
| **local** (기본) | `LLM_BACKEND=local` | Ollama/vLLM 등 OpenAI 호환 서버의 로컬 모델 | 없음 |
| claude | `LLM_BACKEND=claude` | Claude Sonnet(초안) + Haiku(분석) | RAG 스니펫만 |

로컬 백엔드는 OpenAI 호환 `chat/completions` 엔드포인트만 있으면 되므로
**Ollama / vLLM / llama.cpp server / LM Studio** 어느 것이든 붙는다 (`LOCAL_LLM_BASE_URL`).

```bash
# Ollama 기준 (macOS)
brew install ollama
ollama serve                 # http://localhost:11434 (상시 구동: brew services start ollama)
ollama pull qwen3:8b         # 기본 모델 (한국어·코드 모두 무난, 약 5GB)
# 대안: exaone3.5:7.8b (한국어 특화), qwen3:14b (품질↑, 메모리↑)
```

### 실동작 검증 결과 (2026-07-07, M시리즈 맥북 · CPU 추론)

qwen3:8b + Ollama로 가상 개정 시나리오(자녀세액공제 15만원→25만원) 전체 파이프라인 검증:

| 단계 | 소요 시간 | 결과 |
|---|---|---|
| `analyze_change` (요약·영향 분석) | 약 1.5분 | JSON 파싱 정상, 요약 정확 |
| `propose_edits` → unified diff | 약 4.5분 | 앵커를 원본과 동일하게 복사, `git apply` 가능한 diff 생성 |

- 모델이 제공되지 않은 파일(VO/XML)의 편집을 지어내는 환각이 있었으나,
  `build_unified_diff`가 "파일을 읽을 수 없음" 경고로 걸러내고 실제 파일 편집만
  적용했다. **로컬 소형 모델의 약점이 기존 승인 게이트 안전장치로 상쇄됨을 확인.**
- CPU 추론이라 초안 생성이 수 분 걸린다. 비실시간 워크플로(담당자 승인 대기)라
  실용 범위이며, 응답이 `LOCAL_LLM_TIMEOUT_SECONDS`(기본 600초)를 넘으면 값을 늘릴 것.

### 모델 업그레이드 / 교체

```bash
ollama pull qwen3:14b                # 새 모델 받기
# .env 에서 LOCAL_LLM_MODEL=qwen3:14b 로 변경 후 서버 재시작 — 그게 전부
```

- **재인덱싱 불필요.** 생성 LLM 교체는 임베딩과 무관하다. `chroma_data/` 재구축은
  `EMBEDDING_MODEL`(bge-m3)을 바꿀 때만 필요하다.
- 초안 품질이 아쉬우면 `qwen3:14b`(약 9GB), 속도가 아쉬우면 분석 단계만
  `LOCAL_LLM_MODEL_CHEAP=qwen3:4b` 로 분리.
- qwen3 계열의 `<think>...</think>` 추론 블록은 `LocalClient`가 자동 제거한다.
- Ollama가 아닌 사내 vLLM 등을 쓰려면 모델 설치 없이 `LOCAL_LLM_BASE_URL`만
  해당 서버의 OpenAI 호환 엔드포인트로 지정.

## RAG 동작 방식

ChromaDB는 SQLite처럼 **파일 기반 임베디드 DB**로, 별도 서버 설치 없이 pip 설치만으로 동작한다. 벡터 데이터는 `chroma_data/` 폴더에 저장된다.

### 인덱싱 단계 (최초 1회, 완전 로컬)

```
코드 파일 (Java / SQL / XML …)
    ↓ 용어 사전으로 청크 보강 (암호 컬럼명 → 한글명 헤더 주입)
    ↓ bge-m3 (로컬 임베딩 모델, CPU 동작)
벡터(숫자 배열)
    ↓
chroma_data/ 에 저장
```

서버 시작 시 `chroma_data/`가 비어 있으면 자동으로 인덱싱이 실행된다.

> 인덱싱은 CPU에서 bge-m3로 수행되어 느리다(전체 수십 분). 코드/사전 변경을
> 검색에 반영하려면 `rm -rf chroma_data/` 후 재기동하는 1회성 작업이 필요하다.

### 검색 단계 (map 호출 시, 완전 로컬)

```
법령 변경 텍스트 (개정문 + AI 요약)
    ↓ bge-m3로 벡터 변환
ChromaDB 유사도 검색
    ↓
관련 코드 스니펫 (벡터가 가장 가까운 청크들)
```

### 생성 단계 (apply 호출 시)

```
법령 변경 내용 + 관련 코드 스니펫 (RAG로 좁힌 것만)
    ↓ 로컬 추론 서버 (기본) 또는 Claude API (LLM_BACKEND=claude)
patch 초안 (unified diff)
```

### 단계별 코드 반출 여부

| 단계 | 실행 위치 | 코드 외부 반출 |
|---|---|---|
| 임베딩 (bge-m3) | 로컬 | ✗ |
| 벡터 저장 (ChromaDB) | 로컬 `chroma_data/` | ✗ |
| 유사도 검색 | 로컬 | ✗ |
| patch 생성 — local 백엔드 (기본) | 로컬 추론 서버 | ✗ |
| patch 생성 — claude 백엔드 | 외부 API | RAG로 좁힌 스니펫만 ✓ |

기본(local) 구성에서는 코드가 한 줄도 외부로 나가지 않는다. claude 백엔드를
선택한 경우에도 검색으로 좁혀진 함수 몇 개만 전송된다.

## 암호 컬럼명 대응 (용어 사전)

eHR 레거시는 컬럼명이 `a0121` / `b0181` / `n0200` 같은 암호 코드라, "자녀세액공제"로
검색해도 임베딩이 코드와 연결하지 못한다. 그런데 **코드↔한글명 사전이 이미 코드 안에
흩어져 있다** — SQL mapper 인라인 주석(`AS n0200  -- 자녀세액공제 공제대상자녀`)과
VO 필드 주석(`private Long l0160; // 대중교통`).

`app/embedding/term_dict.py`가 이 주석을 regex로 긁어 `{코드: [한글명...]}` 사전을
자동 생성한다(수작업 0, 코드 원본 변경 0, 언제든 재생성 가능). 이 사전을 두 곳에서 쓴다:

1. **인덱싱 보강** — 청크 앞에 `[관련 항목] b0181=자녀세액공제대상수` 헤더를 붙여
   임베딩한다. 코드만 있던 청크도 한글 의미로 검색된다.
2. **매핑 부트스트랩** — `/map`에서 법령 텍스트와 사전의 한글 토큰을 정확 어휘 일치시켜
   (IDF×길이 점수, 흔한 토큰은 자동으로 낮게) 해당 컬럼코드를 가리키는 파일을
   고신뢰 `Mapping`으로 시드한다. 퍼지 RAG가 못 잡는 암호 컬럼을 보완한다.

사전은 `term_dict_cache.json`(코드→한글명), `term_loc_cache.json`(코드→파일)에 캐시되며,
`REPO_ROOT`가 비면(mock 모드) 비활성화된다.

## API 플로우

```
POST /collect              수집 + 신구대조 자동 조회 (한 번에)
POST /changes/{id}/analyze LLM으로 변경 분석·요약 (local: 경량 모델 / claude: Haiku)
POST /changes/{id}/map     RAG 검색 + 사전 정확매칭 부트스트랩 → Mapping 저장
                           (응답에 rag_hits / dict_matches 분리 표기)
PATCH /mappings/{id}/verify 담당자 매핑 검증 (정확도 향상)
POST /changes/{id}/apply   LLM으로 patch 초안 생성 (local: 기본 모델 / claude: Sonnet)
POST /proposals/{id}/approve 사람 승인 → patch 파일 출력
POST /proposals/{id}/reject  거절 → 재작업
```

> 매핑 검증(`verify`)을 하지 않아도 플로우는 진행된다.
> 이 경우 RAG confidence 기반으로 자동 선택되며, 모델이 스니펫 불일치 시 초안에 경고를 명시한다.
> 승인 게이트가 최후 방어선이므로 잘못된 코드가 자동 적용되는 일은 없다.

## 구조

```
regtax-monitor/
├── config.py              설정 (.env 로드)
├── mock_repo/             개발용 mock 코드베이스 (Java/SQL/XML)
├── app/
│   ├── main.py            FastAPI 엔트리
│   ├── db/                저장 레이어 (SQLite → 추후 Postgres)
│   │   ├── database.py
│   │   └── models.py      LawChange / Mapping / Review / PatchProposal / SyncState
│   ├── llm/               [이음새 1] 추론
│   │   ├── __init__.py    get_llm_client() — LLM_BACKEND에 따라 백엔드 선택
│   │   ├── base.py        LlmClient 인터페이스
│   │   ├── common.py      공용 프롬프트 + 응답 파싱(JSON/편집 블록/diff 변환)
│   │   ├── local_client.py   로컬 추론 (Ollama/vLLM 등 OpenAI 호환) — 기본
│   │   └── claude_client.py  Claude Haiku(분석) + Sonnet(patch) — 하이브리드
│   ├── codebase/          [이음새 2] 코드 분석
│   │   ├── base.py        CodebaseAdapter 인터페이스
│   │   └── mock_adapter.py   mock_repo 대상 구현체
│   ├── embedding/
│   │   ├── indexer.py     bge-m3 로컬 임베딩 + ChromaDB + 청킹(Java/SQL/XML) + 사전 보강
│   │   └── term_dict.py   용어 사전 수확(코드↔한글명) + 매핑 부트스트랩(법령→코드→파일)
│   └── collector/
│       └── law_api.py     법제처 OPEN API 수집 + 신구대조 조회
```

## 시작하기

```bash
python3 -m venv .venv
source .venv/bin/activate        # (Windows) .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # LAW_API_OC 입력 (claude 백엔드 사용 시 ANTHROPIC_API_KEY도)

# 로컬 추론 서버 준비 (기본 백엔드)
ollama serve &
ollama pull qwen3:8b

uvicorn app.main:app --reload    # http://127.0.0.1:8000/health
# 서버 시작 시 ChromaDB가 비어 있으면 mock_repo 자동 인덱싱
```

## 환경별 이식 방법

### 집 개발 (현재)
- `MockCodebaseAdapter` + `mock_repo/` 사용
- `LAW_API_OC` 없으면 mock 법령 데이터로 동작

### 회사 이식 시

git에는 **코드만** 들어 있다. `.env`, `.venv/`, `chroma_data/`, Ollama 모델은
모두 gitignore 대상이라 pull 후 머신마다 다시 준비해야 한다:

1. `python3.10 -m venv .venv && pip install -r requirements.txt`
   (SSL 프록시 환경이면 `HF_HUB_DISABLE_SSL=true` + pip `--trusted-host` 필요할 수 있음)
2. `cp .env.example .env` 후 `LAW_API_OC`, `REPO_ROOT` 등 입력
3. 추론 서버 준비 — 셋 중 하나:
   - `brew install ollama && ollama pull qwen3:8b` (외부망 가능 시)
   - 프록시가 ollama.com을 막으면 집에서 받은 `~/.ollama/models/` 폴더를 통째로
     복사해도 동작한다 (모델 저장소는 단순 파일)
   - 사내 vLLM 등이 있으면 설치 없이 `LOCAL_LLM_BASE_URL`만 지정
4. `MOCK_REPO_ROOT`를 실제 repo 경로로 교체 → 첫 기동 시 자동 인덱싱(수십 분)
5. 초기 1회 담당자 매핑 검증 (`PATCH /mappings/{id}/verify`) 권장

기본 local 백엔드면 코드 외부 반출이 없으므로 보안 검토가 단순해진다.
claude 백엔드를 쓰려면 보안팀에 "RAG로 좁힌 스니펫 단위 외부 전송 가능 여부" 확인.

## 주요 설계 결정

- **ChromaDB**: 별도 서버 불필요, 파일 기반 임베디드 동작
- **bge-m3**: 한국어 강점, CPU/M1에서 GPU 없이 동작
- **청킹**: Java는 메서드 단위, SQL은 문장 단위, XML은 쿼리/resultMap 단위
- **용어 사전**: 암호 컬럼명(a0121 등)을 SQL/VO 주석에서 자동 수확 → 인덱싱 보강 + 매핑 부트스트랩
- **Mapping 테이블**: AI 부트스트랩(verified=False) → 담당자 검증(verified=True) → 이후 재변경 시 직행
- **로컬 추론(OpenAI 호환)**: 특정 런타임에 종속되지 않음 — Ollama/vLLM/llama.cpp 교체 자유
- **배치 API**: claude 백엔드 사용 시 비실시간 워크로드로 Anthropic Batch API 50% 할인 적용 가능
