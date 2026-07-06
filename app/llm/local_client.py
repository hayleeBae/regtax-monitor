import httpx

from app.llm import common
from app.llm.base import LlmClient
from config import settings


class LocalClient(LlmClient):
    """
    로컬 추론 서버 구현체 (이음새 1 — 완전 로컬 모드).

    OpenAI 호환 chat completions 엔드포인트를 사용하므로
    Ollama / vLLM / llama.cpp server / LM Studio 어느 것이든 붙는다.
    코드 스니펫이 외부로 전혀 나가지 않는다.
    """

    def __init__(self, model: str | None = None):
        self.base_url = settings.local_llm_base_url.rstrip("/")
        self.model = model or settings.local_llm_model
        self.model_cheap = settings.local_llm_model_cheap or self.model
        self.timeout = settings.local_llm_timeout_seconds

    def analyze_change(self, before: str, after: str, context: str = "") -> dict:
        text = self._chat(
            common.analyze_prompt(before, after, context),
            model=self.model_cheap, max_tokens=2048, temperature=0.2,
        )
        return common.parse_json_response(text, required=("summary", "impact"))

    def propose_edits(self, law_diff: str, code_snippets: list[str]) -> str:
        return self._chat(
            common.propose_prompt(law_diff, code_snippets),
            model=self.model, max_tokens=4096, temperature=0.1,
        )

    def _chat(self, prompt: str, model: str, max_tokens: int, temperature: float) -> str:
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=self.timeout,
            )
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"로컬 추론 서버({self.base_url})에 연결할 수 없습니다. "
                "Ollama라면 `ollama serve` 실행 및 `ollama pull <모델>` 후 재시도하세요. "
                "다른 서버는 LOCAL_LLM_BASE_URL을 확인하세요."
            ) from e
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"] or ""
        return common.strip_reasoning(text).strip()
