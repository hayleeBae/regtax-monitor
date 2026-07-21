from anthropic import Anthropic

from app.llm import common
from app.llm.base import LlmClient
from config import settings


class ClaudeClient(LlmClient):
    """
    하이브리드 구현체 (LLM_BACKEND=claude).
    중요: 전체 코드가 아니라, RAG로 좁혀진 스니펫만 API로 전송한다.
    """

    def __init__(self, model: str | None = None):
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.llm_model

    def analyze_change(self, before: str, after: str, context: str = "") -> dict:
        resp = self.client.messages.create(
            model=settings.llm_model_cheap,
            max_tokens=2048,
            messages=[{"role": "user", "content": common.analyze_prompt(before, after, context)}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return common.parse_json_response(text, required=("summary", "impact"))

    def propose_edits(self, law_diff: str, code_snippets: list[str]) -> str:
        return self.complete(common.propose_prompt(law_diff, code_snippets))

    def classify_change(self, before: str, after: str, normalized: dict) -> dict:
        text = self.complete(common.classify_prompt(before, after, normalized), max_tokens=1024)
        return common.parse_json_response(
            text, required=("primary_type", "confidence", "reason", "signals")
        )

    def complete(self, prompt: str, max_tokens: int = 4096) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
