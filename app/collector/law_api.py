"""
법제처 OPEN API 수집기 (Phase 1).

국세 관련 법령을 공포일 기준으로 조회하고, last_sync 이후 변경을 감지한다.
실시간 불필요 — 평일 1회 배치로 충분.

법령(law) 외 추가 target — 인사 도메인 확장 대비 (참조 구현: korean-law-mcp, MIT):
  - admrul  행정규칙(고시·훈령·예규·공고). 최저임금·보험요율처럼 법령 개정이 아니라
            고시로 바뀌는 수치를 잡는다. ADMIN_RULE_QUERIES 설정 시에만 수집.
  - eflaw   시행일 기준 조회 — "곧 시행될 개정" 관점의 보완 (search_effective)
  - thdCmp  위임조문 3단비교 — "대통령령으로 정하는 금액"이 시행령 몇 조인지
            직행하는 위임 매핑 (fetch_three_tier)

주의: 법제처 API는 target별로 사용 신청이 필요하다. 미신청 target을 호출하면
HTML 오류 페이지가 오며 ApiNotGrantedError로 변환된다 — open.law.go.kr의
OPEN API 신청 관리에서 해당 목록/본문을 추가 신청해야 한다.

API 문서: https://open.law.go.kr/LSO/openApi/openApiInfo.do
"""
import re
import ssl

import httpx

from app.domain.changes.amendment import derive_before_after, parse_amendment
from config import settings

BASE_URL = "https://www.law.go.kr/DRF"

# 국세 관련 기본 법률 (법제처 API는 소관부처 필터 미지원 → 법령명으로 대체)
TAX_LAWS = ["국세기본법", "소득세법", "법인세법", "부가가치세법", "조세특례제한법"]

# 수집 계층. 세법은 위임 구조라 실제 수치·요건(총급여 기준, 간이세액표 등)이
# 시행령·시행규칙에만 있는 경우가 많다 — 법률만 수집하면 그 개정을 놓친다.
TIERS = ["", " 시행령", " 시행규칙"]

# 행정규칙 종류 (admrul 검색 결과의 행정규칙종류 필드 값)
ADMIN_RULE_KINDS = ("고시", "공고", "훈령", "예규")


def _ssl_verify():
    """회사 SSL 인터셉트 프록시 환경에서는 기본 인증서 검증이 실패한다
    (self-signed certificate in chain). truststore가 있으면 OS 인증서
    저장소(프록시 CA 포함)를 신뢰하고, 없으면 기본 검증을 쓴다."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return True


_VERIFY = _ssl_verify()


class ApiNotGrantedError(RuntimeError):
    """OC 키에 해당 target 사용 신청이 안 되어 있음 (법제처 API는 target별 신청제)."""


def build_whitelist(include_decrees: bool = True, laws: list[str] | None = None) -> list[str]:
    """수집 대상 법령명 화이트리스트. 시행령·시행규칙은 개정이 잦아서
    이 정확 법령명 필터가 없으면 무관한 변경이 노이즈로 쏟아진다.
    laws 미지정 시 기본 세법 목록(TAX_LAWS)."""
    base = laws if laws is not None else TAX_LAWS
    tiers = TIERS if include_decrees else [""]
    return [law + tier for law in base for tier in tiers]


def law_tier(law_name: str, source: str = "law") -> str:
    """계층 분류: 법률 / 시행령 / 시행규칙 / 고시·훈령·예규·공고.
    법령은 이름으로 판별하고, 행정규칙은 수집 시 저장한 source(종류)를 그대로 쓴다
    — 고시명에는 종류가 안 드러나는 경우가 많다 ('2026년 적용 최저임금')."""
    if source and source != "law":
        return source
    if law_name.endswith("시행규칙"):
        return "시행규칙"
    if law_name.endswith("시행령"):
        return "시행령"
    return "법률"


class LawApiClient:
    def __init__(self, oc: str | None = None):
        self.oc = oc or settings.law_api_oc

    @property
    def _mock_mode(self) -> bool:
        return not self.oc or self.oc == "your_oc_key"

    def _get_json(self, endpoint: str, params: dict) -> dict:
        """공통 GET. 미신청 target의 HTML 오류 페이지를 명확한 예외로 변환한다."""
        merged = {"OC": self.oc, "type": "JSON", **params}
        with httpx.Client(timeout=30, verify=_VERIFY) as client:
            resp = client.get(f"{BASE_URL}/{endpoint}", params=merged)
            resp.raise_for_status()
        body = resp.text.strip()
        if not body.startswith("{"):
            target = merged.get("target", "?")
            if "미신청" in body:
                raise ApiNotGrantedError(
                    f"OC 키에 '{target}' API 사용 신청이 없습니다. "
                    "open.law.go.kr → OPEN API 신청 관리에서 해당 목록/본문을 추가 신청하세요."
                )
            raise RuntimeError(f"법제처 API 비정상 응답 (target={target}): {body[:120]}")
        return resp.json()

    # ── 법령 (law) ──────────────────────────────────────────────

    def search_changed(self, since: str, laws: list[str] | None = None) -> list[dict]:
        """
        since(YYYYMMDD) 이후 공포된 법령(법률 + 시행령·시행규칙) 목록을 반환한다.
        laws 미지정 시 기본 세법 목록. OC 키가 없으면 mock 데이터를 반환한다.
        """
        if self._mock_mode:
            return self._mock_results(since)

        whitelist = build_whitelist(settings.collect_decrees, laws)
        results: list[dict] = []
        for query in whitelist:
            results.extend(self._fetch_one_query(query, since))

        return dedupe_and_filter(results, whitelist)

    def fetch_detail(self, mst: str) -> dict:
        """
        법령 MST로 개정문·제개정이유를 조회하여 4필드로 반환한다.
        HTTP 호출 후 파싱은 순수 함수 `_parse_law_detail`에 위임한다.
        반환값: {"article_no", "amendment_text", "reason_text",
                 "before_text", "after_text", "amendment_parsed"}
        """
        data = self._get_json("lawService.do", {"target": "law", "MST": mst})
        return _parse_law_detail(data.get("법령", {}))

    def _fetch_one_query(self, query: str, since: str) -> list[dict]:
        data = self._get_json("lawSearch.do", {
            "target": "law",
            "query": query,
            "display": 20,
            "page": 1,
            "sort": "date",
            "promulgationDateFrom": since,
        })
        laws = data.get("LawSearch", {}).get("law", [])
        if isinstance(laws, dict):
            laws = [laws]

        return [
            {
                "law_id": item.get("법령ID", ""),
                "law_mst": item.get("법령일련번호", ""),
                "law_name": item.get("법령명한글", ""),
                "promulgation_date": item.get("공포일자", ""),
                "effective_date": item.get("시행일자", ""),
                "article_no": "",
                "amendment_text": "",
                "reason_text": "",
                "before_text": "",
                "after_text": "",
                "source": "law",
            }
            for item in laws
            if item.get("공포일자", "") >= since
        ]

    # ── 행정규칙 (admrul) — 고시·훈령·예규·공고 ─────────────────

    def search_admin_rules(self, since: str, queries: list[str] | None = None) -> list[dict]:
        """검색어별로 since 이후 발령된 행정규칙을 조회한다.
        queries 미지정 시 ADMIN_RULE_QUERIES 설정 사용.

        행정규칙은 법령과 달리 이름이 매년 바뀌는 경우가 많아
        ('2026년 적용 최저임금 고시') 정확 일치 화이트리스트 대신
        검색어 부분일치 + 발령일 필터를 쓴다. 검색어가 비어 있으면 수집하지 않는다.
        """
        if queries is None:
            queries = [q.strip() for q in settings.admin_rule_queries.split(",") if q.strip()]
        if not queries:
            return []
        if self._mock_mode:
            return self._mock_admin_rules(since)

        items: list[dict] = []
        seen: set[str] = set()
        for query in queries:
            data = self._get_json("lawSearch.do", {
                "target": "admrul",
                "query": query,
                "display": 50,
            })
            root = data.get("AdmRulSearch") or next(
                (v for v in data.values() if isinstance(v, dict)), {}
            )
            rules = root.get("admrul", [])
            if isinstance(rules, dict):
                rules = [rules]
            for item in rules:
                name = (item.get("행정규칙명") or "").strip()
                rule_id = item.get("행정규칙ID", "")
                prom = item.get("발령일자", "")
                if not rule_id or rule_id in seen:
                    continue
                if prom < since or query not in name:
                    continue
                seen.add(rule_id)
                kind = (item.get("행정규칙종류") or "").strip() or "행정규칙"
                items.append({
                    "law_id": rule_id,
                    "law_mst": item.get("행정규칙일련번호", ""),
                    "law_name": name,
                    "promulgation_date": prom,
                    "effective_date": item.get("시행일자", ""),
                    "article_no": "",
                    "amendment_text": "",
                    "reason_text": "",
                    "before_text": "",
                    "after_text": "",
                    "source": kind,
                })
        return items

    def fetch_admin_rule_detail(self, rule_seq: str, rule_id: str = "") -> dict:
        """행정규칙 본문 조회. 행정규칙은 법령과 달리 신구대조 API가 없어
        본문 전문을 after_text로 반환한다 (before_text는 비움).

        실API 검증(2026-07): ID 파라미터는 행정규칙ID가 아니라 **행정규칙일련번호**를
        받는다 (행정규칙ID는 LID 파라미터). 최저임금 고시처럼 조문내용이 비어 있고
        실내용이 첨부파일(PDF)에만 있는 경우가 많아, 본문이 비면 PDF 첨부를
        내려받아 텍스트를 추출한다 (HWP 등 비PDF는 미지원 — 파일명만 남김)."""
        params: dict = {"target": "admrul"}
        if rule_seq:
            params["ID"] = rule_seq
        elif rule_id:
            params["LID"] = rule_id
        else:
            raise ValueError("행정규칙일련번호 또는 행정규칙ID가 필요합니다.")
        data = self._get_json("lawService.do", params)
        svc = data.get("AdmRulService", data)
        text = _collect_text(
            svc, keys=("조문내용", "부칙", "개정문내용", "제개정이유내용")
        )
        if len(text) < 50:
            text = (text + "\n\n" + self._attachment_text(svc)).strip()
        # 행정규칙은 신구대조가 없어 본문 전문을 after_text로 둔다 (스펙 §3-5,
        # "신설 공표" 의미). 개정문/제개정이유는 없으므로 빈 문자열로 계약 정렬.
        return {
            "article_no": _extract_article_no(text),
            "amendment_text": "",
            "reason_text": "",
            "before_text": "",
            "after_text": text,
            "amendment_parsed": False,
        }

    def _attachment_text(self, svc: dict, max_pages: int = 20) -> str:
        """행정규칙 첨부파일(PDF) 텍스트 추출. 고시의 실제 수치(최저임금액 등)가
        본문이 아니라 첨부에만 있는 경우의 폴백."""
        att = svc.get("첨부파일") or {}
        links = att.get("첨부파일링크") or []
        names = att.get("첨부파일명") or []
        if isinstance(links, str):
            links = [links]
        if isinstance(names, str):
            names = [names]

        parts: list[str] = []
        for link, name in zip(links, names):
            if not name.lower().endswith(".pdf"):
                parts.append(f"[첨부(텍스트 미추출): {name}]")
                continue
            try:
                with httpx.Client(timeout=60, verify=_VERIFY, follow_redirects=True) as client:
                    resp = client.get(link)
                    resp.raise_for_status()
                from io import BytesIO

                from pypdf import PdfReader

                reader = PdfReader(BytesIO(resp.content))
                body = "\n".join(
                    (page.extract_text() or "") for page in reader.pages[:max_pages]
                ).strip()
                parts.append(f"[첨부: {name}]\n{body}" if body else f"[첨부(빈 텍스트): {name}]")
            except Exception as e:
                parts.append(f"[첨부 추출 실패: {name} — {e}]")
        return "\n\n".join(parts)[:20000]

    def _mock_admin_rules(self, since: str) -> list[dict]:
        """OC 키 없을 때 개발용 더미 행정규칙 (최저임금 고시 — 인사 도메인 대표 사례)"""
        return [
            {
                "law_id": "MOCK-ADM-001",
                "law_mst": "",
                "law_name": "2026년 적용 최저임금",
                "promulgation_date": since,
                "effective_date": since,
                "article_no": "",
                "amendment_text": "",
                "reason_text": "",
                "before_text": "시간급 최저임금액은 10,030원으로 한다.",
                "after_text": "시간급 최저임금액은 10,320원으로 한다.",
                "source": "고시",
            },
        ]

    # ── 확장 유틸리티 (eflaw / thdCmp) — 수집 파이프라인 미연결 ──

    def search_effective(self, ef_from: str, queries: list[str] | None = None) -> list[dict]:
        """시행일 기준 조회(eflaw). 공포일 수집(search_changed)의 보완 —
        "다음 달 시행 예정" 관점으로 반영 마감이 임박한 개정을 본다.
        반환 항목에 현행연혁코드(예: '시행예정')가 포함된다."""
        whitelist = queries or build_whitelist(settings.collect_decrees)
        results: list[dict] = []
        seen: set[tuple] = set()
        for query in whitelist:
            data = self._get_json("lawSearch.do", {
                "target": "eflaw",
                "query": query,
                "display": 20,
                "sort": "efdes",
            })
            laws = data.get("LawSearch", {}).get("law", [])
            if isinstance(laws, dict):
                laws = [laws]
            for item in laws:
                name = (item.get("법령명한글") or "").strip()
                key = (item.get("법령ID", ""), item.get("시행일자", ""))
                if key in seen or name not in whitelist:
                    continue
                if item.get("시행일자", "") < ef_from:
                    continue
                seen.add(key)
                results.append({
                    "law_id": item.get("법령ID", ""),
                    "law_mst": item.get("법령일련번호", ""),
                    "law_name": name,
                    "promulgation_date": item.get("공포일자", ""),
                    "effective_date": item.get("시행일자", ""),
                    "status_code": item.get("현행연혁코드", ""),
                    "revision_type": item.get("제개정구분명", ""),
                })
        return results

    def fetch_three_tier(self, mst: str) -> list[dict]:
        """위임조문 3단비교(thdCmp). 법률 조문이 시행령·시행규칙 어느 조문으로
        위임되는지의 공식 매핑 — 이름 기반 계층 수집을 실제 위임 관계로 보강한다.
        반환: [{"law_article": "제55조", "decree": ["소득세법 시행령 제116조", ...],
               "rule": [...]}] (위임이 있는 조문만)."""
        data = self._get_json("lawService.do", {"target": "thdCmp", "MST": mst, "knd": "2"})
        svc = data.get("LspttnThdCmpLawXService", {})
        articles = svc.get("위임조문삼단비교", {}).get("법률조문", [])
        if isinstance(articles, dict):
            articles = [articles]

        merged: dict[str, dict] = {}
        for art in articles:
            refs = {
                "decree": art.get("시행령조문"),
                "rule": art.get("시행규칙조문"),
            }
            if not any(refs.values()):
                continue
            key = _format_article_no(art)
            entry = merged.setdefault(key, {"law_article": key, "decree": [], "rule": []})
            for bucket, tgt in refs.items():
                if not tgt:
                    continue
                for t in tgt if isinstance(tgt, list) else [tgt]:
                    ref = f"{t.get('법령명', '')} {_format_article_no(t)}".strip()
                    if ref not in entry[bucket]:
                        entry[bucket].append(ref)
        return list(merged.values())

    def _mock_results(self, since: str) -> list[dict]:
        """OC 키 없을 때 개발용 더미 데이터 (법률 1건 + 시행령 1건)"""
        return [
            {
                "law_id": "MOCK-001",
                "law_mst": "",
                "law_name": "소득세법",
                "promulgation_date": since,
                "effective_date": since,
                "article_no": "제55조",
                # 실 개정문 P1 문형 — mock이 실 데이터 구조를 흉내 내야 결함이 숨지 않는다.
                "amendment_text": '제55조제1항 중 "1천200만원"을 "1천400만원"으로 한다.',
                "reason_text": "종합소득 기본세율 최저구간 과세표준 상한을 "
                               "1천200만원에서 1천400만원으로 상향.",
                "before_text": "제55조제1항 과세표준 1천200만원 이하",
                "after_text": "제55조제1항 과세표준 1천400만원 이하",
                "source": "law",
            },
            {
                # 위임 수치 개정의 예 — 법률엔 "대통령령으로 정하는 세율"만 있고
                # 실제 수치는 시행령에 있어, 시행령 수집 없이는 상수 매칭이 불가한 유형
                "law_id": "MOCK-002",
                "law_mst": "",
                "law_name": "소득세법 시행령",
                "promulgation_date": since,
                "effective_date": since,
                "article_no": "제189조",
                "amendment_text": '제189조제1항 중 "100분의 6"을 "100분의 7"로 한다.',
                "reason_text": "간이세액표 최저구간 원천징수 세율을 "
                               "100분의 6에서 100분의 7로 조정.",
                "before_text": "간이세액표 적용 시 과세표준 1천400만원 이하 구간의 "
                               "세율은 100분의 6으로 한다.",
                "after_text": "간이세액표 적용 시 과세표준 1천400만원 이하 구간의 "
                              "세율은 100분의 7로 한다.",
                "source": "law",
            },
        ]


def _parse_law_detail(law: dict) -> dict:
    """lawService.do 응답의 '법령' dict → 4필드 + 계측.

    개정문/제개정이유 원문을 보존하고(`amendment_text`/`reason_text`),
    before/after는 개정문 파싱으로 파생한다(스펙 §2, ADR-014). HTTP 없는
    순수 함수라 응답 dict fixture만으로 테스트할 수 있다.
    반환: {"article_no": str, "amendment_text": str, "reason_text": str,
           "before_text": str, "after_text": str, "amendment_parsed": bool}
    """
    amendment_text = _extract_content(law, "개정문", "개정문내용")
    reason_text = _extract_content(law, "제개정이유", "제개정이유내용")

    edits = parse_amendment(amendment_text)
    before_text, after_text = derive_before_after(edits, fallback_text=amendment_text)

    return {
        "article_no": _extract_article_no(amendment_text),
        "amendment_text": amendment_text,
        "reason_text": reason_text,
        "before_text": before_text,
        "after_text": after_text,
        "amendment_parsed": bool(edits),
    }


def _extract_content(law: dict, outer: str, inner: str) -> str:
    """'법령' dict의 outer.inner 하위 문자열을 개행 join으로 추출한다.
    법제처 응답은 리스트-안-리스트 변형이 있어(첫 원소가 list) 한 겹 벗긴다."""
    items = law.get(outer, {}).get(inner, [])
    if isinstance(items, list) and items:
        raw = items[0] if isinstance(items[0], list) else items
        return "\n".join(str(s) for s in raw if str(s).strip())
    return ""


def dedupe_and_filter(items: list[dict], whitelist: list[str]) -> list[dict]:
    """law_id 중복 제거 + 법령명 정확 일치 필터.
    query 검색은 이름 부분일치라 '소득세법' 검색에 유사 법령이 섞여 들어온다 —
    화이트리스트에 정확히 있는 법령명만 통과시킨다."""
    allowed = set(whitelist)
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        key = item["law_id"]
        if key in seen or item.get("law_name", "").strip() not in allowed:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _extract_article_no(text: str) -> str:
    """개정문 텍스트에서 첫 번째 조문 번호를 추출한다."""
    m = re.search(r"제\d+조(?:의\d+)?(?:제\d+항)?", text)
    return m.group() if m else ""


def _format_article_no(article: dict) -> str:
    """thdCmp의 조번호('0055')·조가지번호('02')를 '제55조의2' 형식으로."""
    try:
        no = int(article.get("조번호", "0"))
    except (TypeError, ValueError):
        return ""
    try:
        branch = int(article.get("조가지번호") or 0)
    except (TypeError, ValueError):
        branch = 0
    return f"제{no}조의{branch}" if branch else f"제{no}조"


def _collect_text(obj, keys: tuple, limit: int = 20000) -> str:
    """중첩 JSON에서 지정 키 하위의 문자열을 순서대로 모은다.
    행정규칙 본문 응답은 구조 변형이 많아 키 경로를 고정하지 않는다."""
    out: list[str] = []

    def walk(node, hit: bool) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, hit or k in keys)
        elif isinstance(node, list):
            for v in node:
                walk(v, hit)
        elif hit and isinstance(node, str) and node.strip():
            out.append(node.strip())

    walk(obj, False)
    return "\n".join(out)[:limit]
