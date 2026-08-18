# Step 1: excluded-dirs

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL — "제외 목록은 `RealCodebaseAdapter.EXCLUDED_DIRS`이며 `app/golden.py::_IGNORE`와 같은 어휘다 — 한쪽을 고치면 다른 쪽도 함께 볼 것")
- `/docs/specifications/EHR_INDEXING_SPEC.md` §5-1
- `/docs/architecture/ADR.md`의 **ADR-015**
- `/app/codebase/base.py` (`EXCLUDED_DIRS`, `_is_excluded()` — 수정 대상. docstring에 이 목록의 존재 이유 3가지가 적혀 있다)
- `/app/codebase/real_adapter.py` (`_is_indexable`, `list_files` — `_is_excluded` 호출부)
- `/app/codebase/mock_adapter.py` (`_is_excluded`를 쓰는지 확인 — 어댑터 공통 규칙이므로 양쪽 동작이 같아야 한다)
- `/app/golden.py` (`_IGNORE` — 동기화 대상)
- `/app/embedding/symbol_index.py` 상단 주석 (adapter 경유 소비자 — `EXCLUDED_DIRS` 혜택을 상속받는 구조 확인용, 수정 금지)

## 배경 (자기완결 요약)

2026-08-14 eHR 실측: `classes/` 디렉토리가 1.7GB/32,398파일이며 exploded WAR가 자기 자신을 4중 재귀 중첩한다(`classes\artifacts\eHR_war_exploded\WEB-INF\classes\classes\...`). 무제한 인덱싱 시 인덱스의 91%가 중복본이 된다. 또한 현행 `_is_excluded()`는 **절대경로** `path.parts`를 검사하므로, repo 루트 경로 자체에 제외어가 포함되면(예: `H:\build\eHR`) 저장소 전체가 통째로 제외되는 잠재 결함이 있다.

## 작업

### 1) `app/codebase/base.py`

- `EXCLUDED_DIRS`에 `"classes"` 추가. docstring에 추가 근거를 한 줄 덧붙여라 (eHR `classes/` 1.7GB 4중 중첩 exploded WAR — 2026-08-14 실측).
- `_is_excluded()`를 **repo root 상대 경로 기준**으로 판정하도록 수정한다. 시그니처 설계는 재량이되 다음을 만족하라:
  - 어댑터가 root를 알고 있으면 root 아래 상대 부분의 구성요소만 검사한다.
  - root를 모르는 호출 문맥(있다면)에서는 기존 동작(전체 parts 검사)으로 폴백해도 된다 — 단 회귀 테스트가 지나가야 한다.
  - 구성요소 정확 일치 원칙(부분일치 금지)은 유지한다.

### 2) `app/golden.py`

- `_IGNORE`(스크래치 복사 제외 목록)에 `"classes"`를 추가한다. **CLAUDE.md CRITICAL 규칙** — 두 목록은 같은 어휘를 유지해야 한다. 형태가 다르므로(콜러블 vs 집합) 값만 맞춘다.

### 3) 두 목록 동기화 가드 (권장)

- `EXCLUDED_DIRS`와 `golden._IGNORE`의 어휘가 어긋나면 실패하는 테스트를 추가하라 (예: `_IGNORE` 패턴 집합이 `EXCLUDED_DIRS`를 포함하는지). 이런 테스트가 이미 있으면 갱신만 한다. 이유: "한쪽을 고치면 다른 쪽도"를 사람 기억이 아니라 테스트가 강제하게 만든다.

## 테스트

`tests/test_excluded_dirs.py` 신규 (또는 기존 어댑터 테스트 파일 확장 — `tests/test_real_adapter_listing.py`가 있다면 그쪽 관례를 따르라):

1. tmp_path에 `classes/foo/Bar.java`와 `src/Baz.java`를 만들고 `RealCodebaseAdapter.list_files()`가 `src/Baz.java`만 반환.
2. **루트 경로 함정 회귀**: repo root가 `.../build/repo`(경로에 제외어 포함)여도 `repo/src/*.java`가 제외되지 않음.
3. 중첩 산출물: `classes/artifacts/x_war_exploded/WEB-INF/classes/src/A.java` 제외 확인.
4. golden 동기화 가드 테스트 (위 3항).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - CLAUDE.md CRITICAL(두 목록 동기) 준수?
   - mock adapter 경로 회귀 없음?
3. `phases/issue-0024/index.json`의 step 1을 업데이트한다.

## 금지사항

- `EXCLUDED_DIRS`에 `"eHR"`, `"web"`, `"nexacro14lib"` 같은 경로별 이름을 넣지 마라. 이유: base 공용 어휘는 일반적 산출물 이름만 담는다 — 경로별 산출물은 `REPO_INDEX_PATHS` 화이트리스트가 1차 방어다(스펙 §5-2). 특히 `"eHR"`은 repo 루트 디렉토리명과 충돌해 저장소 전체를 제외시킬 수 있다.
- `app/embedding/indexer.py`, `term_dict.py`를 수정하지 마라. 이유: step 2·3의 scope다.
- 실제 eHR 저장소(`H:\workspace\eHR`)를 읽거나 수정하는 테스트를 만들지 마라. 이유: 테스트는 tmp_path 픽스처로 자기완결이어야 하고, 실 repo 의존 테스트는 집 환경에서 깨진다.
- 기존 테스트를 깨뜨리지 마라.
