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

### ADR-012: replay 파이프라인은 verified 자산 없이, worktree 전용 캐시 인덱스로 (Issue #0022)
**결정**: `#0018` 의 `ReplayPipeline` seam 에 붙일 실제 파이프라인을 `app/evaluation/replay/real_pipeline.py` 에 만들고 **CLI(`--pipeline real`)에서만 지연 import** 한다 — runner 는 여전히 임베딩·ChromaDB·LLM 을 import 하지 않는다(ADR-011 유지). 파이프라인은 `RealCodebaseAdapter(repo_root=<임시 worktree>)` 를 인덱싱해 검색하고 `propose_and_build` 로 초안을 만든다. **DB 계층(`Mapping` 행 조회)은 통과하지 않는다** — replay 가 재는 것은 "이 법령 변경과 이 시점 코드에서 파이프라인이 무엇을 내놓는가"이며, 저장된 매핑은 입력이 아니다. **`VerifiedMappingProvider` 와 `#0016` rerank 는 replay 에서 제외한다.** 인덱스는 `evaluation/replay_index/<key>/` 에 캐시하며 key 는 스펙 §6 대로 repository id + base commit + 임베딩 모델 + 인덱서 버전이다. 운영 `chroma_data/` 는 읽지도 쓰지도 않는다.
**이유**: 과거 개정을 그 시점에서 재현하면서 **그 개정을 처리하며 만들어진 오늘의 검증 매핑**을 입력으로 쓰면 정답을 보고 정답을 맞히는 것이 된다(look-ahead leakage). 지표가 부풀려지고, 그 숫자로 `#0019·#0020` 의 효과를 판단하면 방향이 틀어진다 — replay 의 존재 이유가 사라진다. 지연 import 는 ADR-011 이 seam 을 둔 이유(집 환경 테스트를 가볍게 유지)를 지키기 위해서다. 인덱스 캐시는 선택이 아니라 필수다: 실제 eHR repo 인덱싱은 수십 분이고, 캐시가 없으면 케이스마다 반복해 회사에서 한 번도 완주하지 못한다. 캐시를 `evaluation/` 하위에 두는 것은 그 디렉토리 전체가 이미 반출 금지·gitignore 구역이기 때문이다 — 인덱스에는 회사 코드의 임베딩이 담긴다.
**트레이드오프**: replay 가 재는 대상이 "verified 자산 없는 순수 검색·생성 성능"으로 좁아진다 — 검증 매핑의 효과는 `#0016` 경로에서 따로 측정해야 한다. 인덱스 캐시가 디스크를 크게 차지하고(회사 repo 기준 수 GB 가능) 수동 정리가 필요하다. base commit 이 바뀔 때마다 재인덱싱이라 fixture 를 늘리면 첫 실행 비용이 선형으로 증가한다.

#### ADR-012 보강 (2026-08-10, 구현 중 발견)
최초 결정은 `real_pipeline.py` 가 `get_llm_client`·`propose_and_build` 를 직접 부르도록 했으나, `#0004` 부터 있던 계층 가드(`tests/test_evaluation.py::test_evaluation_layer_has_no_forbidden_imports`)가 `app/evaluation/` 전체에서 `app.llm` import 를 금지한다 — 정면 충돌이다. 그 가드는 "evaluation 은 **측정** 계층이며 생성 스택에 의존하지 않는다"를 강제하고, ARCHITECTURE 의 문서화된 규칙("DB·FastAPI 없이 실행 가능")보다 한 단계 엄격하다.

**보강 결정**: 초안 생성을 evaluation 밖으로 뺀다.

1. `app/application/replay_draft.py`(신규)가 `get_llm_client()` + `propose_and_build` 로 **초안 생성 함수**를 만든다. LLM 의존은 여기에만 있다. `app/application/` 은 이미 `MappingService`·`ProposalService` 가 orchestrator 와 LLM 을 조합하는 자리라 성격이 맞는다.
2. `app/evaluation/replay/real_pipeline.py` 는 인덱싱·검색·스니펫 구성까지만 하고 **`draft_fn` 을 주입받는다**. 주입되지 않으면 오류다 — 기본값으로 LLM 을 끌어오면 같은 문제가 되돌아온다.
3. `runner.py` 의 CLI 는 `--pipeline real` 분기에서만 application 팩토리를 지연 import 한다.

**이유**: 가드에 예외를 뚫는 것은 가드가 막으려던 바로 그 일을 편의를 위해 허용하는 것이다. `importlib`·`from app import llm` 같은 회피는 문자열 검사만 통과시킬 뿐 의존을 숨긴다. 의존을 제자리(application)로 옮기면 가드도 설계 의도도 함께 유지된다.

**추가 트레이드오프**: replay 실제 파이프라인이 두 계층에 걸쳐 있어 배선 지점이 하나 늘어난다. `app/application/` 이 `ReplayContext`·`PipelineOutput` 계약(evaluation 정의)을 import 하므로 계약 방향이 evaluation → application 한 방향으로 유지되는지 리뷰에서 확인해야 한다.

### ADR-013: 코드 심볼 인덱스는 휴리스틱 추출 + gitignore 캐시로, provider는 #0020에 분리 (Issue #0019)
**결정**: Java·MyBatis XML·SQL 에서 최소 심볼(Java class/method, MyBatis namespace/statement id, test method, constant usage)과 관계(class→method, Service→Mapper, Test→Service, constant usage)를 추출하는 `app/embedding/symbol_index.py` 를 만든다. `term_dict.py`·`const_inventory.py` 와 같은 **harvest/load/cache 가족**이다 — `harvest(adapter)` → JSON 직렬화 그래프, `load(adapter, refresh)` → `symbol_index_cache.json`(프로젝트 루트, gitignore, 자동 재생성). 파싱은 **정규식·휴리스틱**이며 진짜 파서·신규 의존성을 쓰지 않는다(`_chunk_java` 의 중괄호 매칭 재사용). 소스 파일 접근은 **`CodebaseAdapter` 를 경유**한다(`list_files()`/`read_file()`) — `EXCLUDED_DIRS`(빌드 산출물 제외)와 CP949 처리를 상속하기 위해서다. `RetrievalSource.CODE_GRAPH`(이미 예약)를 소비하는 `CodeGraphProvider` 와 검색 배선은 **이 이슈에 넣지 않고 #0020 으로 분리**한다. 파일 단위 try/except 로 파싱 실패 파일은 건너뛰고 개수만 기록하며 전체 추출을 중단시키지 않는다.
**이유**: 로드맵이 "완전한 컴파일러 수준 분석"과 Neo4j 를 명시적으로 비범위로 두었고, 레거시 eHR 은 깔끔히 파싱되지 않아 휴리스틱이 현실적이다. 신규 파서 의존성은 회사망 SSL 제약(HuggingFace·pip 차단)에서 설치 부담이 크다. 어댑터 경유는 2026-08-05 실측 교훈의 직접 반영이다 — 직접 파일 순회는 `EXCLUDED_DIRS`·CP949 를 다시 구현해야 하고, 빠뜨리면 빌드 산출물(exploded WAR)의 심볼이 인덱스를 오염시킨다. provider 분리는 로드맵 구조(#0020 이 이 인덱스를 소비)와 일치하며, #0019 를 mock 으로 완결(수용 기준=mock repo 심볼 추출·일부 관계 연결)할 수 있게 한다. 회사 실측에서 검색이 VO 필드/term_dictionary 에 편중된 것이 이 인덱스의 동기지만, 그 격차를 좁히는 재정렬은 #0020 의 몫이다 — #0019 는 재료(관계 그래프)만 만든다.
**트레이드오프**: 휴리스틱 추출은 오버로드·중첩 클래스·동적 MyBatis(`<include>`/`<sql>`)에서 놓치거나 오탐한다("일부 연결"이 수용 기준이라 감수). 캐시가 eHR 내부 구조 파생물이라 커밋되면 반출 사고가 된다 — `term_dict_cache.json` 등과 같은 gitignore·비커밋 규칙으로 방어한다. #0019 만으로는 검색 품질이 바뀌지 않아 효과가 #0020 에서야 측정된다(#0016 rerank 와 같은 지연 검증 구조).

### ADR-007: 도메인 레지스트리(domains.json)로 수집 확장
**결정**: 수집 대상 법령·고시 검색어를 코드가 아니라 `domains.json`(tax/hr)에서 관리. 파일 없으면 기존 세법 5종 폴백.
**이유**: 연말정산(세법)을 넘어 인사 법령 전반으로 확장 + 도메인별 담당자 라우팅 기반. 법률만 수집하면 올해 개정(시행령·시행규칙 10건, 법률 0건)을 전부 놓쳤을 상황 — 3계층 수집이 필수임을 실검증.
**트레이드오프**: 법제처 등록명 정확 일치 관리 부담 (가운뎃점 `ㆍ` U+318D 등).

### ADR-014: before/after는 개정문 파싱으로 파생하고, 원문은 amendment/reason 필드로 보존 (제안 이슈 #0023)
**결정**: `fetch_detail()`이 개정문을 `before_text`에, 제개정이유를 `after_text`에 넣던 것을 중단한다. 원문은 신규 필드 `amendment_text`(개정문)·`reason_text`(제개정이유)에 보존하고, `before_text`/`after_text`는 개정문의 정형 문형("제N조 중 'A'를 'B'로 한다" 등)을 파싱하는 순수 함수(`app/domain/changes/amendment.py`)로 **파생**한다. 파싱 실패 시 `before=""`/`after=개정문 전문` 폴백(전부 "추가" 해석). 제개정이유는 LLM 분석 컨텍스트로만 쓰고 값 델타 계산에는 넣지 않는다. 기존 행은 경량 마이그레이션으로 idempotent 이관·재파생한다. 상세: `docs/specifications/COLLECTION_SEMANTICS_SPEC.md`.
**이유**: `ChangeNormalizer`·분류기·정책 게이트가 두 필드를 "개정 전/후 조문"으로 간주해 값 델타를 계산하는데, 실 API 데이터에서는 "개정문 vs 제개정이유"라는 다른 종류의 문서를 비교하게 되어 감지 신호가 통째로 왜곡된다 — mock만 진짜 쌍이라 집 환경에서 관측되지 않는 결함이다. 개정문 자체가 공식적인 before→after 진술문이므로, 신구법 대비 API 추가 연동 없이 파싱만으로 올바른 쌍을 복원할 수 있다.
**트레이드오프**: 파서가 개정문 문형 변형을 못 잡으면 폴백으로 내려가 델타 정밀도가 떨어진다(폴백 여부를 `amendment_parsed`로 계측). `law_change` 컬럼 2개 증가. 조문별 행 분리(1공포=N행)는 이번에 하지 않아, 여러 조문이 한 행에 섞이는 기존 한계는 남는다.

### ADR-015: eHR 인덱싱은 .xfdl 1급 지원 + utf-8-sig 우선 + classes 제외·화이트리스트 기본 (제안 이슈 #0024)
**결정**: `.xfdl`(Nexacro)을 `SOURCE_EXTS`·청커(`_chunk_xfdl`: Script CDATA 추출 → 함수 단위 분리)·심볼 추출·용어 사전·상수 인벤토리의 1급 대상으로 추가한다. 파일 읽기는 `utf-8-sig → cp949 → utf-8(replace)` 순으로 통일하고 XML 선언의 encoding 속성은 신뢰하지 않는다. `EXCLUDED_DIRS`에 `"classes"`를 추가하며(`golden.py::_IGNORE` 동기), `_is_excluded`는 repo root **상대 경로** 기준으로 고친다. 경로별 산출물(`web/eHR/`, `nexacro14lib/` 등)은 `REPO_INDEX_PATHS` 화이트리스트를 1차 방어로 삼아 배제하고, 수확기(`term_dict`/`const_inventory`)의 스캔 루트도 같은 설정을 공유한다. 상세: `docs/specifications/EHR_INDEXING_SPEC.md`.
**이유**: 2026-08-14 eHR 실측 — 세법 한도값(직무발명보상금 비과세 등)이 Java/SQL이 아니라 **xfdl 내 JavaScript에 하드코딩**되어 있어, 현행 인덱싱으로는 수치 개정의 핵심 patch 대상이 검색에 절대 잡히지 않는다(재현율 공백 — 이 프로젝트 최우선 실패 유형). 최신 연말정산 mapper(`PayRefCom_2022~2026.xml`)가 CP949이고 선언·실제 불일치 파일이 실존해, BOM·스니핑 처리 없이는 컬럼 코드의 유일한 의미 사전(한글 주석)이 깨진다. `classes/`는 exploded WAR가 4중 재귀 중첩된 1.7GB라 블랙리스트 안전망이 필요하다.
**트레이드오프**: 화이트리스트 방식은 신규 모듈 추가 시 운영자가 목록을 갱신해야 커버된다(누락 위험을 문서화로 완화). xfdl 레이아웃부(Dataset 등)는 버리므로 화면 구조 기반 검색은 안 된다(#0020 계열로 이월). `"classes"`가 일반적 이름이라 정당한 소스 디렉토리를 배제할 이론적 위험이 있다(상대 경로 판정 + 화이트리스트 우선으로 완화).

### ADR-016: DB 데이터 개정은 ChangeType이 아니라 레지스트리 기반 직교 라우팅으로 분리 (제안 이슈 #0025)
**결정**: 소득세율표·4대보험요율처럼 eHR에서 DB 데이터(`T_PAY_TAX`/`T_INS_RATE` 계열)로 관리되는 항목의 개정을, 큐레이션된 `DbDataRegistry`(domains.json `db_items` 확장, 라벨 일반화) 정확 매칭으로 판정해 새 결정값 `AutomationDecision.DB_UPDATE_GUIDANCE`로 라우팅한다. 매칭 건은 코드 draft 정책 게이트·LLM 생성 경로에 진입하지 않고 "DB 갱신 안내"(대상 항목 라벨 + 조문 전후값 + 안내문구)를 산출한다. 분류·검색은 그대로 수행해 개정 감지·기록은 유지한다. 상세: `docs/specifications/DB_DATA_ROUTING_SPEC.md`.
**이유**: DB-backing은 rate/value 등 ChangeType과 직교한 축이다 — 새 ChangeType으로 흡수하면 "무엇이 바뀌었나" 정보가 손실되고, "코드 매핑이 없으면 DB일 것"이라는 retrieval 기반 추론은 검색 미스를 DB 개정으로 오인해 "개정 놓침"(이 프로젝트 최악의 실패)을 유발한다. 큐레이션 레지스트리의 정확 매칭만 쓰면 오인 없이 코드 patch 무의미 항목을 올바른 안내로 전환할 수 있다. NO_CODE_IMPACT(법인세 등 미구현)와 DB_DATA(구현하되 DB)를 구분한다.
**트레이드오프**: 레지스트리는 담당자가 수기 큐레이션해야 커버된다(누락 시 종전대로 코드 경로/차단으로 안전 폴백 — 재현율은 유지). 실제 DB 테이블/컬럼명은 보안상 커밋 파일에 못 넣어 라벨 일반화만 두므로, 실제 갱신 위치 매핑은 담당자 로컬 지식에 남는다. 초기 항목·값 델타 외 안내 상세는 열린 질문(스펙 §9).

### ADR-017: 그래프 검색은 독립 provider가 아니라 merge 이후 확장 단계 + 실물 정합 (제안 이슈 #0020)
**결정**: 그래프 검색을 orchestrator의 **선택적 "merge 이후 확장 단계"**로 배선한다(독립 provider ✗). 파이프라인은 `providers → merge → graph-expand → rerank → truncate`이며, `config.graph_enabled`(기본 False)로 가드해 off면 결과가 기존과 바이트 동일하다(기존 rerank 단계와 동일 패턴). 확장 단계는 **merge된 상위 N 후보를 seed로 재사용**한다 — RagProvider가 이미 계산한 후보를 쓰므로 RAG를 다시 돌리지 않는다(이중 RAG 금지). seed→`SymbolNode` 매핑 후 `SymbolGraph.edges` 이웃 순회(depth 1 기본·2 상한, allowlist·top-N·max_neighbor·산출물 제외)로 관계 파일을 `RetrievalSource.CODE_GRAPH` evidence로 얹는다. EdgeKind별 고정 점수(service_to_mapper 0.75/uses_constant 0.70/test_to_service 0.65/contains 0.60). **CODE_GRAPH는 자동화 정책의 `min_independent_sources` 카운트에서 제외**한다(그래프는 seed에 인과 종속 — 단독으로도 borrowed seed로도 draft를 유발하지 않는다). 또한 `CODE_GRAPH_SPEC.md`를 **실물(`symbol_index.py`)에 맞게 갱신**한다 — 이전 스펙의 CodeSymbol/CodeRelation(confidence·evidence)·SQLite 3테이블·commit snapshot·`CodeGraph.neighbors()` API는 실제로 구현되지 않았고 도입하지 않는다. 실물은 `SymbolNode`/`SymbolEdge`/`SymbolGraph`(인메모리 + `symbol_index_cache.json`, 읽기 쉬운 id, confidence 없는 4 EdgeKind)다. 상세: `docs/specifications/CODE_GRAPH_SPEC.md`.
**이유**: 그래프 확장의 본질은 독립 검색이 아니라 "이미 나온 후보를 seed로 넓히는 후처리"다. 이를 독립 provider로 모델링하면 seed를 얻으려 RAG를 provider 안에서 다시 돌려 이중 RAG(latency 낭비)가 되고, 자가 seed는 품질도 낮다. merge 이후 단계로 두면 (1) 이중 RAG 없이 진짜 상위 후보를 seed로 쓰고(그래프가 실제로 도움 될 가능성↑), (2) flag off 시 기존 경로가 그대로라 동작 보존이 명확하며, (3) rerank 선례가 있어 orchestrator 변경이 가산적·저위험이다. #0019 엣지는 regex 휴리스틱("일부 연결")이라 오탐 확장이 노이즈를 얹을 수 있으므로, 단독 draft 금지(인과 종속 근거 제외)와 ablation(불필요후보율 포함)으로 안전하게 관리한다. 스펙은 코드가 기준이라는 프로젝트 원칙(문서≠코드 시 코드)에 따라 실물로 갱신한다.
**트레이드오프**: orchestrator에 단계 하나를 추가해 공용 경로가 커진다(회귀는 off-불변 테스트로 고정). 고정밀 엣지·depth 1로 시작해 노이즈를 억제하나, 그래프 이득 자체가 불확실하며 실 eHR 수치는 9월 W1(실 fixture) 이후에만 확정된다 — 현 ablation은 합성 mock 기반. EdgeKind 점수 절대값은 미보정(9월 W4 캘리브레이션 대상).

### ADR-018: 그래프 검색은 이 코드베이스의 레버가 아님 — xfdl 실험으로 확정, 코드 미채택 (제안 이슈 #0020 후속)
**결정**: #0020 그래프가 실 eHR서 무효(0/25)인 원인을 규명하기 위해 별도 실험(feat-graph-xfdl-exp)에서 symbol_index에 xfdl 심볼 추출 + 파일 간 특이수치 공동출현 엣지(SHARES_VALUE)를 추가해 재측정했다. 결과: 그래프를 노드 4,969→15,796·엣지 4,150→14,568로 3배 키워도 **graph on/off × 실 25건 = 0/25(무변화)**. 진단으로 근본원인을 확정했다 — 엣지의 94%가 same-file CONTAINS(확장 무용), **SERVICE_TO_MAPPER=0·USES_CONSTANT=0**. seed→노드 매핑은 되나 그 노드에서 다른 파일로 가는 유효 엣지가 없다. 원인은 xfdl이 아니라 **eHR 전체가 서비스 호출을 SvcID 런타임 간접참조로 해석**해 콜그래프가 코드 텍스트에 존재하지 않는다는 것(xfdl→backend에 `.xp`·statement id 없음, 표준 transaction 호출 0건 — 2026-08-20 실측). **결론: 그래프 검색은 이 코드베이스의 레버가 아니다. `graph_enabled=False`를 영구 유지하고, 실험 코드(xfdl 심볼·SHARES_VALUE 엣지)는 main에 채택하지 않는다**(off여도 harvest 비용만 늘고 효용 0). 그래프 기제 자체(#0020, merge 이후 확장 단계)는 이미 main에 있고 off 기본이라 그대로 둔다.
**이유**: 저비용 실험의 목적은 "그래프를 더 투자할지/접을지"를 근거로 판정하는 것이었고, 측정이 그 답을 명확히 냈다. xfdl을 넣어 그래프를 3배 키워도 0/25라는 것은 문제가 특정 파일유형이 아니라 코드베이스의 구조(SvcID 간접참조)임을 증명한다 — 텍스트 휴리스틱으로 복원 불가능한 콜그래프에 그래프 검색은 성립하지 않는다. 코드를 채택하면 쓰지도 않을 엣지 계산을 매 재인덱싱마다 지불하게 되므로 문서(이 결론)만 남긴다.
**트레이드오프**: xfdl 심볼 추출 코드는 심볼 커버리지 측면의 잠재 가치가 있으나, 그래프가 유일 소비처였고 그래프를 접으므로 지금은 채택하지 않는다(필요 시 이 ADR과 feat-graph-xfdl-exp 이력에서 복원 가능). 9월 성능 개선은 그래프 무관 레버(few-shot·reranker 캘리브레이션 등)로 방향을 고정한다.
