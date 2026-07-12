"""스모크 테스트 — 설정·도메인 레지스트리의 현재 동작을 고정한다.

유지보수 프로젝트 원칙: 수정 전 현재 동작을 테스트로 고정 (CLAUDE.md).
무거운 의존성(chromadb, sentence-transformers 로드)은 여기서 건드리지 않는다.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_settings_load_with_defaults():
    from config import settings

    assert settings.llm_backend in ("local", "claude")
    assert settings.database_url.startswith("sqlite")
    # 업로드 폴더는 프로젝트 문서(docs/)와 분리되어야 한다
    assert settings.docs_dir == "data/uploads"
    # local 모드 기본값 — anthropic 패키지 없이도 동작해야 하는 전제
    assert settings.local_llm_base_url


def test_domains_registry_shape():
    domains = json.loads((ROOT / "domains.json").read_text(encoding="utf-8"))
    assert "tax" in domains and "hr" in domains
    for key, d in domains.items():
        assert d["label"], key
        assert isinstance(d["laws"], list) and d["laws"], key
        assert isinstance(d["admin_rule_queries"], list), key


def test_llm_client_factory_selects_local_without_anthropic():
    from app.llm import get_llm_client
    from app.llm.local_client import LocalClient

    client = get_llm_client()
    # 기본(local) 백엔드 — anthropic 미설치 환경에서도 임포트/생성 가능해야 한다
    assert isinstance(client, LocalClient)
