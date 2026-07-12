# 프로젝트: {프로젝트명}

## 프로젝트 유형

{하나 선택하고 나머지는 삭제}

- **신규 개발** — 처음부터 만드는 프로젝트. TDD 기본.
- **기존 코드 유지보수** — 동작 중인 코드 수정. 회귀 방지 최우선.
- **마이그레이션 (동작 보존)** — 기술 스택/UI 교체, 비즈니스 로직 보존. 원본 대비 검증 필수.

## 기술 스택

{`profiles/<스택>.md`의 "기술 스택" 섹션을 복사해 붙이고 프로젝트에 맞게 수정}

- {프레임워크}
- {언어 및 버전}
- {DB / 주요 라이브러리}

## 아키텍처 규칙

- CRITICAL: {절대 규칙 1 — 예: 모든 DB 접근은 repository 레이어에서만}
- CRITICAL: {절대 규칙 2 — 예: 원본 시스템의 계산 로직을 임의로 "개선"하지 말 것}
- {일반 규칙 — 예: 디렉토리 구조는 docs/ARCHITECTURE.md를 따를 것}

## 개발 프로세스 (공통 — 프로젝트마다 수정하지 않음)

**파이프라인**: 설계(/design 승인) → 구현(/harness + execute.py) → 테스트(verify.sh full) → 보안점검(/secscan, Critical/High 시 중단) → 배포(/release, docs/DEPLOY.md 런북). 앞 단계 게이트를 통과하기 전에 다음 단계로 넘어가지 않는다.

- CRITICAL: 모든 검증은 `bash scripts/verify.sh quick|full`로 실행한다. 개별 명령을 직접 조합하지 마라.
- CRITICAL: 마이그레이션 프로젝트에서는 동작 보존이 최우선이다. 리팩토링 중 발견한 "개선점"은 코드를 고치지 말고 보고만 하라.
- 신규 기능은 테스트 먼저 작성(TDD). 레거시 수정 시에는 수정 전 현재 동작을 고정하는 테스트를 먼저 추가.
- 커밋 메시지는 conventional commits (feat:, fix:, docs:, refactor:, chore:)
- 대규모 변경은 step으로 쪼개고 각 step마다 산출 리포트를 남긴다 (`.claude/commands/harness.md` 참조)
- 관측성: execute.py는 모든 run을 `.harness/traces/`에, 실패를 `.harness/failures/`에 자동 기록한다. 중요한 기술적 분기는 `python3 scripts/trace.py event --type decision`으로 기록하고, 에러를 만나면 먼저 `.harness/failures/index.jsonl`에서 유사 실패 이력을 grep으로 검색한다.
- CRITICAL: 실패 레코드는 삭제하지 않는다. 해결 시 `failure.json`의 status를 `resolved`로 갱신하고 `verified_cause`를 채운다 (cause_code는 `.harness/taxonomy.yml` 값만 사용, 미검증이면 `unverified`).

## 명령어

```bash
bash scripts/verify.sh quick   # 빠른 검증 (lint + 컴파일/타입체크) — Stop hook용
bash scripts/verify.sh full    # 전체 검증 (quick + 테스트/빌드) — step AC/리뷰용
```

{프로파일의 개별 명령어를 아래에 추가 — 예: npm run dev, ./gradlew bootRun, uvicorn main:app 등}

## 팀 협업 (팀 프로젝트인 경우 — 1인 프로젝트면 섹션 삭제)

- 브랜치: `feat-{phase}` / `fix-*` 등 작업 브랜치 → dev → main. main 직접 push 금지.
- 머지는 PR로만. 머지 조건: CI 그린 + 리뷰어 1인 이상 승인.
- phase 1개 = 소유자 1명. 다른 사람의 phases/ 디렉토리를 수정하지 않는다.
- CI(.github/workflows/ci.yml)와 로컬은 동일한 scripts/verify.sh를 사용한다.

## 도메인 컨텍스트 (선택)

{이 프로젝트만의 도메인 지식. 없으면 섹션 삭제.
예: 다계열사(multi-company) 구조 — company_id에 따라 화면/로직 분기.
예: 급여 계산은 원단위 절사, 세액은 십원단위 절사.}
