# Step 1: replay-loader

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 코드는 외부로 나가지 않는다, 설정은 config 경유)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙)
- `/docs/architecture/ADR.md` (**ADR-010** — path XOR path_env, revision 문자 제한, golden_command allowlist의 근거)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§3 fixture 포맷, §5 git allowlist**)
- `/app/evaluation/replay/fixture.py` (Step 0 산출물 — 이 로더가 만들어낼 타입)
- `/app/evaluation/loader.py` (**본보기 패턴** — 오류 누적 방식, 상수 집합, `_parse_*` 분해, path traversal 검사)
- `/app/evaluation/errors.py` (`DatasetValidationError` — 재사용 대상)
- `/app/evaluation/decision_fixtures.py` (최근 추가된 YAML 로더 스타일 — `yaml.safe_load`)

`app/evaluation/loader.py`의 오류 처리 방식을 꼼꼼히 읽어라. 이 로더도 **첫 오류에서 멈추지 않고 케이스별 오류를 모아 한 번에 보고**해야 한다.

## 작업

`app/evaluation/replay/loader.py`를 신규 생성한다.

```python
class ReplayFixtureLoader:
    def __init__(self, root_dir: Path | str | None = None, check_paths: bool = False) -> None: ...

    def load_yaml(self, path: Path | str) -> list[ReplayFixture]: ...
```

`app/evaluation/loader.py`와 동일하게 `cases:` 키를 가진 dict / 리스트 / 단일 dict 세 형태를 받는다. 파싱은 `yaml.safe_load`만 쓴다.

오류는 `DatasetValidationError`(`app/evaluation/errors.py`)를 재사용하고, `details`에 `[case_id] 사유` 형식으로 누적한다. **새 예외 타입을 만들지 마라.**

### 검증 규칙 — 이 step의 핵심

fixture YAML은 이 이슈의 **유일한 외부 입력 지점**이다. 아래 네 가지는 반드시 로더가 막는다.

#### (1) repo 위치: `path` XOR `path_env`

- 둘 다 있으면 오류, 둘 다 없어도 오류.
- `path`(mock 전용): 프로젝트 상대 경로만. 절대경로(`/`로 시작) 거부, `..` 구성요소 거부.
- `path_env`(실데이터 전용): **환경변수 이름만** 담는다. 이름 형식은 `[A-Z][A-Z0-9_]*`로 제한한다. 로더는 환경변수를 **읽지 않는다** — 해석은 #0018 runner의 몫이고, 여기서 읽으면 로드 시점에 회사 경로가 메모리·오류 메시지로 새어나온다.
- 이유(주석으로 남겨라): 회사 repo 절대경로가 YAML에 남으면 fixture 파일 자체가 반출 위험물이 된다(ADR-010).

#### (2) git revision 문자 제한

`base_commit`·`answer_commit`에 적용한다.

- 허용 문자: `[A-Za-z0-9._/-]` 만.
- 거부: 빈 문자열, `..`를 포함하는 값, `^`·`~`·`:`·공백·`-`로 시작하는 값.
- `-`로 시작하는 값을 특히 막아라. 이유: `--upload-pack=...` 같은 값이 git 인자로 해석되면 임의 명령 실행이 된다(#0018이 이 값을 git에 넘긴다).

#### (3) `golden_command` 실행파일 allowlist

- 값이 있으면 `shlex.split()`으로 토큰화하고 **첫 토큰**을 allowlist와 대조한다. 파싱 실패(따옴표 불균형 등)도 오류다.
- allowlist는 모듈 상수로 명시하고 최소한 `mvn`, `gradle`, `./gradlew`, `pytest`를 포함한다. **범용 셸(`bash`/`sh`/`zsh`)은 넣지 마라** — 허용 도구는 replay 대상 repo 안의 스크립트를 실행하지만 `bash -c "<문자열>"`은 fixture YAML에 적힌 문자열을 그대로 실행해 allowlist를 무의미하게 만든다.
- 첫 토큰이 절대경로거나 `/`를 포함하면 거부한다(`./gradlew`는 예외로 허용).
- 이유(주석으로 남겨라): `config.golden_test_cmd`는 운영자가 `.env`에 직접 넣는 값이지만 fixture YAML은 **파일로 주고받을 수 있어 신뢰 수준이 다르다.** 입구에서 막는다(ADR-010).
- 이 step은 명령을 **실행하지 않는다.** 검증만 한다.

#### (4) scope 경로 traversal

`relevant_paths`·`excluded_paths`·`expected_replacements[].path` 전부에 대해 절대경로와 `..` 구성요소를 거부한다.

추가 검증:

- `schema_version`은 `{"1"}`만 허용.
- `source_type`은 `{"local_git"}`만 허용.
- `privacy_mode`는 `PrivacyMode` 값만 허용(미지 값 → 오류).
- `timeout_seconds` 범위 검사(`app/evaluation/loader.py`의 `MIN_TIMEOUT`/`MAX_TIMEOUT` 값을 재사용하거나 같은 범위를 쓴다).
- `case_id` 중복 검사.
- `relevant_paths`와 `excluded_paths`에 같은 경로가 동시에 있으면 오류.
- `check_paths=True`이고 `root_dir`이 있으며 `repository.path`가 설정된 경우에만 repo 디렉토리 존재를 확인한다. `path_env` 케이스는 **경로 검사를 건너뛴다**(환경변수를 읽지 않으므로 확인할 수 없다).

## 테스트

`tests/test_replay_loader.py` 신규 작성. 최소 아래를 덮어라:

- 정상 fixture 로드 → `ReplayFixture` 필드가 YAML과 일치.
- `path`와 `path_env` 둘 다 지정 → 오류 / 둘 다 없음 → 오류 / 각각 하나만 → 성공.
- `path`가 절대경로·`..` 포함 → 오류.
- `path_env` 이름이 소문자·하이픈 등 형식 위반 → 오류.
- **로더가 환경변수를 읽지 않는지**: 존재하지 않는 이름을 `path_env`에 줘도 로드가 성공하는지(monkeypatch로 환경변수 미설정 확인).
- revision 거부 케이스 각각: 빈 값, `..` 포함, `^`/`~` 포함, 공백 포함, `-`로 시작(`--upload-pack=evil`).
- revision 허용 케이스: 40자 SHA, `case1/base` 같은 태그명.
- `golden_command`: allowlist 통과(`mvn -q test -Dtest=X`), 거부(`rm -rf /`, `curl ... | sh`), 절대경로 거부(`/usr/bin/mvn`), `./gradlew` 허용, 따옴표 불균형 → 오류.
- `privacy_mode` 미지 값 → 오류, 생략 시 `METADATA_ONLY`.
- `relevant_paths` ∩ `excluded_paths` 비어 있지 않음 → 오류.
- 중복 `case_id` → 오류.
- **오류가 여러 개일 때 한 번의 예외에 전부 담기는지**(`details` 길이 확인) — 첫 오류에서 멈추면 fixture 수정이 여러 번 왕복하게 된다.

임시 YAML은 `tmp_path` fixture로 만들어라. 실제 git repo나 네트워크가 필요한 테스트를 여기에 넣지 마라(Step 2·3의 범위).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `yaml.safe_load`를 썼는가(`yaml.load` 금지)?
   - 로더가 `os.environ`을 읽지 않는가? (`grep -n "environ\|getenv" app/evaluation/replay/loader.py` 결과가 비어야 한다)
   - subprocess·git 호출이 없는가?
   - `DatasetValidationError`를 재사용했는가(새 예외 타입 미생성)?
   - 오류가 누적 보고되는가(첫 오류에서 raise하지 않는가)?
3. 결과에 따라 `phases/issue-0017/index.json`의 step 1을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, 검증 규칙 4종, allowlist 내용 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- 로더에서 `os.environ`/`os.getenv`로 `path_env`를 해석하지 마라. 이유: 로드 시점에 회사 절대경로가 메모리와 오류 메시지에 실린다. 해석은 #0018 runner의 몫이다.
- `yaml.load`(Loader 미지정)를 쓰지 마라. 이유: 임의 객체 생성이 가능해 fixture 파일이 코드 실행 경로가 된다.
- `golden_command`를 실행하거나 `shell=True`로 넘기는 코드를 쓰지 마라. 이유: 이 step은 검증만 한다. 실행은 #0018이며 그때도 shell 없이 인자 배열로 넘긴다.
- git·subprocess를 호출하지 마라. 이유: 로더는 선언을 읽을 뿐이고, ARCHITECTURE.md가 `app/evaluation/replay/`를 계약 계층으로 정의했다.
- `app/evaluation/loader.py`(기존 `DatasetLoader`)를 수정하지 마라. 이유: benchmark·runner·테스트 다수가 의존하며 ADR-010이 두 스키마 분리를 결정했다.
- allowlist를 통과시키려고 첫 토큰 검사 대신 문자열 부분일치를 쓰지 마라. 이유: `echo mvn; rm -rf /` 같은 값이 통과한다.
- 기존 테스트를 깨뜨리지 마라.
