from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_backend: str = "local"  # "local"(로컬 추론, 기본) | "claude"(Anthropic API 하이브리드)

    # 로컬 추론 서버 (OpenAI 호환: Ollama / vLLM / llama.cpp server / LM Studio)
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen3:8b"
    local_llm_model_cheap: str = ""  # 분석용 경량 모델. 비우면 local_llm_model 사용
    local_llm_timeout_seconds: int = 600  # CPU 추론은 느릴 수 있어 넉넉히
    # qwen3 등의 thinking 모드 — CPU에서 매우 느리고, 생각이 max_tokens을 소진하면
    # 최종 답변이 빈 응답이 된다. 기본 비활성(/no_think 소프트 스위치 주입)
    local_llm_think: bool = False

    # Anthropic API (LLM_BACKEND=claude 일 때만 사용)
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"
    llm_model_cheap: str = "claude-haiku-4-5-20251001"

    docs_dir: str = "docs"  # 참고 문서(개정세법 해설 PDF 등) 업로드 폴더

    # 골든 테스트 — 스크래치 repo 루트에서 실행할 명령 (exit 0=통과). 비우면 검증 생략
    golden_test_cmd: str = ""
    golden_test_timeout_seconds: int = 300

    law_api_oc: str = ""
    collect_decrees: bool = True  # 시행령·시행규칙 수집 포함 (위임 수치·간이세액표 개정 감지)
    # 도메인 레지스트리 파일 — 도메인별 수집 법령·행정규칙 검색어 (app/collector/registry.py)
    domains_file: str = "domains.json"
    # 행정규칙(고시·훈령 등) 수집 검색어 — 쉼표 구분. domains.json이 없을 때의 폴백.
    # 주의: OC 키에 행정규칙 목록/본문 API를 별도 신청해야 한다 (open.law.go.kr).
    admin_rule_queries: str = ""
    database_url: str = "sqlite:///./regtax.db"
    embedding_model: str = "BAAI/bge-m3"
    repo_root: str = ""  # 비어 있으면 mock_repo 사용, 실제 경로 지정 시 RealCodebaseAdapter 사용
    repo_index_paths: str = ""  # 쉼표 구분 서브디렉토리 필터 (예: "src/tax,src/salary"). 비우면 전체
    hf_hub_disable_ssl: bool = False  # 회사 SSL 프록시 환경에서 True로 설정
    scheduler_enabled: bool = False   # True로 설정 시 주기적 법령 수집 자동 실행
    scheduler_interval_hours: int = 24  # 수집 주기 (시간)


settings = Settings()
