from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    law_api_oc: str = ""
    database_url: str = "sqlite:///./regtax.db"
    embedding_model: str = "BAAI/bge-m3"
    llm_model: str = "claude-sonnet-4-6"
    llm_model_cheap: str = "claude-haiku-4-5-20251001"


settings = Settings()
