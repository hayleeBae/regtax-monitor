# Step 0: encoding-policy

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL — 환경별 차이는 `.env`로 흡수, 코드 분기 금지)
- `/docs/specifications/EHR_INDEXING_SPEC.md` (**이 작업의 계약** — §1 실측 배경, §4 인코딩 정책)
- `/docs/architecture/ADR.md`의 **ADR-015**
- `/app/codebase/real_adapter.py` (`read_file`, `apply_patch` — 수정 대상)
- `/app/embedding/term_dict.py`의 `_read()` (수정 대상)

## 배경 (자기완결 요약)

2026-08-14 eHR 실측: `.xfdl`은 UTF-8+BOM, sqlmap XML 절반이 CP949(특히 최신 연말정산 `PayRefCom_2022~2026.xml`), XML 선언과 실제 바이트가 불일치하는 파일 2건(`TimTimm.xml`/`TimVac.xml` — 선언 EUC-KR, 실제 UTF-8) 존재. 현행 읽기 순서 `utf-8 → cp949`는 CP949는 처리하지만 **BOM이 텍스트 선두에 `﻿`로 잔존**한다 — 청킹·정규식·용어 수확의 첫 토큰을 오염시킨다. 이 step은 읽기 정책을 `utf-8-sig → cp949 → utf-8(errors="replace")`로 통일한다. `utf-8-sig`는 BOM 유무 모두 처리한다(BOM 있으면 제거, 없으면 일반 utf-8과 동일).

## 작업

### 1) `app/codebase/real_adapter.py` — `read_file()`

- 시도 순서를 `("utf-8-sig", "cp949")` → 실패 시 `utf-8, errors="replace"`로 변경 (기존 구조 유지, 첫 시도만 교체).
- **XML 선언의 `encoding=` 속성은 절대 참조하지 마라** — 실측상 선언이 거짓인 파일이 있다. 바이트 폴백 방식이 정답이다 (현행 방식이 이미 그렇다 — 유지).

### 2) `app/codebase/real_adapter.py` — `apply_patch()`

- 현재 디코딩이 `utf-8 → cp949` 순서다. BOM 파일에 patch를 적용하면 BOM이 첫 줄 텍스트에 섞여 왕복이 깨진다.
- 수정: `raw`가 BOM(`b"\xef\xbb\xbf"`)으로 시작하면 기억해 두고 `utf-8-sig`로 디코딩, 재인코딩 시 BOM을 재부착한다. BOM 없는 파일은 기존 동작 그대로.
- 기존의 개행(CRLF/LF) 보존·말미 개행 보존 로직은 건드리지 마라.

### 3) `app/embedding/term_dict.py` — `_read()`

- 시도 순서를 `("utf-8-sig", "cp949")`로 변경. 폴백(`errors="replace"`)과 OSError 처리 구조는 유지.
- `const_inventory.py`는 이 함수를 import해서 쓰므로 자동으로 함께 고쳐진다 — 별도 수정 금지.

## 테스트

`tests/test_encoding_policy.py` 신규. tmp_path에 바이트 레벨로 파일을 만들어 검증:

1. **UTF-8+BOM 파일** (xfdl 시나리오): `read_file`·`_read` 결과 선두에 `﻿`가 없고 한글 무손실.
2. **CP949 파일** (sqlmap 시나리오): `"자녀세액공제"` 같은 한글 주석이 무손실 판독.
3. **선언·실제 불일치 파일**: 내용에 `encoding="EUC-KR"` 선언이 있지만 실제 UTF-8 바이트 — 선언 무시하고 정상 판독.
4. **apply_patch BOM 왕복**: BOM 있는 파일에 hunk 적용 후 파일 바이트가 여전히 BOM으로 시작하고 본문 무손실. (RealCodebaseAdapter를 tmp_path root로 생성해 검증 — 실제 eHR 접근 금지)

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - CLAUDE.md CRITICAL: 환경 분기 하드코딩이 없는가? (인코딩 폴백은 데이터 기반이지 환경 분기가 아니다)
   - 기존 mock repo(UTF-8) 경로가 회귀 없이 동작하는가?
3. `phases/issue-0024/index.json`의 step 0을 업데이트한다.

## 금지사항

- XML 선언을 파싱해 인코딩을 결정하는 코드를 넣지 마라. 이유: 실측상 선언이 실제 바이트와 불일치하는 파일이 있다 (스펙 §4).
- `chardet` 등 감지 라이브러리를 추가하지 마라. 이유: 3단 폴백으로 충분하고 의존성 추가는 설계 범위 밖이다.
- `read_file`/`_read`를 공용 모듈로 통합하지 마라. 이유: 스펙 §4가 이번 이슈에서 변경 반경 최소화를 위해 중복 유지를 명시했다.
- `app/embedding/indexer.py`, `app/codebase/base.py`를 수정하지 마라. 이유: 청킹은 step 2, 제외 목록은 step 1의 scope다.
- 기존 테스트를 깨뜨리지 마라.
