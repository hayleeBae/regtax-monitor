# Step 4: docs-env

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/specifications/EHR_INDEXING_SPEC.md` §5-2 권장 화이트리스트, §6 보안 (**이 작업의 계약**)
- `/.env.example` (수정 대상)
- `/docs/operations/COMPANY_VALIDATION.md` (수정 대상 — 회사 검증 절차 문서. 기존 구성·문체를 따르라)
- step 0~3에서 변경된 파일들 (`app/codebase/real_adapter.py`, `app/codebase/base.py`, `app/golden.py`, `app/embedding/indexer.py`, `app/embedding/term_dict.py`) — 문서가 코드와 일치해야 한다

## 배경 (자기완결 요약)

step 0~3으로 코드가 eHR을 다룰 수 있게 됐다. 이 step은 **운영자가 회사 PC에서 실제로 돌릴 때 필요한 문서·설정 본보기**를 갱신한다. 문서만 다루는 step이다 — 코드 수정 금지.

## 작업

### 1) `.env.example`

- `REPO_INDEX_PATHS` 항목에 eHR 권장 화이트리스트를 주석 예시로 추가 (스펙 §5-2):

```
# eHR 권장 화이트리스트 (도메인: 급여·연말정산/연차/4대보험 + SQL + Nexacro 화면)
# REPO_INDEX_PATHS=src/hr/pay,src/hr/tim/annl,src/hr/ins,src/hr/sta/pay,src/hr/sqlmap,web/nexacro/solution/pay,web/nexacro/solution/tim,web/nexacro/solution/ins
```

- 화이트리스트가 1차 방어라는 점(경로별 산출물 `web/eHR/`, `nexacro14lib/`, `UbiService/` 차단)을 한 줄 주석으로 명시.

### 2) `docs/operations/COMPANY_VALIDATION.md`

다음 절차를 기존 문서 구성에 맞는 위치에 추가한다:

1. **인덱싱 범위 변경 시 캐시 재생성 절차**: `term_dict_cache.json`·`term_loc_cache.json`·`const_inventory_cache.json`·`symbol_index_cache.json` 삭제 + `chroma_data/` 삭제 후 재기동 (스캔 범위가 바뀌면 캐시가 구 범위를 반영하므로).
2. **xfdl 인덱싱 검증 항목**: 인덱싱 후 `.xfdl` 청크가 검색에 잡히는지 확인하는 체크 (예: "직무발명" 또는 한도 상수로 `/index` 후 검색 스모크).
3. **인코딩 검증 항목**: `PayRefCom_2026.xml`(CP949) 한글 주석이 용어 사전에 깨지지 않고 수확되는지 확인.
4. **보안 경고**: `H:\workspace\eHR\build.xml`에 평문 자격증명 존재(2026-08-14 실측) — 화이트리스트가 차단하지만, `REPO_INDEX_PATHS`를 비우고(전체 인덱싱) 돌리면 노출된다. **화이트리스트 없이 eHR을 인덱싱하지 말 것** + 자격증명 외부화·정보보호팀(security@pantechcni.com) 통보 권고를 명시 (스펙 §6).

### 3) `STATUS.md` (선택 — 시간이 남으면)

- 이 파일은 2026-06-22 이후 갱신되지 않았다. 갱신한다면 issue-0023/0024 반영 상태 한 절만 추가하고 기존 내용은 건드리지 마라. 부담되면 건너뛰어도 된다 (AC 아님).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

(문서 step이지만 verify를 돌려 코드 무변경·문서 lint 통과를 확인한다)

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트:
   - 문서의 경로·설정 이름이 step 0~3 실제 코드와 일치하는가?
   - `git diff`에 `app/` 변경이 없는가? (문서·설정 예시만)
3. `phases/issue-0024/index.json`의 step 4를 업데이트한다.

## 금지사항

- `app/` 하위 코드를 수정하지 마라. 이유: 이 step은 문서 전용이다. 코드 결함을 발견하면 수정하지 말고 summary에 보고만 하라.
- `.env`(실제 파일)를 수정하지 마라. 이유: 환경 파일은 운영자 소유다 — 본보기는 `.env.example`에만.
- 실제 자격증명 값(IP·계정·비밀번호)을 어떤 문서에도 옮겨 적지 마라. 이유: 문서가 repo에 커밋되면 그 자체가 유출이다. "build.xml에 평문 자격증명 존재"라는 사실만 적는다.
