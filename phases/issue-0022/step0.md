# Step 0: replay-index-cache

## 읽어야 할 파일

- `/CLAUDE.md` (CRITICAL 규칙 — 특히 **테스트에서 임베딩 모델 로드·ChromaDB 인덱싱을 트리거하지 않는다**)
- `/docs/architecture/ARCHITECTURE.md` (레이어 규칙 — "replay 인덱스는 `evaluation/replay_index/<key>/`에만 만들고 운영 `chroma_data/`를 읽거나 쓰지 않는다")
- `/docs/architecture/ADR.md` (**ADR-012** — 이 작업의 근거)
- `/docs/specifications/HISTORICAL_REPLAY_SPEC.md` (**§6 index cache** — 키 구성요소가 여기 정의돼 있다)
- `/app/embedding/indexer.py` (`CodeIndexer.__init__(persist_dir)`, `index(adapter)`)
- `/app/codebase/real_adapter.py` (`RealCodebaseAdapter`, `EXCLUDED_DIRS`)
- `/app/evaluation/replay/runner.py` (`ReplayContext` — `repo_id`·`base_commit`이 여기서 온다)
- `/config.py` (`settings.embedding_model`)

## 작업

`app/evaluation/replay/index_cache.py`를 신규 생성한다. **인덱스 준비와 캐시 판단만** 담당하고 검색·초안 생성은 다루지 않는다(Step 1).

### 1) 캐시 키 (스펙 §6)

```python
REPLAY_INDEXER_VERSION = "replay-index-v1"


def replay_index_key(
    repo_id: str,
    base_commit: str,
    embedding_model: str,
    indexer_version: str = REPLAY_INDEXER_VERSION,
) -> str: ...
```

- 네 요소를 합쳐 안정적인 해시(sha256 앞 16자 등)로 만든다. 스펙 §6이 정한 구성요소 그대로다.
- **키에 절대경로를 넣지 마라.** `repo_id`는 runner가 이미 해시 형태로 만들어 넘긴다(`case_id:sha256(경로)[:16]`).
- 같은 입력 → 같은 키(결정적), 하나라도 다르면 다른 키.
- `indexer_version`을 키에 넣는 이유: 청킹 규칙이 바뀌면 기존 인덱스를 재사용하면 안 된다.

### 2) 인덱스 디렉토리

```python
REPLAY_INDEX_ROOT = Path("evaluation/replay_index")


def replay_index_dir(key: str, root: Path | None = None) -> Path: ...
```

- 반환 경로는 **반드시 root 하위**여야 한다. `key`에 `/`·`..`가 섞여 들어와도 루트를 벗어나지 않게 검증하라(키는 해시라 정상 경로에서는 문제없지만, 경계는 코드가 지켜야 한다).
- 운영 `chroma_data`를 가리키는 경로가 나올 수 없어야 한다.

### 3) 인덱스 준비

```python
def prepare_index(
    worktree: Path,
    key: str,
    *,
    root: Path | None = None,
    indexer_factory=None,      # 기본 CodeIndexer — 테스트가 가짜를 주입한다
    adapter_factory=None,      # 기본 RealCodebaseAdapter
) -> tuple[Any, bool]: ...
```

- 반환은 `(indexer, reused)` — `reused=True`면 캐시 적중이라 인덱싱을 건너뛴 것이다.
- 캐시 판단: 해당 key 디렉토리가 있고 컬렉션에 문서가 있으면(`collection.count() > 0`) 재사용, 아니면 `indexer.index(adapter)` 실행.
- 어댑터는 **worktree를 repo_root로** 만든다 — `RealCodebaseAdapter(repo_root=str(worktree), indexer=indexer)`. 운영 `settings.repo_root`를 쓰지 마라.
- `indexer_factory`/`adapter_factory`를 인자로 받는 이유: 테스트가 임베딩 모델과 ChromaDB를 띄우지 않고 캐시 로직만 검증하기 위해서다(CLAUDE.md).

### 4) 알려진 한계를 주석으로 남겨라

`CodeIndexer.term_dict`는 `settings.repo_root`(오늘의 repo)에서 용어 사전을 읽는다 — worktree가 아니다. 컬럼코드↔한글명 매핑은 개정과 무관하게 거의 변하지 않으므로 이번 범위에서 바꾸지 않되, **look-ahead 관점의 잔여 항목**임을 모듈 docstring에 명시하라. 코드를 고치지 말고 기록만 한다.

## 테스트

`tests/test_replay_index_cache.py` 신규:

- 키 결정성: 같은 입력 두 번 → 같은 키. base_commit·embedding_model·indexer_version 각각 하나만 바꿔도 키가 달라지는지.
- 키에 절대경로 문자열이 섞이지 않는지(해시 형태 확인).
- `replay_index_dir`가 root 하위를 벗어나지 않는지 — `key`에 `../../etc` 같은 값을 줘도 거부되거나 루트 하위로 정규화되는지.
- `prepare_index`: 빈 캐시 → 가짜 indexer의 `index()`가 호출되고 `reused=False`. 이미 문서가 있는 캐시 → `index()`가 **호출되지 않고** `reused=True`.
- 어댑터가 worktree 경로로 만들어지는지(가짜 adapter_factory로 인자 캡처).
- 운영 `chroma_data` 경로가 반환되지 않는지.

**임베딩 모델·ChromaDB·LLM을 트리거하지 마라** — 전부 가짜 팩토리로 검증한다.

## Acceptance Criteria

```bash
bash scripts/verify.sh full
```

```bash
grep -n "chroma_data\|settings.repo_root" app/evaluation/replay/index_cache.py
```

## 검증 절차

1. 위 AC를 실행한다. 두 번째 커맨드 출력에 운영 경로를 **사용하는** 코드가 없어야 한다(주석·docstring 언급은 허용).
2. 체크리스트:
   - 캐시 키 구성요소가 스펙 §6과 일치하는가?
   - 인덱스 경로가 `evaluation/replay_index/` 하위로 한정되는가?
   - 테스트가 임베딩·ChromaDB를 띄우지 않는가?
3. `phases/issue-0022/index.json`의 step 0 갱신 (성공 → `completed` + summary / 3회 실패 → `error` / 개입 필요 → `blocked` 후 중단).

## 금지사항

- 운영 `./chroma_data`를 읽거나 쓰지 마라. 이유: 재인덱싱에 수십 분이 들고 개발 서버 인덱스가 오염된다(ARCHITECTURE 레이어 규칙).
- `settings.repo_root`를 어댑터 repo_root로 쓰지 마라. 이유: replay는 **과거 시점 worktree**를 인덱싱해야 한다. 오늘 코드를 인덱싱하면 replay 자체가 무의미하다.
- 테스트에서 실제 임베딩·인덱싱을 돌리지 마라. 이유: CLAUDE.md 규칙이며 CI 시간이 폭발한다.
- 검색·초안 생성·CLI를 이 step에서 만들지 마라. 이유: Step 1·2의 범위다.
- 캐시 무효화를 위해 디렉토리를 지우는 기능을 만들지 마라. 이유: 삭제는 사고로 이어진다. 재인덱싱이 필요하면 사용자가 직접 지운다.
- 기존 테스트를 깨뜨리지 마라.
