from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_backend: str = "local"  # "local"(로컬 추론, 기본) | "claude"(Anthropic API 하이브리드)

    # 로컬 추론 서버 (OpenAI 호환: Ollama / vLLM / llama.cpp server / LM Studio)
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen3:8b"
    local_llm_model_cheap: str = ""  # 분석용 경량 모델. 비우면 local_llm_model 사용
    local_llm_timeout_seconds: int = 600  # CPU 추론은 느릴 수 있어 넉넉히

    # Anthropic API (LLM_BACKEND=claude 일 때만 사용)
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"
    llm_model_cheap: str = "claude-haiku-4-5-20251001"

    law_api_oc: str = ""
    database_url: str = "sqlite:///./regtax.db"
    embedding_model: str = "BAAI/bge-m3"
    repo_root: str = ""  # 비어 있으면 mock_repo 사용, 실제 경로 지정 시 RealCodebaseAdapter 사용
    repo_index_paths: str = ""  # 쉼표 구분 서브디렉토리 필터 (예: "src/tax,src/salary"). 비우면 전체
    hf_hub_disable_ssl: bool = False  # 회사 SSL 프록시 환경에서 True로 설정
    scheduler_enabled: bool = False   # True로 설정 시 주기적 법령 수집 자동 실행
    scheduler_interval_hours: int = 24  # 수집 주기 (시간)


settings = Settings()
