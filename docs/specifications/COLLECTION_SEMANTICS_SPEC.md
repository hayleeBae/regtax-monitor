# COLLECTION_SEMANTICS_SPEC — 수집 필드 의미 정정과 개정문 파싱

- 상태: **Draft (승인 대기)**
- 제안 이슈: #0023
- 관련: ADR-014, `CHANGE_CLASSIFICATION_SPEC.md`(정규화·분류 소비자), `EVALUATION_SPEC.md`

## 1. 배경 — 무엇이 잘못되어 있나

`LawApiClient.fetch_detail()`(`app/collector/law_api.py`)은 현재:

| 필드 | 현재 담기는 것 | 하위 단계가 가정하는 것 |
|---|---|---|
| `before_text` | **개정문** ("제55조 중 'X'를 'Y'로 한다") | 개정 **전** 조문 |
| `after_text` | **제개정이유** (개정 배경·주요내용) | 개정 **후** 조문 |

`ChangeNormalizer.normalize(before, after)`는 두 텍스트의 값 목록을 위치 정렬(`zip_longest`)로 비교해 money/rate/date 델타를 만든다. 실 API 데이터에서는 "개정문 vs 제개정이유"라는 **서로 다른 종류의 문서**를 비교하게 되어 델타가 무의미해지고, 그 위의 분류(`RuleChangeClassifier`)·정책 게이트(`automation.py`)·상수 매칭 질의가 연쇄로 왜곡된다.

mock 데이터(`_mock_results`)만 진짜 개정 전/후 쌍이라 **집 환경에서는 이 결함이 관측되지 않는다.** 이 프로젝트에서 가장 비싼 실패는 "개정을 놓친 것"인데, 이 결함은 감지 신호 자체를 조용히 무너뜨린다.

## 2. 필드 의미 재정의 (계약)

`LawChange`와 `fetch_detail()` 반환은 아래 4개 필드를 갖는다:

| 필드 | 의미 | 출처 |
|---|---|---|
| `amendment_text` | 개정문 원문 (신규) | law API `개정문내용` |
| `reason_text` | 제개정이유 원문 (신규) | law API `제개정이유내용` |
| `before_text` | **개정 전** 텍스트 발췌 | 개정문 파싱으로 **파생** (§3) |
| `after_text` | **개정 후** 텍스트 발췌 | 개정문 파싱으로 **파생** (§3) |

- `ChangeNormalizer` 입력은 파생된 `before_text`/`after_text`만 사용한다.
- LLM 분석(analyze) 프롬프트는 `amendment_text` + `reason_text`를 컨텍스트로 사용한다 — 제개정이유는 요약·영향 판단에 유용하지만 값 델타 계산에는 절대 넣지 않는다.
- 검색 질의(map) 구성도 기존과 동일하게 법령명·조문·요약·before/after를 쓰되, before/after가 이제 진짜 개정 전/후 발췌라는 점만 달라진다.

## 3. 개정문 파서 — `app/domain/changes/amendment.py` (신규)

한국 법령 개정문은 정형 문형을 따른다. 파서는 **순수 함수**(IO·LLM 없음)로 다음을 추출한다.

### 3-1. 출력 계약

```python
@dataclass(frozen=True)
class AmendmentEdit:
    article_ref: str      # "제59조의2제1항" (없으면 "")
    kind: str             # replace | rewrite | insert | delete
    before_fragment: str  # 개정 전 문구 (rewrite/insert는 "")
    after_fragment: str   # 개정 후 문구 (delete는 "")

def parse_amendment(text: str) -> list[AmendmentEdit]: ...
def derive_before_after(edits: list[AmendmentEdit]) -> tuple[str, str]: ...
```

### 3-2. 인식 문형 (우선순위 순)

| # | kind | 문형 | 예 |
|---|---|---|---|
| P1 | replace | `<위치> 중 "A"를(을) "B"로(으로) 한다/하고/하며` | `제59조의2제1항 중 "연 15만원"을 "연 25만원"으로 한다` |
| P2 | replace | 따옴표 없는 치환: `<위치> 중 A를 B로 한다` (A·B가 짧은 명사구/수치일 때) | `제55조 중 100분의 6을 100분의 7로 한다` |
| P3 | rewrite | `<위치>를(을) 다음과 같이 한다.` + 후속 본문 | 조·항 전문 교체 |
| P4 | insert | `<위치>를(을) 다음과 같이 신설한다` / `<위치>에 ...를 신설한다` | 신설 |
| P5 | delete | `<위치>를(을) 삭제한다` | 삭제 |

- `<위치>` = `제N조(의M)(제K항)(제L호)(목)` 조합. 파서는 위치를 각 edit에 보존한다.
- 하나의 개정문에 여러 edit이 나오는 것이 정상이다 (조문별·항별 나열).
- P3의 "후속 본문"은 다음 edit 문형 시작 전까지의 텍스트다.

### 3-3. before/after 파생 규칙

- `before_text` = 각 edit의 `article_ref + " " + before_fragment` 를 개행으로 join (delete 포함, rewrite/insert 제외)
- `after_text` = 각 edit의 `article_ref + " " + after_fragment` 를 개행으로 join (rewrite/insert 본문 포함, delete 제외)
- article_ref를 양쪽에 남기는 이유: `_text_delta`(토큰 diff)가 위치 문맥을 공통 토큰으로 정렬해, 값 델타가 같은 조문끼리 대응하게 한다.

### 3-4. 폴백 (파싱 실패)

`parse_amendment()`가 0건이면:
- `before_text = ""`, `after_text = amendment_text 원문 전문`
- 정규화기는 전부 "추가"로 처리한다 — 왜곡된 짝짓기 델타보다 낫고, 값 존재 신호(상수 매칭 질의)는 유지된다.
- 이 경우를 식별할 수 있도록 분석 결과에 `amendment_parsed: bool`을 남긴다 (품질 추적용 — Provider 기여도와 같은 계열의 계측).

### 3-5. 행정규칙 (변경 없음, 의미 명문화)

`fetch_admin_rule_detail()`은 신구대조가 없어 `before_text=""`, `after_text=본문 전문`을 유지한다. 이는 "신설 공표" 의미로 §3-4 폴백과 동일한 해석이다. `amendment_text`/`reason_text`는 빈 문자열.

## 4. DB 마이그레이션 (`app/db/database.py::_migrate`)

기존 경량 ADD COLUMN 패턴을 따른다:

1. `law_change`에 `amendment_text TEXT`, `reason_text TEXT` 추가.
2. 백필(idempotent): `amendment_text IS NULL AND before_text != ''` 인 행에 대해
   `amendment_text ← before_text`, `reason_text ← after_text` 로 이관 후,
   `before_text`/`after_text`를 §3 파서로 재파생.
3. 이미 이관된 행(amendment_text NOT NULL)은 건너뛴다 — 기동마다 호출돼도 1회만 수행.

## 5. mock 데이터 갱신

`_mock_results()`/`_mock_admin_rules()`에 `amendment_text`/`reason_text`를 추가하고, mock의 `amendment_text`는 실제 개정문 문형(P1)을 따르게 바꾼다 — mock과 실 데이터의 구조 차이가 이 결함을 숨겼으므로, mock이 실 문형을 흉내 내는 것이 재발 방지책이다.

## 6. 수용 기준 (AC)

1. `parse_amendment()` 단위 테스트: P1~P5 각 문형 + 복수 edit + 파싱 실패 폴백 + 실제 채록 개정문 샘플 최소 2건 (법제처 API 실응답 발췌를 fixture로).
2. `fetch_detail()`이 4필드를 모두 반환하고, `before_text != amendment_text` (파싱 성공 시).
3. 마이그레이션 idempotent 테스트 (2회 실행 시 동일 상태).
4. 기존 mock E2E 흐름(collect→analyze→map) 회귀 없음 — `bash scripts/verify.sh full` green.
5. analyze 프롬프트에 reason_text가 포함되고 normalize 입력에는 포함되지 않음을 확인하는 테스트.

## 7. 비범위 (이번 이슈에서 하지 않음)

- 조문별 `LawChange` 분리 (1공포=1행 유지) — 열린 질문으로 이월.
- 신구법 대비(연혁 두 버전 조문 diff) API 연동 — 개정문 파싱으로 충분한지 실측 후 판단.
- `eflaw`/`thdCmp` 파이프라인 연결 (별도 이슈).
- 행정규칙 HWP 첨부 텍스트 추출.

## 8. 보안 검토 사전 표시

- 외부 입력 지점: 법제처 API 응답(개정문 텍스트)이 파서 입력이다. 파서는 정규식 기반 순수 함수로 코드 실행·파일 접근이 없어 주입 표면이 없다. 정규식은 catastrophic backtracking을 피하는 형태로 작성하고 입력 길이 상한(기존 `_collect_text` 20,000자)을 유지한다.
- 민감 데이터: 없음 (법령 텍스트는 공개 정보).
