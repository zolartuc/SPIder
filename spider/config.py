from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPIDER_", env_file=".env", extra="ignore")

    # Watchtower
    subnet: str = ""
    scan_interval: int = 300
    scan_method: str = "auto"  # auto | arp | ping
    offline_after_minutes: int = 15

    # Alerts
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""

    # Pi-hole
    pihole_url: str = ""
    pihole_password: str = ""

    # Let the Settings page change the Pi's hostname, timezone and network.
    # Off by default: needs SPIder running directly on the Pi as root (not in Docker).
    allow_system_changes: bool = False

    # Web
    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: Path = Path("data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "spider.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
