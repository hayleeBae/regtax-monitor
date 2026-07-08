"""도메인 레지스트리 — 도메인(세법/인사 등)별 수집 대상을 한 곳에서 관리한다.

repo 루트의 domains.json이 있으면 그것을 쓰고, 없으면 기존 동작
(TAX_LAWS + ADMIN_RULE_QUERIES 환경변수)과 동일한 tax 단일 도메인으로 폴백한다.

domains.json 형식:
{
  "tax": {
    "label": "세법(연말정산)",
    "laws": ["소득세법", ...],            # 정확 법령명 (시행령·시행규칙은 자동 확장)
    "admin_rule_queries": []              # 행정규칙(고시) 검색어 — 부분일치
  },
  ...
}

주의: laws는 법제처 등록명과 **정확히** 일치해야 한다. 특히 가운뎃점은
'ㆍ'(U+318D)를 쓴다 — '남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률'.
새 법령명은 lawSearch로 정확 명칭을 확인하고 넣을 것.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from config import settings

from app.collector.law_api import TAX_LAWS


@dataclass
class Domain:
    key: str
    label: str
    laws: list[str]
    admin_rule_queries: list[str]


def load_domains() -> dict[str, Domain]:
    path = Path(settings.domains_file)
    if not path.exists():
        queries = [q.strip() for q in settings.admin_rule_queries.split(",") if q.strip()]
        return {"tax": Domain("tax", "세법(연말정산)", list(TAX_LAWS), queries)}

    raw = json.loads(path.read_text(encoding="utf-8"))
    domains = {
        key: Domain(
            key=key,
            label=d.get("label", key),
            laws=list(d.get("laws", [])),
            admin_rule_queries=list(d.get("admin_rule_queries", [])),
        )
        for key, d in raw.items()
    }
    if not domains:
        raise ValueError(f"{path}에 도메인이 하나도 없습니다.")
    return domains
