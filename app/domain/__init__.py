"""V2 domain 계층 — 순수 Python 계약.

이 패키지와 하위 모듈은 FastAPI, SQLAlchemy, 외부 LLM SDK(anthropic 등)를
import 하지 않는다. `app.main`, DB session, LLM client 도 역참조하지 않는다.
(docs/architecture/ARCHITECTURE_V2.md §3, IMPLEMENTATION_ROADMAP #0003 제약)
"""
