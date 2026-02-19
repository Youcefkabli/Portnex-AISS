from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── AISstream ─────────────────────────────────────────────
    AISSTREAM_API_KEY: str = ""
    AISSTREAM_WS_URL: str = "wss://stream.aisstream.io/v0/stream"

    # ── Fixed zone bounding box ───────────────────────────────
    ZONE_NAME: str = "Port of Halifax"
    ZONE_LAT_MIN: float =  44.647222
    ZONE_LAT_MAX: float =  44.670833
    ZONE_LON_MIN: float = -63.622500
    ZONE_LON_MAX: float = -63.585833

    # ── Database ──────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://ais_user:ais_password@localhost:5432/ais_db"

    # ── Redis (live stream fanout) ────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_LIVE_CHANNEL: str = "ais:live"
    REDIS_STATS_KEY: str = "ais:stats"

    # ── Storage windows ───────────────────────────────────────
    HIGH_FREQ_RETENTION_HOURS: int = 24

    # ── Ingestion tuning ──────────────────────────────────────
    BATCH_SIZE: int = 50
    BATCH_TIMEOUT_SEC: float = 1.0

    # ── API ───────────────────────────────────────────────────
    API_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    def bounding_box(self) -> list:
        """AISstream format: [[[lat_min, lon_min], [lat_max, lon_max]]]"""
        return [
            [
                [self.ZONE_LAT_MIN, self.ZONE_LON_MIN],
                [self.ZONE_LAT_MAX, self.ZONE_LON_MAX],
            ]
        ]


settings = Settings()
