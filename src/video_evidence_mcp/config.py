from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    auth_mode: Literal["none", "oidc"] = "none"
    host: str = "127.0.0.1"
    trusted_loopback_proxy: bool = False
    trusted_dns_proxy_cidr: str | None = None
    port: int = Field(8787, ge=1, le=65535)
    public_base_url: str | None = None
    data_dir: Path = Path("/data/video-evidence-mcp/app")
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "INFO"
    analysis_concurrency: int = Field(1, ge=1, le=8)
    max_video_seconds: int = Field(7200, ge=30)
    max_temp_bytes: int = Field(4 * 1024**3, ge=64 * 1024**2)
    max_cache_bytes: int = Field(10 * 1024**3, ge=128 * 1024**2)
    cache_ttl_days: int = Field(14, ge=1, le=365)
    job_timeout_seconds: int = Field(3600, ge=60)
    default_max_frames: int = Field(24, ge=4, le=48)
    deep_max_frames: int = Field(48, ge=8, le=96)
    window_max_seconds: int = Field(300, ge=10, le=900)
    asr_model: str = "small"
    asr_device: Literal["cpu", "cuda", "auto"] = "cpu"
    asr_compute_type: str = "int8"
    ocr_enabled: bool = True
    playwright_enabled: bool = True
    openai_vision_enabled: bool = False
    openai_api_key: SecretStr | None = None
    domain: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_required_scopes: str = "video:read"
    oidc_jwks_url: str | None = None
    rate_limit_per_minute: int = Field(30, ge=1, le=10000)
    max_http_concurrency: int = Field(8, ge=1, le=128)

    @model_validator(mode="after")
    def validate_network_auth(self) -> Settings:
        is_loopback = self.host in {"127.0.0.1", "::1", "localhost"}
        if self.auth_mode == "none" and not is_loopback and not self.trusted_loopback_proxy:
            raise ValueError(
                "AUTH_MODE=none requires loopback or TRUSTED_LOOPBACK_PROXY=true behind a loopback-only port"
            )
        if self.auth_mode == "oidc":
            missing = [
                name
                for name, value in {
                    "PUBLIC_BASE_URL": self.public_base_url,
                    "OIDC_ISSUER": self.oidc_issuer,
                    "OIDC_AUDIENCE": self.oidc_audience,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(f"OIDC mode requires: {', '.join(missing)}")
            assert self.public_base_url is not None
            if not self.public_base_url.startswith("https://"):
                raise ValueError("PUBLIC_BASE_URL must use HTTPS in OIDC mode")
        if self.openai_vision_enabled and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when OPENAI_VISION_ENABLED=true")
        if self.trusted_dns_proxy_cidr:
            try:
                trusted_network = ipaddress.ip_network(self.trusted_dns_proxy_cidr, strict=False)
            except ValueError as exc:
                raise ValueError("TRUSTED_DNS_PROXY_CIDR must be a valid CIDR") from exc
            benchmark_network = ipaddress.IPv4Network("198.18.0.0/15")
            if not isinstance(
                trusted_network, ipaddress.IPv4Network
            ) or not trusted_network.subnet_of(benchmark_network):
                raise ValueError("TRUSTED_DNS_PROXY_CIDR may only select a subnet of 198.18.0.0/15")
        return self

    @property
    def database_path(self) -> Path:
        return self.data_dir / "video-evidence.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
