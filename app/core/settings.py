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

    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = ""
    RABBITMQ_VHOST: str = "/"

    PAYMENTS_NEW_QUEUE: str = "payments.new"
    PAYMENTS_EXCHANGE: str = "payments"
    PAYMENTS_DLX: str = "payments.dlx"
    PAYMENTS_RETRY_QUEUE: str = "payments.new.retry"
    PAYMENTS_DLQ: str = "payments.new.dlq"
    PAYMENTS_RETRY_BASE_DELAY_MS: int = 5_000
    MAX_CONSUMER_ATTEMPTS: int = 3

    OUTBOX_BASE_RETRY_DELAY_SECONDS: int = 3
    OUTBOX_MAX_RETRY_DELAY_SECONDS: int = 60
    OUTBOX_MAX_ATTEMPTS: int = 10
    OUTBOX_PUBLISH_TIMEOUT_SECONDS: float = 5.0

    WEBHOOK_TIMEOUT_SECONDS: int = 10
    WEBHOOK_MAX_ATTEMPTS: int = 3
    WEBHOOK_BASE_DELAY_SECONDS: int = 2

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def rabbit_url(self) -> str:
        vhost = self.RABBITMQ_VHOST
        if vhost != "/" and not vhost.startswith("/"):
            vhost = f"/{vhost}"
        return (
            f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}" f"@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}{vhost}"
        )


settings = Settings()
