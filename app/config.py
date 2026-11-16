from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    classifier_model: str = "openrouter:openrouter/free"
    agent_model: str = "openrouter:openrouter/free"

    database_url: str = "postgresql://support_user:support_pass@localhost:5433/support_db"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    support_email: str = ""

    poll_interval_seconds: int = 30
    dashboard_password: str = "admin123"


settings = Settings()
