from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    law_api_oc: str = ""
    database_url: str = "sqlite:///./regtax.db"
    embedding_model: str = "BAAI/bge-m3"
    llm_model: str = "claude-sonnet-4-6"
    llm_model_cheap: str = "claude-haiku-4-5-20251001"
    repo_root: str = ""  # 비어 있으면 mock_repo 사용, 실제 경로 지정 시 RealCodebaseAdapter 사용
    repo_index_paths: str = ""  # 쉼표 구분 서브디렉토리 필터 (예: "src/tax,src/salary"). 비우면 전체
    hf_hub_disable_ssl: bool = False  # 회사 SSL 프록시 환경에서 True로 설정
    scheduler_enabled: bool = False   # True로 설정 시 주기적 법령 수집 자동 실행
    scheduler_interval_hours: int = 24  # 수집 주기 (시간)


settings = Settings()
