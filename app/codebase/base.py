from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CodeHit:
    path: str
    symbol: str
    snippet: str
    score: float


class CodebaseAdapter(ABC):
    """
    분석 대상 코드베이스 추상화 (이음새 2).

    집에서는 MockCodebaseAdapter(mock repo)를 가리키고,
    회사에서는 RealCodebaseAdapter(실제 repo)를 같은 인터페이스로
    구현해 교체한다. 같은 코드, 타겟·실행 장소만 다름.
    """

    EXCLUDED_DIRS = frozenset(
        {
            ".git",
            ".svn",
            "target",
            "build",
            "out",
            "dist",
            "node_modules",
            ".venv",
            "venv",
            "__pycache__",
        }
    )
    """인덱싱에서 제외할 디렉토리 이름 (경로 구성요소 기준).

    빌드 산출물을 인덱싱하면 세 가지가 동시에 망가진다:

    1. **매핑이 쓸모없어진다** — exploded WAR 같은 산출물은 빌드할 때마다 덮어써지므로,
       거기를 가리키는 매핑으로 patch 초안을 만들어도 실제 수정이 되지 않는다.
    2. **검색이 오염된다** — 같은 코드가 소스와 산출물로 중복 인덱싱돼 상위 후보를
       산출물이 차지한다.
    3. **인덱싱 시간이 몇 배가 된다** — 실제 eHR repo 관측에서 인덱싱 대상 8,100개 중
       상당수가 `out/artifacts/..._war_exploded/` 하위였다(2026-08-05 회사 실측).

    이음새(adapter) 전체의 규칙이라 base 에 둔다 — 어느 어댑터를 끼우든 `list_files()`
    가 같은 것을 제외해야 `symbol_index` 처럼 adapter 만 경유하는 소비자가 산출물을
    보지 않는다(ADR-013). 목록은 `app/golden.py::_IGNORE`(스크래치 복사 제외 목록)와
    같은 어휘다. 그쪽은 `shutil.ignore_patterns` 콜러블이고 여기는 경로 구성요소
    집합이라 형태가 달라 공유하지 않는다 — 한쪽을 고치면 다른 쪽도 함께 볼 것.
    """

    def _is_excluded(self, path: Path) -> bool:
        """경로 구성요소에 제외 디렉토리가 있는지. (부분일치가 아니라 구성요소 일치)"""
        return bool(self.EXCLUDED_DIRS & set(path.parts))

    @abstractmethod
    def list_files(self) -> list[str]:
        """인덱싱 대상 파일 경로 목록."""
        raise NotImplementedError

    @abstractmethod
    def read_file(self, path: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, k: int = 5) -> list[CodeHit]:
        """RAG 검색: 법령 변경과 관련된 코드 조각만 좁혀서 반환."""
        raise NotImplementedError

    @abstractmethod
    def apply_patch(self, proposal_id: int, diff: str) -> str:
        """승인된 patch 반영(PR/commit). 반드시 사람 승인 후에만 호출."""
        raise NotImplementedError

    def find_usages(self, class_name: str, max_results: int = 5) -> list[str]:
        """class_name을 참조하는 파일 경로 목록. 기본 구현은 빈 리스트."""
        return []

    def repository_revision(self) -> str | None:
        """정책·재현에 사용할 commit 또는 fixture 식별자."""
        return None
