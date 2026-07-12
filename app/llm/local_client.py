import re

import httpx

from app.llm import common
from app.llm.base import LlmClient
from config import settings

# 서버 실효 컨텍스트 확인 시도 상한 — 첫 호출 시점에는 모델이 아직 로드되지 않아
# /api/ps에 안 보일 수 있으므로, 결론이 날 때까지 몇 번 더 재시도한다
_CTX_CHECK_MAX_ATTEMPTS = 3


class LocalClient(LlmClient):
    """
    로컬 추론 서버 구현체 (이음새 1 — 완전 로컬 모드).

    OpenAI 호환 chat completions 엔드포인트를 사용하므로
    Ollama / vLLM / llama.cpp server / LM Studio 어느 것이든 붙는다.
    코드 스니펫이 외부로 전혀 나가지 않는다.
    """

    # 실효 컨텍스트 대조 상태 — 프로세스당 1회만 결론 낸다 (클래스 공유)
    _ctx_check_done = False
    _ctx_check_attempts = 0

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

    def _verify_server_context(self, model: str) -> None:
        """서버의 실효 컨텍스트를 조회해 LOCAL_LLM_NUM_CTX와 대조한다.

        LOCAL_LLM_NUM_CTX(초과 경고 기준)와 OLLAMA_CONTEXT_LENGTH(실효 값)는 수동
        동기화 구조라, 서버만 기본값(4096)으로 뜨면 경고 없이 context shift가
        재발한다 (.harness/failures/F-20260712-0001 잔여 리스크). Ollama가 아니거나
        조회에 실패하면 조용히 건너뛴다.
        """
        cls = type(self)
        if cls._ctx_check_done or cls._ctx_check_attempts >= _CTX_CHECK_MAX_ATTEMPTS:
            return
        cls._ctx_check_attempts += 1
        try:
            effective = self._fetch_server_num_ctx(model)
        except Exception:
            cls._ctx_check_done = True  # 비-Ollama 서버 등 — 재시도 무의미
            return
        if effective is None:
            return  # 아직 판단 불가(모델 미로드 등) — 다음 호출에서 재시도
        cls._ctx_check_done = True
        if effective != self.num_ctx:
            msg = (f"[LLM] ⚠ 서버 실효 컨텍스트({effective:,})가 "
                   f"LOCAL_LLM_NUM_CTX({self.num_ctx:,})와 불일치합니다.")
            if effective < self.num_ctx:
                msg += (" 이 상태로는 초과 경고 없이 프롬프트가 잘릴 수 있습니다"
                        "(context shift — F-20260712-0001 재발 위험).")
            msg += (f" 해결: `OLLAMA_CONTEXT_LENGTH={self.num_ctx} ollama serve`로 "
                    f"재기동하거나 .env의 LOCAL_LLM_NUM_CTX를 {effective}로 맞추세요.")
            print(msg, flush=True)

    def _fetch_server_num_ctx(self, model: str) -> int | None:
        """Ollama에서 실효 num_ctx를 조회. 판단 불가면 None, 조회 실패면 예외.

        1) /api/show — 모델 파라미터(Modelfile num_ctx)에 있으면 그 값
        2) /api/ps  — OLLAMA_CONTEXT_LENGTH로만 설정된 경우 모델 파라미터에 없으므로
                      로드된 모델의 context_length로 폴백 (미로드면 None)
        """
        api_root = self.base_url[: -len("/v1")] if self.base_url.endswith("/v1") else self.base_url
        resp = httpx.post(f"{api_root}/api/show",
                          json={"model": model, "name": model}, timeout=5)
        resp.raise_for_status()
        m = re.search(r"\bnum_ctx\s+(\d+)", resp.json().get("parameters") or "")
        if m:
            return int(m.group(1))
        resp = httpx.get(f"{api_root}/api/ps", timeout=5)
        resp.raise_for_status()
        for entry in resp.json().get("models") or []:
            if model in (entry.get("name"), entry.get("model")):
                ctx = entry.get("context_length")
                if isinstance(ctx, int):
                    return ctx
        return None

    def _chat(self, prompt: str, model: str, max_tokens: int, temperature: float) -> str:
        self._verify_server_context(model)
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
                  "중단될 수 있습니다. 서버 컨텍스트를 늘리세요 "
                  "(Ollama: OLLAMA_CONTEXT_LENGTH=<값> ollama serve) 후 "
                  "LOCAL_LLM_NUM_CTX도 같은 값으로.", flush=True)
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    # 주의: Ollama의 OpenAI 호환 레이어(/v1)는 이 필드를 무시한다
                    # (0.31.1 확인, CLAUDE.md 환경 제약). Ollama의 실효 컨텍스트는
                    # 서버 기동 시 OLLAMA_CONTEXT_LENGTH로 설정. 이 필드는 options를
                    # 지원하는 다른 OpenAI 호환 서버용으로 유지하며, LOCAL_LLM_NUM_CTX는
                    # 위 초과 경고의 기준값 역할이 주 목적이다.
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
