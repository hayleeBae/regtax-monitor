# 3단계 설계 문서: 하네스 옵티마이저 층 (보류 — 추후 적용)

> 상태: **설계만 확정, 구현 보류.**
> 착수 조건(§7)을 충족하기 전에는 구현하지 않는다.
> 전제: 1·2단계(`docs/OBSERVABILITY.md` — `.harness/traces`, `.harness/failures`)가 운영 중이고, 스키마가 본 문서와 호환될 것.

## 1. 목표와 비목표

**목표.** 하네스가 자신의 실행 데이터(traces, failures)를 근거로 스스로의 개선안을 제안하고, 이중 검증과 사람 승인을 거쳐 병합되는 루프를 만든다. 대상은 하네스 구성물이지 모델이 아니다 — 하네스는 배치를 좋게 만들 뿐, 지능의 원천은 모델이라는 한계를 전제한다.

**비목표.** 완전 무인 자기수정 루프는 목표가 아니다. 사람 승인 게이트 제거, 권한/보안 정책의 자동 수정, 모델 자체의 개선은 범위 밖이다.

## 2. 아키텍처 개요

Self-Harness 패턴의 3부 루프를 따르되, 승인 게이트를 PR로 구현한다.

```
[운영 하네스 v(t)]
   │ 실행 → traces/, failures/ 축적
   ▼
① Weakness Mining (약점 채굴)
   │ 주기 실행 (예: 주 1회 또는 실패 N건 누적 시)
   │ 입력: failures/index.jsonl, traces/, taxonomy.yml
   │ 출력: weakness-report.md + 클러스터링된 실패 패턴(JSON)
   ▼
② Harness Proposal (수정안 제안)
   │ 허용 표면(§3) 안에서만, 패턴당 최대 1~2건의 좁은 수정
   │ 출력: PR (diff + 근거가 되는 failure_id/trace 링크 필수)
   ▼
③ Proposal Validation (이중 검증)
   │ 시험 A: 해당 실패 패턴 재현 케이스가 이제 통과하는가 (fix test)
   │ 시험 B: 기존 골든 런 회귀 스위트가 깨지지 않는가 (regression test)
   │ 둘 다 통과 → 사람 리뷰 대기 / 하나라도 실패 → 자동 close + 기록
   ▼
④ Human Review & Merge
   │ 사람이 승인해야만 병합. 병합 시 harness_version 증가.
   ▼
[운영 하네스 v(t+1)] → ①로 순환
```

## 3. 허용 수정 표면 (Mutable Surface)

옵티마이저가 고칠 수 있는 것과 절대 못 고치는 것을 파일 수준에서 미리 고정한다. "고쳐도 되는 표면을 미리 정해두고 그 안에서만 좁게 고친다"가 이 설계의 제1원칙이다.

| 구분 | 대상 (이 템플릿 기준) | 근거 |
|---|---|---|
| ✅ 수정 가능 | CLAUDE.md의 지정 섹션(아키텍처 규칙·도메인 컨텍스트 제외한 "흔한 함정" 성격 지침), `profiles/*.md`의 "흔한 함정" 섹션, step 설계 원칙 문서(`.claude/commands/harness.md`)의 주의사항 목록, execute.py 파라미터 기본값(재시도 횟수, 타임아웃) | 실패 패턴이 직접 가리키는 표면 |
| ❌ 수정 불가 | `.claude/settings.json`(권한/차단 정책), `scripts/verify.sh`(검증 게이트), `scripts/execute.py`·`scripts/trace.py`, `.harness/taxonomy.yml`, `.github/workflows/ci.yml`, 골든 런 회귀 스위트, 옵티마이저 자신의 코드 | 평가자와 권한 통제가 루프 안에 들어가면 보상 해킹의 표적이 됨 |

구현 기반은 이미 존재한다 — execute.py의 `PROTECTED_PATTERNS`(안티게이밍)가 불변 표면의 씨앗이다. 3단계 착수 시 이 목록을 `config/immutable.list`로 분리해 옵티마이저용 검사에 재사용하고, ②단계 산출 diff가 목록을 건드리면 검증 이전에 기계적으로 거부한다. CI(GitHub Actions)에서도 동일 검사를 이중으로 건다 — 옵티마이저의 자기 판단을 신뢰하지 않는다.

## 4. 각 컴포넌트 상세

### ① Weakness Mining

입력은 2단계 데이터 그대로다. 처리 순서:

1. `failures/index.jsonl`에서 `status: recurring` 또는 동일 `cause_code` 3건 이상 클러스터 추출
2. 각 클러스터에 대해 `causal_chain`의 trace 이벤트를 grep으로 역추적하여 "하네스의 어떤 지점이 이 실패를 허용했나"를 특정
3. `verified_by: unverified` 실패는 채굴 대상에서 제외 (미검증 원인 기반 수정 금지)

출력 스키마:

```json
{
  "pattern_id": "W-2026Q3-001",
  "cause_code": "assumption_stale_api",
  "evidence": ["F-20260707-0003", "F-20260712-0001", "F-20260719-0004"],
  "harness_gap": "02_implementation 단계에 DB 스키마 실측 확인 체크가 없음",
  "proposed_surface": "stages/02_implementation/checklist.md"
}
```

### ② Harness Proposal

제안 에이전트에 주는 제약: 패턴 1건당 수정 1건, diff는 허용 표면 내부만, PR 본문에 evidence failure_id와 harness_gap을 반드시 인용. 근거 없는 수정(=실패 데이터가 가리키지 않는 "좋아 보이는" 개선)은 금지한다. 이 제약이 없으면 제안이 취향 리팩토링으로 흐른다.

### ③ Proposal Validation — 이중 시험

**시험 A (fix test).** 해당 실패를 재현하는 최소 케이스를 실패 레코드의 artifacts에서 구성해 두고, 수정 후 통과를 확인한다. 실패 보존(2단계)이 여기서 재료가 된다 — 재현 케이스를 만들 수 없는 실패는 수정 대상에서 제외.

**시험 B (regression test).** "골든 런" 세트 — 과거 성공 런 중 대표 태스크 5~10개를 고정해 둔 스위트 — 를 새 하네스로 재실행하여 outcome이 유지되는지 확인. 골든 런 세트 자체는 불변 표면(사람 관리)이다.

판정: A∧B 통과 시에만 사람 리뷰로 승격. 어느 쪽이든 실패하면 PR을 자동 close하되, **그 실패도 failures/에 기록한다** (`cause_code: optimizer_rejected` 추가 필요). 기각된 제안은 탐색 공간을 줄여주는 가장 좋은 재료이므로 버리지 않는다.

### ④ Human Review

사람이 확인할 것: (a) 근거 failure와 수정의 인과가 실제로 맞는가, (b) 수정이 다른 스택 프로필에 부작용이 없는가, (c) 지침 비대화 — 수정이 CLAUDE.md를 계속 길게만 만들면 ACE식 증분 갱신(전체 재작성 대신 항목 단위 병합)을 강제한다.

## 5. 안전 장치 (루프 바깥에 사는 것들)

1. **평가 기준 외부화.** 무엇이 성공인지의 정의(검증 게이트, 골든 런)는 옵티마이저가 접근 불가한 고정 파일. 유닛 테스트가 보상이면 테스트에 과적합하고, 판정 모델이 보상이면 판정자를 속이는 법을 배운다 — 그래서 보상 정의는 루프 밖에 둔다.
2. **모델 역량 게이트.** STOP 실험처럼 굴리는 모델이 약하면 반복이 성능을 깎는다. 옵티마이저 도입 후 첫 3사이클은 병합된 수정의 효과를 `harness_version`별 실패율로 측정하고, 개선이 확인되지 않으면 루프를 중단한다 (킬 스위치: 옵티마이저 스케줄 비활성화 한 줄).
3. **롤백.** 모든 병합은 단일 커밋으로, `harness_version` 태그와 함께. 실패율 급증 시 `git revert` 한 번으로 이전 버전 복귀.
4. **실행 흔적 감사.** 옵티마이저 자신의 실행도 traces에 남긴다 (actor: `optimizer`). 옵티마이저는 감사 대상이지 특권 주체가 아니다.

## 6. 1·2단계 스키마와의 접점 (지금 지켜야 나중에 무마이그레이션)

- `cause_code`/`symptom_category`는 taxonomy.yml enum만 사용 — 채굴기의 클러스터링 키
- `causal_chain`의 event_id 형식 준수 — 역추적 grep의 전제
- `verified_by` 정직 기록 — unverified를 verified로 쓰면 채굴기가 오염됨
- `harness_version`을 run.json에 필수 기록 — 버전별 효과 측정의 조인 키
- `recurrence_of` 링크 유지 — recurring 판정의 근거

## 7. 착수 조건 (이 조건 전에는 구현하지 않음)

1. failures/index.jsonl 누적 **30건 이상**, 그중 `verified_by: human|test`가 과반
2. 동일 `cause_code` 3건 이상 클러스터가 **최소 2개** 존재 (채굴할 패턴이 실재)
3. 골든 런 후보가 될 성공 런 **5개 이상** 확보
4. 1·2단계 스키마가 2주 이상 변경 없이 안정

조건 1·2가 뜻하는 바: 데이터가 없으면 옵티마이저는 근거 없는 취향 수정만 생산한다. 데이터가 조건을 채우는 속도 자체가 "이 프로젝트에 옵티마이저가 필요한가"의 답이기도 하다 — 실패가 30건 모이지 않는다면 수동 관리로 충분하다.

## 8. 구현 순서 (착수 시)

1. `config/immutable.list` + CI 검사 (안전 장치 먼저)
2. 골든 런 스위트 고정 및 재실행 스크립트
3. Weakness Mining 스크립트 (읽기 전용이므로 리스크 없음 — 가장 먼저 단독 운영 가능)
4. Proposal 에이전트 + PR 자동화
5. 이중 검증 CI 잡
6. 3사이클 효과 측정 후 정식 편입 여부 결정
