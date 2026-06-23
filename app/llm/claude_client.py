import json
import re

from anthropic import Anthropic

from app.llm.base import LlmClient
from config import settings


class ClaudeClient(LlmClient):
    """
    하이브리드 기본 구현체.
    중요: 전체 코드가 아니라, RAG로 좁혀진 스니펫만 API로 전송한다.
    """

    def __init__(self, model: str | None = None):
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.llm_model

    def analyze_change(self, before: str, after: str, context: str = "") -> dict:
        prompt = (
            "다음은 국세 관련 법령 조문의 개정 전후 내용입니다. "
            "변경의 핵심을 한국어로 요약하고, 시스템에 미칠 영향을 분석하세요.\n\n"
            f"[참고 맥락]\n{context}\n\n"
            f"[개정 전]\n{before}\n\n[개정 후]\n{after}\n\n"
            '반드시 JSON으로만 응답: {"summary": "...", "impact": "..."}'
        )
        resp = self.client.messages.create(
            model=settings.llm_model_cheap,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return _parse_json_response(text, required=("summary", "impact"))

    def propose_patch(self, law_diff: str, code_snippets: list[str]) -> str:
        joined = "\n\n---\n\n".join(code_snippets)
        prompt = (
            "아래 법령 변경에 맞춰 코드를 수정하는 patch(unified diff) 초안을 작성하세요. "
            "확실하지 않은 부분은 주석으로 남기고, 절대 임의로 자동 적용하지 마세요. "
            "사람이 검토할 초안입니다.\n\n"
            "응답은 반드시 ```diff ... ``` 코드블록 하나만 출력하세요. 설명 텍스트는 diff 안에 주석으로 작성하세요.\n\n"
            f"[법령 변경]\n{law_diff}\n\n[관련 코드 스니펫]\n{joined}"
        )
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return _extract_diff(text)


def _extract_diff(text: str) -> str:
    """응답 텍스트에서 ```diff ... ``` 또는 ``` ... ``` 블록을 추출한다."""
    m = re.search(r"```(?:diff)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).rstrip()
    # 코드블록이 없으면 원문 그대로 (하위 호환)
    return text.strip()


def _parse_json_response(text: str, required: tuple[str, ...] = ()) -> dict:
    """모델 응답에서 JSON 블록을 추출하여 파싱한다. 실패하면 raw 키로 반환."""
    # ```json ... ``` 블록 우선 추출
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw_json = m.group(1) if m else text.strip()

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        # 중괄호 범위만 잘라서 재시도
        m2 = re.search(r"\{.*\}", raw_json, re.DOTALL)
        if m2:
            try:
                parsed = json.loads(m2.group())
            except json.JSONDecodeError:
                return {"raw": text}
        else:
            return {"raw": text}

    # 필수 키가 모두 있으면 정상 반환
    if all(k in parsed for k in required):
        return parsed

    return {"raw": text}
