# Architecture Decision Records

## 철학
"개정을 놓친 것"이 가장 비싼 실패 — 수집·감지의 재현율이 초안 품질보다 우선한다. AI는 초안까지, 적용은 사람이. 코드는 외부로 나가지 않는다.

---

### ADR-001: 생성 단계까지 완전 로컬 (하이브리드 → local 기본)
**결정**: 임베딩뿐 아니라 생성도 로컬 추론 서버(OpenAI 호환)로 수행. Claude API는 `LLM_BACKEND=claude` 옵션으로 보존.
**이유**: 초기 하이브리드(로컬 임베딩 + Claude 생성)는 "RAG 스니펫 외부 전송"에 보안 검토가 필요했다. 완전 로컬이면 검토 자체가 불필요.
**트레이드오프**: 로컬 소형 모델(qwen3:8b)의 초안 품질·속도(CPU 수 분) 저하. 승인 게이트 + 골든 테스트 + 앵커 재시도로 상쇄.

### ADR-002: 사람 승인 게이트 — 자동 적용 영구 제외
**결정**: AI는 patch 초안 생성까지만. 승인(approve) 시에만 patch 파일 출력, 자동 적용 경로 없음.
**이유**: 환각(존재하지 않는 파일 편집 등)의 최후 방어선. 로컬 소형 모델의 약점이 게이트로 상쇄됨을 실검증.
**트레이드오프**: 완전 자동화 포기 — 비실시간 워크플로 전제.

### ADR-003: 두 개의 seam (LlmClient / CodebaseAdapter)
**결정**: 환경에 따라 바뀌는 지점을 `app/llm/`(로컬↔API)과 `app/codebase/`(mock↔실 repo) 두 인터페이스로 고정.
**이유**: 집(mock, LAW_API 없음)↔회사(실 repo, SSL 프록시) 이식이 설정 변경만으로 되도록.
**트레이드오프**: 소규모 프로젝트 대비 간접층 추가.

### ADR-004: ChromaDB 임베디드 + bge-m3 CPU 임베딩
**결정**: 별도 서버 없는 파일 기반 벡터 DB(`chroma_data/`) + 로컬 CPU 임베딩.
**이유**: pip 설치만으로 동작 — 회사 PC 이식 장벽 최소화. 코드 반출 없음.
**트레이드오프**: 첫 인덱싱 수십 분(CPU). 재인덱싱은 `rm -rf chroma_data/` 후 재기동. 알려진 취약점 PYSEC-2026-311은 수정판 미출시로 verify.sh security에서 문서화된 ignore (수정판 출시 시 해제).

### ADR-005: 확률적 매칭의 한계는 3중 부트스트랩 + 매핑 자산으로 보완
**결정**: RAG 퍼지 검색에 용어 사전 정확매칭(암호 컬럼코드)과 상수 값매칭(개정 수치)을 병행하고, 담당자 검증(verified Mapping)을 축적해 이듬해부터 추측 없이 직행.
**이유**: eHR 컬럼명이 암호 코드(`a0121`)라 임베딩만으로는 연결 불가. 연말정산은 매년 같은 조문 부근이 바뀌므로 검증된 매핑이 최고의 자산.
**트레이드오프**: 첫 시즌 담당자 검증 노력 필요 (운영 방침 — 로드맵 1).

### ADR-006: 골든 테스트는 스크래치 사본에서
**결정**: 초안 diff를 repo 사본에 적용해 `GOLDEN_TEST_CMD`로 검증. 실패해도 승인은 가능(경고 명시), 실제 repo는 절대 불변.
**이유**: 환각의 구조적 차단 + 계산 결과가 바뀌는 개정은 기대값 갱신까지 patch에 포함되도록 강제.
**트레이드오프**: 골든 케이스 유지 비용. `GOLDEN_TEST_CMD` 미설정 시 검증 생략.

### ADR-008: 매핑 검증을 append-only 결정 이력으로 (Issue #0015)
**결정**: 단일 `Mapping.verified` boolean을 유지하되, 실제 검증·거절·stale·revoke는 `mapping_decision` append-only 이벤트로 기록한다. `Mapping.verified`는 최신 상태를 담는 compatibility cache로 남기고 이벤트 insert와 같은 트랜잭션에서 갱신한다. 상태는 이벤트를 시간순으로 접는 순수 함수(`resolve_state`)로 계산한다(`VERIFIED→STALE→VERIFIED`=VERIFIED, REVOKED는 직전 verified 취소하되 후보 삭제 안 함). #0013 audit(records/repository) 3층 패턴을 그대로 답습한다.
**이유**: boolean 하나로는 "누가·왜·어느 commit에서" 검증했는지 추적이 안 되고, #0016 리랭킹(검증 boost·거절 penalty·stale 무효화)의 근거를 만들 수 없다. 기존 `apply`/`get_mappings`가 `verified` 컬럼에 의존하므로 컬럼을 삭제하지 않고 cache로 보존해 회귀를 막는다.
**트레이드오프**: 검증 상태가 컬럼과 이벤트 두 곳에 존재(cache 동기화 책임). 인증 레이어가 없어 `actor`는 자기신고값(단일 담당자 도구로 수용, 기본 'owner'). 유효성 스냅샷(commit/path_hash/symbol_hash)은 #0015에서 nullable best-effort — 코드 본문은 저장하지 않고 해시만.

### ADR-009: 검증 이력 기반 검색 재정렬은 후처리 rerank 단계로 (Issue #0016)
**결정**: #0015의 결정 이력(verified/rejected/stale/golden/historical/legacy)을 검색 점수에 반영하되, 기존 source 가중치(`_merge_candidates`)에 섞지 않고 merge·rank **뒤에 별도 rerank 단계**로 delta를 적용해 재정렬한다. 순수 도메인(`app/domain/mappings/reranking.py`: `classify_reuse`, `rerank_delta`, `RERANK_VERSION`)과 DB lookup(`app/mappings/reranking_lookup.py`)을 분리한다. boost/penalty는 후보 위치의 이력을 쿼리(article_id + change_type)와 대조해 **exact/compatible/unrelated로 문맥 게이팅**한 뒤 exact·compatible에만 적용한다(스펙 §9·§11). 기본 활성(config `verified_reranking_enabled=True`, flag로 off 가능), 버전은 `SCORING_VERSION`을 유지하고 응답에 별도 `rerank_version`을 노출한다.
**이유**: 검증 매핑 자산을 검색 품질로 환원하는 것이 이 시스템의 핵심 가치(ADR-005)지만, 매핑을 무관 개정에 과적합하면 "개정을 놓침"보다 나쁜 오탐을 만든다 — 문맥 게이팅으로 다른 법령의 거절이 영구 차단이 되지 않게 한다. 후처리 분리는 ablation으로 전후 성능을 수치 입증(로드맵 원칙: 측정 먼저)하고 §1 scoring을 안정적으로 유지한다.
**트레이드오프**: rerank 단계가 검색 지연에 소량 추가. stale 신호가 merge(-0.50, content drift)와 rerank(결정 STALE)에서 겹칠 수 있어 **총 penalty를 -0.50으로 cap**해 이중 계산을 막는다. rerank가 켜지면 순위가 바뀌므로 회귀는 flag off·reranker 미주입 시 동일 결과로 방어하고 ablation으로 검증한다.

#### ADR-009 보강 (2026-08-02, 구현 착수 전 코드 검토)
설계 승인 후 기존 코드를 대조해 확인한 세 가지를 결정에 추가한다. 셋 다 위 결정을 바꾸지 않고 성립 조건을 못 박는 것이다.

1. **쿼리 문맥은 `RetrievalQuery`로 나른다.** 문맥 게이팅에 필요한 `article_id`·`change_type`이 현재 검색 seam에 없다 — `article_id`는 `_make_mapping_service` 클로저에만 있고 `change_type`은 전달되지 않는다. `RetrievalQuery`에 `article_id: str | None`·`change_type: str | None`을 **기본값 None으로** 추가하고 `MappingService.map()`이 이를 받아 넘긴다. 기본값을 두는 이유는 기존 호출자(ablation runner, 테스트)가 그대로 동작해야 하기 때문이고, 두 값이 없으면 rerank는 게이팅 불가로 판단해 delta 0을 반환한다(무문맥 boost 금지 — 스펙 §9).
2. **rerank는 `final_top_k` 절단 전에 실행한다.** 현재 orchestrator는 merge 직후 `final_top_k`로 자르고 rank를 매긴다. 절단 뒤에 rerank를 두면 상위 K 밖의 검증 후보가 boost를 받아도 올라올 수 없어 "유효한 verified 후보가 상단으로 이동"(#0016 수용 기준)이 구조적으로 불가능하다. 순서는 **merge → rerank → 정렬 → 절단 → rank 부여**로 고정한다.
3. **stale cap은 merge 적용분을 후보에 실어 계산한다.** `_merge_candidates`는 stale에 -0.50을 적용한 뒤 `max(0.0, ...)`로 클램프하므로, rerank 시점에는 페널티가 이미 바닥에 흡수돼 얼마가 적용됐는지 알 수 없다. merge가 stale 페널티를 적용했다는 사실을 후보에 실어(`RetrievalCandidate.stale` 재사용) rerank가 STALE 결정 페널티를 **추가로 얹지 않도록** 한다 — 총 -0.50 cap은 이 방식으로 보장한다.

**추가 트레이드오프**: `RetrievalQuery`·`MappingService.map()` 시그니처가 넓어진다(기본값으로 하위 호환 유지). rerank가 절단 전으로 오면 merge 결과 전체를 대상으로 lookup해야 해 DB 조회량이 K가 아니라 후보 수에 비례한다 — article_id 단위 1회 조회로 묶어 흡수한다.

### ADR-010: 과거 개정 replay fixture는 EvaluationCase와 분리한 별도 스키마로 (Issue #0017)
**결정**: HISTORICAL_REPLAY_SPEC §3 의 replay fixture 를 `EvaluationCase`(#0005) 확장이 아니라 **별도 `ReplayFixture` 스키마**로 만든다(`app/evaluation/replay/fixture.py` 순수 dataclass + `loader.py` YAML 로더, `ExpectedReplacement` 만 재사용). repo 위치는 `path`(프로젝트 상대, mock 전용)와 `path_env`(환경변수 이름, 실데이터 전용) 중 **정확히 하나**만 허용한다. `base_commit`/`answer_commit` 은 SHA 가 아니라 git revision 문자열을 허용하되 `[A-Za-z0-9._/-]` 로 제한하고 `..`·`^`·`~` 를 거부한다 — mock fixture 는 태그(`case1/base`)를 쓴다. mock git repo 3건은 커밋하지 않고, 커밋된 평범한 파일 트리(`evaluation/fixtures/replay_sources/`)에서 빌드 스크립트가 gitignore 된 위치에 생성한다. `privacy_mode`(full/redacted/metadata_only)는 #0017 에서 어휘와 "모드별 저장 가능 artifact" 순수 함수까지만 정의하고, 실제 저장 억제는 #0018 runner 책임이다. `golden_command` 는 로더가 첫 토큰을 실행파일 allowlist 와 대조해 거부한다.
**이유**: `DatasetLoader` 는 benchmark·runner·테스트 등 다수가 의존해 확장 시 회귀 위험이 크고, 스펙의 YAML 모양(`source_type`/`path_env`/`scope`/`privacy_mode`)이 애초에 `EvaluationCase` 와 다르다 — 억지로 합치면 두 용도가 서로의 검증 규칙을 오염시킨다. 경로를 환경변수로 분리하는 것은 **회사 repo 절대경로가 YAML 에 남아 외부로 나가는 것**을 막기 위해서다(CLAUDE.md 코드 반출 금지). revision 을 문자 집합으로 제한하는 것은 fixture YAML 이 이 이슈의 유일한 외부 입력 지점이고 git 인자 주입의 출발점이기 때문이다. `golden_command` 는 config(`golden_test_cmd`)와 달리 **파일로 전달받을 수 있어 신뢰 수준이 다르므로** 입구에서 막는다.
**트레이드오프**: 유사한 스키마가 두 벌 존재(케이스 평가용 / replay 용) — 대신 #0018 이 소비할 계약이 좁고 명확해진다. mock git repo 를 빌드해야 해서 fixture 실행 전 준비 단계가 하나 늘고, 태그 기반이라 fixture YAML 이 특정 빌드 스크립트에 결합된다. allowlist 는 정당한 골든 명령을 막을 수 있어 목록 유지 비용이 생긴다.

### ADR-011: replay 실행은 임시 detached worktree + git allowlist wrapper로 (Issue #0018)
**결정**: 과거 시점 코드 재현은 `git worktree add --detach`로 **임시 디렉토리에** 만들고, 원본 working tree에는 checkout/reset/clean/commit/push를 하지 않는다(스펙 §4). 모든 git 호출은 단일 wrapper(`app/evaluation/replay/git_cmd.py`)를 통과하며, **서브커맨드 allowlist 검사는 wrapper 안에서** 한다(호출자가 우회할 수 없게) — 스펙 §5의 `rev-parse`/`cat-file`/`diff`/`show`/`worktree add|remove`/`status`/`apply --check`/`apply`만 허용하고 `shell=False` + 전 호출 timeout이다. cleanup은 `finally`에서 `worktree remove --force` → `worktree prune` 순으로 수행한다. `golden_command` 실행은 **`app/golden.py`의 `run_golden_tests`를 재사용하지 않는다** — 그 함수는 `shell=True`이며, `config.golden_test_cmd`(운영자가 `.env`에 넣는 값)에는 타당하지만 fixture YAML에서 온 명령에는 신뢰 수준이 맞지 않는다. replay는 worktree를 cwd로 고정하고 `shlex.split` 결과를 인자 배열로 `shell=False` 실행하며, 절대경로 인자와 `-f`/`--file`/`-p` 계열 옵션을 거부한다(#0017 secscan Low #1 이월). 파이프라인(인덱싱·검색·초안 생성)은 runner가 **주입받는 callable seam**이며 runner 안에 구현하지 않는다. `privacy_mode` 강제는 리포트 writer **한 곳**에서 `allowed_artifacts()`(#0017)를 게이트로 삼아 수행한다.
**이유**: worktree는 복사 없이 과거 시점을 재현해 대형 eHR repo에서도 실용적이고 스펙 §4·§5가 명시한 방식이다. `.git/worktrees/` 메타데이터가 원본에 생기지만 이는 `worktree remove`/`prune`으로 되돌릴 수 있는 부가 정보이고, 작업 트리·커밋·refs는 건드리지 않으므로 "실제 repo는 절대 수정하지 않는다"(CLAUDE.md)의 취지를 지킨다. allowlist를 wrapper 안에 두는 이유는 호출 지점이 늘어날 때 검사를 빠뜨리는 것을 구조적으로 막기 위해서다. 파이프라인을 seam으로 두는 이유는 로드맵 #0018 구현 범위가 worktree·diff 추출·비교·cleanup까지이고, 실제 파이프라인을 넣으면 mock 3건 검증에 임베딩 인덱싱과 LLM 호출이 필요해져 집 환경 테스트가 무거워지기 때문이다(CLAUDE.md — 테스트에서 무거운 의존성 금지). 회사에서는 실제 파이프라인을 주입해 같은 runner를 쓴다.
**트레이드오프**: worktree 방식은 프로세스가 비정상 종료하면 `.git/worktrees/`에 stale 항목이 남는다 — 다음 실행의 `prune`으로 회수하지만 그 사이 원본 repo에 흔적이 남는다. allowlist wrapper는 새 git 기능이 필요할 때마다 목록을 늘려야 한다. seam 방식은 end-to-end 동작이 회사 환경에서만 실증되므로, 집에서는 "비교 로직이 맞다"까지만 증명된다. `app/golden.py`와 replay가 골든 실행 경로를 각각 갖게 되어 코드가 한 벌 늘어난다(신뢰 경계가 달라 의도된 분리다).

### ADR-007: 도메인 레지스트리(domains.json)로 수집 확장
**결정**: 수집 대상 법령·고시 검색어를 코드가 아니라 `domains.json`(tax/hr)에서 관리. 파일 없으면 기존 세법 5종 폴백.
**이유**: 연말정산(세법)을 넘어 인사 법령 전반으로 확장 + 도메인별 담당자 라우팅 기반. 법률만 수집하면 올해 개정(시행령·시행규칙 10건, 법률 0건)을 전부 놓쳤을 상황 — 3계층 수집이 필수임을 실검증.
**트레이드오프**: 법제처 등록명 정확 일치 관리 부담 (가운뎃점 `ㆍ` U+318D 등).
