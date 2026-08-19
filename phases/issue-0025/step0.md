# Step 0: db-registry

## 읽어야 할 파일

먼저 아래를 읽고 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 특히 보안·동작 보존)
- `/docs/specifications/DB_DATA_ROUTING_SPEC.md` (구현 계약 — §3 판정 모델, §4 domains.json 스키마, §8 보안)
- `/docs/architecture/ADR.md` (ADR-016)
- `/app/collector/registry.py` (`Domain` dataclass, `load_domains()` — 확장 대상)
- `/domains.json` (스키마 확장 대상)

## 작업

DB 데이터 개정 판정을 위한 **레지스트리**를 config/domain 레이어에 추가한다.

1. `app/collector/registry.py`:
   - `DbItem` dataclass 추가 (frozen). 필드:
     ```python
     @dataclass(frozen=True)
     class DbItem:
         law_id: str            # domains.json의 law 식별과 동일 체계
         article_pattern: str   # 조문 부분일치 키 (예: "제N조")
         item_label: str        # 일반화 라벨 (테이블명 아님)
         db_hint: str = ""      # 담당자용 일반 서술
         guidance: str = ""     # DB 갱신 안내 문구
     ```
   - `Domain` dataclass에 `db_items: list[DbItem]` 필드 추가.
   - `load_domains()`가 각 도메인의 `db_items`(JSON 배열)를 파싱해 `DbItem`으로 로드한다. **키가 없으면 빈 리스트**로 처리(회귀 안전).
   - `DbDataRegistry` 클래스(또는 동등 함수) 추가:
     ```python
     class DbDataRegistry:
         def __init__(self, domains: dict[str, Domain]): ...
         def match(self, law_id: str, article_no: str) -> DbItem | None:
             """law_id 정확 일치 AND article_pattern이 article_no에 부분일치하는
             첫 DbItem을 반환. 없으면 None. 추론하지 않는다."""
     ```

2. `domains.json`: tax·hr 도메인에 **빈** `"db_items": []`를 추가한다(스키마만 도입, 실제 항목은 담당자가 나중에 채움).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

추가 테스트(직접 작성, `tests/` 하위):
- `DbDataRegistry.match`가 law_id+article_pattern 일치 시 해당 DbItem, 불일치 시 None 반환
- `db_items` 키가 없는/빈 도메인 로드 시 `Domain.db_items == []` (기존 domains.json 동작 회귀 고정)

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크: CLAUDE.md 보안 규칙 준수, ADR-016/스펙과 일치.
3. `phases/issue-0025/index.json`의 step 0을 업데이트:
   - 성공 → `"status": "completed"`, `"summary"`에 생성/수정 파일과 `DbDataRegistry.match` 시그니처 명시
   - 3회 실패 → `"status": "error"` + `error_message`
   - 개입 필요 → `"status": "blocked"` + `blocked_reason` 후 중단

## 금지사항

- 실제 DB 테이블/컬럼명 원문(`T_PAY_TAX` 등)이나 자격증명을 코드·`domains.json`·테스트에 기재하지 마라. 이유: org 보안 규칙(DB 스키마 컬럼 일반화, eHR 내부 파생물 커밋 금지). `item_label`/`db_hint`는 일반화된 서술만.
- `article_pattern`이 빈 문자열일 때 전체 매칭시키지 마라. 이유: 조문 무관 오탐 → "개정 놓침"의 반대인 과잉 라우팅.
- "코드 매핑이 없으면 DB"라는 추론 로직을 넣지 마라. 이유: retrieval 미스를 DB 개정으로 오인하면 개정을 놓친다(스펙 §3).
- 기존 `Domain` 필드·`load_domains` 동작을 깨뜨리지 마라.
- 기존 테스트를 깨뜨리지 마라.
