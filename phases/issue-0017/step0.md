# Step 0: replay-schema

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙 — 특히 **코드는 외부로 나가지 않는다**)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙 — `app/evaluation/`은 DB·FastAPI 없이 실행 가능해야 하고, `app/evaluation/replay/`는 **선언만 담는 계약**이다)
- `/docs/architecture/ADR.md` (**ADR-010** — 이 작업의 근거)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (구현 계약 — **§3 fixture 포맷, §7 지표, §8 privacy**)
- `/app/evaluation/case.py` (본보기 패턴 + `ExpectedReplacement` — 이 step이 재사용할 타입)
- `/app/domain/mappings/reranking.py` (최근 추가된 순수 계약 모듈의 스타일 — frozen dataclass, 스펙 조항 인용 docstring)

`app/evaluation/case.py`를 꼼꼼히 읽고 같은 스타일(frozen dataclass, Optional 기본값, 표준 라이브러리만 의존, 섹션 주석 구분)을 그대로 따르라.

## 작업

`app/evaluation/replay/__init__.py`와 `app/evaluation/replay/fixture.py`를 신규 생성한다. **표준 라이브러리만 사용한다** — yaml 파싱, 파일 읽기, git 실행, 경로 존재 검사를 여기서 하지 않는다(Step 1·2의 범위).

### 1) privacy 어휘와 정책 (스펙 §8)

```python
class PrivacyMode(str, Enum):
    FULL = "full"
    REDACTED = "redacted"
    METADATA_ONLY = "metadata_only"


class ArtifactKind(str, Enum):
    DIFF_BODY = "diff_body"            # diff 의 +/- 실제 코드 라인
    DIFF_STRUCTURE = "diff_structure"  # 파일 목록·hunk 헤더·변경 줄 수
    FILE_PATH = "file_path"            # 실제 상대 경로
    CODE_SNIPPET = "code_snippet"      # 코드 본문 발췌
    GOLDEN_OUTPUT = "golden_output"    # 골든 테스트 표준출력(스택트레이스에 코드가 나온다)
    METRIC = "metric"                  # 수치 지표
    HASH = "hash"
    COUNT = "count"


def allowed_artifacts(mode: PrivacyMode) -> frozenset[ArtifactKind]: ...
```

모드별 허용 집합은 **정확히 아래와 같이** 정의한다:

| ArtifactKind | FULL | REDACTED | METADATA_ONLY |
|---|---|---|---|
| DIFF_BODY | ✅ | ❌ | ❌ |
| DIFF_STRUCTURE | ✅ | ✅ | ❌ |
| FILE_PATH | ✅ | ✅ | ❌ |
| CODE_SNIPPET | ✅ | ❌ | ❌ |
| GOLDEN_OUTPUT | ✅ | ❌ | ❌ |
| METRIC / HASH / COUNT | ✅ | ✅ | ✅ |

이 설계의 핵심은 **화이트리스트**라는 점이다 — "위험한 것을 찾아 지우는" 마스킹이 아니라 "안전한 것만 남기는" 방식이라 누락으로 인한 유출이 구조적으로 발생하지 않는다. docstring에 이 의도를 남겨라.

`REDACTED`가 `DIFF_STRUCTURE`를 허용하는 이유도 적어라: `METADATA_ONLY`는 "뭔가 틀렸다"만 알려주고 "어디서 틀렸는지"를 답하지 못해 회사 환경에서 디버깅이 불가능하다. 구조(파일·hunk·줄 수)까지 있으면 코드 본문 없이도 어긋난 지점을 특정할 수 있다.

### 2) fixture 계약 (스펙 §3)

```python
REPLAY_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class ReplayRepository:
    source_type: str                    # 현재 "local_git" 만 유효
    base_commit: str
    answer_commit: str
    path: Optional[str] = None          # 프로젝트 상대 경로 (mock 전용)
    path_env: Optional[str] = None      # 절대경로를 담은 환경변수 이름 (실데이터 전용)


@dataclass(frozen=True)
class ReplayScope:
    relevant_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...] = ()
    expected_replacements: tuple[ExpectedReplacement, ...] = ()


@dataclass(frozen=True)
class ReplayExecution:
    privacy_mode: PrivacyMode = PrivacyMode.METADATA_ONLY
    golden_command: Optional[str] = None
    timeout_seconds: int = 1800


@dataclass(frozen=True)
class ReplayFixture:
    case_id: str
    law: LawInput                       # app/evaluation/case.py 재사용
    repository: ReplayRepository
    scope: ReplayScope
    execution: ReplayExecution
    reviewed: bool = False
    schema_version: str = REPLAY_SCHEMA_VERSION
```

- `ExpectedReplacement`와 `LawInput`은 `app/evaluation/case.py`에서 import해 재사용한다. **같은 모양의 dataclass를 새로 정의하지 마라.**
- `ReplayExecution.privacy_mode` 기본값은 `METADATA_ONLY`다. 이유: 스펙 §8이 "실제 회사 사례는 metadata_only를 기본으로 한다"고 정했고, 기본값이 느슨하면 실수로 코드가 저장된다.
- `__post_init__`에는 **자기완결적 불변식만** 넣어라(예: `case_id` 비어 있으면 ValueError, `timeout_seconds` 양수). `path` XOR `path_env` 같은 **입력 검증은 Step 1 로더의 책임**이다 — dataclass는 이미 검증된 값을 담는 그릇이다.

### 3) 패키지 export

`app/evaluation/replay/__init__.py`에서 위 심볼을 export 한다(`app/domain/mappings/__init__.py` 스타일).

## 테스트

`tests/test_replay_fixture.py` 신규 작성:

- `allowed_artifacts` 3개 모드 각각의 정확한 집합 — 위 표 그대로 단언하라.
- `PrivacyMode.METADATA_ONLY`에 `DIFF_BODY`·`CODE_SNIPPET`·`GOLDEN_OUTPUT`·`FILE_PATH`가 **없음**을 명시적으로 단언(회귀 시 코드 유출로 이어지는 지점이다).
- `PrivacyMode.REDACTED`에 `DIFF_BODY`·`CODE_SNIPPET`·`GOLDEN_OUTPUT`이 없고 `DIFF_STRUCTURE`·`FILE_PATH`가 있음을 단언.
- `ReplayExecution()` 기본 privacy_mode가 `METADATA_ONLY`인지.
- `ReplayFixture` 불변식: 빈 `case_id` → ValueError, `timeout_seconds` 0/음수 → ValueError.
- frozen 확인: 필드 대입 시 `FrozenInstanceError`.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
grep -n "^import\|^from" app/evaluation/replay/fixture.py
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 두 번째 AC 출력에 표준 라이브러리와 `app.evaluation.case`만 있는가? (yaml·subprocess·SQLAlchemy·FastAPI가 있으면 위반)
   - `ExpectedReplacement`/`LawInput`을 재정의하지 않고 재사용했는가?
   - `allowed_artifacts` 표가 위 명세와 정확히 일치하는가?
   - ARCHITECTURE.md의 `app/evaluation/replay/` 위치를 따르는가?
3. 결과에 따라 `phases/issue-0017/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성 파일, export 심볼, 모드별 허용 artifact 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- `app/evaluation/replay/fixture.py`에서 yaml·subprocess·os.system·파일 읽기/쓰기를 하지 마라. 이유: ARCHITECTURE.md 레이어 규칙이 이 패키지를 "선언만 담는 계약"으로 정의했고, git 실행이 섞이면 #0018 runner와 책임이 겹친다.
- `path` XOR `path_env` 검증을 dataclass `__post_init__`에 넣지 마라. 이유: Step 1 로더가 다른 검증들과 함께 오류를 모아 보고해야 하는데, dataclass가 먼저 예외를 던지면 오류 하나만 보이고 나머지가 가려진다.
- `ExpectedReplacement`·`LawInput`과 같은 모양의 dataclass를 새로 만들지 마라. 이유: 필드가 갈라지면 #0018 리포트에서 두 스키마를 따로 다뤄야 한다.
- `PrivacyMode` 기본값을 `FULL`로 두지 마라. 이유: 기본값이 느슨하면 회사 실행에서 실수로 코드 본문이 디스크에 남는다(CLAUDE.md 코드 반출 금지).
- 마스킹(치환) 기반으로 redacted를 구현하지 마라. 이유: 블랙리스트 방식은 누락 시 그대로 유출된다. 허용 집합 화이트리스트가 승인된 설계다.
- 기존 테스트를 깨뜨리지 마라.
