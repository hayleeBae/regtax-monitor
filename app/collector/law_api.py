"""
법제처 OPEN API 수집기 (Phase 1).

국세 관련 법령을 공포일 기준으로 조회하고, last_sync 이후 변경을 감지한다.
실시간 불필요 — 평일 1회 배치로 충분.

API 문서: https://open.law.go.kr/LSO/openApi/openApiInfo.do
"""
import re

import httpx

from config import settings

BASE_URL = "https://www.law.go.kr/DRF"

# 국세 관련 기본 법률 (법제처 API는 소관부처 필터 미지원 → 법령명으로 대체)
TAX_LAWS = ["국세기본법", "소득세법", "법인세법", "부가가치세법", "조세특례제한법"]

# 수집 계층. 세법은 위임 구조라 실제 수치·요건(총급여 기준, 간이세액표 등)이
# 시행령·시행규칙에만 있는 경우가 많다 — 법률만 수집하면 그 개정을 놓친다.
TIERS = ["", " 시행령", " 시행규칙"]


def build_whitelist(include_decrees: bool = True) -> list[str]:
    """수집 대상 법령명 화이트리스트. 시행령·시행규칙은 개정이 잦아서
    이 정확 법령명 필터가 없으면 무관한 변경이 노이즈로 쏟아진다."""
    tiers = TIERS if include_decrees else [""]
    return [law + tier for law in TAX_LAWS for tier in tiers]


def law_tier(law_name: str) -> str:
    """법령명으로 계층 분류: 법률 / 시행령 / 시행규칙."""
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

    def search_changed(self, since: str) -> list[dict]:
        """
        since(YYYYMMDD) 이후 공포된 국세 관련 법령(법률 + 시행령·시행규칙) 목록을
        반환한다. OC 키가 없으면 mock 데이터를 반환한다.
        """
        if self._mock_mode:
            return self._mock_results(since)

        whitelist = build_whitelist(settings.collect_decrees)
        results: list[dict] = []
        for query in whitelist:
            results.extend(self._fetch_one_query(query, since))

        return dedupe_and_filter(results, whitelist)

    def fetch_detail(self, mst: str) -> dict:
        """
        법령 MST로 개정문·제개정이유를 조회하여 반환한다.
        반환값: {"article_no": str, "before_text": str, "after_text": str}
        """
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{BASE_URL}/lawService.do", params={
                "OC": self.oc,
                "target": "law",
                "MST": mst,
                "type": "JSON",
            })
            resp.raise_for_status()
            data = resp.json()

        law = data.get("법령", {})

        # 개정문 — 구체적인 변경 내용 ("X를 Y로 한다" 형식)
        gaejung_items = law.get("개정문", {}).get("개정문내용", [])
        if isinstance(gaejung_items, list) and gaejung_items:
            raw = gaejung_items[0] if isinstance(gaejung_items[0], list) else gaejung_items
            before_text = "\n".join(str(s) for s in raw if str(s).strip())
        else:
            before_text = ""

        # 제개정이유 — 개정 배경·주요내용
        iyou_items = law.get("제개정이유", {}).get("제개정이유내용", [])
        if isinstance(iyou_items, list) and iyou_items:
            raw = iyou_items[0] if isinstance(iyou_items[0], list) else iyou_items
            after_text = "\n".join(str(s) for s in raw if str(s).strip())
        else:
            after_text = ""

        # 개정문에서 조문 번호 추출 (첫 번째 언급 기준)
        article_no = _extract_article_no(before_text)

        return {
            "article_no": article_no,
            "before_text": before_text,
            "after_text": after_text,
        }

    def _fetch_one_query(self, query: str, since: str) -> list[dict]:
        params = {
            "OC": self.oc,
            "target": "law",
            "type": "JSON",
            "query": query,
            "display": 20,
            "page": 1,
            "sort": "date",
            "promulgationDateFrom": since,
        }
        with httpx.Client(timeout=20) as client:
            resp = client.get(f"{BASE_URL}/lawSearch.do", params=params)
            resp.raise_for_status()
            data = resp.json()

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
                "before_text": "",
                "after_text": "",
            }
            for item in laws
            if item.get("공포일자", "") >= since
        ]

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
                "before_text": "종합소득에 대한 소득세는 … 세율을 적용한다.",
                "after_text": "종합소득에 대한 소득세는 … 개정된 세율을 적용한다.",
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
                "before_text": "간이세액표 적용 시 과세표준 1천400만원 이하 구간의 "
                               "세율은 100분의 6으로 한다.",
                "after_text": "간이세액표 적용 시 과세표준 1천400만원 이하 구간의 "
                              "세율은 100분의 7로 한다.",
            },
        ]


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
