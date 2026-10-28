from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    neo4j_uri: str = "neo4j://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str
    repo_storage_path: str = "./repos"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    llm_max_concurrency: int = 4
    llm_max_retries: int = 3
    llm_retry_base_seconds: float = 0.5
    llm_timeout_seconds: float = 180.0
    enrich_max_calls_per_run: int = 100

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
