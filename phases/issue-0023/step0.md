# Step 0: amendment-parser

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 특히 "수집·감지의 재현율이 초안 품질보다 우선한다")
- `/docs/specifications/COLLECTION_SEMANTICS_SPEC.md` (**이 작업의 계약** — §3 개정문 파서 전체)
- `/docs/architecture/ADR.md`의 **ADR-014** (이 작업의 근거와 트레이드오프)
- `/app/domain/changes/normalization.py` (**스타일 본보기** — frozen dataclass + 순수 함수 + 모듈 상수 정규식 패턴. 이 파일과 같은 결로 작성한다. 이 step에서 이 파일을 수정하지는 않는다)
- `/app/collector/law_api.py`의 `fetch_detail()` (개정문이 API에서 어떤 형태로 오는지 — `개정문내용`은 문자열 리스트다)

## 배경 (자기완결 요약)

현재 `fetch_detail()`은 `before_text`←개정문, `after_text`←제개정이유를 넣는데, `ChangeNormalizer`는 이 둘을 "개정 전/후 조문"으로 간주해 값 델타를 계산한다. 실 API에서는 서로 다른 종류의 문서를 비교하게 되어 감지 신호가 왜곡된다. 해결책: 개정문 자체가 공식 before→after 진술문("제59조의2제1항 중 '연 15만원'을 '연 25만원'으로 한다")이므로, 이를 파싱해 올바른 쌍을 파생한다. 이 step은 그 **파서만** 만든다 — law_api/DB/라우트 배선은 이후 step이다.

## 작업

`app/domain/changes/amendment.py`를 신규 생성한다.

### 1) 자료구조와 시그니처

```python
@dataclass(frozen=True)
class AmendmentEdit:
    article_ref: str      # "제59조의2제1항" — 없으면 ""
    kind: str             # "replace" | "rewrite" | "insert" | "delete"
    before_fragment: str  # 개정 전 문구 (rewrite/insert는 "")
    after_fragment: str   # 개정 후 문구 (delete는 "")


def parse_amendment(text: str) -> list[AmendmentEdit]: ...
def derive_before_after(edits: list[AmendmentEdit]) -> tuple[str, str]: ...
```

### 2) 인식 문형 (스펙 §3-2 — 우선순위 순)

| # | kind | 문형 | 예 |
|---|---|---|---|
| P1 | replace | `<위치> 중 "A"를(을) "B"로(으로) 한다/하고/하며` | `제59조의2제1항 중 "연 15만원"을 "연 25만원"으로 한다` |
| P2 | replace | 따옴표 없는 치환: `<위치> 중 A를 B로 한다` (A·B가 수치/짧은 명사구) | `제55조 중 100분의 6을 100분의 7로 한다` |
| P3 | rewrite | `<위치>를(을) 다음과 같이 한다.` + 후속 본문 | 조·항 전문 교체 |
| P4 | insert | `<위치>를(을) 다음과 같이 신설한다` / `<위치>에 ...를 신설한다` | 신설 |
| P5 | delete | `<위치>를(을) 삭제한다` | 삭제 |

- `<위치>` = `제N조(의M)(제K항)(제L호)(목)` 조합. 기존 `law_api._extract_article_no`의 정규식(`제\d+조(?:의\d+)?(?:제\d+항)?`)을 확장 출발점으로 삼되, 이 모듈 안에 자체 정의한다 (collector에 의존하지 마라 — 계층 방향은 collector→domain이다).
- 한 개정문에 여러 edit이 나오는 것이 정상이다 (조문별·항별 나열). 개정문은 여러 줄이며, 줄 단위 순회 + 문형 매칭이 무난한 접근이다 (P3의 "후속 본문"은 다음 문형 매치 전까지의 줄들).
- 따옴표는 실제 API에서 `"…"`, `'…'`, `“…”`(U+201C/201D) 변형이 온다 — 전부 허용하라.

### 3) derive_before_after 규칙 (스펙 §3-3, §3-4)

- `before_text` = 각 edit의 `article_ref + " " + before_fragment`를 개행 join (replace·delete 대상. rewrite/insert 제외)
- `after_text` = 각 edit의 `article_ref + " " + after_fragment`를 개행 join (replace·rewrite·insert 대상. delete 제외)
- article_ref를 양쪽에 남기는 이유: `ChangeNormalizer._text_delta`(토큰 diff)가 위치 문맥을 공통 토큰으로 정렬해 값 델타가 같은 조문끼리 대응하게 된다.
- **폴백**: edits가 빈 리스트면 `("", 원문 전문)`을 반환한다 — 전부 "추가" 해석. 이때 원문은 derive의 두 번째 인자가 아니라, 호출자가 폴백을 조립할 수 있게 `derive_before_after(edits, fallback_text="")` 형태로 받아라 (edits 비면 `("", fallback_text)` 반환).

### 4) 안전 규칙

- 입력 길이 상한 20,000자 — 넘치면 앞부분만 파싱한다 (`law_api._collect_text`와 같은 상한).
- 정규식은 catastrophic backtracking을 피하라: 중첩 수량자(`(a+)+` 류) 금지, 따옴표 내부는 `[^"]*` 식 부정 문자클래스로.
- 순수 함수 — 파일/네트워크/DB/LLM 접근 금지.

## 테스트

`tests/test_amendment_parser.py` 신규. 최소 케이스:

1. P1 따옴표 치환 (곧은따옴표 + 둥근따옴표 변형)
2. P2 무따옴표 치환 (`100분의 6을 100분의 7로 한다`)
3. P3 전문개정 + 후속 본문 수집
4. P4 신설 / P5 삭제
5. 복수 edit (서로 다른 조문 2건 이상) — 순서 보존
6. 파싱 0건 폴백 — `derive_before_after([], fallback_text=원문)` == `("", 원문)`
7. **실채록 문형 샘플 2건 이상**: 실제 법령 개정문 문체를 따르는 다줄 텍스트를 테스트 파일 안에 문자열 상수로 넣어라 (API 호출 금지). 예: 소득세법류 "제59조의2제1항 중 …을 …으로 하고, 같은 조 제2항 중 …" 연결 문형.
8. `derive_before_after` 결과를 `ChangeNormalizer().normalize(before, after)`에 넣었을 때 money delta가 기대 방향(15만→25만)으로 잡히는 통합 케이스 1건 (normalization은 read-only 사용).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `app/domain/changes/`에 위치하는가? (ARCHITECTURE 계층 — domain은 최하층, 표준 라이브러리만)
   - CLAUDE.md CRITICAL 위반 없는가? (외부 전송 없음, seam 우회 없음)
   - normalization.py의 코드 결(주석 밀도·네이밍)과 맞는가?
3. 결과에 따라 `phases/issue-0023/index.json`의 step 0을 업데이트한다 (completed+summary / error / blocked).

## 금지사항

- `app/collector/law_api.py`, `app/domain/changes/normalization.py`, `app/main.py`를 수정하지 마라. 이유: 이 step의 scope는 파서 신규 생성뿐이며, 배선은 step 1~3이 담당한다.
- 신규 의존성을 추가하지 마라. 이유: 표준 라이브러리 `re`·`dataclasses`로 충분하고, requirements 추가는 이 설계 범위 밖이다.
- LLM으로 파싱을 보조하지 마라. 이유: 파서는 결정론·재현 가능해야 하며, 스펙이 순수 함수를 계약으로 못 박았다.
- 기존 테스트를 깨뜨리지 마라.
