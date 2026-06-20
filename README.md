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

## 구조

```
regtax-monitor/
├── config.py              설정 (.env 로드)
├── app/
│   ├── main.py            FastAPI 엔트리
│   ├── db/                저장 레이어 (SQLite -> 추후 Postgres)
│   │   ├── database.py
│   │   └── models.py      law_change / mapping / review / patch_proposal
│   ├── llm/               [이음새 1] 추론
│   │   ├── base.py        LlmClient 인터페이스
│   │   └── claude_client.py
│   ├── codebase/          [이음새 2] 코드 분석
│   │   ├── base.py        CodebaseAdapter 인터페이스
│   │   └── mock_adapter.py
│   ├── embedding/
│   │   └── indexer.py     로컬 임베딩(bge-m3) + ChromaDB
│   └── collector/
│       └── law_api.py     법제처 OPEN API 수집기
```

## 폴더 ↔ 단계(Phase) 매핑

| 폴더 | 단계 |
|---|---|
| db, llm/base, codebase/base | Phase 0 (지금: 골격) |
| collector | Phase 1 (수집·변경감지) |
| llm/claude_client (analyze) | Phase 2 (분석·검토) |
| embedding, codebase/mock | Phase 3 (매핑 엔진) |
| llm/claude_client (patch), codebase.apply_patch | Phase 4 (코드수정) |

## 시작하기

```bash
python3 -m venv .venv
source .venv/bin/activate          # (Windows) .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # 키 채우기 (Anthropic, 법제처 OC)

uvicorn app.main:app --reload      # http://127.0.0.1:8000/health
```

## 다음 할 일 (Phase 1)

1. 법제처 OPEN API 키(OC) 발급 후 `.env`에 입력
2. `app/collector/law_api.py`의 `search_changed()` 실제 파라미터 확정
3. 공포일 기준 변경감지 + `last_sync` 저장 로직 추가
