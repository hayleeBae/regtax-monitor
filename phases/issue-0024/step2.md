# Step 2: xfdl-chunker

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/specifications/EHR_INDEXING_SPEC.md` §2 (**이 작업의 계약**)
- `/docs/architecture/ADR.md`의 **ADR-015**
- `/app/embedding/indexer.py` (`_chunk` dispatch, `_chunk_java`의 **중괄호 균형 매칭** — 이 기법을 재사용한다, `_chunk_xml`, `_extract_symbol`)
- `/app/codebase/real_adapter.py` (`SOURCE_EXTS` — 수정 대상)

## 배경 (자기완결 요약)

eHR의 UI는 JSP가 아니라 **Nexacro**다(`web/nexacro/solution/**/*.xfdl` 1,301개). `.xfdl`은 XML 컨테이너 안 `<Script>` 섹션(CDATA)에 JavaScript가 들어 있고, **세법 한도값이 이 JS에 하드코딩**되어 있다 — 실측 예: 직무발명보상금 비과세 한도(`PayRefCom003.xfdl` 669~707행, `base_year` 분기로 300만→500만→700만원), 지방소득세 절사(`PayPayCom955.xfdl:712`, `Math.floor(tax_amt * 0.01) * 10`). 현재 `.xfdl`은 `SOURCE_EXTS`에 없어 인덱싱이 0건이다 — 수치 개정의 핵심 patch 대상이 검색에 안 잡히는 재현율 공백이며, 이 step이 청킹까지 뚫는다 (용어·상수 수확은 step 3).

실제 xfdl Script 형태 (테스트 fixture 작성 시 이 모양을 따르라):

```xml
<?xml version="1.0" encoding="utf-8"?>
<FDL version="1.5">
  <Form id="PayRefCom003" ...>
    <Layout>...</Layout>
    <Script type="xscript5.1"><![CDATA[
this.fn_calcUntaxLimit = function(base_year)
{
    var job_invention_untax_amt_limit = 3000000 ;  // 직무발명보상금 비과세 한도
    if (base_year >= "2024") {
        job_invention_untax_amt_limit = 7000000 ;
    }
    return job_invention_untax_amt_limit;
};

this.PayRefCom003_onload = function(obj,e)
{
    this.fn_calcUntaxLimit("2026");
};
]]></Script>
  </Form>
</FDL>
```

## 작업

### 1) `app/codebase/real_adapter.py`

- `SOURCE_EXTS`에 `".xfdl"` 추가.

### 2) `app/embedding/indexer.py` — `_chunk_xfdl()` 신규 + dispatch 등록

```python
def _chunk_xfdl(text: str) -> list[str]:
    """Nexacro xfdl의 <Script> CDATA를 추출해 함수 단위로 분리.
    Script가 없거나 함수 매치가 없으면 파일 전체 반환."""
```

- `_chunk()`에 `.xfdl` 분기 추가 (`.xml`보다 **먼저** 볼 필요는 없다 — suffix가 `.xfdl`이라 충돌 없음).
- 구현 규칙:
  1. `<Script ...><![CDATA[ ... ]]></Script>` 블록(복수 가능)에서 스크립트 텍스트를 추출한다. CDATA 없이 이스케이프된 변형이 있으면 관대하게 처리하되 과투자하지 마라.
  2. 스크립트를 함수 경계로 분리: `this.<이름> = function(...)`과 `function <이름>(...)` 두 형태. 경계 이후 본문 끝은 `_chunk_java`처럼 중괄호 깊이 추적으로 찾는다. **주의**: 위 예시처럼 여는 중괄호가 함수 선언 다음 줄에 오는 스타일이 흔하다 — `_chunk_java`의 `text.index("{", start)` 방식이면 자연 처리된다.
  3. Script가 없거나 함수 매치가 없으면 `[text]` 폴백 (기존 청커들과 동일 규칙).
  4. **레이아웃 XML부(Layout/Dataset/Grid 등)는 청크로 만들지 마라** — 검색 노이즈다 (스펙 §2-2). Script만 청킹한다.

### 3) `app/embedding/indexer.py` — `_extract_symbol()` 확장

- `.xfdl` 분기: `this.(\w+)\s*=\s*function` 우선, 없으면 `function\s+(\w+)`. 매치 없으면 `""`.

## 테스트

`tests/test_xfdl_chunker.py` 신규:

1. 위 형태의 xfdl 텍스트 → 함수 2개가 각각 별도 청크로 분리되고, 한도 상수(`3000000`)가 첫 청크에 포함.
2. 청크에 `<Layout>` 내용이 포함되지 않음.
3. `_extract_symbol("x.xfdl", 청크)` == `"fn_calcUntaxLimit"`.
4. Script 없는 xfdl(레이아웃만) → 파일 전체 1청크 폴백.
5. Script 2개 블록인 파일 → 두 블록의 함수가 모두 수집됨.
6. `RealCodebaseAdapter._is_indexable`이 `.xfdl`을 대상에 포함 (tmp_path 픽스처).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 청킹 로직이 `indexer.py` 안에 있고(기존 dispatch 구조 유지) 새 모듈을 만들지 않았는가?
   - 기존 `.java`/`.xml`/`.sql`/`.py`/`.kt` 청킹 회귀 없음?
3. `phases/issue-0024/index.json`의 step 2를 업데이트한다.

## 금지사항

- XML 파서 라이브러리(lxml 등)를 추가하지 마라. 이유: 표준 `re`로 충분하고(기존 `_chunk_xml`도 정규식), 의존성 추가는 설계 범위 밖이다.
- `term_dict.py`/`const_inventory.py`를 수정하지 마라. 이유: 수확 확장은 step 3의 scope다.
- `symbol_index.py`를 확장하지 마라. 이유: xfdl 심볼·관계는 #0020 계열로 명시적 이월(스펙 §7).
- 실서버 인덱싱(임베딩 모델 로드·ChromaDB)을 테스트에서 트리거하지 마라. 이유: CLAUDE.md — 무거운 의존성 금지. `_chunk_xfdl`은 순수 함수라 텍스트만으로 검증된다.
- 기존 테스트를 깨뜨리지 마라.
