# Step 3: replay-fixtures

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL 규칙)
- `/docs/architecture/ADR.md` (**ADR-010**)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§3 fixture 포맷 예시, §8 privacy, §10 최소 fixture 3건, §12 수용 기준**)
- `/app/evaluation/replay/fixture.py` (Step 0 — 필드와 기본값)
- `/app/evaluation/replay/loader.py` (Step 1 — 검증 규칙. fixture는 이 규칙을 전부 통과해야 한다)
- `/scripts/build_replay_repos.py`, `/evaluation/fixtures/replay_sources/` (Step 2 — 케이스 구성과 태그 이름)
- `/evaluation/datasets/core.yaml` (기존 데이터셋 YAML 스타일 — 주석·들여쓰기)
- `/evaluation/datasets/company_private.template.yaml` (private 템플릿 스타일)

Step 2가 만든 3개 케이스의 실제 파일 경로와 태그 이름을 확인한 뒤 fixture를 작성하라. **경로나 태그를 추측하지 마라.**

## 작업

### 1) mock fixture 3건

`evaluation/fixtures/replay/mock_cases.yaml`(경로·파일명은 재량, `replay_sources/`와 구분되게) 신규 작성. Step 2의 3개 케이스에 1:1 대응한다.

각 fixture는 스펙 §3 포맷을 따른다:

```yaml
cases:
  - schema_version: "1"
    case_id: "replay_mock_value_change"
    law:
      law_name: "소득세법"
      tier: "law"
      article: "제59조의2"
      before_text: "..."
      after_text: "..."
    repository:
      source_type: "local_git"
      path: "evaluation/fixtures/replay_repos/case1_value_change"
      base_commit: "case1_value_change/base"
      answer_commit: "case1_value_change/answer"
    scope:
      relevant_paths: [...]
      excluded_paths: [...]
      expected_replacements:
        - {path: "...", before: "150000L", after: "250000L"}
    execution:
      privacy_mode: "full"
      timeout_seconds: 600
    metadata:
      reviewed: true
```

핵심 규칙:

- mock fixture는 `path`(프로젝트 상대)를 쓴다. `path_env`는 실데이터 전용이다.
- `base_commit`/`answer_commit`은 Step 2가 만든 **태그 이름**을 쓴다(SHA 아님 — 빌드마다 달라진다).
- mock은 합성 데이터이므로 `privacy_mode: "full"`이 적절하다. 코드 반출 문제가 없다.
- **case3(unrelated noise)의 `excluded_paths`에 문서 파일을 반드시 명시한다.** 이것이 "answer commit 전체를 자동 정답으로 쓰지 않는다"(스펙 §2·§13)를 데이터로 표현하는 지점이다.
- case2는 `relevant_paths`에 로직 파일과 테스트 파일을 **둘 다** 넣는다.
- `golden_command`는 mock에 실행 가능한 빌드 도구가 없으므로 **생략한다**(None). 넣으려면 Step 1 allowlist를 통과해야 한다.

### 2) 회사 실데이터 템플릿

`evaluation/datasets/company_private.template.yaml`과 같은 위치·명명 규칙으로 replay용 템플릿을 1건 추가한다(예: `evaluation/datasets/company_replay.template.yaml`).

- `path` 대신 **`path_env: "EHR_REPO_ROOT"`** 를 쓴다. 실제 절대경로를 템플릿에 적지 마라.
- `base_commit`/`answer_commit`은 `"<실제 SHA로 교체>"` 같은 placeholder로 둔다. **실제 SHA를 넣지 마라.**
- `privacy_mode: "metadata_only"`를 기본으로 한다(스펙 §8).
- 파일 상단 주석에 다음을 명시한다: 이 템플릿을 복사해 `evaluation/private/`에 두고 쓸 것, `evaluation/private/`는 gitignore이므로 **작성한 실데이터 fixture를 커밋하지 말 것**.
- 템플릿 자체에는 실제 경로·SHA·코드가 들어가지 않으므로 커밋 대상이다.

### 3) 통합 테스트

`tests/test_replay_fixtures.py` 신규 작성:

- **mock fixture 3건이 `ReplayFixtureLoader`로 오류 없이 로드되는지.** Step 1 검증 규칙을 전부 통과해야 한다.
- 회사 템플릿도 로더를 통과하는지(`path_env` 경로, placeholder revision 포함). placeholder가 revision 문자 규칙을 위반하면 템플릿을 규칙에 맞게 고쳐라 — 로더를 느슨하게 만들지 마라.
- 로드된 fixture의 `base_commit`/`answer_commit` 태그가 **실제로 빌드된 repo에 존재하는지**. Step 2 빌드 스크립트를 `tmp_path`에 실행해 확인하거나, `replay_repos/`가 이미 있으면 그것을 쓰되 **없으면 skip**하라(빌드는 git이 필요하므로 환경에 따라 불가능할 수 있다).
- case3 fixture의 `excluded_paths`가 비어 있지 않은지 — 무관 변경 배제가 데이터에 실제로 표현됐는지 고정한다.
- case2 fixture의 `relevant_paths`가 2개 이상인지.
- 회사 템플릿에 절대경로·40자 SHA 형태의 값이 **없는지** 문자열 검사로 단언(실수로 실데이터가 커밋되는 것을 막는 회귀 테스트).

무거운 의존성(임베딩 모델 로드, ChromaDB 인덱싱, LLM 호출)을 트리거하지 마라(CLAUDE.md).

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
source .venv/bin/activate && python3 -c "
from app.evaluation.replay.loader import ReplayFixtureLoader
cases = ReplayFixtureLoader().load_yaml('evaluation/fixtures/replay/mock_cases.yaml')
print(len(cases), [c.case_id for c in cases])
"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 두 번째 커맨드가 3건과 case_id 목록을 출력하면 성공이다.
2. 아키텍처 체크리스트를 확인한다:
   - fixture가 Step 1 로더 검증을 **수정 없이** 통과하는가? (로더를 느슨하게 고쳐서 통과시키지 않았는지 `git diff app/evaluation/replay/loader.py`로 확인)
   - 회사 템플릿에 실제 절대경로·SHA·코드 본문이 없는가?
   - `evaluation/private/`에 파일을 만들지 않았는가? (그 디렉토리는 사용자가 회사에서 채운다)
   - case3의 `excluded_paths`, case2의 다중 `relevant_paths`가 실제로 들어갔는가?
3. 결과에 따라 `phases/issue-0017/index.json`의 step 3을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "fixture 파일 경로, 3건 구성, 회사 템플릿 위치·기본 privacy_mode 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"` 후 즉시 중단

## 금지사항

- fixture를 통과시키려고 Step 1 로더의 검증을 느슨하게 고치지 마라. 이유: 로더는 보안 경계다. fixture가 규칙에 맞춰야지 그 반대가 아니다.
- 회사 템플릿에 실제 repo 절대경로·commit SHA·코드 본문을 넣지 마라. 이유: CLAUDE.md 코드 반출 금지. 템플릿은 커밋되는 파일이다.
- `evaluation/private/` 아래에 파일을 만들지 마라. 이유: 그 디렉토리는 회사 환경에서 사용자가 채우는 자리이며 gitignore 대상이다.
- mock fixture에 `path_env`를 쓰지 마라. 이유: 환경변수 지정 없이는 #0018이 실행할 수 없어 mock 3건이 무용지물이 된다.
- `base_commit`에 빌드된 SHA를 하드코딩하지 마라. 이유: 빌드마다 달라져 fixture가 즉시 깨진다. 태그를 쓴다.
- case3에서 문서 변경을 `relevant_paths`에 넣지 마라. 이유: 이 케이스의 목적이 "answer commit에 섞인 무관 변경을 정답에서 배제한다"를 표현하는 것이다.
- 기존 테스트를 깨뜨리지 마라.
