# Windows 트랙 핸드오프 (2026-08-20 기준)

맥(홈/개발)에서 할 수 있는 검증은 끝났다. 이 문서는 **회사망 윈도우 PC**에서 이어갈 작업을 정리한다.
윈도우에서 Claude Code 세션을 새로 열고 이 문서를 먼저 읽혀라. (맥 세션의 로컬 메모리·`evaluation/private/`
메모는 기기 로컬이라 전해지지 않는다 — 근거는 커밋된 ADR·스펙에 있으니 그걸 참조한다.)

> ⚠️ 컴플라이언스: Oracle 실 DB에는 급여 등 민감정보가 있다. **읽기 전용·스키마/구조만**, PII 행 추출 금지.
> 커밋 산출물엔 DB 스키마 원문·값·경로 원문을 남기지 않는다(일반화 라벨·건수만). org 규칙 준수.

## 0. 지금까지 (맥에서 확정된 것)

- ✅ issue-0025 DB 데이터 개정 라우팅 (머지). `domains.json` `db_items`는 **스키마만, 내용 빈 상태**.
- ✅ issue-0020 그래프 검색 (머지, `graph_enabled=False`).
- ✅ **그래프 접음** — xfdl+SHARES_VALUE로 3배 키워도 실 25건 0/25. 근본원인 SvcID 런타임 간접참조
  (콜그래프가 코드 텍스트에 없음). 상세 ADR-018. **윈도우에서 그래프 재시도 불필요.**
- ✅ §3 재인덱싱·§4 검색확인·analyze/apply E2E (맥). §4 결과 `evaluation/private/`(맥 로컬).

## 1. 윈도우 환경 점검 (착수 전)

```bash
git pull origin main
source .venv/bin/activate   # 또는 회사 PC venv
python --version
bash scripts/verify.sh full
```

- `.env`: `REPO_ROOT`(실 eHR), `LLM_BACKEND`(local 권장 — 반출 금지), Ollama 기동
  (`OLLAMA_CONTEXT_LENGTH=16384 ollama serve`), `HF_HUB_OFFLINE=1`(회사망 SSL).
- **인덱스는 기기별 재생성** — 맥 `chroma_data`·`*_cache.json` 복사 금지. 필요 시 여기서 재인덱싱
  (화이트리스트 `REPO_INDEX_PATHS` 필수 — `build.xml` 자격증명 노출 방지, COMPANY_VALIDATION §2).
- SVN·Oracle 도달성 확인: `ping 172.20.88.58`, Oracle MCP 연결 상태.

## 2. 트랙 A — SVN 클린 리비전 → 초안 정확도 replay (§4-2 심화)

맥에서 불가했던 이유: eHR git은 **SVN→Git 단일 마이그레이션 커밋**이라 개정 이력이 없다(연도 짝 코드
diff는 95%가 공백 노이즈). **실 개정 이력은 SVN에만** 있다(`https://172.20.88.58:8443/svn/eHR/eHR`).

절차(읽기 전용, 자격증명은 사람이 — Claude가 입력 금지):
1. **SVN 인증**(사람): `svn log --limit 1 <URL>` 한 번 실행해 인증 캐시(또는 인증서 수락).
2. **개정 리비전 찾기**: `svn log`로 급여/연말정산 경로의 세법 개정 커밋 + 그 직전 리비전(base) 식별.
   커밋 메시지가 티켓 중심이라 사람 판단 필요.
3. **base/answer 트리 확보**: `svn export -r <base>`·`-r <answer>`로 두 시점 트리 추출.
4. **⚠️ 배관 결정 필요**: 기존 replay 인프라(`app/evaluation/replay/`)는 **git base/answer 커밋** 기반이다.
   SVN 리비전을 어떻게 먹일지 recon 후 결정 — (a) 해당 경로만 `git init`으로 두 시점 커밋 만들기,
   (b) 또는 트리 경로 직접 비교 모드. 배관 확정 전엔 stub으로 fixture 일관성부터(§4-2 실행 1단계).
5. **replay 실행**: `python -m app.evaluation.replay.runner --pipeline real` → file_coverage,
   expected_replacement_accuracy, git_apply, golden. `LLM_BACKEND=local` 유지. 결과는 metadata_only.
   기록은 `evaluation/private/`(gitignore) + 건수만 공유(COMPANY_VALIDATION §4-2 양식).

## 3. 트랙 B — Oracle DB-data 케이스 → issue-0025 실검증 + db_items 큐레이션

세율/한도 등 핵심 세법 수치는 **코드가 아니라 DB**에 있음이 확인됐다(그래서 코드 replay로 안 잡힘).
Oracle MCP로 이걸 실검증하고 issue-0025 레지스트리를 실제로 채운다.

절차(읽기 전용, PII 금지):
1. **Oracle 스키마 확인**(MCP): 세율/요율/한도를 담는 테이블·컬럼 파악(예 `T_PAY_TAX`/`T_INS_RATE` 계열).
   **구조만, 데이터 행 조회 최소화, PII 없는 코드성 테이블 위주.**
2. **매핑 큐레이션**: 실 세법 개정 1~2건에 대해 (법령ID+조문 → DB 항목)을 판정하고
   `domains.json` `db_items`에 **일반화 라벨로만** 추가(테이블/컬럼 원문 금지 — DB_DATA_ROUTING_SPEC §8).
3. **issue-0025 실검증**: 해당 change_id로 `POST /changes/{id}/apply` → 응답이 `db_update_guidance`로
   라우팅되는지 확인(코드 draft 미진입). 미매칭이면 db_items 패턴 조정.
4. 기록: 매핑 건수·라우팅 성공 여부만(경로·스키마 원문 제외).

## 4. 코디네이션 (맥과 꼬임 방지)

- 맥 작업은 멈춘 상태(직렬). 윈도우는 **자체 브랜치** 사용, 인덱스·캐시 기기별.
- `phases/index.json`은 공유 파일 — 하네스로 돌릴 땐 충돌 주의(트랙 A/B는 하네스보다 탐색·수동 성격이 큼).
- 결과 산출물(`evaluation/private/`, `chroma_data`, `*_cache.json`)은 커밋 금지(gitignore 확인).

## 5. 9월 방향 (baseline 확보 후)

트랙 A가 실사례 baseline(정확도 지표)을 주면, 9월은 **그래프 무관 레버**로 개선한다:
few-shot 주입(W2) · 모델 라우팅/구조화출력(W3) · reranker·provider 가중치 캘리브레이션(W4).
그래프는 접었으므로 제외. (플랜 EXECUTION_PLAN_202608-09 §2, 단 W1 fixture 출처는 연도짝이 아니라 SVN.)
