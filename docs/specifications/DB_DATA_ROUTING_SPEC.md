# DB Data Revision Routing Specification (제안 이슈 #0025)

> 상태: Draft (설계 승인 2026-08-19). 구현 계약 문서.
> 관련: EHR_INDEXING_SPEC §7(비범위—별도 이슈 C), CHANGE_CLASSIFICATION_SPEC §9, ADR-016, ADR-007(domains.json).

## 1. 목적

소득세율표·4대보험요율처럼 eHR에서 **코드가 아니라 DB 데이터**로 관리되는 항목의 법령 개정을,
코드 patch 초안 대상에서 분리해 **"DB 갱신 안내"**로 라우팅한다. 개정 자체는 실재하므로 놓치지 않되
(재현율 우선), 코드 patch가 무의미한 항목에 대해 잘못된 초안을 만들거나 근거 없이 차단하지 않는다.

## 2. 범위 / 비범위

**범위**: 판정(레지스트리 매칭), 라우팅 결정값 추가, DB 갱신 안내 산출, 정책/제안 레이어 통합, domains.json 스키마 확장.

**비범위**: eHR DB 직접 접근·실제 값 조회·자동 갱신(사람 승인 게이트 유지). 레지스트리 초기 항목의 도메인 큐레이션(담당자 몫, §9 열린 질문).

## 3. 판정 모델 — DbDataRegistry (직교 라우팅 차원)

- DB-backing 여부는 **ChangeType과 직교**한 별개 축이다(RATE/VALUE 개정이 코드일 수도 DB일 수도 있으므로 ChangeType에 섞지 않는다 — ADR-016).
- 판정은 **큐레이션된 레지스트리의 정확 매칭**으로만 한다. "코드 매핑이 없으면 DB일 것"이라는 **추론은 금지**한다(retrieval 미스를 DB 개정으로 오인하면 "개정 놓침" — 이 프로젝트 최악의 실패).
- 매칭 키: `law_id` + `article_pattern`(조문 부분일치). 매칭되면 DB 데이터 항목으로 간주.

## 4. domains.json 스키마 확장

각 도메인(tax/hr)에 선택적 `db_items` 배열을 추가한다. **실제 DB 테이블/컬럼명 원문은 기재하지 않는다**(§8 보안). 일반화된 라벨만 둔다.

```jsonc
"tax": {
  "label": "...", "laws": [...], "admin_rule_queries": [...],
  "db_items": [
    {
      "law_id": "<법령ID>",              // domains.json 기존 law 식별 방식과 동일
      "article_pattern": "제N조",         // 조문 부분일치
      "item_label": "근로소득 간이세액표", // 일반화 라벨 (테이블명 아님)
      "db_hint": "급여 세액 산정표(DB 관리)", // 담당자용 힌트, 일반 서술
      "guidance": "본 개정은 코드 patch 대상이 아니라 DB 데이터 갱신 대상입니다. ..."
    }
  ]
}
```

- `db_items` 미존재/빈 배열이면 기존 동작과 동일(회귀 안전).
- 스키마 원문(T_*, 컬럼 코드)은 커밋 파일에 넣지 않는다 — 실제 테이블 매핑은 담당자 로컬 지식/비커밋 메모로 유지.

## 5. 라우팅

- 새 결정값 `AutomationDecision.DB_UPDATE_GUIDANCE`를 추가한다(기존 `ANALYSIS_ONLY`와 구분: 그건 "미구현/영향 없음", 이건 "DB에서 갱신하라").
- 판정 위치: **제안(apply) 진입 시** 레지스트리 매칭을 먼저 확인한다. 매칭되면 코드 draft 정책 게이트/LLM 생성 경로에 **진입하지 않고** DB 갱신 안내를 생성한다.
- 분류(analyze)·검색(map)은 그대로 수행한다(개정 감지·기록은 유지 — 재현율).
- 승인 게이트 원칙 유지: 안내는 초안일 뿐, 실제 DB 갱신은 사람이 한다. 자동 갱신 경로를 만들지 않는다(CLAUDE.md CRITICAL).

## 6. DB 갱신 안내 산출물

```
DbUpdateGuidance {
  item_label:   레지스트리 라벨 (일반화)
  law_name, article: 개정 조문
  before / after: ChangeNormalizer가 파생한 값 델타(있으면)
  guidance:     레지스트리 안내문구
  decision:     db_update_guidance
}
```

- 실제 DB 값은 포함하지 않는다(접근 없음). 조문 전후값 + 갱신 위치 라벨 + 안내만 제공.

## 7. NO_CODE_IMPACT 와의 구분

| 상황 | 예 | 결정 |
|---|---|---|
| eHR가 해당 법령을 **미구현** | 법인세·부가세 | `analysis_only`(NO_CODE_IMPACT) |
| eHR가 구현하되 **DB 데이터**로 관리 | 소득세율표·4대보험요율 | `db_update_guidance`(레지스트리 매칭) |
| eHR가 **코드**로 구현 | 한도 상수(xfdl 등) | 기존 코드 draft 경로 |

## 8. 보안 (/secscan 대상)

- 레지스트리에 **실제 DB 스키마 원문·자격증명 금지**(org 규칙: DB 스키마 컬럼 일반화, eHR 내부 파생물 커밋 금지). domains.json에는 일반화 라벨만.
- eHR DB **직접 접근 없음**. 외부 연동 추가 없음(입력은 기존 법령 텍스트).
- 승인 게이트 우회 경로 신설 금지 — 안내 생성까지만.

## 9. 열린 질문 (담당자 판단 — 임의 결정하지 않음)

1. 초기 `db_items` 항목: 어떤 법령 조문이 급여 세액표/4대보험 요율표(DB)에 대응하는가.
2. 안내에 값 델타 외에 추가 정보(갱신 절차 등)를 넣을지.
3. NO_CODE_IMPACT vs DB_DATA 구분을 레지스트리로만 할지, 보조 신호를 둘지.

## 10. 테스트 (TDD — 구현 전 작성)

1. 레지스트리 매칭: law_id+article_pattern 일치 시 DB_UPDATE_GUIDANCE, 불일치 시 기존 경로(회귀).
2. `db_items` 빈/부재 시 기존 동작 불변(회귀 고정 테스트).
3. 라우팅: 매칭 건은 LLM 생성 경로에 진입하지 않음(출력 토큰 0 검증).
4. 안내 산출물에 DB 스키마 원문이 새어나가지 않음(라벨만).
5. NO_CODE_IMPACT(미구현)과 DB_DATA(레지스트리 매칭) 분리.

## 11. 수용 기준 (AC)

1. domains.json에 `db_items`(비어 있어도 됨) 스키마 추가 + 로더 확장, 기존 필드 회귀 없음.
2. `AutomationDecision.DB_UPDATE_GUIDANCE` 추가 + apply 라우트가 매칭 시 안내를 반환(코드 draft 미진입).
3. 레지스트리 정확 매칭만 사용(추론 금지) — 매핑 없는 건은 종전대로 처리.
4. 보안: 커밋 산출물에 DB 스키마 원문 부재 확인.
5. `bash scripts/verify.sh full` 통과.

## 12. Claude Code 요청문 (harness 착수용)

레지스트리 로더 → 라우팅 결정 → 안내 산출 → apply 통합 순의 step으로 분해. 각 step TDD, 동작 보존 우선.
