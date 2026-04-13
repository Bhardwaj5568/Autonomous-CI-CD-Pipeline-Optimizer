import os


class Settings:
    app_name: str = os.getenv("APP_NAME", "Autonomous CI/CD Pipeline Optimizer")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./optimizer.db")
    app_api_key: str = os.getenv("APP_API_KEY", "")
    github_webhook_secret: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")

    risk_block_threshold: int = int(os.getenv("RISK_BLOCK_THRESHOLD", "85"))
    risk_delay_threshold: int = int(os.getenv("RISK_DELAY_THRESHOLD", "70"))
    risk_canary_threshold: int = int(os.getenv("RISK_CANARY_THRESHOLD", "50"))


settings = Settings()
