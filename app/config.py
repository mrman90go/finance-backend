from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    gc_secret_id: str = ""
    gc_secret_key: str = ""
    database_url: str = "sqlite:///./finance.db"
    app_base_url: str = "http://localhost:8000"
    sync_interval_hours: int = 24
    cors_origins: str = "http://localhost:8000"
    api_key: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
