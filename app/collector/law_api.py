"""
법제처 OPEN API 수집기 (Phase 1).

국세 관련 법령을 공포일 기준으로 조회하고, last_sync 이후 변경을 감지한다.
실시간 불필요 — 평일 1회 배치로 충분.

API 문서: https://open.law.go.kr/LSO/openApi/openApiInfo.do
"""
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

    def _fetch_one_query(self, query: str, since: str) -> list[dict]:
        params = {
            "OC": self.oc,
            "target": "law",
            "type": "JSON",
            "query": query,
            "display": 20,
            "page": 1,
            "sort": "date",                  # 최신 공포일순
            "promulgationDateFrom": since,   # 이 날짜 이후 공포분만
        }
        with httpx.Client(timeout=20) as client:
            resp = client.get(f"{BASE_URL}/lawSearch.do", params=params)
            resp.raise_for_status()
            data = resp.json()

        laws = data.get("LawSearch", {}).get("law", [])
        if isinstance(laws, dict):   # 단건이면 dict로 오는 경우 있음
            laws = [laws]

        return [
            {
                "law_id": item.get("법령ID", ""),
                "law_name": item.get("법령명한글", ""),
                "promulgation_date": item.get("공포일자", ""),
                "effective_date": item.get("시행일자", ""),
                "article_no": "",   # 신구대조 연동 시 채움
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
                "law_name": "소득세법",
                "promulgation_date": since,
                "effective_date": since,
                "article_no": "제55조",
                "before_text": "종합소득에 대한 소득세는 … 세율을 적용한다.",
                "after_text": "종합소득에 대한 소득세는 … 개정된 세율을 적용한다.",
            }
        ]
