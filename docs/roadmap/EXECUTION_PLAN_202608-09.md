# 실행 플랜 — 2026년 8월 말 ~ 9월 (기준일 2026-08-18)

> 세션·기기가 바뀌어도 이어갈 수 있도록 확정 플랜을 저장소에 기록한다.
> 각 이슈의 공통 게이트: 설계(/design 승인) → step 설계 승인 → harness 실행 →
> verify full → 보안점검(/secscan, Critical/High 시 중단) → PR → 머지.

## 0. 현재 상태 스냅샷 (2026-08-18)

- ✅ **issue-0023** 수집 필드 의미 정정 (PR #9) — before/after를 개정문 파싱으로 파생
- ✅ **issue-0024** eHR 인덱싱 적합화 (PR #10) — xfdl 지원·인코딩 정책·classes 제외·수확 범위 통일
- ✅ **임베딩 실측 검증 4/4** (Windows 회사 PC) — `COMPANY_VALIDATION.md` §3-3 실측 기록 참조
- 🔶 법제처 API 신청 완료(현행법령·행정규칙·국세청 법령해석) — 승인 대기. "고시 수집" = 행정규칙(admrul) target이므로 별도 신청 불요 (목록/본문 둘 다 신청됐는지만 확인)
- ⏳ LLM 검증 미실시 — **맥북(M3, Ollama 설치기)에서 진행** (B안: Windows=eHR·임베딩 검증, 맥=LLM. 인덱스·캐시는 기기별 재생성 — 기기 간 복사 금지)

## 1. 8월 잔여 플랜

| 기간 | 작업 | 게이트/산출물 |
|---|---|---|
| ~8/20 | **맥 셋업 + LLM 검증**: `COMPANY_VALIDATION.md` §1~§4-2 순서 — 재인덱싱(M3), `/analyze`·`/apply` E2E, rerank 실발동(§4-1), F-20260712-0002 절단 재현, replay `--pipeline real` 1케이스(§4-2) | 실측 검증 리포트 (이후 이슈 설계의 근거) |
| 8/21~22 | **issue-0025** DB 데이터 개정 판정 — 세율·요율은 코드가 아니라 DB(`T_PAY_TAX`/`T_INS_RATE`)라, 해당 개정을 "코드 patch 불가 → DB 갱신 안내"로 라우팅 | 스펙+ADR, 구현, PR |
| 8/25~26 | **issue-0020** 그래프 검색 — `symbol_index`(#0019 산출물) 소비처 `CodeGraphProvider` 배선 + ablation | 재현율 전후 수치 입증 |
| 8/27~29 | **issue-0026** 출력 절단 봉합(F-0002, 앵커 최소화) + eHR 골든 정의 → **issue-0021** V2 릴리스 게이트 착수 | F-0002 레코드의 사전 정의 판정 기준(출력 토큰 절반↓ + 절단·타임아웃 소멸) |

병행(코드 외): admrul 승인 확인 시 실수집 스모크 + `domains.json` `tax.admin_rule_queries` 채우기 / eHR `build.xml` 자격증명 외부화 + 정보보호팀 통보(소유자 몫).

**issue-0027(연도 쌍 마이닝)은 9월로 이월** — 규모가 커서 8월에 압축하면 품질이 깨진다.

## 2. 9월 플랜 — 모델 성능 향상

원칙: **모델 파라미터는 건드리지 않고 입력(검색·예시·형식)을 먼저 개선한다**
(ARCHITECTURE_V2·개선 우선순위: 검증 매핑 → reranker → few-shot → 선택적 튜닝).
모든 주차의 통과 조건은 "고정 평가셋 + replay 전후 비교 수치".

| 주차 | 작업 | 게이트 |
|---|---|---|
| W1 (9/1~5) | **issue-0027 연도 쌍 마이닝** — eHR `_2016~_2026` 연도 접미사 파일 쌍(java 52·xml 11·xfdl 263)을 diff해 ①replay 실사례 fixture 10건+ 자동 생성 ②change_type별 편집쌍 풀(few-shot 원료) 추출 | 실사례 replay로 **baseline 지표 확정** (file_coverage, expected_replacement_accuracy, anchor 성공률) |
| W2 (9/8~12) | **Few-shot 주입** — `propose_prompt`에 같은 change_type의 실제 편집 예시 1~3개 (W1 풀에서). 컨텍스트 증가에 따른 F-0001(context shift) 재발 감시 — 토큰 예산 로깅 연동 | few-shot on/off ablation — anchor 1차 성공률·형식 준수율 개선폭 |
| W3 (9/15~19) | **모델 라우팅 + 구조화 출력** — M3에서 qwen3:8b vs 14B급 측정(tokens/s·timeout·품질), 난이도별 라우팅(수치=8b, 조건·다중파일=14b). Ollama grammar/JSON schema 출력으로 형식 이탈 원천 축소 | 라우팅 규칙별 replay 지표 + 운영 지표(p95) 비교표 |
| W4 (9/22~26) | **Reranker 조정 + 튜닝 게이트 판단** — provider 가중치·검증 이력 boost를 W1 실데이터로 재조정(`retrieval_benchmark` ablation 재실행). **LoRA/SFT 착수 여부를 4조건 체크리스트로 판단**(승인 편집쌍 충분 / 거절 사유 기록 / 고정 평가셋 / 데이터 경계 통제) — 미달이면 착수하지 않고 few-shot 고도화로 대체 | ablation 수치 + 튜닝 go/no-go 결정 기록(ADR) |

9월 말 도달점: 실사례 정답 데이터 10건+, few-shot·라우팅·reranker 개선폭 수치 입증, 튜닝 여부 근거 기반 결정 완료.

## 3. 원리적 한계 (플랜이 "해결"하지 않는 것 — 관리 대상)

- 구조 개정(서식 개편 등)은 감지·알림까지가 상한 — 로드맵 효용 한계 그대로
- 세율표 등 DB 데이터 항목은 patch 불가 — issue-0025는 이를 "안내"로 전환하는 것
- 완전 자동 수정은 하지 않는다 — 사람 승인 게이트는 설계 원칙 (CLAUDE.md CRITICAL)
- 초안 품질 상한은 로컬 모델 성능 — 9월 플랜이 점진 개선하는 영역
