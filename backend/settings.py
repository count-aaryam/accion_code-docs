from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    neo4j_uri: str = "neo4j://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str
    repo_storage_path: str = "./repos"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    llm_max_concurrency: int = 4
    llm_max_retries: int = 3
    llm_retry_base_seconds: float = 0.5
    llm_timeout_seconds: float = 180.0
    enrich_max_calls_per_run: int = 100
    estimation_sample_size: int = 30
    estimation_max_output_tokens: int = 512
    estimation_default_output_tokens: dict = {"Function": 100, "Method": 100, "Class": 140, "File": 180, "Project": 220}
    wiki_planner_config: str = "backend/wiki/wiki.yaml"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
