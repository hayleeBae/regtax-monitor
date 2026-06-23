from abc import ABC, abstractmethod
from dataclasses import dataclass


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
