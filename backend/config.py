"""
FRIS centralized settings.
All FRIS_* env vars in one place, replacing the scattered os.getenv calls
that used to live at the top of main.py. Kept as simple attributes (not a
BaseSettings subclass with strict validation) so the existing all-inline
dev-default convention keeps working unchanged.
"""

import os

_DEV_SECRET_KEY = "dev-only-insecure-secret-change-me"
_DEV_ENCRYPTION_KEY = "kX9v2mZ1nY8pQ4rT6wU3sD5fG7hJ0kL2aB4cE6gI8oM="


class Settings:
    # Deployment environment. "production" turns on hard fail-fast checks
    # below — everywhere else (development/staging/local) keeps the
    # permissive dev-default behavior this codebase has always had.
    ENV = os.getenv("FRIS_ENV", "development")

    # Neo4j
    NEO4J_URI = os.getenv("FRIS_NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("FRIS_NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("FRIS_NEO4J_PASSWORD", "fris_dev_password")

    # Postgres control DB
    CONTROL_DB_DSN = os.getenv(
        "FRIS_CONTROL_DB_DSN",
        "postgresql://fris:fris_dev_password@localhost:5432/fris_control"
    )

    # Embeddings
    EMBEDDING_MODEL_NAME = os.getenv("FRIS_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    # Startup retry
    STARTUP_RETRY_ATTEMPTS = 30
    STARTUP_RETRY_DELAY_SECONDS = 4

    # Auth / secrets — dev defaults only. MUST be overridden in any real deployment.
    SECRET_KEY = os.getenv("FRIS_SECRET_KEY", "dev-only-insecure-secret-change-me")
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRES_MINUTES = int(os.getenv("FRIS_JWT_EXPIRES_MINUTES", "1440"))

    # Fernet key for encrypting stored BYO LLM credentials at rest.
    # Dev default is a valid (but well-known, insecure) Fernet key.
    ENCRYPTION_KEY = os.getenv("FRIS_ENCRYPTION_KEY", "kX9v2mZ1nY8pQ4rT6wU3sD5fG7hJ0kL2aB4cE6gI8oM=")

    # OAuth (Google / Azure — the only providers that need OAuth, not API keys)
    GOOGLE_OAUTH_CLIENT_ID = os.getenv("FRIS_GOOGLE_OAUTH_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("FRIS_GOOGLE_OAUTH_CLIENT_SECRET", "")
    AZURE_OAUTH_CLIENT_ID = os.getenv("FRIS_AZURE_OAUTH_CLIENT_ID", "")
    AZURE_OAUTH_CLIENT_SECRET = os.getenv("FRIS_AZURE_OAUTH_CLIENT_SECRET", "")
    OAUTH_REDIRECT_BASE = os.getenv("FRIS_OAUTH_REDIRECT_BASE", "http://localhost:8000")


def _fail_fast_on_dev_secrets(s: "Settings"):
    """
    Refuse to boot in production with the documented dev-default secrets.
    These defaults are public (they're printed in README.md and
    docker-compose.yml), so running them in production means anyone can
    forge JWTs or decrypt stored BYO-LLM credentials.
    """
    if s.ENV != "production":
        return
    insecure = []
    if s.SECRET_KEY == _DEV_SECRET_KEY:
        insecure.append("FRIS_SECRET_KEY")
    if s.ENCRYPTION_KEY == _DEV_ENCRYPTION_KEY:
        insecure.append("FRIS_ENCRYPTION_KEY")
    if insecure:
        raise RuntimeError(
            f"FRIS_ENV=production but {', '.join(insecure)} still "
            f"{'equals' if len(insecure) == 1 else 'equal'} the documented "
            f"insecure dev default. Set real secrets before starting in "
            f"production."
        )


settings = Settings()
_fail_fast_on_dev_secrets(settings)
