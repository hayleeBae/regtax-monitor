# Step 3: harvest-scan-roots

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (도메인 컨텍스트 — 용어 사전·상수 인벤토리가 왜 존재하는지)
- `/docs/specifications/EHR_INDEXING_SPEC.md` §2-4, §3 (**이 작업의 계약**)
- `/docs/architecture/ADR.md`의 **ADR-015**
- `/app/embedding/term_dict.py` (`_iter_source_files`, `harvest`, `_JAVA_LINE`/`_SQL_LINE` 패턴 — 수정 대상)
- `/app/embedding/const_inventory.py` (`harvest` — `term_dict._iter_source_files`/`_read`를 import함. 순회자 공유 구조 확인)
- `/config.py` (`repo_index_paths` 설정)
- `/app/codebase/real_adapter.py`의 `list_files()` (REPO_INDEX_PATHS를 스캔 루트로 쓰는 기존 예 — 같은 해석을 따른다)

## 배경 (자기완결 요약)

`term_dict._iter_source_files()`는 현재 `<root>/src`를 하드코딩으로 우선하고 확장자도 `.xml/.sql/.java`뿐이다. 이 순회자를 `const_inventory`도 공유한다. 결과: **xfdl(웹 하위)의 한도 상수와 한글 주석이 두 수확기에 원천적으로 안 잡힌다.** step 2에서 인덱서가 xfdl을 청킹하게 됐으니, 이 step은 수확기가 같은 범위를 보게 한다 — "15만원→25만원" 개정의 상수 매칭이 xfdl 한도값(`3000000` 등)에 닿는 것이 실질 효용이다.

## 작업

### 1) `app/embedding/term_dict.py` — `_iter_source_files()` 스캔 루트 통일

- `settings.repo_index_paths`가 설정돼 있으면 그 목록(쉼표 구분, root 상대)을 스캔 루트로 사용한다 — `RealCodebaseAdapter.list_files()`와 같은 해석.
- **미설정 시 기존 동작 유지**: `<root>/src` 있으면 src, 없으면 root (mock repo 회귀 방지 — 스펙 §3).
- 존재하지 않는 경로는 조용히 건너뛴다 (환경 간 차이 흡수).

### 2) `term_dict.py` — `.xfdl` 수확 확장

- `_iter_source_files`의 확장자에 `.xfdl` 추가.
- `harvest()`에서 `.xfdl`은 `.java`와 같은 계열(`// 한글주석`)로 처리한다 — Script 내 JS 주석에서 라벨을 수확한다 (스펙 §2-4). 기존 `_JAVA_LINE` 패턴이 그대로 맞는지 확인하고, suffix 분기만 추가하라.
- 파일 우선순위 함수(`_priority` 등 — VO(.java) → 현행 XML → 연도 XML)가 있으면 `.xfdl`을 XML과 같은 취급으로 둔다 (재량이되 VO보다 앞세우지 마라 — VO 선언부가 매핑·초안에 필수라 항상 앞이어야 한다는 기존 주석을 존중).

### 3) `const_inventory.py` — 자동 확장 확인

- 순회자 공유로 자동 확장된다. `const_inventory.py` 자체는 **수정하지 않는 것이 기본**이다. 단, xfdl JS의 숫자 리터럴(`3000000`, `7000000`, `0.01`)이 기존 `_NUM_RE`/`_is_law_constant` 규칙으로 수확되는지 테스트로 증명하라.

### 4) 캐시 주의

- `term_dict_cache.json`/`term_loc_cache.json`/`const_inventory_cache.json`은 기존 refresh 규칙 그대로 둔다. 스캔 범위가 바뀌므로 운영자는 캐시 삭제 후 재생성해야 한다 — 그 절차 문서화는 step 4의 몫이다 (이 step에서 문서 수정 금지).

## 테스트

`tests/test_harvest_scan_roots.py` 신규 (tmp_path 픽스처, 실제 eHR 접근 금지):

1. **스캔 루트**: `repo_index_paths="src/a,web/n"` 설정(monkeypatch) 시 두 루트만 순회하고 `src/b`는 안 봄. 미설정 시 `src` 폴백.
2. **xfdl 라벨 수확**: `n0200` 코드와 `// 자녀세액공제` 주석이 있는 xfdl → `harvest()` 결과에 라벨 등장.
3. **xfdl 상수 수확**: `var limit = 3000000 ;` 있는 xfdl → `const_inventory.harvest()` 인벤토리에 `"3000000"` 키 + 해당 파일 위치.
4. **존재하지 않는 인덱스 경로**는 오류 없이 건너뜀.
5. 기존 `.java`/`.xml` 수확 회귀 없음 (기존 테스트가 있으면 그대로 green이어야 한다).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 설정은 `config.Settings` 경유인가? (환경변수 직접 읽기 금지)
   - `const_inventory.py` 수정이 정말 필요했는가? (기본은 무수정)
3. `phases/issue-0024/index.json`의 step 3을 업데이트한다.

## 금지사항

- `settings.repo_index_paths`의 해석을 `list_files()`와 다르게 만들지 마라 (예: glob 지원 추가). 이유: 인덱서와 수확기가 **같은 범위**를 보는 것이 이 step의 목적이다 — 어휘가 갈라지면 새 격차가 생긴다.
- 캐시 파일을 커밋하지 마라. 이유: eHR 내부 파생물 — 반출 금지 (CLAUDE.md).
- 실제 eHR 저장소를 스캔하는 테스트를 만들지 마라. 이유: 집 환경에서 깨지고, 테스트는 tmp_path로 자기완결이어야 한다.
- docs·`.env.example`을 수정하지 마라. 이유: step 4의 scope다.
- 기존 테스트를 깨뜨리지 마라.
