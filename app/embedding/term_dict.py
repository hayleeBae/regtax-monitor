"""
컬럼코드 → 한글명 사전 자동 수확기.

eHR 레거시는 컬럼명이 a0121 / b0181 / n0200 같은 '암호 코드'다.
사람이 읽는 한글명은 이미 코드 안에 흩어져 있다:
  - SQL mapper 인라인 주석:  `NVL(rd.n0200,0) AS n0200   -- 자녀세액공제 공제대상자녀`
  - VO 필드 주석:            `private Long l0160 = 0L;   // 대중교통`

이 모듈은 그 주석들을 regex로 긁어 {코드: [한글명...]} 사전을 만든다.
수작업 0, 코드 원본 변경 0, 언제든 재생성 가능.

단독 실행 시 커버율 통계 출력:
    python -m app.embedding.term_dict
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from config import settings

# 컬럼 코드: 소문자 1 + 숫자 4 (a0121, b0181, n0200 ...)
CODE_RE = re.compile(r"\b[a-z][0-9]{4}\b")

# SQL/XML 한 줄: (코드가 있을 수 있는 앞부분) -- 한글주석
_SQL_LINE = re.compile(r"^(.*?)--\s*(.+)$")
# Java VO 한 줄: private Type code ... // 한글주석
_JAVA_LINE = re.compile(r"private\s+\w[\w<>\[\]]*\s+([a-z][0-9]{4})\b.*?//\s*(.+)$")
# xfdl(Nexacro JS) 한 줄: (코드가 있을 수 있는 앞부분) // 한글주석
# .java 계열(// 주석)이되, VO 선언 문법(private ...)이 없는 JS 참조·상수 줄까지 잡는다.
# _JAVA_LINE 매치 줄도 이 패턴에 포함되므로(첫 코드=컬럼코드, // 뒤=라벨) 별도 분기 불필요.
_JS_LINE = re.compile(r"^(.*?)//\s*(.+)$")

_HANGUL = re.compile(r"[가-힣]")
_SKIP_DIRS = {".git", "classes", "target", "build", "node_modules", ".venv", "out", "dist"}

_CACHE = Path(__file__).resolve().parents[2] / "term_dict_cache.json"
_LOC_CACHE = Path(__file__).resolve().parents[2] / "term_loc_cache.json"

# 매핑 부트스트랩에서 법령 텍스트와 대조할 때 쓸 '구분력 있는' 한글 토큰 최소 길이.
# 2자(공제·소득 등)는 너무 흔해 오매칭 → 3자 이상만 사용.
_TERM_MIN = 3
_MAX_LOC = 12  # 코드당 위치 캐시 상한


def _read(path: Path) -> str:
    """utf-8-sig → CP949 fallback (eHR Java 레거시 대응). utf-8-sig는 BOM 유무 모두 처리(xfdl UTF-8+BOM 커버)."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, OSError):
            continue
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _clean(label: str) -> str:
    """주석에 섞인 XML 주석 기호(<!-- -->)·태그·탭·과한 공백 제거."""
    label = label.replace("<!--", " ").replace("-->", " ").replace("!--", " ")
    label = re.split(r"[<\t]", label)[0]
    label = re.sub(r"\s+", " ", label).strip()
    return label.strip(" \t\r\n*/-_")


def _scan_roots(root: Path) -> list[Path]:
    """수확기 스캔 루트 목록. 인덱서(RealCodebaseAdapter.list_files)와 같은 해석 —
    settings.repo_index_paths(쉼표 구분, root 상대) 설정 시 그 목록, 미설정 시
    <root>/src 있으면 src, 없으면 root (mock repo 회귀 방지 — 스펙 §3).
    존재하지 않는 경로는 호출부에서 조용히 건너뛴다(환경 간 차이 흡수)."""
    index_paths = [p.strip() for p in settings.repo_index_paths.split(",") if p.strip()]
    if index_paths:
        return [root / p for p in index_paths]
    return [root / "src" if (root / "src").is_dir() else root]


def _iter_source_files(repo_root: str):
    """repo의 SQL/XML/Java/xfdl 소스 파일을 (Path, relpath) 로 순회."""
    root = Path(repo_root).resolve()
    for scan_root in _scan_roots(root):
        if not scan_root.is_dir():
            continue  # 미설정 경로는 조용히 건너뜀
        for path in scan_root.rglob("*"):
            if not path.is_file() or _SKIP_DIRS & set(path.parts):
                continue
            if path.suffix.lower() in (".xml", ".sql", ".java", ".xfdl"):
                yield path, path.relative_to(root).as_posix()


def harvest(repo_root: str) -> dict[str, list[str]]:
    """repo의 SQL/XML/Java 주석을 스캔해 {코드: [한글명...]} 반환 (빈도순)."""
    raw: dict[str, Counter] = defaultdict(Counter)

    for path, _rel in _iter_source_files(repo_root):
        suffix = path.suffix.lower()
        for line in _read(path).splitlines():
            if not _HANGUL.search(line):
                continue
            if suffix == ".java":
                m = _JAVA_LINE.search(line)
                if m:
                    label = _clean(m.group(2))
                    if _HANGUL.search(label):
                        raw[m.group(1)][label] += 1
            elif suffix == ".xfdl":  # Nexacro JS — // 주석 계열, 코드가 앞부분에 등장
                m = _JS_LINE.match(line)
                if not m:
                    continue
                codes = CODE_RE.findall(m.group(1))
                if not codes:
                    continue
                label = _clean(m.group(2))
                if _HANGUL.search(label):
                    raw[codes[0]][label] += 1
            else:  # sql / xml
                m = _SQL_LINE.match(line)
                if not m:
                    continue
                codes = CODE_RE.findall(m.group(1))
                if not codes:
                    continue
                label = _clean(m.group(2))
                if _HANGUL.search(label):
                    # 한 줄에 코드가 여럿이면(예: NVL(rd.n0200,0) AS n0200) 첫 코드가 원본 컬럼
                    raw[codes[0]][label] += 1

    return {
        code: [lbl for lbl, _ in counter.most_common() if lbl]
        for code, counter in raw.items()
    }


def load(repo_root: str, refresh: bool = False) -> dict[str, list[str]]:
    """캐시가 있으면 로드, 없으면 수확 후 캐시. repo_root 비면 빈 사전(mock 대응)."""
    if not repo_root:
        return {}
    if _CACHE.exists() and not refresh:
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    table = harvest(repo_root)
    try:
        _CACHE.write_text(
            json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError:
        pass
    return table


def _hangul_set(s: str) -> set:
    return set(_HANGUL.findall(s))


def representative_labels(labels: list[str], k: int = 2) -> list[str]:
    """대표 라벨 최대 k개 선택.
    한글이 풍부한 순으로 보되, 이미 고른 라벨과 의미가 겹치면(한글 60%+ 중복) 건너뛴다.
    → 다의어 코드(예: l0160=전통시장/대중교통)는 둘 다, 동의어 변형은 1개만 남는다."""
    ranked = sorted(
        (lb for lb in labels if _HANGUL.search(lb)),
        key=lambda s: (len(_HANGUL.findall(s)), len(s)),
        reverse=True,
    )
    chosen: list[str] = []
    for lab in ranked:
        hs = _hangul_set(lab)
        if any(len(hs & _hangul_set(c)) / max(1, len(hs)) > 0.6 for c in chosen):
            continue
        chosen.append(lab)
        if len(chosen) >= k:
            break
    return chosen or ranked[:1]


# ── 매핑 부트스트랩 (법령조항 → 코드 → 파일) ─────────────────────────

def harvest_locations(repo_root: str) -> dict[str, list[str]]:
    """{코드: [코드가 등장하는 파일 relpath ...]} 반환. (주석 유무 무관, 코드 출현 기준)
    VO(.java) 선언부는 매핑·초안에 꼭 필요하므로 항상 앞쪽에 보존하고, XML 등은 _MAX_LOC로 제한."""
    java_loc: dict[str, list[str]] = defaultdict(list)
    other_loc: dict[str, list[str]] = defaultdict(list)
    for path, rel in _iter_source_files(repo_root):
        bucket = java_loc if path.suffix.lower() == ".java" else other_loc
        for code in set(CODE_RE.findall(_read(path))):
            bucket[code].append(rel)
    loc: dict[str, list[str]] = {}
    for code in set(java_loc) | set(other_loc):
        # .java를 먼저 두고 합친 뒤 상한 적용 → VO가 cap에 밀려 잘리지 않는다
        loc[code] = (java_loc.get(code, []) + other_loc.get(code, []))[:_MAX_LOC]
    return loc


def load_locations(repo_root: str, refresh: bool = False) -> dict[str, list[str]]:
    """코드→파일 위치 캐시 로드(없으면 수집·저장). repo_root 비면 빈 dict."""
    if not repo_root:
        return {}
    if _LOC_CACHE.exists() and not refresh:
        try:
            return json.loads(_LOC_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    loc = harvest_locations(repo_root)
    try:
        _LOC_CACHE.write_text(json.dumps(loc, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return loc


def _distinct_terms(label: str) -> list[str]:
    """라벨에서 구분력 있는 한글 토큰(3자+) 추출. (비한글 경계로 분리)"""
    return [t for t in re.split(r"[^가-힣]+", label) if len(t) >= _TERM_MIN]


def _term_df(table: dict[str, list[str]]) -> Counter:
    """토큰별 document-frequency: 그 토큰을 라벨에 가진 코드 수 (IDF 계산용)."""
    df: Counter = Counter()
    for labels in table.values():
        terms = {t for lab in labels for t in _distinct_terms(lab)}
        df.update(terms)
    return df


def match_codes(
    text: str, table: dict[str, list[str]], top_k: int = 8
) -> list[tuple[str, str, float]]:
    """법령 텍스트와 정확 어휘 일치하는 코드를 (코드, 매칭토큰, 점수)로 반환 (상위 top_k).
    점수 = 토큰 길이 × IDF → 길고 희소한 토큰(예: '자녀세액공제')일수록 높다.
    '세액공제'·'사용금액'처럼 여러 코드에 흔한 토큰은 자동으로 낮게 깔린다."""
    import math

    df = _term_df(table)
    n = max(1, len(table))
    scored: list[tuple[str, str, float]] = []
    for code, labels in table.items():
        best_t, best_s = "", 0.0
        for lab in labels:
            for term in _distinct_terms(lab):
                if term in text:
                    s = len(term) * math.log(1 + n / df[term])
                    if s > best_s:
                        best_s, best_t = s, term
        if best_t:
            scored.append((code, best_t, round(best_s, 2)))
    scored.sort(key=lambda x: -x[2])
    return scored[:top_k]


def rank_locations(files: list[str]) -> list[str]:
    """매핑 시드용 파일 우선순위: VO(.java) → 연도 없는(현행) XML → 최신 연도 XML."""
    def key(p: str):
        low = p.lower()
        m = re.search(r"_(\d{4})\.", low)
        year = int(m.group(1)) if m else 9999  # 연도 없는 현행 파일 최우선
        return (0 if low.endswith(".java") else 1, -year, p)

    return sorted(files, key=key)


def build_header(chunk: str, table: dict[str, list[str]], max_terms: int = 60) -> str:
    """청크에 등장하는 코드의 한글명을 헤더로 만들어 반환. 없으면 빈 문자열."""
    seen: list[str] = []
    for code in CODE_RE.findall(chunk):
        if code in table and code not in seen:
            seen.append(code)
            if len(seen) >= max_terms:
                break
    if not seen:
        return ""
    terms = "; ".join(f"{c}={' / '.join(representative_labels(table[c]))}" for c in seen)
    return f"[관련 항목] {terms}"


if __name__ == "__main__":
    from config import settings

    repo = settings.repo_root
    print(f"repo_root = {repo!r}")
    table = harvest(repo)
    print(f"수확된 distinct 컬럼코드: {len(table)}개")
    print(f"캐시 경로: {_CACHE}")
    _CACHE.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")

    # 세금 관련 핵심 코드 몇 개 점검
    probes = ["b0181", "n0200", "n0201", "l0200", "a0121", "a0136", "l0160"]
    print("\n── 샘플 점검 ──")
    for c in probes:
        print(f"  {c}: {table.get(c, '∅ (사전에 없음)')}")

    # 라벨이 여러 개인(=파일마다 표현 다른) 코드 상위 10개
    multi = sorted(
        ((len(v), k, v) for k, v in table.items() if len(v) > 1), reverse=True
    )[:10]
    if multi:
        print("\n── 라벨이 여러 갈래인 코드 상위 10 (참고) ──")
        for n, code, labels in multi:
            print(f"  {code} ({n}): {labels[:4]}")
