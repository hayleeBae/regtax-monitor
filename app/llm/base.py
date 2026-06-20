from abc import ABC, abstractmethod


class LlmClient(ABC):
    """
    추론 백엔드 추상화 (이음새 1).

    하이브리드에서는 Claude API 구현체(ClaudeClient)를 쓴다.
    나중에 사내 추론(로컬 모델)으로 바꾸려면 이 인터페이스만
    새로 구현해서 갈아끼우면 된다. 나머지 코드는 그대로.
    """

    @abstractmethod
    def analyze_change(self, before: str, after: str, context: str = "") -> dict:
        """변경 조문의 요약·영향을 분석. {'summary': ..., 'impact': ...} 형태."""
        raise NotImplementedError

    @abstractmethod
    def propose_patch(self, law_diff: str, code_snippets: list[str]) -> str:
        """법령 변경 + 매핑된 코드 스니펫 -> 수정안(diff 초안) 생성."""
        raise NotImplementedError
