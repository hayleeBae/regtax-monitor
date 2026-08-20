"""도메인 레지스트리 — 도메인(세법/인사 등)별 수집 대상을 한 곳에서 관리한다.

repo 루트의 domains.json이 있으면 그것을 쓰고, 없으면 기존 동작
(TAX_LAWS + ADMIN_RULE_QUERIES 환경변수)과 동일한 tax 단일 도메인으로 폴백한다.

domains.json 형식:
{
  "tax": {
    "label": "세법(연말정산)",
    "laws": ["소득세법", ...],            # 정확 법령명 (시행령·시행규칙은 자동 확장)
    "admin_rule_queries": [],             # 행정규칙(고시) 검색어 — 부분일치
    "db_items": []                        # DB 데이터 개정 라우팅 항목 (선택, 이슈 #0025)
  },
  ...
}

주의: laws는 법제처 등록명과 **정확히** 일치해야 한다. 특히 가운뎃점은
'ㆍ'(U+318D)를 쓴다 — '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률'.
새 법령명은 lawSearch로 정확 명칭을 확인하고 넣을 것.

db_items: DB 데이터 개정 판정을 위한 큐레이션 레지스트리(DB_DATA_ROUTING_SPEC §4).
실제 DB 테이블/컬럼명 원문은 넣지 않는다 — item_label/db_hint는 일반화된 서술만.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from config import settings

from app.collector.law_api import TAX_LAWS


@dataclass(frozen=True)
class DbItem:
    law_id: str
    article_pattern: str
    item_label: str
    db_hint: str = ""
    guidance: str = ""


@dataclass
class Domain:
    key: str
    label: str
    laws: list[str]
    admin_rule_queries: list[str]
    db_items: list[DbItem]


def _parse_db_items(raw_items: list) -> list[DbItem]:
    return [
        DbItem(
            law_id=item["law_id"],
            article_pattern=item["article_pattern"],
            item_label=item["item_label"],
            db_hint=item.get("db_hint", ""),
            guidance=item.get("guidance", ""),
        )
        for item in raw_items
    ]


def load_domains() -> dict[str, Domain]:
    path = Path(settings.domains_file)
    if not path.exists():
        queries = [q.strip() for q in settings.admin_rule_queries.split(",") if q.strip()]
        return {"tax": Domain("tax", "세법(연말정산)", list(TAX_LAWS), queries, [])}

    raw = json.loads(path.read_text(encoding="utf-8"))
    domains = {
        key: Domain(
            key=key,
            label=d.get("label", key),
            laws=list(d.get("laws", [])),
            admin_rule_queries=list(d.get("admin_rule_queries", [])),
            db_items=_parse_db_items(d.get("db_items", [])),
        )
        for key, d in raw.items()
    }
    if not domains:
        raise ValueError(f"{path}에 도메인이 하나도 없습니다.")
    return domains


class DbDataRegistry:
    """DB 데이터 개정 판정 레지스트리 — 정확 매칭만 수행한다(추론 금지, ADR-016)."""

    def __init__(self, domains: dict[str, Domain]):
        self._items: list[DbItem] = [
            item for domain in domains.values() for item in domain.db_items
        ]

    def match(self, law_id: str, article_no: str) -> DbItem | None:
        """law_id 정확 일치 AND article_pattern이 article_no에 부분일치하는
        첫 DbItem을 반환한다. 없으면 None. 추론하지 않는다."""
        if not article_no:
            return None
        for item in self._items:
            if item.law_id != law_id:
                continue
            if not item.article_pattern:
                continue
            if item.article_pattern in article_no:
                return item
        return None
