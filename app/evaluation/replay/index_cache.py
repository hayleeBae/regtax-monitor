"""replay 인덱스 캐시 — HISTORICAL_REPLAY_SPEC §6, ADR-012.

과거 시점 worktree 를 인덱싱하고, 같은 (repo·base commit·임베딩 모델·인덱서 버전)
조합이면 그 인덱스를 재사용한다. **인덱스 준비와 캐시 판단만** 한다 — 검색·초안
생성은 이 모듈의 일이 아니다(`real_pipeline` 의 몫).

## 캐시가 선택이 아닌 이유 (ADR-012)

실제 eHR repo 인덱싱은 수십 분이다. 캐시가 없으면 fixture 케이스마다 처음부터
인덱싱해 회사에서 한 번도 완주하지 못한다. 반대로 캐시 키가 헐거우면 청킹 규칙이
바뀐 뒤에도 옛 인덱스를 재사용해 측정이 조용히 틀린다 — 그래서 키에 인덱서 버전을
넣는다(`REPLAY_INDEXER_VERSION`).

## 운영 인덱스와 완전히 분리한다

인덱스는 `evaluation/replay_index/<key>/` 아래에만 만든다. 개발 서버가 쓰는 운영
벡터DB 디렉토리(`./chroma_data`)는 읽지도 쓰지도 않는다 — 거기에 replay 가 쓰면
개발 서버 검색 결과가 과거 시점 코드로 오염되고, 복구에 다시 수십 분이 든다
(ARCHITECTURE.md 레이어 규칙). `evaluation/` 하위에 두는 것은 그 구역이 이미 반출
금지·gitignore 대상이기 때문이다 — 인덱스에는 대상 코드의 임베딩이 담긴다.

어댑터의 `repo_root` 는 **항상 인자로 받은 worktree** 다. 운영 설정의 repo 경로
(오늘의 코드)를 인덱싱하면 replay 가 "과거 시점 재현"이 아니게 되어 존재 이유가
사라진다.

## 키에 경로를 넣지 않는다 (ADR-010)

`repo_id` 는 runner 가 `case_id:sha256(경로)[:16]` 형태로 만들어 넘긴다. 키는 그
값을 다시 해싱한 16자 hex 이므로, 캐시 디렉토리 이름·로그·리포트 어디에 남아도
회사 repo 절대경로가 복원되지 않는다.

## 알려진 한계 — look-ahead 잔여 항목

`CodeIndexer.term_dict` 는 용어 사전을 **운영 설정의 repo 경로(오늘의 repo)** 에서
읽는다. worktree 가 아니다. 즉 replay 인덱스의 컬럼코드→한글명 보강 헤더는 엄밀히는
"오늘의 사전"이며, 과거 시점 재현이라는 전제에 미세한 look-ahead 가 남는다. 이번
범위에서는 고치지 않는다 — 이 매핑(예: `a0121` → 지급항목명)은 법령 개정과 무관하게
거의 변하지 않아 지표를 뒤집을 크기가 아니고, 인덱서 시그니처를 바꾸는 일은 운영
인덱싱 경로에 회귀 위험을 만든다(CLAUDE.md — 동작 보존 우선). **기록만 남기고 코드는
건드리지 않는다.**
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
"""저장소 루트 — 기본 캐시 루트를 CWD 가 아니라 여기에 고정한다.

`runner.PROJECT_ROOT` 와 같은 기준점이다. 상대경로를 CWD 기준으로 두면 실행 위치가
바뀔 때마다 수 GB 짜리 인덱스가 새로 생긴다.
"""

REPLAY_INDEXER_VERSION = "replay-index-v1"
"""청킹·보강 규칙의 버전 (스펙 §6 의 chunker/indexer version).

`CodeIndexer._chunk` 계열 규칙이나 `_enrich` 방식이 바뀌면 **이 값을 올린다.** 올리지
않으면 규칙이 다른 옛 인덱스를 그대로 재사용해, 바뀐 청킹의 효과를 측정하는 실행이
옛 청킹 결과를 재는 일이 된다.
"""

REPLAY_INDEX_ROOT = Path("evaluation/replay_index")
"""캐시 루트 (프로젝트 상대). ADR-012 — 운영 벡터DB 디렉토리와 겹치지 않는다."""

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
"""캐시 키로 허용하는 형태 — 경로 구분자·`.` 을 아예 배제한다.

`replay_index_key` 가 만드는 값은 hex 라 항상 통과한다. 이 검사는 **다른 경로로 들어온
값**(직접 구성한 키, 상위 계층의 문자열 결합 실수)이 `evaluation/replay_index/` 밖을
가리키지 못하게 하는 경계다. `.` 을 막으므로 `..` 도 자동으로 걸린다.
"""


class ReplayIndexKeyError(ValueError):
    """캐시 키가 디렉토리 이름으로 안전하지 않은 경우."""


def replay_index_key(
    repo_id: str,
    base_commit: str,
    embedding_model: str,
    indexer_version: str = REPLAY_INDEXER_VERSION,
) -> str:
    """스펙 §6 의 네 구성요소로 만드는 결정적 캐시 키 (sha256 앞 16자).

    구성요소: repository id, base commit, 임베딩 모델, chunker/indexer 버전. 하나라도
    다르면 다른 인덱스여야 하므로 전부 키에 들어간다.

    구분자로 `\\0` 을 쓴다 — 이어 붙이기만 하면 `("ab", "c")` 와 `("a", "bc")` 가 같은
    키가 된다(경계 모호성). 필드 값에 NUL 이 들어오는 경우는 없다.
    """
    parts = [repo_id, base_commit, embedding_model, indexer_version]
    if any(not isinstance(part, str) or not part for part in parts):
        raise ReplayIndexKeyError(
            "캐시 키 구성요소(repo_id·base_commit·embedding_model·indexer_version)는 "
            "모두 비어 있지 않은 문자열이어야 합니다."
        )
    payload = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def replay_index_dir(key: str, root: Optional[Path] = None) -> Path:
    """`key` 에 해당하는 인덱스 디렉토리 경로 — 항상 `root` 하위다.

    `root` 를 생략하면 `PROJECT_ROOT/evaluation/replay_index` 다. 키 형태를 먼저 막고
    (`_SAFE_KEY`), 그다음 해석된 경로가 정말 루트 안에 있는지 다시 확인한다 — 앞의
    검사는 규칙이고 뒤의 검사는 결과다. 둘 중 하나만 두면 규칙이 느슨해지는 순간
    조용히 밖을 가리킨다.
    """
    if not isinstance(key, str) or not _SAFE_KEY.match(key):
        raise ReplayIndexKeyError(
            f"캐시 키는 영숫자·`_`·`-` 로만 이루어져야 합니다: {key!r}"
        )

    base = Path(root) if root is not None else PROJECT_ROOT / REPLAY_INDEX_ROOT
    target = base / key

    resolved_root = base.resolve()
    resolved_target = target.resolve()
    if resolved_root != resolved_target and resolved_root not in resolved_target.parents:
        # 심볼릭 링크 등으로 루트를 벗어난 경우 — 도달할 수 없는 것이 정상이다.
        raise ReplayIndexKeyError("캐시 경로가 replay 인덱스 루트를 벗어났습니다.")
    return target


def _default_indexer_factory(persist_dir: str) -> Any:
    """기본 인덱서 — `CodeIndexer(persist_dir=...)`.

    import 를 함수 안에 두는 이유(ADR-011·ADR-012): 모듈 최상단에 두면 이 모듈을
    import 하는 것만으로 ChromaDB 가 딸려 온다. seam 을 가볍게 유지해야 집 환경에서
    replay 테스트가 무거운 의존성 없이 돈다(CLAUDE.md).
    """
    from app.embedding.indexer import CodeIndexer

    return CodeIndexer(persist_dir=persist_dir)


def _default_adapter_factory(repo_root: str, indexer: Any) -> Any:
    """기본 어댑터 — `RealCodebaseAdapter(repo_root=<worktree>, indexer=...)`.

    코드베이스 접근은 `CodebaseAdapter` seam 을 통해서만 한다(CLAUDE.md). 제외 목록
    (`EXCLUDED_DIRS` — 빌드 산출물)도 그대로 따라오므로 replay 인덱싱이 exploded WAR 을
    빨아들이지 않는다.
    """
    from app.codebase.real_adapter import RealCodebaseAdapter

    return RealCodebaseAdapter(repo_root=repo_root, indexer=indexer)


def _document_count(indexer: Any) -> int:
    """인덱스에 든 문서 수 — 셀 수 없으면 0(= 다시 인덱싱)으로 본다.

    캐시 디렉토리는 남았는데 컬렉션이 비었거나 깨진 경우가 실제로 생긴다(중단된 첫
    인덱싱, 부분 삭제). 그때 재사용을 택하면 빈 인덱스로 검색해 케이스가 통째로 0점이
    되므로, 판단이 서지 않으면 비싼 쪽(재인덱싱)으로 기운다.
    """
    collection = getattr(indexer, "collection", None)
    if collection is None:
        return 0
    try:
        return int(collection.count())
    except Exception:  # noqa: BLE001 - 벡터DB 구현의 모든 실패를 "캐시 없음"으로 본다
        logger.warning("replay 인덱스 문서 수를 확인할 수 없어 재인덱싱합니다.")
        return 0


def prepare_index(
    worktree: Path,
    key: str,
    *,
    root: Optional[Path] = None,
    indexer_factory: Optional[Callable[..., Any]] = None,
    adapter_factory: Optional[Callable[..., Any]] = None,
) -> tuple[Any, bool]:
    """`key` 의 캐시 인덱스를 준비하고 `(indexer, reused)` 를 돌려준다.

    `reused=True` 면 캐시 적중이라 인덱싱을 건너뛴 것이다. 캐시 판단은 **디렉토리 존재
    + 문서 수 > 0** 두 조건을 모두 본다.

    인덱싱 대상은 인자로 받은 `worktree` 다 — 운영 설정의 repo 경로(오늘의 코드)를
    쓰지 않는다(ADR-012). 팩토리를 인자로 여는 이유는 테스트가 임베딩 모델과 ChromaDB
    를 띄우지 않고 캐시 로직만 검증하기 위해서다.

    **캐시를 지우는 경로는 여기 없다.** 재인덱싱이 필요하면 사용자가 해당 디렉토리를
    직접 지운다 — 자동 삭제는 사고로 이어지고, 되돌리는 데 수십 분이 든다.
    """
    target = replay_index_dir(key, root)
    cached = target.is_dir()
    target.mkdir(parents=True, exist_ok=True)

    make_indexer = indexer_factory or _default_indexer_factory
    indexer = make_indexer(persist_dir=str(target))

    if cached and _document_count(indexer) > 0:
        logger.info("replay 인덱스 캐시 적중 (key=%s)", key)
        return indexer, True

    make_adapter = adapter_factory or _default_adapter_factory
    adapter = make_adapter(repo_root=str(Path(worktree)), indexer=indexer)
    logger.info("replay 인덱스를 새로 만듭니다 (key=%s)", key)
    indexer.index(adapter)
    return indexer, False
