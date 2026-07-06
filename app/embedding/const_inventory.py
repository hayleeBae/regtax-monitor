"""
세법 상수 인벤토리 — '값'으로 코드 위치를 찾는다 (효율 개선 로드맵 2번).

개정문은 "연 15만원을 25만원으로"처럼 금액·세율을 말하고, 코드에는 그 값이
숫자 리터럴(150000L, 0.15, upperLimit="14000000")로 박혀 있다. 임베딩·변수명
매칭은 확률적 추측이지만 숫자는 정확 일치가 가능하다:

  1. harvest: 코드의 세법성 숫자 리터럴(1000원 단위 금액, 소수 세율)을
     {값: [(파일, 줄번호, 줄내용)...]} 인벤토리로 수확.
  2. parse_amounts: 개정문의 한국어 금액 표기(15만원, 1천500만원, 100분의 6)를
     숫자 값으로 변환.
  3. match_constants: 두 결과를 정확 일치시켜 매핑 후보를 만든다.
     값이 희소할수록(등장 파일이 적을수록) 점수가 높다 — term_dict의 IDF와 동일 원리.

수작업 0, 코드 원본 변경 0, 캐시는 언제든 재생성 가능 (term_dict와 동일 규약).

단독 실행 시 인벤토리 통계 출력:
    python -m app.embedding.const_inventory
"""
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from app.embedding.term_dict import _iter_source_files, _read

_CACHE = Path(__file__).resolve().parents[2] / "const_inventory_cache.json"

_MAX_LOC = 12          # 값당 위치 캐시 상한 (term_dict._MAX_LOC와 동일)
_MAX_VALUE = 1e13      # 이보다 큰 정수는 ID·타임스탬프로 간주
_EXCERPT_LEN = 160

# 코드의 숫자 리터럴: 정수(뒤에 Java L 접미사 허용) 또는 소수
_NUM_RE = re.compile(r"(?<![\w.])(\d+\.\d+|\d{4,})[Ll]?(?![\w.])")

# 노이즈 소수 (xml version="1.0" 등)
_DENY = {0.0, 1.0}

# 개정문 한국어 금액 표기 — 구체적 표현이 앞에 오도록 배열 (finditer가 소비하며 진행)
_AMOUNT_RE = re.compile(
    r"(?P<a_eok>\d[\d,]*)\s*억(?:\s*(?P<a_cheonman>\d[\d,]*)\s*천만?)?\s*원"  # 2억원, 1억2천만원
    r"|(?P<b_cheon>\d[\d,]*)\s*천\s*(?P<b_man>\d[\d,]*)\s*만\s*원"            # 1천500만원
    r"|(?P<c_cheonman>\d[\d,]*)\s*천만\s*원"                                   # 7천만원
    r"|(?P<d_man>\d[\d,]*)\s*만\s*원"                                          # 15만원, 150만원
    r"|(?P<e_won>\d[\d,]*)\s*원"                                               # 150,000원
)
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|퍼센트|프로)")
_BUNUI_RE = re.compile(r"(\d[\d,]*)\s*분의\s*(\d+(?:\.\d+)?)")  # 100분의 6 → 0.06


def _key(v: float) -> str:
    """값의 정규화 키: 0.0600 → '0.06', 14000000.0 → '14000000'."""
    if float(v).is_integer():
        return str(int(v))
    return f"{v:g}"


def _is_tax_constant(v: float) -> bool:
    """세법성 판정: 1000원 단위 금액(반올림 값) 또는 1 미만~100 미만 소수(세율·배율)."""
    if v in _DENY or v <= 0 or v > _MAX_VALUE:
        return False
    if float(v).is_integer():
        return v >= 1000 and int(v) % 1000 == 0
    return v < 100


def harvest(repo_root: str) -> dict[str, list[list]]:
    """repo의 Java/SQL/XML에서 세법성 숫자 리터럴을 수확.
    반환: {값키: [[relpath, 줄번호, 줄내용], ...]} (값당 _MAX_LOC 제한)."""
    inv: dict[str, list[list]] = defaultdict(list)
    for path, rel in _iter_source_files(repo_root):
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            seen_in_line: set[str] = set()
            for m in _NUM_RE.finditer(line):
                v = float(m.group(1))
                if not _is_tax_constant(v):
                    continue
                key = _key(v)
                if key in seen_in_line:
                    continue
                seen_in_line.add(key)
                if len(inv[key]) < _MAX_LOC:
                    inv[key].append([rel, lineno, line.strip()[:_EXCERPT_LEN]])
    return dict(inv)


def load_inventory(repo_root: str, refresh: bool = False) -> dict[str, list[list]]:
    """캐시가 있으면 로드, 없으면 수확 후 캐시. repo_root 비면 빈 인벤토리."""
    if not repo_root:
        return {}
    if _CACHE.exists() and not refresh:
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    inv = harvest(repo_root)
    try:
        _CACHE.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return inv


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


def parse_amounts(text: str) -> dict[str, str]:
    """개정문에서 금액·세율 표기를 추출해 {값키: 원문표현} 반환."""
    found: dict[str, str] = {}

    def put(v: float, expr: str) -> None:
        found.setdefault(_key(v), expr.strip())

    for m in _AMOUNT_RE.finditer(text):
        g = m.groupdict()
        if g["a_eok"]:
            v = _to_int(g["a_eok"]) * 10**8
            if g["a_cheonman"]:
                v += _to_int(g["a_cheonman"]) * 10**7
        elif g["b_cheon"]:
            v = (_to_int(g["b_cheon"]) * 1000 + _to_int(g["b_man"])) * 10**4
        elif g["c_cheonman"]:
            v = _to_int(g["c_cheonman"]) * 10**7
        elif g["d_man"]:
            v = _to_int(g["d_man"]) * 10**4
        else:
            v = _to_int(g["e_won"])
        put(v, m.group(0))

    for m in _PCT_RE.finditer(text):
        pct = float(m.group(1))
        put(pct / 100, m.group(0))   # 코드는 0.15 형태가 일반적
        put(pct, m.group(0))         # rate="15" 형태 대비

    for m in _BUNUI_RE.finditer(text):
        den, num = _to_int(m.group(1)), float(m.group(2))
        if den > 0:
            put(num / den, m.group(0))

    return found


def match_constants(
    text: str, inv: dict[str, list[list]], top_k: int = 8
) -> list[tuple[str, str, float, list[str]]]:
    """개정문 금액 ↔ 인벤토리 값 정확 일치 → (값키, 원문표현, 점수, 파일들) 상위 top_k.
    점수 = 유효자릿수 × IDF: 크고 희소한 값(예: 14000000이 파일 2곳)일수록 높고,
    흔한 값(1000 등 도처에 있는 것)은 자동으로 낮게 깔린다."""
    if not inv:
        return []
    n_files = len({loc[0] for locs in inv.values() for loc in locs}) or 1
    scored: list[tuple[str, str, float, list[str]]] = []
    for key, expr in parse_amounts(text).items():
        locs = inv.get(key)
        if not locs:
            continue
        files: list[str] = []
        for rel, _lineno, _text in locs:
            if rel not in files:
                files.append(rel)
        digits = len(key.replace(".", "").lstrip("0")) or 1
        score = digits * math.log(1 + n_files / len(files))
        scored.append((key, expr, round(score, 2), files))
    scored.sort(key=lambda x: -x[2])
    return scored[:top_k]


if __name__ == "__main__":
    from config import settings

    repo = settings.repo_root or "mock_repo"
    print(f"repo_root = {repo!r}")
    inv = harvest(repo)
    total_locs = sum(len(v) for v in inv.values())
    print(f"수확된 distinct 상수 값: {len(inv)}개, 위치 {total_locs}건")
    print(f"캐시 경로: {_CACHE}")
    _CACHE.write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")

    sample = sorted(inv.items(), key=lambda kv: -len(kv[0]))[:10]
    print("\n── 자릿수 큰 상수 상위 10 ──")
    for key, locs in sample:
        rel, lineno, text = locs[0]
        print(f"  {key}: {rel}:{lineno}  {text[:70]}")
