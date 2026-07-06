# 국세 법령 변경 모니터링 & 코드 반영 시스템

연말정산은 매년 세법이 개정되고, 그때마다 eHR 연말정산 로직(공제 한도·공제율·
적용 요건 등)을 수정해야 한다. 이 시스템은 그 사이클을 지원한다:

1. 국세청 소관 법령의 변경을 **자동 수집·감지**하여 담당자에게 알리고,
2. 변경 조문을 **관련 코드 위치에 매핑**하고,
3. 담당자가 "반영"을 누르면 **수정안 초안(git apply 가능한 patch)** 을 생성한다.

연말정산 실무에서 가장 비싼 실패는 "잘못 고친 것"이 아니라 **"개정을 놓친 것"**이다.
초안 품질과 별개로, 수집→요약→매핑 파이프라인만으로도 이 리스크를 줄인다.

## 핵심 원칙

- **코드는 외부로 나가지 않는다.** 임베딩·검색·생성 모두 로컬에서 수행한다(기본).
- **두 개의 이음새(seam)** 만 환경에 따라 바뀐다:
  - `app/llm/`      — `LlmClient` (로컬 모델 ↔ API 교체 지점, `get_llm_client()`)
  - `app/codebase/` — `CodebaseAdapter` (mock repo ↔ 실제 repo 교체 지점)
- **사람 승인 게이트.** AI는 초안만 생성, 자동 적용 금지. 승인 게이트가 최후
  방어선이므로 잘못된 코드가 자동 적용되는 일은 없다.

---

## 아키텍처: 기존(하이브리드) → 현재(완전 로컬)

### 기존 방식 — 하이브리드 (Claude API)

초기 구조는 **로컬 임베딩 + 외부 API 생성**의 하이브리드였다.

- 인덱싱·검색은 로컬(bge-m3 + ChromaDB)에서 수행하고,
- 생성(분석·초안)만 RAG로 좁혀진 스니펫을 **Claude API**로 전송했다.
  분석은 Haiku(저비용), 초안 생성은 Sonnet(고품질)으로 분리.
- 전체 코드는 절대 반출되지 않고, 검색으로 좁혀진 함수 몇 개만 나가는 설계였지만
  "스니펫 단위 외부 전송"에 대한 보안 검토가 필요했다.

이 구현은 `app/llm/claude_client.py`로 남아 있으며 `LLM_BACKEND=claude`로 언제든
되돌릴 수 있다 (`ANTHROPIC_API_KEY` 필요).

### 현재 방식 — 완전 로컬 (기본)

생성 단계까지 로컬 추론 서버로 옮겨 **코드가 한 줄도 외부로 나가지 않는다.**

- `app/llm/local_client.py`가 OpenAI 호환 `chat/completions` 엔드포인트를 호출한다.
  → **Ollama / vLLM / llama.cpp server / LM Studio** 어느 것이든 붙는다
  (`LOCAL_LLM_BASE_URL`만 지정, 추가 의존성 없음).
- 프롬프트와 응답 파싱(JSON 추출, 앵커 편집 블록 → unified diff 변환)은
  `app/llm/common.py`로 분리되어 **양 백엔드가 동일한 로직을 공유**한다.
  백엔드를 바꿔도 편집 형식·후처리·승인 플로우는 변하지 않는다.
- `app/llm/__init__.py`의 `get_llm_client()`가 `LLM_BACKEND` 설정에 따라
  구현체를 선택한다. local 모드에서는 anthropic 패키지가 없어도 동작한다(지연 import).
- qwen3 계열의 `<think>...</think>` 추론 블록은 자동 제거된다.

### 백엔드 비교

| 백엔드 | 설정 | 생성 모델 | 코드 반출 | 보안 검토 |
|---|---|---|---|---|
| **local** (기본) | `LLM_BACKEND=local` | Ollama/vLLM 등의 로컬 모델 | 없음 | 불필요 |
| claude (기존) | `LLM_BACKEND=claude` | Claude Sonnet(초안) + Haiku(분석) | RAG 스니펫만 | 스니펫 반출 승인 필요 |

---

## 동작 방식

### RAG 파이프라인

ChromaDB는 SQLite처럼 **파일 기반 임베디드 DB**로, 별도 서버 없이 pip 설치만으로
동작한다. 벡터는 `chroma_data/`에 저장된다.

**① 인덱싱 (최초 1회, 완전 로컬)**

```
코드 파일 (Java / SQL / XML …)
    ↓ 용어 사전으로 청크 보강 (암호 컬럼명 → 한글명 헤더 주입)
    ↓ bge-m3 (로컬 임베딩 모델, CPU 동작)
벡터 → chroma_data/ 에 저장
```

서버 시작 시 `chroma_data/`가 비어 있으면 자동 인덱싱된다. CPU라 느리다(수십 분).
코드/사전 변경을 검색에 반영하려면 `rm -rf chroma_data/` 후 재기동.

**② 검색 (map 호출 시, 완전 로컬)**

```
법령 변경 텍스트 (개정문 + AI 요약)
    ↓ bge-m3로 벡터 변환 → ChromaDB 유사도 검색
관련 코드 스니펫 (벡터가 가장 가까운 청크들)
```

**③ 생성 (apply 호출 시)**

```
법령 변경 내용 + 관련 코드 스니펫 (RAG로 좁힌 것만)
    ↓ 로컬 추론 서버 (기본) 또는 Claude API (LLM_BACKEND=claude)
앵커 기반 검색/치환 편집 → 서버가 unified diff로 변환 (git apply 가능)
```

**단계별 코드 반출 여부**

| 단계 | 실행 위치 | 코드 외부 반출 |
|---|---|---|
| 임베딩 (bge-m3) | 로컬 | ✗ |
| 벡터 저장·검색 (ChromaDB) | 로컬 `chroma_data/` | ✗ |
| patch 생성 — local 백엔드 (기본) | 로컬 추론 서버 | ✗ |
| patch 생성 — claude 백엔드 | 외부 API | RAG로 좁힌 스니펫만 ✓ |

### 암호 컬럼명 대응 (용어 사전)

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

### API 플로우

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

> 매핑 검증(`verify`)을 하지 않아도 플로우는 진행된다. 이 경우 RAG confidence
> 기반으로 자동 선택되며, 모델이 스니펫 불일치 시 초안에 경고를 명시한다.

---

## 실동작 검증 결과 (2026-07-07, M시리즈 맥북 · CPU 추론)

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

### 효용 평가 — 개정 유형별

세법 개정을 유형별로 나누면 현재 시스템의 효용이 명확해진다:

| 개정 유형 | 예 | 현재 효용 |
|---|---|---|
| **수치 개정** | 공제 한도·공제율·세율표 변경 (15만원→25만원) | ◎ git apply 가능한 초안까지 자동. 매년 개정의 상당수가 이 유형 |
| **요건 개정** | 적용 대상·소득 기준 조건 변경 | △ 초안 품질은 낮으나 "어느 파일·어느 메서드"를 짚는 매핑 가치 유지 |
| **구조 개정** | 공제 항목 신설·통합, 계산 체계 변경 | ✗ 자동 초안 무리. 놓치지 않고 감지 + 영향 범위 알림까지가 가치 |

요약: **"탐지·알림 + 수치 개정의 초안 자동화" 수준에서 실질 효용이 있고,
복잡한 개정에는 보조 도구.** 이 격차는 모델 크기보다 아래 로드맵(도메인 구조의
시스템화)으로 줄이는 것이 효과적이다.

---

## 효율 개선 로드맵

임베딩·변수명 매칭은 확률적 추측이라 천장이 있다. 레버리지가 큰 순서:

**1. 조문→코드 매핑을 자산으로 축적** (비용 최소 — 이미 있는 구조 활용)
연말정산 세법은 매년 **같은 자리**(소득세법 제50~59조 부근)가 바뀐다. `Mapping`
테이블(verified 플래그)에 한 시즌만 담당자 검증을 투자하면, 이듬해부터는 RAG 추측
없이 검증된 매핑으로 직행한다. 퍼지 검색은 첫 부트스트랩용이고, 진짜 자산은 검증된
매핑의 누적이다. 구현이 아니라 운영 방침의 문제.

**2. 세법 상수 인벤토리** (변수명이 아니라 '값'으로 매칭)
`term_dict.py`가 주석에서 컬럼명 사전을 수확하듯, 코드의 **숫자 리터럴**(`150000L`,
`0.15`, `7000000`)을 주변 주석과 함께 수확해 "값→위치→법적 근거" 인벤토리를 만든다.
개정문의 "15만원을 25만원으로"를 `150000` 정확 매칭으로 위치 특정 — 수치 개정에서는
임베딩보다 압도적으로 정밀하고, 기존 수확 패턴의 확장이라 구현 비용도 낮다.
장기적으로는 이 상수들을 **연도 버전 세법 파라미터 테이블**로 빼는 리팩토링이 근본
해법: 매년 개정이 "코드 수정"이 아니라 "파라미터 행 추가"가 되고, LLM의 역할도
위험한 코드 편집이 아니라 검증 쉬운 데이터 제안으로 바뀐다.

**3. 골든 테스트로 초안 검증** (환각의 구조적 차단)
국세청이 매년 내는 연말정산 계산 사례·모의계산 값으로 골든 테스트를 만들고, 초안
patch를 스크래치 워크트리에 적용해 테스트를 돌리는 단계를 파이프라인에 추가한다.
"그럴듯해 보임"이 "계산이 맞음"으로 바뀐다.

**4. 앵커 실패 피드백 루프** (반나절짜리 개선, 즉시 가능)
현재 `build_unified_diff`의 "앵커를 찾지 못함" 경고는 사람에게만 간다. 이를 모델에게
되돌려 "이 앵커가 원본에 없다, 다시"로 1~2회 재시도시키면 로컬 소형 모델의 복사
실수 상당 부분이 자체 해결된다.

**5. 컨텍스트 소스 확장**
법률 본문만으로는 부족하다. 실무 디테일은 **시행령·시행규칙**에 있고, 국세청
**『개정세법 해설』**은 개정 의도와 계산 예시까지 담고 있어 analyze 단계 RAG 컨텍스트로
최적. 또한 회사 repo의 **과거 세법개정 반영 커밋**들이 최고의 few-shot 예시 —
"이 코드베이스에서 한도 변경은 이렇게 고친다"를 모델에게 보여주는 것만으로 초안
품질이 올라간다.

**6. 모델 업그레이드는 마지막**
qwen3:14b/32b로 올리면 나아지지만, 위 1~5 없이 모델만 키우는 건 추측의 정확도를
올리는 것에 그친다. 구조(매핑 자산·상수 인벤토리·테스트 검증)가 갖춰지면 8B로도
충분한 영역이 넓어진다.

---

## 로컬 모델 운영

### 설치 (Ollama 기준, macOS)

```bash
brew install ollama
ollama serve                 # http://localhost:11434 (상시 구동: brew services start ollama)
ollama pull qwen3:8b         # 기본 모델 (한국어·코드 모두 무난, 약 5GB)
# 대안: exaone3.5:7.8b (한국어 특화), qwen3:14b (품질↑, 메모리↑)
```

### 모델 업그레이드 / 교체

```bash
ollama pull qwen3:14b                # 새 모델 받기
# .env 에서 LOCAL_LLM_MODEL=qwen3:14b 로 변경 후 서버 재시작 — 그게 전부
```

- **재인덱싱 불필요.** 생성 LLM 교체는 임베딩과 무관하다. `chroma_data/` 재구축은
  `EMBEDDING_MODEL`(bge-m3)을 바꿀 때만 필요하다.
- 초안 품질이 아쉬우면 `qwen3:14b`(약 9GB), 속도가 아쉬우면 분석 단계만
  `LOCAL_LLM_MODEL_CHEAP=qwen3:4b` 로 분리.
- Ollama가 아닌 사내 vLLM 등을 쓰려면 모델 설치 없이 `LOCAL_LLM_BASE_URL`만
  해당 서버의 OpenAI 호환 엔드포인트로 지정.
- Claude API로 되돌리기: `.env`에 `LLM_BACKEND=claude` + `ANTHROPIC_API_KEY` 입력.

---

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

## 환경별 이식

### 집 개발 (현재)
- `MockCodebaseAdapter` + `mock_repo/` 사용
- `LAW_API_OC` 없으면 mock 법령 데이터로 동작

### 회사 PC 이식

git에는 **코드만** 들어 있다. `.env`, `.venv/`, `chroma_data/`, Ollama 모델은
모두 gitignore 대상이라 pull 후 머신마다 다시 준비해야 한다:

| 항목 | git으로 옮겨지나 | 회사에서 할 일 |
|---|---|---|
| 코드 (LocalClient, 팩토리 등) | ✓ | pull만 하면 됨 |
| `.env` | ✗ | `.env.example` 복사 후 작성 |
| `.venv/` | ✗ | venv 재생성 + pip install |
| Ollama + 모델 | ✗ | 아래 3번 참고 |
| `chroma_data/` (벡터 인덱스) | ✗ | 첫 기동 시 자동 재인덱싱 |

1. `python3.10 -m venv .venv && pip install -r requirements.txt`
   (SSL 프록시 환경이면 `HF_HUB_DISABLE_SSL=true` + pip `--trusted-host` 필요할 수 있음)
2. `cp .env.example .env` 후 `LAW_API_OC`, `REPO_ROOT` 등 입력
3. 추론 서버 준비 — 셋 중 하나:
   - `brew install ollama && ollama pull qwen3:8b` (외부망 가능 시)
   - 프록시가 ollama.com을 막으면 집에서 받은 `~/.ollama/models/` 폴더를 통째로
     복사해도 동작한다 (모델 저장소는 단순 파일)
   - 사내 vLLM 등이 있으면 설치 없이 `LOCAL_LLM_BASE_URL`만 지정
4. `REPO_ROOT`를 실제 repo 경로로 지정 → 첫 기동 시 자동 인덱싱(수십 분)
5. 초기 1회 담당자 매핑 검증 (`PATCH /mappings/{id}/verify`) 권장 — 개선 로드맵 1번

기본 local 백엔드면 코드 외부 반출이 없으므로 보안 검토가 단순해진다.
claude 백엔드를 쓰려면 보안팀에 "RAG로 좁힌 스니펫 단위 외부 전송 가능 여부" 확인.

---

## 프로젝트 구조

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
│   │   └── claude_client.py  Claude Haiku(분석) + Sonnet(patch) — 기존 하이브리드
│   ├── codebase/          [이음새 2] 코드 분석
│   │   ├── base.py        CodebaseAdapter 인터페이스
│   │   └── mock_adapter.py   mock_repo 대상 구현체
│   ├── embedding/
│   │   ├── indexer.py     bge-m3 로컬 임베딩 + ChromaDB + 청킹(Java/SQL/XML) + 사전 보강
│   │   └── term_dict.py   용어 사전 수확(코드↔한글명) + 매핑 부트스트랩(법령→코드→파일)
│   └── collector/
│       └── law_api.py     법제처 OPEN API 수집 + 신구대조 조회
```

## 주요 설계 결정

- **ChromaDB**: 별도 서버 불필요, 파일 기반 임베디드 동작
- **bge-m3**: 한국어 강점, CPU/M1에서 GPU 없이 동작
- **청킹**: Java는 메서드 단위, SQL은 문장 단위, XML은 쿼리/resultMap 단위
- **용어 사전**: 암호 컬럼명(a0121 등)을 SQL/VO 주석에서 자동 수확 → 인덱싱 보강 + 매핑 부트스트랩
- **Mapping 테이블**: AI 부트스트랩(verified=False) → 담당자 검증(verified=True) → 이후 재변경 시 직행
- **로컬 추론(OpenAI 호환)**: 특정 런타임에 종속되지 않음 — Ollama/vLLM/llama.cpp 교체 자유
- **배치 API**: claude 백엔드 사용 시 비실시간 워크로드로 Anthropic Batch API 50% 할인 적용 가능
