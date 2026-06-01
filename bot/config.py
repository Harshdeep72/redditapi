"""
Bot configuration — reads from .env using pydantic-settings.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Discord
    discord_bot_token: str = Field(..., description="Discord bot token")
    discord_guild_id: int | None = Field(
        default=None, description="Guild ID for instant slash commands (None = global)"
    )

    # Reddit API (fallback)
    reddit_client_id: str = Field(default="", description="Reddit app client ID")
    reddit_client_secret: str = Field(default="", description="Reddit app client secret")
    reddit_user_agent: str = Field(
        default="RedditIntelBot/1.0", description="Reddit API user agent"
    )

    # OpenAI
    openai_api_key: str = Field(default="", description="OpenAI API key")
    openai_model: str = Field(default="gpt-4o-mini", description="OpenAI model to use")

    # Fetcher
    max_fetch_items: int = Field(default=500, description="Max items per analysis")
    request_delay_min: float = Field(default=1.0, description="Min delay between requests (s)")
    request_delay_max: float = Field(default=3.0, description="Max delay between requests (s)")
    cookie_refresh_interval: int = Field(default=600, description="How often to re-acquire Reddit session cookies (seconds)")
    reddit_session: str | None = Field(default=None, description="Reddit account session cookie (reddit_session value)")

    # Cache
    cache_backend: str = Field(default="memory", description="Cache backend: memory or sqlite")
    sqlite_path: str = Field(default="data/cache.db", description="SQLite DB path")
    cache_ttl: int = Field(default=600, description="Cache TTL in seconds")
    database_url: str | None = Field(default=None, description="PostgreSQL Connection URL")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_reddit_api(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    @property
    def guild_ids(self) -> list[int] | None:
        return [self.discord_guild_id] if self.discord_guild_id else None


settings = Settings()
