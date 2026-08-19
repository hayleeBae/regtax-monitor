"""Issue #0025 Step 0 — DbDataRegistry / domains.json db_items 스키마 단위 테스트.

DB_DATA_ROUTING_SPEC §3, §4, §10(1),(2) 대응. 무거운 의존성 없음.
"""

from app.collector.registry import DbDataRegistry, DbItem, Domain, load_domains


def _domain(db_items):
    return Domain(
        key="tax",
        label="세법",
        laws=["소득세법"],
        admin_rule_queries=[],
        db_items=db_items,
    )


# --- DbDataRegistry.match ---------------------------------------------------


def test_match_returns_item_on_law_id_and_article_pattern_match():
    item = DbItem(
        law_id="001766",
        article_pattern="제129조",
        item_label="근로소득 간이세액표",
    )
    registry = DbDataRegistry({"tax": _domain([item])})

    assert registry.match("001766", "제129조제1항") is item


def test_match_returns_none_on_law_id_mismatch():
    item = DbItem(law_id="001766", article_pattern="제129조", item_label="x")
    registry = DbDataRegistry({"tax": _domain([item])})

    assert registry.match("999999", "제129조제1항") is None


def test_match_returns_none_on_article_pattern_mismatch():
    item = DbItem(law_id="001766", article_pattern="제129조", item_label="x")
    registry = DbDataRegistry({"tax": _domain([item])})

    assert registry.match("001766", "제55조") is None


def test_match_does_not_wildcard_on_empty_article_pattern():
    # article_pattern이 빈 문자열이면 전체 매칭시키지 않는다 (과잉 라우팅 방지).
    item = DbItem(law_id="001766", article_pattern="", item_label="x")
    registry = DbDataRegistry({"tax": _domain([item])})

    assert registry.match("001766", "제1조") is None
    assert registry.match("001766", "") is None


def test_match_returns_none_when_registry_empty():
    registry = DbDataRegistry({"tax": _domain([])})
    assert registry.match("001766", "제129조") is None


# --- domains.json 로딩 회귀 --------------------------------------------------


def test_load_domains_from_real_file_has_db_items_key():
    domains = load_domains()
    for domain in domains.values():
        assert domain.db_items == []


def test_domain_db_items_defaults_to_empty_list_when_key_absent(tmp_path, monkeypatch):
    domains_file = tmp_path / "domains.json"
    domains_file.write_text(
        '{"tax": {"label": "세법", "laws": ["소득세법"], "admin_rule_queries": []}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.collector.registry.settings.domains_file", str(domains_file))

    domains = load_domains()

    assert domains["tax"].db_items == []


def test_domain_db_items_parsed_when_present(tmp_path, monkeypatch):
    domains_file = tmp_path / "domains.json"
    domains_file.write_text(
        """
        {"tax": {"label": "세법", "laws": ["소득세법"], "admin_rule_queries": [],
          "db_items": [{"law_id": "001766", "article_pattern": "제129조",
                         "item_label": "근로소득 간이세액표", "db_hint": "급여 세액 산정표",
                         "guidance": "DB에서 갱신하세요."}]}}
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr("app.collector.registry.settings.domains_file", str(domains_file))

    domains = load_domains()

    items = domains["tax"].db_items
    assert len(items) == 1
    assert items[0] == DbItem(
        law_id="001766",
        article_pattern="제129조",
        item_label="근로소득 간이세액표",
        db_hint="급여 세액 산정표",
        guidance="DB에서 갱신하세요.",
    )
