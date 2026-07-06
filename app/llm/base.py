from abc import ABC, abstractmethod


class LlmClient(ABC):
    """
    추론 백엔드 추상화 (이음새 1).

    기본은 로컬 추론 구현체(LocalClient — Ollama/vLLM 등 OpenAI 호환 서버).
    LLM_BACKEND=claude 로 바꾸면 Claude API 구현체(ClaudeClient)를 쓴다.
    선택은 app/llm/__init__.py 의 get_llm_client()가 담당. 나머지 코드는 그대로.
    """

    @abstractmethod
    def analyze_change(self, before: str, after: str, context: str = "") -> dict:
        """변경 조문의 요약·영향을 분석. {'summary': ..., 'impact': ...} 형태."""
        raise NotImplementedError

    @abstractmethod
    def propose_edits(self, law_diff: str, code_snippets: list[str]) -> str:
        """법령 변경 + 매핑된 코드 스니펫 -> 앵커 기반 검색/치환 편집 블록 생성.
        서버가 이를 실제 unified diff로 변환한다(build_unified_diff)."""
        raise NotImplementedError

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 4096) -> str:
        """단일 프롬프트 → 텍스트 응답 (기본 생성 모델).
        앵커 재시도 등 보조 프롬프트 호출에 사용한다."""
        raise NotImplementedError
