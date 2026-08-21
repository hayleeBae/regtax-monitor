# 실행 플랜 — 2026년 8월 말 ~ 9월 (기준일 2026-08-18)

> 세션·기기가 바뀌어도 이어갈 수 있도록 확정 플랜을 저장소에 기록한다.
> 각 이슈의 공통 게이트: 설계(/design 승인) → step 설계 승인 → harness 실행 →
> verify full → 보안점검(/secscan, Critical/High 시 중단) → PR → 머지.

## 0. 현재 상태 스냅샷 (갱신 2026-08-21)

- ✅ **issue-0023** 수집 필드 의미 정정 (머지) — before/after를 개정문 파싱으로 파생
- ✅ **issue-0024** eHR 인덱싱 적합화 (머지) — xfdl 지원·인코딩 정책·classes 제외·수확 범위 통일
- ✅ **issue-0025** DB 데이터 개정 라우팅 + db_items 큐레이션 (머지, 8/20)
- ✅ **issue-0020** 그래프 검색 (머지, 8/20 — **접음** ADR-018: SvcID 런타임 간접참조로 콜그래프 텍스트 부재)
- ✅ **임베딩 실측 검증 4/4** (Windows) — `COMPANY_VALIDATION.md` §3-3
- ✅ **법제처 API 실검증** (2026-08-21 Windows): OC 키 실동작, 실 수집 11건, **행정규칙(고시) 권한 승인 확인**(최저임금 고시 실수집). 추가 신청 불요.
- ✅ **Windows 고유 작업 완료** — SVN 접근 + **값-개정 카탈로그 34건**(보육수당·식대·직무발명·고향사랑 등, `evaluation/private/svn_track_a_recon.md`), Oracle 스키마 recon(db_items 근거). **SVN/Oracle 의존 작업 없음.**
- ✅ **recall 측정 인프라 커밋** — 도구 3종(`scripts/embed_cache.py`·`recall_eval.py`·`index_resumable.py`) + **fixture 8케이스**(`evaluation/datasets/recall_ehr_fixtures.yaml`). 맥에서 pull해 GPU로 실행.
- ⏳ **LLM/모델 검증 미실시** → **맥(GPU)에서 진행.** Windows(GPU 없음)는 bge-m3 CPU 인코딩이 청크당 ~3.5s로 비실용 → recall/초안 측정을 맥으로 이관 확정(2026-08-21). 인덱스·캐시(`embed_cache.db`·`chroma_data`)는 기기별 재생성 — 커밋/복사 금지.

### 기기 분담 (확정)
| 맥에서만 (GPU/로컬모델) | 어디서든 (harness·claude API) |
|---|---|
| recall 측정, 초안정확도 replay(`--pipeline real`) | issue-0026 **코드**(앵커 최소화) |
| rerank 실발동(§4-1), F-20260712-0002 재현 | issue-0021 릴리스 게이트 문서 |
| 9월 few-shot·라우팅·reranker 실측 | 카탈로그→fixture 확장 |

## 1. 8월 잔여 플랜 (갱신 2026-08-21)

코드 이슈(0025·0020)는 일정보다 앞서 완료. 남은 것은 **맥 검증 + 0026 + 0021**.

| 작업 | 상태 | 기기 | 비고 |
|---|---|---|---|
| **맥 recall 측정** | ⏳ 다음 | 맥 | `git pull` → `HF_HUB_OFFLINE=1 python scripts/embed_cache.py` → `recall_eval.py --fixtures evaluation/datasets/recall_ehr_fixtures.yaml --redacted --out evaluation/results/recall_report.md`. 첫 숫자 결과. 리포트(redacted) 커밋. |
| **맥 초안정확도 replay** | ⏳ | 맥 | `COMPANY_VALIDATION.md` §4-2 — `/analyze`·`/apply`, F-0002 재현, replay real 1케이스. Ollama 기동 필요. |
| **rerank 실발동(§4-1)** | ⏳ | 맥 | 검증 매핑 쌓은 뒤 symbol 일치로 발동하는지 |
| issue-0026 출력 절단 봉합 + eHR 골든 | ⬜ 미착수 | 코드=아무데나 / 재현검증=맥 | F-0002 판정 기준: 출력토큰 절반↓ + 절단·타임아웃 소멸 |
| issue-0021 V2 릴리스 게이트 | ⬜ 미착수 | 아무데나 | verify.sh 3모드 통과 문서화 |

병행(코드 외, 사용자): eHR `build.xml` 자격증명 외부화 + 정보보호팀 통보. `domains.json` `tax.admin_rule_queries` 채우기(고시 수집 확장).

**issue-0027(연도 쌍 마이닝)은 9월로 이월** — W1 소스는 값-개정 카탈로그 34건으로 확보 완료.

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
