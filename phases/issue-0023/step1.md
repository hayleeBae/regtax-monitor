# Step 1: fetch-detail-fields

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/specifications/COLLECTION_SEMANTICS_SPEC.md` (§2 필드 의미, §3-5 행정규칙, §5 mock 갱신)
- `/docs/architecture/ADR.md`의 **ADR-014**
- `/app/domain/changes/amendment.py` (**step 0 산출물** — `parse_amendment`, `derive_before_after` 시그니처를 그대로 사용한다)
- `/app/collector/law_api.py` 전체 (수정 대상 — 특히 `fetch_detail`, `fetch_admin_rule_detail`, `_mock_results`, `_mock_admin_rules`)

## 배경 (자기완결 요약)

step 0에서 개정문 파서가 만들어졌다. 이 step은 법제처 API 클라이언트가 그 파서를 사용해 **4필드 계약**(`amendment_text`/`reason_text` 원문 보존 + `before_text`/`after_text` 파생)을 반환하게 한다. `app/collector/law_api.py`에는 현재 전용 테스트가 없다 — HTTP와 파싱이 한 함수에 붙어 있어서다. 이 step에서 파싱을 순수 함수로 분리해 테스트 공백도 함께 해소한다.

## 작업

### 1) `app/collector/law_api.py` — 파싱 분리와 4필드 반환

```python
def _parse_law_detail(law: dict) -> dict:
    """lawService.do 응답의 '법령' dict → 4필드 + 계측.
    반환: {"article_no": str, "amendment_text": str, "reason_text": str,
           "before_text": str, "after_text": str, "amendment_parsed": bool}
    """
```

- 기존 `fetch_detail()` 내부의 `개정문내용`/`제개정이유내용` 추출 로직(리스트-안-리스트 변형 처리 포함)을 이 함수로 옮긴다.
- `amendment_text` ← 개정문, `reason_text` ← 제개정이유 (원문 보존).
- `before_text`/`after_text` ← `derive_before_after(parse_amendment(amendment_text), fallback_text=amendment_text)`.
- `amendment_parsed` ← 파싱된 edit이 1건 이상이면 True. 폴백이면 False.
- `article_no`는 기존 `_extract_article_no(amendment_text)` 유지.
- `fetch_detail()`은 HTTP 호출 후 `_parse_law_detail(law)`를 반환하는 얇은 껍데기로 만든다.

### 2) `fetch_admin_rule_detail()` — 계약 정렬

- 반환 dict에 `amendment_text: ""`, `reason_text: ""`, `amendment_parsed: False`를 추가한다. `before_text=""`/`after_text=본문`은 그대로 (스펙 §3-5 — "신설 공표" 의미).

### 3) mock 데이터 갱신 (스펙 §5)

- `_mock_results()`의 각 항목에 `amendment_text`(**실제 개정문 P1 문형으로 작성** — 예: `제55조제1항 중 "100분의 6"을 "100분의 7"로 한다`)과 `reason_text`(한 줄 개정 이유)를 추가한다.
- mock의 기존 `before_text`/`after_text`는 amendment_text를 파서에 넣었을 때 파생되는 값과 **의미가 일치**하도록 조정한다 (문자 그대로 같을 필요는 없으나 같은 수치 델타가 나와야 한다).
- `_mock_admin_rules()`에도 두 필드(빈 문자열)를 추가한다.
- mock이 실 문형을 흉내 내는 것이 이 결함의 재발 방지책이다 — mock과 실 데이터의 구조 차이가 결함을 숨겼었다.

### 4) 검색 결과 dict (`_fetch_one_query`, `search_admin_rules`)

- 목록 검색 결과 항목에도 `amendment_text: ""`, `reason_text: ""` 키를 추가해 키 집합을 일관시킨다 (상세 조회 전 상태).

## 테스트

`tests/test_law_api_parsing.py` 신규 — **HTTP 호출 없이** 응답 dict fixture로 검증:

1. `_parse_law_detail`: 개정문·제개정이유가 있는 law dict → 4필드 + `amendment_parsed=True`, `before_text != amendment_text`.
2. 리스트-안-리스트 응답 변형(`개정문내용: [[...]]`) 처리.
3. 개정문이 비정형(파서 미인식)인 law dict → `before_text == ""`, `after_text == amendment_text`, `amendment_parsed=False`.
4. 개정문 자체가 없는 law dict → 4필드 모두 빈 문자열, `amendment_parsed=False`.
5. mock 데이터 계약: `_mock_results()` 각 항목에 `amendment_text`/`reason_text` 존재, `parse_amendment(amendment_text)`가 1건 이상 반환.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 계층 방향이 collector→domain 단방향인가? (domain이 collector를 import하면 안 된다)
   - CLAUDE.md CRITICAL 위반 없는가?
3. `phases/issue-0023/index.json`의 step 1을 업데이트한다.

## 금지사항

- `app/main.py`를 수정하지 마라. 이유: 라우트 배선은 step 3의 scope다. `fetch_detail` 반환에 키가 추가되는 것은 하위 호환이다(기존 소비자는 기존 키만 읽는다).
- `app/db/models.py`를 수정하지 마라. 이유: DB 반영은 step 2다.
- 테스트에서 실제 법제처 API를 호출하지 마라. 이유: 네트워크 의존 테스트는 회사망/집 환경에서 재현성이 없고, OC 키가 필요하다.
- 기존 `before_text`/`after_text` 키를 제거하거나 이름을 바꾸지 마라. 이유: DB 저장·분석·검색 질의가 이 키를 소비한다 — 의미만 바뀌고 키는 유지된다.
