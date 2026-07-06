from app.llm.base import LlmClient
from config import settings


def get_llm_client() -> LlmClient:
    """LLM_BACKEND 설정에 따라 추론 백엔드를 선택한다.

    - "local"  : 로컬 추론 서버 (Ollama/vLLM 등, OpenAI 호환) — 기본값
    - "claude" : Anthropic API (하이브리드)

    지연 import — claude 백엔드를 쓰지 않으면 anthropic 패키지가 없어도 동작한다.
    """
    if settings.llm_backend == "claude":
        from app.llm.claude_client import ClaudeClient
        return ClaudeClient()
    from app.llm.local_client import LocalClient
    return LocalClient()
