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
        self.num_ctx = settings.local_llm_num_ctx

    def analyze_change(self, before: str, after: str, context: str = "") -> dict:
        text = self._chat(
            common.analyze_prompt(before, after, context),
            model=self.model_cheap, max_tokens=2048, temperature=0.2,
        )
        return common.parse_json_response(text, required=("summary", "impact"))

    def propose_edits(self, law_diff: str, code_snippets: list[str]) -> str:
        return self.complete(common.propose_prompt(law_diff, code_snippets))

    def complete(self, prompt: str, max_tokens: int = 4096) -> str:
        return self._chat(prompt, model=self.model, max_tokens=max_tokens, temperature=0.1)

    def _chat(self, prompt: str, model: str, max_tokens: int, temperature: float) -> str:
        if not settings.local_llm_think:
            # qwen3 소프트 스위치 — thinking이 max_tokens을 소진해 빈 응답이 되는 것을 방지
            prompt = prompt + " /no_think"
        # 한국어+코드 혼합 기준 문자수/2 근사 — 컨텍스트 창(num_ctx) 초과 진단용
        approx_tokens = len(prompt) // 2
        print(f"[LLM] {model} 호출 — 프롬프트 {len(prompt):,}자 (약 {approx_tokens:,} 토큰), "
              f"max_tokens={max_tokens}, num_ctx={self.num_ctx}", flush=True)
        if approx_tokens + max_tokens > self.num_ctx:
            print(f"[LLM] ⚠ 프롬프트(약 {approx_tokens:,}) + max_tokens({max_tokens:,})가 "
                  f"num_ctx({self.num_ctx:,})를 초과 — 프롬프트 앞부분이 잘리거나 응답이 "
                  "중단될 수 있습니다. LOCAL_LLM_NUM_CTX를 늘리세요.", flush=True)
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    # Ollama 확장 — OpenAI 표준 외 필드. vLLM 등 다른 서버는 무시하거나
                    # 거부할 수 있다 (거부 시 LOCAL_LLM_NUM_CTX 조정이 아니라 서버 설정 사용)
                    "options": {"num_ctx": self.num_ctx},
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
