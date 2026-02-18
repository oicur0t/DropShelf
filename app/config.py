"""OPDS Server Configuration

Environment variable-based configuration with sensible defaults.
"""
import os
from pathlib import Path


class Config:
    """Server configuration from environment variables."""

    # Directory settings
    BOOKS_DIR: Path = Path(os.getenv("BOOKS_DIR", "/books"))

    # Cache settings
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "300"))  # 5 minutes default
    CACHE_MAX_SIZE: int = int(os.getenv("CACHE_MAX_SIZE", "1000"))  # Max books in cache

    # Pagination
    MAX_RESULTS: int = int(os.getenv("MAX_RESULTS", "50"))

    # Server settings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # OPDS feed metadata
    FEED_TITLE: str = os.getenv("FEED_TITLE", "My Book Library")
    FEED_AUTHOR: str = os.getenv("FEED_AUTHOR", "DropShelf OPDS Server")

    # Authentication
    AUTH_ENABLED: bool = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    HTPASSWD_FILE: str = os.getenv("HTPASSWD_FILE", "")
    AUTH_USERNAME: str = os.getenv("AUTH_USERNAME", "")
    AUTH_PASSWORD: str = os.getenv("AUTH_PASSWORD", "")

    # Security
    ALLOWED_EXTENSIONS: set[str] = {".epub", ".pdf", ".mobi"}

    @classmethod
    def validate(cls) -> None:
        """Validate configuration and raise if invalid."""
        if not cls.BOOKS_DIR.exists():
            raise ValueError(f"BOOKS_DIR does not exist: {cls.BOOKS_DIR}")

        if cls.CACHE_TTL < 0:
            raise ValueError("CACHE_TTL must be non-negative")

        if cls.MAX_RESULTS < 1:
            raise ValueError("MAX_RESULTS must be at least 1")


# Singleton instance
config = Config()
