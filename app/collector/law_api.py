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

# 국세 관련 주요 검색어 (법제처 API는 소관부처 필터 미지원 → 키워드로 대체)
TAX_QUERIES = ["국세기본법", "소득세법", "법인세법", "부가가치세법", "조세특례제한법"]


class LawApiClient:
    def __init__(self, oc: str | None = None):
        self.oc = oc or settings.law_api_oc

    @property
    def _mock_mode(self) -> bool:
        return not self.oc or self.oc == "your_oc_key"

    def search_changed(self, since: str) -> list[dict]:
        """
        since(YYYYMMDD) 이후 공포된 국세 관련 법령 목록을 반환한다.
        OC 키가 없으면 mock 데이터를 반환한다.
        """
        if self._mock_mode:
            return self._mock_results(since)

        results: list[dict] = []
        for query in TAX_QUERIES:
            items = self._fetch_one_query(query, since)
            results.extend(items)

        # law_id 기준 중복 제거
        seen: set[str] = set()
        unique = []
        for item in results:
            key = item["law_id"]
            if key not in seen:
                seen.add(key)
                unique.append(item)

        return unique

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
        """OC 키 없을 때 개발용 더미 데이터"""
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
            }
        ]


def _extract_article_no(text: str) -> str:
    """개정문 텍스트에서 첫 번째 조문 번호를 추출한다."""
    m = re.search(r"제\d+조(?:의\d+)?(?:제\d+항)?", text)
    return m.group() if m else ""
