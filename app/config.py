"""
Application Settings and Configuration

This module loads environment variables and provides centralized configuration.
All configuration is immutable after initialization.

Environment Variables:
  APP_NAME: Application display name (default: "Autonomous CI/CD Pipeline Optimizer")
  DATABASE_URL: SQLAlchemy database URL (default: "sqlite:///./optimizer.db")
  APP_API_KEY: Authentication token for API access (optional, default: "")
  GITHUB_WEBHOOK_SECRET: Secret for HMAC signature verification of GitHub webhooks (optional)
  RISK_BLOCK_THRESHOLD: Score >= this value blocks deployment (default: 85)
  RISK_DELAY_THRESHOLD: Score >= this value requests delayed deployment (default: 70)
  RISK_CANARY_THRESHOLD: Score >= this value recommends canary deployment (default: 50)
"""

import os


class Settings:
    """
    Configuration singleton for the application.
    All settings are loaded from environment variables at startup.
    """
    
    # Application metadata
    app_name: str = os.getenv("APP_NAME", "Autonomous CI/CD Pipeline Optimizer")
    
    # Database configuration
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./optimizer.db")
    
    # Authentication: API key for protecting endpoints (optional)
    # Format: Set APP_API_KEY env variable to enable API key validation
    # If not set, API key authentication is disabled
    app_api_key: str = os.getenv("APP_API_KEY", "")
    
    # Webhook Security: Secret for HMAC-SHA256 signature verification
    # Used to validate that webhook payloads originate from trusted source
    # If not set, signature validation is skipped (not recommended for production)
    github_webhook_secret: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")

    # Risk Scoring Thresholds: Control deployment recommendations
    # Higher scores indicate higher risk/uncertainty in CI/CD metrics
    risk_block_threshold: int = int(os.getenv("RISK_BLOCK_THRESHOLD", "85"))
    risk_delay_threshold: int = int(os.getenv("RISK_DELAY_THRESHOLD", "70"))
    risk_canary_threshold: int = int(os.getenv("RISK_CANARY_THRESHOLD", "50"))


# Global settings instance used throughout the application
settings = Settings()
