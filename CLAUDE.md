# 프로젝트: regtax-monitor — 법령 변경 모니터링 & 코드 반영 시스템 (세법·인사)

## 프로젝트 유형

- **기존 코드 유지보수** — 동작 중인 코드 수정. 회귀 방지 최우선.

## 기술 스택

- Python 3.10 (`.venv/` 가상환경 — 회사 PC 기준 버전, 3.11+ 문법 사용 금지)
- FastAPI + uvicorn (단일 서버, `static/index.html` 대시보드 서빙)
- SQLite (`regtax.db`) + SQLAlchemy 2.x
- ChromaDB (임베디드, `chroma_data/`) + bge-m3 (sentence-transformers, 로컬 CPU 임베딩)
- LLM: `LLM_BACKEND=local`(기본, OpenAI 호환 로컬 추론 서버 — Ollama/vLLM 등) ↔ `claude`(Anthropic API)
- APScheduler (주기 수집, 기본 비활성) / pydantic-settings (`config.py` + `.env`)

## 아키텍처 규칙

- CRITICAL: **코드는 외부로 나가지 않는다.** 기본(local) 모드에서 인덱싱·검색·생성 전부 로컬 수행. 외부 API 전송 코드는 `LLM_BACKEND=claude` 경로(`app/llm/claude_client.py`)에만 둔다. local 모드는 anthropic 패키지 없이 동작해야 한다(지연 import 유지).
- CRITICAL: **사람 승인 게이트를 우회하는 경로를 만들지 않는다.** AI는 patch 초안 생성까지만. 자동 적용 기능 추가 금지. 골든 테스트는 스크래치 사본에서만 실행하고 실제 repo는 절대 수정하지 않는다(`app/golden.py`).
- CRITICAL: 두 개의 교체 이음새(seam)를 통해서만 접근한다 — LLM 호출은 `app/llm/`의 `LlmClient`(`get_llm_client()`), 대상 코드베이스 접근은 `app/codebase/`의 `CodebaseAdapter`. 다른 모듈에서 직접 HTTP/파일 접근 금지.
- 설정·시크릿은 `config.py`의 `Settings`(.env)로만 관리. 하드코딩 금지. 의존성은 `requirements.txt`(런타임)/`requirements-dev.txt`(검증 도구)에만 추가.
- 프롬프트·응답 파싱(JSON 추출, 앵커 편집 → unified diff)은 `app/llm/common.py`에 공유 — 백엔드별로 분기 로직을 복제하지 않는다.

## 개발 프로세스 (공통 — 프로젝트마다 수정하지 않음)

**파이프라인**: 설계(/design 승인) → 구현(/harness + execute.py) → 테스트(verify.sh full) → 보안점검(/secscan, Critical/High 시 중단) → 배포(/release, docs/DEPLOY.md 런북). 앞 단계 게이트를 통과하기 전에 다음 단계로 넘어가지 않는다.

- CRITICAL: 모든 검증은 `bash scripts/verify.sh quick|full`로 실행한다. 개별 명령을 직접 조합하지 마라.
- CRITICAL: 마이그레이션 프로젝트에서는 동작 보존이 최우선이다. 리팩토링 중 발견한 "개선점"은 코드를 고치지 말고 보고만 하라.
- 신규 기능은 테스트 먼저 작성(TDD). 레거시 수정 시에는 수정 전 현재 동작을 고정하는 테스트를 먼저 추가.
- 커밋 메시지는 conventional commits (feat:, fix:, docs:, refactor:, chore:)
- 대규모 변경은 step으로 쪼개고 각 step마다 산출 리포트를 남긴다 (`.claude/commands/harness.md` 참조)
- 관측성: execute.py는 모든 run을 `.harness/traces/`에, 실패를 `.harness/failures/`에 자동 기록한다. 중요한 기술적 분기는 `python3 scripts/trace.py event --type decision`으로 기록하고, 에러를 만나면 먼저 `.harness/failures/index.jsonl`에서 유사 실패 이력을 grep으로 검색한다.
- CRITICAL: 실패 레코드는 삭제하지 않는다. 해결 시 `failure.json`의 status를 `resolved`로 갱신하고 `verified_cause`를 채운다 (cause_code는 `.harness/taxonomy.yml` 값만 사용, 미검증이면 `unverified`).

## 명령어

```bash
bash scripts/verify.sh quick     # 빠른 검증 (ruff lint) — Stop hook용
bash scripts/verify.sh full      # 전체 검증 (quick + pytest: tests/ + scripts/) — step AC/리뷰용
bash scripts/verify.sh security  # 시크릿 스캔 + pip-audit

source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python run.py                    # 개발 서버 (= uvicorn app.main:app --reload, :8000)
```

- 테스트는 `pytest`(pyproject.toml 설정: testpaths=tests,scripts)로 실행. 무거운 의존성(임베딩 모델 로드, ChromaDB 인덱싱, LLM 호출)을 테스트에서 직접 트리거하지 않는다.
- 서버 첫 기동 시 `chroma_data/`가 비어 있으면 자동 인덱싱(CPU, 수십 분). 코드/사전 변경을 검색에 반영하려면 `rm -rf chroma_data/` 후 재기동.

## 환경 제약

- 두 실행 환경: 집 PC(macOS, M1/16GB) ↔ 회사 PC(macOS, M3급 — 사양 미확정). 환경별 차이는 전부 `.env`로 흡수한다 — 코드 분기·`verify=False` 하드코딩 금지.
- `REPO_ROOT` 비어 있으면 `mock_repo/`(집), 지정 시 실제 eHR repo(회사).
- 회사망: SSL 제약으로 HuggingFace 직접 다운로드 불가(`HF_HUB_DISABLE_SSL` + pip `--trusted-host`로 대응). Ollama는 설치되어 있음.
- 분석·초안 생성은 Ollama 서버(127.0.0.1:11434) 기동을 전제로 한다. 미기동 시 해당 API는 500.
- CRITICAL: Ollama OpenAI 호환 레이어(`/v1`)는 `options.num_ctx`를 **무시한다**(0.31.1 확인). 컨텍스트 창은 서버 기동 시 설정한다: `OLLAMA_CONTEXT_LENGTH=16384 ollama serve`. serve 로그의 "context shift"는 프롬프트 유실 신호로 간주한다(`.harness/failures/F-20260712-0001`).
- M1 생성 속도 약 5–8 t/s. LLM 타임아웃(600초)과 max_tokens는 출력 형식 설계와 연동해 판단할 것 (`F-20260712-0002` — 출력 토큰 폭발로 상한 절단·타임아웃).

## 도메인 컨텍스트

- **목적**: 법령(세법·노동법) 개정을 자동 수집·감지 → 관련 eHR 코드 위치에 매핑 → git apply 가능한 patch 초안 생성. 가장 비싼 실패는 "잘못 고친 것"이 아니라 **"개정을 놓친 것"** — 수집·감지의 재현율이 초안 품질보다 우선한다.
- **도메인 레지스트리** (`domains.json`): 수집 대상을 도메인(tax/hr) 단위로 관리. `laws`는 법제처 등록명과 **정확 일치**해야 하며 가운뎃점은 `ㆍ`(U+318D). `admin_rule_queries`는 고시 검색어(부분일치 — 고시명이 매년 바뀜).
- **법제처 API 함정**: OC 키는 target별 신청제 — 행정규칙 목록/본문은 별도 신청 필요. 행정규칙 본문 조회의 `ID` 파라미터는 행정규칙ID가 아니라 **행정규칙일련번호**(행정규칙ID는 `LID`). 시행령·시행규칙은 개정이 잦아 정확 법령명 필터로 노이즈를 차단한다.
- **eHR 레거시 특성**: 컬럼명이 `a0121`/`n0200` 같은 암호 코드 — 용어 사전(`term_dict.py`, 주석에서 자동 수확)과 상수 인벤토리(`const_inventory.py`, 값 매칭)로 보완한다. 캐시 파일들(`*_cache.json`)은 gitignore + 자동 재생성 — 커밋 금지(eHR 내부 파생물, 외부 반출 금지).
- **개정 유형별 효용 한계**: 수치 개정(한도·세율)은 초안 자동화 ◎, 요건 개정은 매핑까지 △, 구조 개정은 감지·알림까지 ✗ — 이 격차를 코드로 무리하게 메우려 하지 말 것(로드맵 문서 참조).

## Documentation

Before implementing new features, read the project documentation in the following order.

1. docs/product/PRD.md
2. docs/architecture/ARCHITECTURE.md
3. docs/architecture/ARCHITECTURE_V2.md
4. docs/architecture/ADR.md
5. docs/specifications/*
6. docs/roadmap/IMPLEMENTATION_ROADMAP.md

The documentation under docs/specifications is the implementation contract.

Do not implement functionality that is not defined by the specification.