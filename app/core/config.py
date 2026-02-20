from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── AISstream ─────────────────────────────────────────────
    AISSTREAM_API_KEY: str = ""
    AISSTREAM_WS_URL: str = "wss://stream.aisstream.io/v0/stream"

    # ── Fixed zone bounding box (Port of Halifax) ──
    ZONE_NAME: str = "Port of Halifax"
    ZONE_LAT_MIN: float = 43.0
    ZONE_LAT_MAX: float = 47.0
    ZONE_LON_MIN: float = -65.0
    ZONE_LON_MAX: float = -61.0

    # ── API ───────────────────────────────────────────────────
    API_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    def bounding_box(self) -> list:
        """AISstream format: [[[lat_min, lon_west], [lat_max, lon_east]]] (normalized so west < east)."""
        lon_west = min(self.ZONE_LON_MIN, self.ZONE_LON_MAX)
        lon_east = max(self.ZONE_LON_MIN, self.ZONE_LON_MAX)
        return [
            [
                [self.ZONE_LAT_MIN, lon_west],
                [self.ZONE_LAT_MAX, lon_east],
            ]
        ]


settings = Settings()
