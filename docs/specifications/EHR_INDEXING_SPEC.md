# EHR_INDEXING_SPEC — eHR 인덱싱 적합화 (.xfdl 지원 · 인코딩 · 화이트리스트)

- 상태: **Draft (승인 대기)**
- 제안 이슈: #0024
- 관련: ADR-015, `RETRIEVAL_EXPERIMENT_SPEC.md`, `docs/operations/COMPANY_VALIDATION.md`

## 1. 배경 — 2026-08-14 eHR 실측

`H:\workspace\eHR` (git 아님, Ant 빌드, Spring MVC + iBATIS 2.x + **Nexacro** UI) 조사 결과:

| 발견 | 인덱싱 영향 |
|---|---|
| UI가 JSP가 아니라 Nexacro — `web/nexacro/solution/**/*.xfdl` **1,301개** | `.xfdl`이 `SOURCE_EXTS`에 없어 **인덱싱 0건** |
| **세법 한도값이 xfdl 내 JavaScript에 하드코딩** (직무발명보상금 비과세 한도 300→500→700만원, 국외근로수당 한도, 지방소득세 절사 등 — `PayRefCom003.xfdl:669-707`, `PayPayCom955.xfdl:712`) | 수치 개정의 핵심 patch 대상이 검색 후보에 절대 안 잡힘 (재현율 공백) |
| 소득세율표·4대보험요율은 코드가 아니라 **DB 데이터** (`T_PAY_TAX`, `T_INS_RATE`) | 코드 patch 불가 항목 존재 (별도 이슈 — 비범위) |
| `src/hr/sqlmap/*.xml` 83개 중 **약 절반이 CP949** — 특히 `PayRefCom_2022~2026.xml`(최신 연말정산) | 컬럼 코드(a0121 등 333개)의 유일한 의미 사전인 한글 주석이 깨질 위험 |
| XML 선언과 실제 바이트 **불일치** 2건 (`TimTimm.xml`/`TimVac.xml` — 선언 EUC-KR, 실제 UTF-8) | 선언 기반 디코딩 금지 근거 |
| `.xfdl`은 **UTF-8 + BOM** | BOM 미처리 시 첫 토큰 오염 |
| `classes/` **1.7GB / 32,398 파일** — exploded WAR가 자기 자신을 4중 재귀 중첩 | 무제한 인덱싱 시 인덱스의 91%가 중복본 |
| `build.xml`에 **FTP/SSH 평문 자격증명** | 인덱스·LLM 컨텍스트로 유출 위험 (§6) |
| JSP 71개는 전자서명·에러페이지 — 업무 로직 없음 | JSP 청킹 불필요 (비범위 확정) |

## 2. `.xfdl` 1급 지원

### 2-1. 확장자 등록

- `RealCodebaseAdapter.SOURCE_EXTS`에 `".xfdl"` 추가.

### 2-2. 청킹 — `indexer._chunk_xfdl()` (신규)

`.xfdl`은 XML 컨테이너 안 `<Script>` 섹션(CDATA)에 JavaScript가 들어 있다.

1. `<Script ...><![CDATA[ ... ]]></Script>` 블록의 스크립트 텍스트를 추출한다.
2. 스크립트를 함수 경계로 분리한다 — Nexacro 관례 두 형태:
   - `this.<이름> = function(...)` (이벤트 핸들러·메서드)
   - `function <이름>(...)`
   - 경계 탐색 후 중괄호 균형 매칭으로 본문 끝을 찾는다 (`_chunk_java`와 동일 기법).
3. Script가 없거나 함수 매치가 없으면 파일 전체 1청크 폴백 (기존 규칙과 동일).
4. **레이아웃 XML부(Dataset/Grid/Component)는 청크로 만들지 않는다** — 검색 노이즈가 커서다. Dataset 컬럼 id의 활용은 비범위(§7).

### 2-3. 심볼 추출

`_extract_symbol()`에 `.xfdl` 분기 추가: `this.(\w+)\s*=\s*function` 우선, 없으면 `function (\w+)`.

### 2-4. 용어 사전·상수 인벤토리 수확 확장

`term_dict._iter_source_files()`가 두 수확기의 공용 순회자다 (`const_inventory`가 import).

- 스캔 확장자에 `.xfdl` 추가.
- `.xfdl`의 주석 규칙은 `.java`와 동일 계열(`// 한글주석`)로 처리한다 — Script 내 JS 주석에서 라벨을 수확한다.
- `const_inventory`는 순회자 공유로 자동 확장된다 — xfdl JS의 숫자 리터럴(`3000000`, `0.01` 등)이 기존 `_NUM_RE`/`_is_law_constant` 규칙으로 수확된다. **이것이 이 스펙의 실질 효용이다**: "15만원→25만원" 개정의 상수 매칭이 xfdl 한도값에 닿게 된다.

## 3. 스캔 루트 통일 (REPO_INDEX_PATHS 어휘 공유)

현재 `term_dict._iter_source_files()`는 `<root>/src` 를 하드코딩으로 우선한다 — 인덱서(`RealCodebaseAdapter.list_files()`)는 `REPO_INDEX_PATHS`를 쓰므로 **두 수확기와 인덱서가 서로 다른 범위를 본다.** xfdl은 `web/` 하위라 현행 구조로는 수확이 원천 불가.

- **결정**: `REPO_INDEX_PATHS` 설정 시 그 목록을 수확기 스캔 루트로도 사용한다. 미설정 시 기존 동작(`src` 폴백) 유지 — mock repo 회귀 방지.
- 캐시(`term_dict_cache.json` 등)는 기존 refresh 규칙 그대로 (범위 변경 시 운영자가 캐시 삭제 후 재생성 — `docs/operations/COMPANY_VALIDATION.md`에 절차 추가).

## 4. 인코딩 정책 (계약)

모든 대상 파일 읽기는 아래 순서를 따른다. **XML 선언의 `encoding=` 속성은 신뢰하지 않는다** (실측: 선언·실제 불일치 파일 존재).

```
1. utf-8-sig   ← BOM 유무 모두 처리 (BOM은 제거됨). xfdl(UTF-8+BOM)·일반 UTF-8 커버
2. cp949       ← EUC-KR 확장. 엄격 euc-kr이 아니라 cp949로 (실측: euc-kr로는 일부 파일 변환 실패)
3. utf-8 (errors="replace")  ← 최후 폴백
```

- 적용 지점: `RealCodebaseAdapter.read_file()`, `term_dict._read()` — 현재 첫 시도가 `utf-8`(BOM 잔존)인 것을 `utf-8-sig`로 교체. 두 구현의 중복은 유지한다(이번 이슈에서 공용 유틸 통합은 하지 않음 — 변경 반경 최소화).
- `apply_patch()`의 인코딩·개행 보존 로직도 동일하게 `utf-8-sig` 우선으로 정렬한다 (BOM 파일 왕복 시 BOM 보존: 디코딩 시 BOM 감지 → 인코딩 시 재부착).

## 5. 제외 목록·화이트리스트 재설계

### 5-1. `EXCLUDED_DIRS` (블랙리스트 — 안전망)

- `CodebaseAdapter.EXCLUDED_DIRS`에 `"classes"` 추가 — eHR 빌드 산출물 루트. exploded WAR 4중 중첩이 이 아래 있다.
- **CLAUDE.md 규칙 준수**: `app/golden.py::_IGNORE`에도 `"classes"`를 함께 추가한다 (같은 어휘 유지).
- `_is_excluded()`를 **repo root 상대 경로** 기준으로 판정하도록 수정 — 현재는 절대경로 `path.parts`를 검사해, repo 루트 경로에 제외어가 포함되면(예: `H:\build\eHR`) 저장소 전체가 제외되는 잠재 결함이 있다. `symbol_index` 등 adapter 경유 소비자는 자동으로 혜택.

### 5-2. `REPO_INDEX_PATHS` (화이트리스트 — 1차 방어)

블랙리스트만으로는 `web/eHR/`(컴파일 산출물), `web/nexacro/nexacro14lib/`(벤더 런타임), `web/UbiService/`(리포팅 엔진·로그) 같은 경로별 산출물을 막기 어렵다 — 디렉토리 이름이 일반적이지 않아 base 공용 어휘에 넣기 부적절. **화이트리스트를 1차 방어로 삼는다.**

eHR 권장값 (`.env.example`과 `docs/operations/COMPANY_VALIDATION.md`에 문서화):

```
REPO_INDEX_PATHS=src/hr/pay,src/hr/tim/annl,src/hr/ins,src/hr/sta/pay,src/hr/sqlmap,web/nexacro/solution/pay,web/nexacro/solution/tim,web/nexacro/solution/ins
```

- 도메인 커버: 급여·연말정산(pay), 연차(tim/annl), 4대보험(ins), 급여통계(sta/pay), 전체 SQL(sqlmap), 대응 화면(nexacro/solution 3종).
- 인덱싱 규모 추정: 약 2,000~3,000 파일 (xfdl 단일 최대 폴더 `pay/ref/com` 282개 포함).
- 범위는 운영자가 조정 가능 — 스펙은 "화이트리스트가 기본 운용 방식"이라는 원칙과 권장 시작값만 고정한다.

## 6. 보안 — `build.xml` 평문 자격증명

- `H:\workspace\eHR\build.xml`에 FTP 서버 IP·계정·평문 비밀번호, 주석 블록에 SSH 평문 비밀번호가 존재한다 (2026-08-14 확인).
- 현재 상태: 이 PC의 `chroma_data/` 비어 있음(미인덱싱) + 화이트리스트가 repo 루트 파일을 배제 → **유출 전**.
- 방어: §5-2 화이트리스트가 1차 차단. 수확기(§3)도 화이트리스트를 공유하므로 동일 차단.
- 조치 권고 (regtax-monitor 범위 밖, eHR 소유자 몫): 자격증명 외부화(`build.properties` 분리 + 형상 제외), 사내 정보보호팀(security@pantechcni.com) 통보.

## 7. 비범위

- xfdl Dataset/레이아웃 구조의 인덱싱·컬럼 바인딩 추적 (향후 #0020 계열에서 검토).
- `symbol_index.py`의 xfdl 심볼·관계 확장 (#0020 범위).
- `.js`(7,145개) 청킹 — eHR의 .js는 대부분 벤더·컴파일 산출물이라 화이트리스트로 배제되는 것이 맞다.
- JSP 청킹 (업무 로직 없음 실측).
- "DB 데이터 개정" 판정 라우팅 (별도 이슈 C).
- 인코딩 읽기 유틸의 단일화(공용 모듈 추출).

## 8. 수용 기준 (AC)

1. mock fixture에 `.xfdl` 샘플 추가 (Script 함수 2개 + `// 한글주석` + 한도 상수 + UTF-8 BOM) →
   - `_chunk_xfdl` 함수 단위 분리 테스트
   - `_extract_symbol` 함수명 추출 테스트
   - `term_dict`/`const_inventory`가 xfdl에서 라벨·상수를 수확하는 테스트
2. 인코딩 3종 테스트: UTF-8+BOM(.xfdl) / CP949(.xml) / 선언·실제 불일치(.xml 선언 EUC-KR·실제 UTF-8) — 전부 한글 무손실 판독.
3. `classes/` 하위 파일이 `list_files()`·수확기·`golden` 스크래치 복사에서 모두 제외되는 테스트.
4. repo 루트 경로에 제외어가 포함된 경우(`.../build/repo`) 저장소가 통째로 제외되지 않는 회귀 테스트.
5. `REPO_INDEX_PATHS` 설정 시 수확기 스캔 루트가 그 목록을 따르고, 미설정 시 기존 `src` 폴백이 유지되는 테스트.
6. `bash scripts/verify.sh full` green.

## 9. 보안 검토 사전 표시

- 외부 입력 지점: 없음 (대상 저장소는 로컬 파일시스템, adapter 경유).
- 민감 데이터: eHR 소스 자체가 반출 금지 대상 — 기존 원칙 유지 (local 모드에서 외부 전송 없음, 캐시 파일 gitignore). `build.xml` 자격증명은 §6.
- 새 의존성: 없음 (정규식·표준 라이브러리만).
