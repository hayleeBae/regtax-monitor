"""LLM 백엔드 공용 로직 — 프롬프트 생성 + 응답 파싱.

ClaudeClient(API)와 LocalClient(로컬 추론)가 동일한 프롬프트·파서를 공유한다.
백엔드를 갈아끼워도 편집 블록 형식과 후처리(build_unified_diff)는 변하지 않는다.
"""

import difflib
import json
import re


def analyze_prompt(before: str, after: str, context: str = "") -> str:
    return (
        "다음은 국세 관련 법령 조문의 개정 전후 내용입니다. "
        "변경의 핵심을 한국어로 요약하고, 시스템에 미칠 영향을 분석하세요.\n\n"
        f"[참고 맥락]\n{context}\n\n"
        f"[개정 전]\n{before}\n\n[개정 후]\n{after}\n\n"
        '반드시 JSON으로만 응답: {"summary": "...", "impact": "..."}'
    )


def propose_prompt(law_diff: str, code_snippets: list[str]) -> str:
    joined = "\n\n---\n\n".join(code_snippets)
    return (
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


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """qwen3 등 추론 모델이 출력하는 <think>...</think> 블록을 제거한다."""
    return _THINK_RE.sub("", text)


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


def build_unified_diff(edits: list[dict], read_file) -> tuple[str, list[str], int, list[dict]]:
    """앵커 편집을 실제 파일에 대입해 줄번호가 정확한 unified diff를 만든다.
    반환: (diff_text, warnings, applied_count, failed_edits).
    failed_edits는 파일은 읽혔지만 앵커가 불일치한 편집들 — 재시도 대상이다.
    (파일 자체를 못 읽는 편집은 환각이므로 재시도 대상에서 제외하고 경고만 남긴다.)"""
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for e in edits:
        groups.setdefault(e["file"], [])
        if e["file"] not in order:
            order.append(e["file"])
        groups[e["file"]].append(e)

    sections: list[str] = []
    warnings: list[str] = []
    failed: list[dict] = []
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
                failed.append(e)
        if new != original:
            diff = difflib.unified_diff(
                original.splitlines(), new.splitlines(),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
            )
            sections.append("\n".join(diff))
    return "\n".join(sections), warnings, applied, failed


def _best_excerpt(original: str, search: str, context: int = 25) -> str:
    """실패한 SEARCH와 가장 비슷한 줄 주변을 잘라낸다 — 재시도 프롬프트에 넣을 원본 발췌.
    파일 전체를 보내면 로컬 소형 모델의 컨텍스트가 넘치므로 후보 구역만 보낸다."""
    lines = original.splitlines()
    targets = [ln for ln in search.splitlines() if ln.strip()]
    if not targets or not lines:
        return "\n".join(lines[: context * 2])
    target = max(targets, key=len).strip()  # 가장 긴 줄이 가장 식별력 있다
    best_i, best_r = 0, -1.0
    for i, line in enumerate(lines):
        r = difflib.SequenceMatcher(None, line.strip(), target).ratio()
        if r > best_r:
            best_r, best_i = r, i
    lo = max(0, best_i - context)
    hi = min(len(lines), best_i + context + 1)
    return "\n".join(lines[lo:hi])


def retry_edits_prompt(law_diff: str, failures: list[dict]) -> str:
    """앵커 불일치 편집들의 재작성 요청. failures: {file, search, replace, excerpt}"""
    parts = [
        f"[실패 편집 {i}] 파일: {f['file']}\n"
        f"(불일치한 기존 SEARCH)\n{f['search']}\n\n"
        f"(의도했던 REPLACE)\n{f['replace']}\n\n"
        f"[실제 원본 발췌 — SEARCH 앵커는 반드시 여기서 복사]\n{f['excerpt']}"
        for i, f in enumerate(failures, 1)
    ]
    return (
        "직전에 생성한 편집 중 아래 항목들은 SEARCH 앵커가 원본과 일치하지 않아 적용에 실패했습니다. "
        "각 실패 편집의 앵커를 '실제 원본 발췌'에 실제로 존재하는 줄로 교체해 다시 작성하세요.\n\n"
        "규칙:\n"
        "1. 출력 형식은 동일:\n@@@FILE: <파일경로>\n@@@SEARCH\n<원본 줄(들)>\n@@@REPLACE\n<치환 내용>\n@@@END\n"
        "2. SEARCH는 아래 원본 발췌에서 공백·들여쓰기까지 '한 글자도 바꾸지 말고' 그대로 복사할 것.\n"
        "3. 실패한 편집만 다시 출력할 것. 설명 문장 금지.\n"
        "4. 원본 발췌에서 의도한 수정을 표현할 수 없으면 그 편집은 출력하지 말 것.\n\n"
        f"[법령 변경]\n{law_diff}\n\n" + "\n\n---\n\n".join(parts)
    )


def propose_and_build(
    llm, law_diff: str, code_snippets: list[str], read_file, max_retries: int = 2,
) -> tuple[str, list[str], int, str]:
    """propose_edits → unified diff 변환. 앵커 불일치 편집은 실제 원본 발췌를
    보여주며 모델에 최대 max_retries회 재작성시킨다 (로컬 소형 모델의 복사 실수 보정).
    반환: (diff_text, warnings, applied_count, raw_edits_전체_응답)."""
    raw = llm.propose_edits(law_diff=law_diff, code_snippets=code_snippets)
    edits = parse_edits(raw)
    diff_text, warnings, applied, failed = build_unified_diff(edits, read_file)

    for attempt in range(max_retries):
        if not failed:
            break
        failures = []
        for e in failed:
            try:
                original = read_file(e["file"])
            except (FileNotFoundError, OSError):
                continue  # build_unified_diff에서 걸러지지만 방어적으로
            failures.append({**e, "excerpt": _best_excerpt(original, e["search"])})
        if not failures:
            break
        fixed_raw = llm.complete(retry_edits_prompt(law_diff, failures))
        fixed = parse_edits(fixed_raw)
        if not fixed:
            break
        raw += f"\n\n# --- 앵커 재시도 {attempt + 1}차 ---\n{fixed_raw}"
        edits = [e for e in edits if e not in failed] + fixed
        diff_text, warnings, applied, failed = build_unified_diff(edits, read_file)

    return diff_text, warnings, applied, raw


def parse_json_response(text: str, required: tuple[str, ...] = ()) -> dict:
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
