from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    APP_NAME: str = "payment-processor"
    APP_ENV: str = "local"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    API_KEY: str = "super-secret-api-key"
    API_KEY_NAME: str = "X-API-Key"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "payments"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "payments"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
