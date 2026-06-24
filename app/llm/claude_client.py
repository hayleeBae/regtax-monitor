import difflib
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

    def propose_edits(self, law_diff: str, code_snippets: list[str]) -> str:
        """앵커 기반 검색/치환 편집 블록(원문)을 생성한다.
        줄번호가 아닌 '원본 그대로 복사한 앵커'로 위치를 지정 → 거대 XML에도 적용 가능.
        실제 unified diff 변환은 서버(build_unified_diff)가 담당한다."""
        joined = "\n\n---\n\n".join(code_snippets)
        prompt = (
            "아래 법령 변경에 맞춰 코드를 수정하는 '검색/치환 편집'을 작성하세요. "
            "줄번호 대신, 원본을 그대로 복사한 앵커로 위치를 지정합니다.\n\n"
            "규칙:\n"
            "1. 각 편집은 정확히 다음 형식으로만 출력:\n"
            "@@@FILE: <파일경로>\n@@@SEARCH\n<원본에서 그대로 복사한 기존 줄(들)>\n"
            "@@@REPLACE\n<치환할 내용>\n@@@END\n"
            "2. SEARCH 블록은 제공된 코드에서 공백·들여쓰기까지 '한 글자도 바꾸지 말고' 그대로 복사할 것.\n"
            "3. 새 항목 추가는 기존 앵커 줄을 SEARCH로 두고, REPLACE에 '그 앵커 줄 + 추가할 새 줄'을 넣어 표현할 것.\n"
            "4. 되도록 실제 동작하는 코드를 작성하되, 신설 컬럼코드처럼 확정 불가한 값은 "
            "가장 그럴듯한 값으로 채우고 그 줄 끝에 '-- TODO 확인' 주석을 달 것.\n"
            "5. 설명 문장은 출력하지 말고 편집 블록만 출력. "
            "관련된 VO(.java) 필드 선언과 매퍼(XML) 쿼리를 함께 수정할 것.\n\n"
            f"[법령 변경]\n{law_diff}\n\n[관련 코드 — SEARCH 앵커는 여기서 복사]\n{joined}"
        )
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()


_EDIT_RE = re.compile(
    r"@@@FILE:\s*(?P<file>.+?)[ \t]*\n@@@SEARCH\n(?P<search>.*?)\n@@@REPLACE\n"
    r"(?P<replace>.*?)\n@@@END",
    re.DOTALL,
)


def parse_edits(text: str) -> list[dict]:
    """모델 응답에서 @@@FILE/@@@SEARCH/@@@REPLACE/@@@END 편집 블록들을 추출."""
    return [
        {"file": m.group("file").strip(),
         "search": m.group("search"),
         "replace": m.group("replace")}
        for m in _EDIT_RE.finditer(text)
    ]


def build_unified_diff(edits: list[dict], read_file) -> tuple[str, list[str], int]:
    """앵커 편집을 실제 파일에 대입해 줄번호가 정확한 unified diff를 만든다.
    반환: (diff_text, warnings, applied_count). 앵커를 못 찾으면 경고로 남기고 건너뛴다."""
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for e in edits:
        groups.setdefault(e["file"], [])
        if e["file"] not in order:
            order.append(e["file"])
        groups[e["file"]].append(e)

    sections: list[str] = []
    warnings: list[str] = []
    applied = 0
    for path in order:
        try:
            original = read_file(path)
        except (FileNotFoundError, OSError):
            warnings.append(f"{path}: 파일을 읽을 수 없음")
            continue
        new = original
        for e in groups[path]:
            search = e["search"]
            if search and search in new:
                new = new.replace(search, e["replace"], 1)
                applied += 1
            else:
                first = (search.strip().splitlines() or [""])[0]
                warnings.append(f"{path}: 앵커를 찾지 못함 — “{first[:50]}…”")
        if new != original:
            diff = difflib.unified_diff(
                original.splitlines(), new.splitlines(),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
            )
            sections.append("\n".join(diff))
    return "\n".join(sections), warnings, applied


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
