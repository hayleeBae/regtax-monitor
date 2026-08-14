"""개정문(공식 before→after 진술문)을 조문별 편집으로 분해한다.

한국 법령 개정문은 정형 문형을 따른다 — "제N조 중 'A'를 'B'로 한다". 이 모듈은
그 문형을 정규식으로 인식해 `AmendmentEdit` 목록으로 만들고, `ChangeNormalizer`가
같은 조문끼리 값 델타를 계산할 수 있도록 before/after 텍스트를 파생한다.

순수 함수 — 파일/네트워크/DB/LLM 접근 없음. 결정론·재현 가능.
계약: docs/specifications/COLLECTION_SEMANTICS_SPEC.md §3, ADR-014.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# 개정문 파서 입력 상한 (law_api._collect_text와 동일 값 — 넘치면 앞부분만 파싱).
_MAX_INPUT = 20000

# <위치> = 제N조(의M)(제K항)(제L호)(목). law_api._extract_article_no의 정규식을
# 항·호·목까지 확장한 절대 참조. "같은 조/항"은 직전 위치를 이어받는 상대 참조로
# 따로 인식해, 해소는 파싱 루프에서 조 컨텍스트를 들고 처리한다.
_ABS_LOC = r"제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?(?:[가-힣]목)?"
_REL_LOC = r"같은\s*조(?:\s*제\d+항)?(?:\s*제\d+호)?(?:\s*[가-힣]목)?|같은\s*항(?:\s*제\d+호)?"
_LOC = rf"(?:{_ABS_LOC}|{_REL_LOC})"

# 따옴표 변형: 곧은(" ') + 둥근(U+201C/U+201D)을 모두 허용. 내부는 부정 문자류로만
# 소비해 중첩 수량자 없이 backtracking 폭발을 피한다.
_Q = "[\"'“”]"
_QT = "[^\"'“”]*"
# 무따옴표 치환의 A·B: 따옴표·개행을 넘지 않는 최소 매칭 (짧은 수치/명사구 전제).
_BARE = "[^\"'“”\n]+?"

_JO_RE = re.compile(r"제\d+조(?:의\d+)?")
_HANG_RE = re.compile(r"제\d+항")

# 문형 정규식 — 스펙 §3-2 우선순위 순. 파싱 루프가 이 순서대로 적용하며,
# 이미 소비된 구간과 겹치는 후보는 버린다(P1 따옴표가 P2 무따옴표에 우선).
_P1 = re.compile(  # replace(따옴표): <위치> 중 "A"를 "B"로 한다/하고/하며
    rf"(?P<loc>{_LOC})\s*중\s*{_Q}(?P<a>{_QT}){_Q}\s*[를을]\s*"
    rf"{_Q}(?P<b>{_QT}){_Q}\s*(?:으로|로)\s*(?:한다|하고|하며)"
)
_P2 = re.compile(  # replace(무따옴표): <위치> 중 A를 B로 한다
    rf"(?P<loc>{_LOC})\s*중\s*(?P<a>{_BARE})\s*[를을]\s*"
    rf"(?P<b>{_BARE})\s*(?:으로|로)\s*(?:한다|하고|하며)"
)
_P3 = re.compile(  # rewrite: <위치>를 다음과 같이 한다. (+ 후속 본문)
    rf"(?P<loc>{_LOC})\s*[를을]\s*다음과\s*같이\s*한다\.?"
)
_P4A = re.compile(  # insert: <위치>를 다음과 같이 신설한다 (+ 후속 본문)
    rf"(?P<loc>{_LOC})\s*[를을]\s*다음과\s*같이\s*신설한다\.?"
)
_P4B = re.compile(  # insert: <위치>에 …를 신설한다 (본문 인라인)
    rf"(?P<loc>{_LOC})에\s*(?P<body>[^\n]*?)\s*(?:를|을)?\s*신설한다\.?"
)
_P5 = re.compile(  # delete: <위치>를 삭제한다
    rf"(?P<loc>{_LOC})\s*[를을]\s*삭제한다\.?"
)

# (kind, pattern) — 스펙 §3-2 우선순위. P4A(다음과 같이 신설)를 P4B보다 먼저 본다.
_SPECS = (
    ("replace", _P1),
    ("replace", _P2),
    ("rewrite", _P3),
    ("insert", _P4A),
    ("insert", _P4B),
    ("delete", _P5),
)


@dataclass(frozen=True)
class AmendmentEdit:
    article_ref: str      # "제59조의2제1항" — 없으면 ""
    kind: str             # "replace" | "rewrite" | "insert" | "delete"
    before_fragment: str  # 개정 전 문구 (rewrite/insert는 "")
    after_fragment: str   # 개정 후 문구 (delete는 "")


def parse_amendment(text: str) -> list[AmendmentEdit]:
    """개정문 텍스트를 조문별 `AmendmentEdit` 목록으로 파싱한다.

    문형별로 후보를 모으되 이미 소비된 구간과 겹치는 것은 버리고(우선순위 순),
    최종적으로 원문 등장 순서를 보존한다. rewrite/insert의 후속 본문은 다음 편집
    시작 직전까지의 텍스트다. 인식 실패 시 빈 리스트."""
    if not text:
        return []
    text = text[:_MAX_INPUT]

    # 1) 문형별 후보 수집. 겹치는 구간은 앞선(높은 우선순위) 후보가 차지한다.
    consumed: list[tuple[int, int]] = []
    hits: list[dict] = []
    for kind, pattern in _SPECS:
        for m in pattern.finditer(text):
            span = m.span()
            if _overlaps(span, consumed):
                continue
            consumed.append(span)
            hits.append({"kind": kind, "match": m, "start": span[0], "end": span[1]})

    if not hits:
        return []

    # 2) 등장 순서로 정렬 후 조각을 채운다. 후속 본문은 다음 편집 시작이 경계.
    hits.sort(key=lambda h: h["start"])
    starts = [h["start"] for h in hits]

    edits: list[AmendmentEdit] = []
    prev_jo = ""    # 직전 절대 참조의 조 부분 ("제59조의2") — "같은 조" 해소용
    prev_hang = ""  # 직전 절대 참조의 항 부분 ("제1항") — "같은 항" 해소용
    for idx, hit in enumerate(hits):
        m = hit["match"]
        kind = hit["kind"]
        article_ref, prev_jo, prev_hang = _resolve_loc(m.group("loc"), prev_jo, prev_hang)

        before_fragment = ""
        after_fragment = ""
        if kind == "replace":
            before_fragment = m.group("a").strip()
            after_fragment = m.group("b").strip()
        elif kind == "rewrite":
            after_fragment = _body_after(text, hit["end"], starts, idx)
        elif kind == "insert":
            body = m.groupdict().get("body")
            after_fragment = (
                body.strip() if body and body.strip()
                else _body_after(text, hit["end"], starts, idx)
            )
        # delete: before/after 모두 "" — 조문 존재→소멸 신호만 남긴다.
        edits.append(AmendmentEdit(article_ref, kind, before_fragment, after_fragment))
    return edits


def derive_before_after(
    edits: list[AmendmentEdit], fallback_text: str = ""
) -> tuple[str, str]:
    """편집 목록에서 정규화기 입력용 before/after 텍스트를 파생한다.

    각 행은 `article_ref + " " + fragment` — 위치 문맥을 공통 토큰으로 남겨
    `_text_delta`가 같은 조문끼리 값 델타를 정렬하게 한다. before는 replace·delete,
    after는 replace·rewrite·insert 대상. edits가 비면 ("", fallback_text) 폴백."""
    if not edits:
        return "", fallback_text
    before_rows = [
        f"{e.article_ref} {e.before_fragment}".strip()
        for e in edits
        if e.kind in ("replace", "delete")
    ]
    after_rows = [
        f"{e.article_ref} {e.after_fragment}".strip()
        for e in edits
        if e.kind in ("replace", "rewrite", "insert")
    ]
    return "\n".join(before_rows), "\n".join(after_rows)


def _overlaps(span: tuple[int, int], consumed: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(s < end and start < e for s, e in consumed)


def _body_after(text: str, header_end: int, starts: list[int], idx: int) -> str:
    """rewrite/insert 헤더 끝부터 다음 편집 시작(없으면 끝)까지의 본문."""
    next_start = starts[idx + 1] if idx + 1 < len(starts) else len(text)
    return text[header_end:next_start].strip()


def _resolve_loc(raw: str, prev_jo: str, prev_hang: str) -> tuple[str, str, str]:
    """위치 문자열을 정규화하고 "같은 조/항" 상대 참조를 직전 컨텍스트로 해소한다.

    반환: (article_ref, 갱신된 prev_jo, 갱신된 prev_hang)."""
    loc = re.sub(r"\s+", "", raw)
    if loc.startswith("같은조"):
        rest = loc[len("같은조"):]
        hang = _HANG_RE.search(rest)
        return prev_jo + rest, prev_jo, (hang.group() if hang else prev_hang)
    if loc.startswith("같은항"):
        rest = loc[len("같은항"):]
        return prev_jo + prev_hang + rest, prev_jo, prev_hang
    jo = _JO_RE.search(loc)
    hang = _HANG_RE.search(loc)
    return loc, (jo.group() if jo else prev_jo), (hang.group() if hang else "")
