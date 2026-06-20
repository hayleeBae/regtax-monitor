# 국세 법령 변경 모니터링 & 코드 반영 시스템

국세청 소관 법령의 변경을 자동 수집·감지하여 담당자에게 알리고,
담당자가 "반영"을 누르면 AI가 매핑된 코드 위치를 분석해 **수정안 초안**을
생성하는 시스템. (하이브리드: 로컬 임베딩 + Claude API 생성)

## 핵심 원칙

- **전체 코드는 외부로 나가지 않는다.** 인덱싱(임베딩)은 항상 로컬/사내에서,
  생성 단계에서는 RAG로 좁혀진 스니펫만 API로 보낸다.
- **두 개의 이음새(seam)** 만 환경에 따라 바뀐다:
  - `app/llm/`      — `LlmClient` (API ↔ 로컬 모델 교체 지점)
  - `app/codebase/` — `CodebaseAdapter` (mock repo ↔ 실제 repo 교체 지점)
- **사람 승인 게이트.** AI는 초안만 생성, 자동 적용 금지.

## RAG 동작 방식

ChromaDB는 SQLite처럼 **파일 기반 임베디드 DB**로, 별도 서버 설치 없이 pip 설치만으로 동작한다. 벡터 데이터는 `chroma_data/` 폴더에 저장된다.

### 인덱싱 단계 (최초 1회, 완전 로컬)

```
코드 파일 (Java / SQL / XML …)
    ↓ bge-m3 (로컬 임베딩 모델, CPU 동작)
벡터(숫자 배열)
    ↓
chroma_data/ 에 저장
```

서버 시작 시 `chroma_data/`가 비어 있으면 자동으로 인덱싱이 실행된다.

### 검색 단계 (map 호출 시, 완전 로컬)

```
법령 변경 텍스트 (개정문 + AI 요약)
    ↓ bge-m3로 벡터 변환
ChromaDB 유사도 검색
    ↓
관련 코드 스니펫 (벡터가 가장 가까운 청크들)
```

### 생성 단계 (apply 호출 시, API 사용)

```
법령 변경 내용 + 관련 코드 스니펫 (RAG로 좁힌 것만)
    ↓ Claude API
patch 초안 (unified diff)
```

### 단계별 코드 반출 여부

| 단계 | 실행 위치 | 코드 외부 반출 |
|---|---|---|
| 임베딩 (bge-m3) | 로컬 | ✗ |
| 벡터 저장 (ChromaDB) | 로컬 `chroma_data/` | ✗ |
| 유사도 검색 | 로컬 | ✗ |
| patch 생성 (Claude) | 외부 API | RAG로 좁힌 스니펫만 ✓ |

전체 코드는 절대 외부로 나가지 않으며, Claude에는 검색으로 좁혀진 함수 몇 개만 전송된다.

## API 플로우

```
POST /collect              수집 + 신구대조 자동 조회 (한 번에)
POST /changes/{id}/analyze Claude(Haiku)로 변경 분석·요약
POST /changes/{id}/map     코드베이스 RAG 검색 → Mapping 저장
PATCH /mappings/{id}/verify 담당자 매핑 검증 (정확도 향상)
POST /changes/{id}/apply   Claude(Sonnet)로 patch 초안 생성
POST /proposals/{id}/approve 사람 승인 → patch 파일 출력
POST /proposals/{id}/reject  거절 → 재작업
```

> 매핑 검증(`verify`)을 하지 않아도 플로우는 진행된다.
> 이 경우 RAG confidence 기반으로 자동 선택되며, Claude가 스니펫 불일치 시 초안에 경고를 명시한다.
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
│   │   ├── base.py        LlmClient 인터페이스
│   │   └── claude_client.py  Claude Haiku(분석) + Sonnet(patch)
│   ├── codebase/          [이음새 2] 코드 분석
│   │   ├── base.py        CodebaseAdapter 인터페이스
│   │   └── mock_adapter.py   mock_repo 대상 구현체
│   ├── embedding/
│   │   └── indexer.py     bge-m3 로컬 임베딩 + ChromaDB + 청킹(Java/SQL)
│   └── collector/
│       └── law_api.py     법제처 OPEN API 수집 + 신구대조 조회
```

## 시작하기

```bash
python3 -m venv .venv
source .venv/bin/activate        # (Windows) .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # ANTHROPIC_API_KEY, LAW_API_OC 입력

uvicorn app.main:app --reload    # http://127.0.0.1:8000/health
# 서버 시작 시 ChromaDB가 비어 있으면 mock_repo 자동 인덱싱
```

## 환경별 이식 방법

### 집 개발 (현재)
- `MockCodebaseAdapter` + `mock_repo/` 사용
- `LAW_API_OC` 없으면 mock 법령 데이터로 동작

### 회사 이식 시
1. `RealCodebaseAdapter` 구현 (`CodebaseAdapter` 인터페이스 상속)
2. `MOCK_REPO_ROOT`를 실제 repo 경로로 교체
3. 보안팀에 "RAG로 좁힌 스니펫 단위 외부 전송 가능 여부" 확인
4. 초기 1회 담당자 매핑 검증 (`PATCH /mappings/{id}/verify`) 권장

## 주요 설계 결정

- **ChromaDB**: 별도 서버 불필요, 파일 기반 임베디드 동작
- **bge-m3**: 한국어 강점, CPU/M1에서 GPU 없이 동작
- **청킹**: Java는 메서드 단위, SQL은 문장 단위, XML은 파일 단위
- **Mapping 테이블**: AI 부트스트랩(verified=False) → 담당자 검증(verified=True) → 이후 재변경 시 직행
- **배치 API**: 비실시간 워크로드로 Anthropic Batch API 50% 할인 적용 가능
