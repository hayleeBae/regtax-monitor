# regtax-monitor 작업 현황

> 최종 업데이트: 2026-06-22

---

## 최근 이슈 반영 현황 (2026-08)

### Issue #0024 — eHR 인덱싱 적합화 (진행 중)

실측 eHR(Nexacro UI·iBATIS·Ant 빌드) 대응. 스펙: `docs/specifications/EHR_INDEXING_SPEC.md`.

- step 0 인코딩 정책: 읽기 순서 utf-8-sig→cp949→utf-8(replace) 통일, BOM 왕복 보존 (완료)
- step 1 제외 목록: `EXCLUDED_DIRS`에 `classes`(exploded WAR) 추가, `_is_excluded` root 상대경로 판정 (완료)
- step 2 xfdl 청킹: `.xfdl` 1급 지원(`SOURCE_EXTS` + `_chunk_xfdl` + 심볼 추출) — 한도값 하드코딩 검색 커버 (완료)
- step 3 수확 스캔 루트: `term_dict`/`const_inventory` 스캔 루트를 `REPO_INDEX_PATHS`로 통일 (완료)
- step 4 문서·설정: `.env.example`/`COMPANY_VALIDATION.md`에 eHR 권장 화이트리스트·캐시 재생성·보안 경고 반영 (본 step)

화이트리스트가 1차 방어 — `build.xml` 평문 자격증명(2026-08-14 실측) 노출 방지. 회사 검증 절차는 `docs/operations/COMPANY_VALIDATION.md` 참조.

---

## 완료된 작업

### 1. Windows 이식 (M1 → Windows 11)
- `run.py` 신규 생성 — `python run.py`로 서버 기동
- `.env` UTF-8 인코딩 문제 해결 (PowerShell `Set-Content -Encoding UTF8`)
- `truststore.inject_into_ssl()` 적용 — 회사 SSL 프록시 전역 우회
  - `config.py`에 `HF_HUB_DISABLE_SSL=true` 환경변수 추가
  - `app/main.py` 모듈 레벨에서 적용 (HuggingFace + 법제처 API 모두 커버)

### 2. 실제 코드베이스 연동 (`RealCodebaseAdapter`)
- `app/codebase/real_adapter.py` 신규 구현
  - `list_files()` — `REPO_INDEX_PATHS` 필터 + `.git` 제외
  - `read_file()` — UTF-8 → CP949 fallback (eHR Java 레거시 대응)
  - `find_usages()` — 클래스명 참조 파일 검색 (코드 그래프 추적용)
  - `apply_patch()` — `git apply --check` → `git apply` 순차 실행
- `.env` 설정:
  ```
  REPO_ROOT=H:\workspace\eHR
  REPO_INDEX_PATHS=src/hr/pay/pay/tax,src/hr/pay/ref/com,src/hr/sta/pay
  ```

### 3. ChromaDB 인덱싱 최적화
- `REPO_INDEX_PATHS`로 세금 관련 폴더만 인덱싱 (1637 청크, 전체 대비 대폭 축소)
- 서버 시작 시 ChromaDB 비어있으면 자동 인덱싱 (`_auto_index_if_empty`)
- Python / Kotlin 청킹 추가, 진행률 출력 (`[file_idx/total]`)

### 4. 코드 그래프 추적 (B옵션)
- `/changes/{id}/apply` 엔드포인트에 추가
- RAG로 찾은 VO 파일을 참조하는 Service/DAO 자동 포함
- 결과: `RefDtlVO` 뿐 아니라 `RefDtlDAO`까지 컨텍스트에 포함 → Claude가 mapper 필요 등 실질적 작업 포인트 도출

### 5. 매핑 신뢰도 기본값 조정
- `min_confidence` 기본값 `0.2` → `0.0` (낮은 점수 매핑도 포함)
- verified 매핑 있으면 우선 사용, 없으면 confidence 기준 fallback

### 6. APScheduler 준비 (비활성)
- `config.py`에 `scheduler_enabled`, `scheduler_interval_hours` 추가
- `_start_scheduler()` 구현 완료, `.env`에서 `SCHEDULER_ENABLED=false`로 비활성화 상태
- 활성화하려면 `.env`에서 `SCHEDULER_ENABLED=true`로 변경

### 7. 실제 동작 확인
- 소득세법 제59조의2 (자녀세액공제) 변경 건으로 E2E 테스트 완료
- `RefBaseItemVO` → `RefDtlVO` → `RefDtlDAO` 자동 추적
- Claude가 "2026년 mapper 필요" 등 실제 작업 포인트 도출

---

## 내일 할 작업

### 1. 웹 UI 제작 (담당자용) ← **최우선**

- `static/index.html` 단일 페이지 앱 생성
- `app/main.py`에 두 가지 추가:
  ```python
  # 1) GET / → static/index.html 서빙
  from fastapi.responses import HTMLResponse
  
  @app.get("/", response_class=HTMLResponse)
  def ui():
      with open("static/index.html", encoding="utf-8") as f:
          return HTMLResponse(f.read())
  
  # 2) GET /proposals → 전체 초안 목록
  @app.get("/proposals")
  def list_all_proposals(db: Session = Depends(get_session)) -> list[dict]:
      ...
  ```
- UI 구성:
  - 헤더: 시스템명 + [법령 수집] + [새로고침] 버튼
  - 통계 바: 신규 / 검토대기 / 완료 건수
  - 법령 변경 목록 카드 (상태별 뱃지 + 단계별 액션 버튼)
    - 상태 `new`, `ai_summary 없음` → [분석 요청]
    - 상태 `reviewing` → [매핑] + [초안 생성]
    - 상태 `pending_apply` → 초안 섹션으로 연결
    - 상태 `done` → 완료 표시
  - 초안 검토 섹션: diff 보기 모달 + [승인] [거절] 버튼
- 호출할 API 엔드포인트:
  - `GET /changes`
  - `POST /changes/{id}/analyze`
  - `POST /changes/{id}/map`
  - `POST /changes/{id}/apply`
  - `GET /proposals` (신규 추가 필요)
  - `POST /proposals/{id}/approve`
  - `POST /proposals/{id}/reject`

### 2. 이메일 알림 (실제 배포 시점에 구현)
- 초안 생성 시 담당자에게 자동 이메일 발송
- `config.py`에 SMTP 설정 추가 예정
- 지금은 건너뜀

---

## 현재 .env 핵심 설정

```env
ANTHROPIC_API_KEY=sk-ant-...
LAW_API_OC=...
REPO_ROOT=H:\workspace\eHR
REPO_INDEX_PATHS=src/hr/pay/pay/tax,src/hr/pay/ref/com,src/hr/sta/pay
HF_HUB_DISABLE_SSL=true
SCHEDULER_ENABLED=false
SCHEDULER_INTERVAL_HOURS=24
```

## 서버 실행

```bash
cd H:\workspace\regtax-monitor
python run.py
# → http://127.0.0.1:8000
# → API 문서: http://127.0.0.1:8000/docs
```

## 인덱스 초기화가 필요할 때

```bash
# ChromaDB 재인덱싱
rm -rf chroma_data/
python run.py  # 서버 시작 시 자동 인덱싱

# DB 초기화
rm regtax.db
```
