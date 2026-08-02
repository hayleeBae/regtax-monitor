# Step 2: replay-mock-repos

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 실제 repo는 절대 수정하지 않는다)
- `/docs/architecture/ARCHITECTURE.md` (`evaluation/` 데이터 구조 — `replay_sources/`는 커밋 대상, `replay_repos/`는 gitignore)
- `/docs/architecture/ADR.md` (**ADR-010** — mock git repo를 커밋하지 않고 빌드하는 이유)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§10 최소 fixture 3건**, §4 worktree 절차, §5 git allowlist)
- `/evaluation/fixtures/repositories/mock_tax/` (기존 mock 코드 fixture — 파일 스타일 참고)
- `/mock_repo/` (집 개발용 가짜 eHR — Java/SQL/XML 구성 참고)
- `/scripts/` 하위 기존 스크립트 (CLI 스타일, argparse 사용법)
- `/.gitignore`

## 작업

replay용 mock git repo 3건의 **원본 파일 트리를 커밋**하고, 그것으로부터 실제 git repo를 만드는 **빌드 스크립트**를 작성한다.

### 1) 원본 파일 트리 (커밋 대상)

```
evaluation/fixtures/replay_sources/
├── case1_value_change/{base,answer}/...
├── case2_condition_test/{base,answer}/...
└── case3_unrelated_noise/{base,answer}/...
```

각 케이스는 `base/`와 `answer/`에 **완전한 파일 트리 스냅샷**을 담는다(diff가 아니라 전체 내용). 빌드 스크립트가 base 트리로 커밋 1개, answer 트리로 커밋 1개를 만든다.

케이스 구성은 스펙 §10을 그대로 따른다:

1. **case1_value_change** — 단일 value change. Java 상수 하나가 바뀐다(예: `CHILD_TAX_CREDIT = 150000L` → `250000L`). answer는 그 파일 하나만 변경.
2. **case2_condition_test** — 조건 변경 + **테스트 파일 동시 수정**. answer가 서비스 로직의 조건과 대응하는 테스트를 함께 고친다. 정답이 파일 2개인 사례를 만드는 것이 목적이다.
3. **case3_unrelated_noise** — answer commit에 **무관한 문서 변경이 섞인** 사례. 코드 1개 파일 + `README.md`(또는 `CHANGELOG.md`) 수정이 한 커밋에 들어간다. 이 케이스가 있어야 "answer commit 전체를 정답으로 보지 않는다"(스펙 §2·§11)를 검증할 수 있다.

파일은 기존 mock fixture처럼 Java 중심으로 간결하게 만든다. 실제 eHR 코드를 베끼지 마라 — 합성 데이터다.

### 2) 빌드 스크립트

`scripts/build_replay_repos.py` (argparse CLI, `main(argv) -> int` 형태).

**위치가 `scripts/`인 이유**: ARCHITECTURE.md 레이어 규칙이 `app/evaluation/replay/`를 "선언만 담는 계약 — git 실행·파일 쓰기 금지"로 정의했다. 빌드는 정확히 git 실행과 파일 쓰기이므로 `app/` 아래에 두면 규칙 위반이다.

동작:

1. `evaluation/fixtures/replay_sources/<case>/base/`를 대상 디렉토리에 복사 → `git init` → commit → **태그 `<case>/base`**
2. 작업 트리를 `answer/` 내용으로 교체(기존 파일 삭제 포함) → commit → **태그 `<case>/answer`**
3. 3개 케이스 반복

핵심 규칙:

- 기본 출력 경로는 `evaluation/fixtures/replay_repos/`다. **삭제·쓰기 대상은 이 디렉토리 내부로만 한정한다.** CLI로 임의 경로를 받아 지우는 옵션을 만들지 마라.
- 재실행 가능해야 한다(idempotent): 기존 출력 디렉토리가 있으면 지우고 새로 만든다. 단 삭제 직전에 **그 경로가 출력 루트 하위인지 확인**하고, 아니면 즉시 오류로 중단하라.
- git 커밋이 환경에 따라 실패하지 않도록 `-c user.name=...`, `-c user.email=...`를 명령 인자로 주입한다. 전역 git 설정을 바꾸지 마라.
- `subprocess.run`은 **`shell=False`(인자 배열)** 로만 호출하고 모든 호출에 `timeout`을 준다(스펙 §5).
- git이 없거나 명령이 실패하면 명확한 메시지와 함께 0이 아닌 종료 코드를 반환한다.
- 태그명은 Step 1 로더의 revision 문자 규칙(`[A-Za-z0-9._/-]`)을 만족해야 한다. `case1_value_change/base` 형태면 통과한다.

### 3) gitignore

`.gitignore`에 `evaluation/fixtures/replay_repos/`를 추가한다. `replay_sources/`는 **커밋 대상이므로 무시하지 마라.**

## 테스트

`tests/test_replay_repo_builder.py` 신규 작성:

- 빌드 후 3개 repo가 생기고 각 repo에 `<case>/base`·`<case>/answer` 태그가 있는지(`git tag -l`).
- base 태그 시점의 파일 내용이 `replay_sources/<case>/base/`와 일치하는지(`git show <tag>:<path>`).
- **case3의 answer diff에 코드 파일과 문서 파일이 함께 들어 있는지** — 이 케이스의 존재 이유다.
- **case2의 answer diff에 파일이 2개(로직 + 테스트) 있는지.**
- 재실행 idempotency: 두 번 연속 빌드해도 성공하고 결과가 같은지.
- 출력 루트 밖의 경로가 주어지면 삭제하지 않고 오류로 중단하는지.
- git 미설치 환경을 흉내낸 경우(또는 명령 실패 시) 0이 아닌 코드로 끝나는지.

테스트는 `tmp_path`를 출력 경로로 써서 저장소의 `evaluation/fixtures/replay_repos/`를 건드리지 않게 하라. 다만 "출력 루트 밖 삭제 거부" 테스트는 실제로 파일을 지우지 않는 경로로 구성하라.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
source .venv/bin/activate && python3 scripts/build_replay_repos.py && git -C evaluation/fixtures/replay_repos/case3_unrelated_noise tag -l
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 두 번째 커맨드가 `case3_unrelated_noise/answer`·`case3_unrelated_noise/base` 태그를 출력하면 성공이다.
2. 아키텍처 체크리스트를 확인한다:
   - `git status`가 깨끗한가? (`replay_repos/`가 gitignore되어 추적되지 않아야 한다)
   - `subprocess` 호출이 전부 `shell=False`이고 timeout이 있는가?
   - 삭제 로직이 출력 루트 하위로만 한정되는가?
   - `app/` 아래에 git을 실행하는 코드를 추가하지 않았는가?
3. 결과에 따라 `phases/issue-0017/index.json`의 step 2를 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "3개 케이스 구성, 태그 네이밍, 빌드 스크립트 경로·안전 규칙 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요(git 미설치 등) → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- CLI 인자로 임의 경로를 받아 삭제하는 옵션을 만들지 마라. 이유: 오타 하나로 사용자 디렉토리가 날아간다. 출력 루트는 코드에 고정하고 하위만 다룬다.
- `shell=True`를 쓰지 마라. 이유: 경로에 공백·특수문자가 있으면 명령이 조립되어 임의 실행으로 이어진다.
- 전역 git 설정(`git config --global`)을 변경하지 마라. 이유: 사용자 환경을 오염시킨다. `-c` 인자로 커밋마다 주입한다.
- `mock_repo/`나 프로젝트 저장소 자체에 커밋·태그를 만들지 마라. 이유: CLAUDE.md CRITICAL — 실제 repo는 수정하지 않는다.
- 생성된 git repo(`replay_repos/`)를 커밋하지 마라. 이유: `.git` 중첩으로 서브모듈 취급되며, ADR-010이 빌드 방식을 결정했다.
- 실제 eHR 코드를 베껴 넣지 마라. 이유: 외부 반출 금지 대상이다. 합성 Java 코드를 쓴다.
- fixture YAML을 이 step에서 만들지 마라. 이유: Step 3의 범위다.
- 기존 테스트를 깨뜨리지 마라.
