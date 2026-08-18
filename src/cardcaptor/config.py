"""Application configuration.

All settings come from environment variables and an optional ``.env`` file.
Credentials are never hard-coded and never written to logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # pragma: no cover - dotenv is a hard dependency but keep import defensive
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs) -> bool:  # type: ignore[misc]
        return False


ENV_PREFIX = "CARDCAPTOR_"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(ENV_PREFIX + name, os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    raw = _env(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _default_data_dir() -> Path:
    raw = _env("DATA_DIR", "")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".card-captor"


@dataclass
class AppConfig:
    """Runtime configuration for the application."""

    data_dir: Path = field(default_factory=_default_data_dir)

    # Canvas (optional, from environment / .env only)
    canvas_base_url: str = field(default_factory=lambda: _env("CANVAS_BASE_URL", ""))
    canvas_token: str = field(default_factory=lambda: _env("CANVAS_TOKEN", ""))

    # OCR settings
    tesseract_path: str = field(default_factory=lambda: _env("TESSERACT_PATH", "tesseract"))
    ocr_confidence_threshold: float = field(
        default_factory=lambda: _env_float("OCR_CONFIDENCE_THRESHOLD", 0.6)
    )

    # Review thresholds
    min_confidence_for_auto_review: float = field(
        default_factory=lambda: _env_float("MIN_CONFIDENCE_FOR_AUTO_REVIEW", 0.8)
    )
    min_match_confidence: float = field(
        default_factory=lambda: _env_float("MIN_MATCH_CONFIDENCE", 0.75)
    )

    # Web server defaults - localhost only by design.
    web_host: str = field(default_factory=lambda: _env("WEB_HOST", "127.0.0.1"))
    web_port: int = field(default_factory=lambda: int(_env("WEB_PORT", "8000") or 8000))

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser()

    # -- derived paths -------------------------------------------------
    @property
    def db_path(self) -> Path:
        raw = _env("DB_PATH", "")
        if raw:
            return Path(raw).expanduser()
        return self.data_dir / "cardcaptor.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def cards_dir(self) -> Path:
        return self.data_dir / "cards"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def all_dirs(self) -> list[Path]:
        return [
            self.data_dir,
            self.originals_dir,
            self.cards_dir,
            self.exports_dir,
            self.logs_dir,
            self.tmp_dir,
        ]

    def ensure_dirs(self) -> None:
        for directory in self.all_dirs():
            directory.mkdir(parents=True, exist_ok=True)

    def has_canvas_credentials(self) -> bool:
        return bool(self.canvas_base_url and self.canvas_token)

    def __repr__(self) -> str:  # never leak the token
        return (
            f"AppConfig(data_dir={str(self.data_dir)!r}, "
            f"canvas_base_url={self.canvas_base_url!r}, canvas_token=REDACTED)"
        )


_config: AppConfig | None = None


def get_config(refresh: bool = False) -> AppConfig:
    """Return the process-wide configuration, loading ``.env`` on first use."""
    global _config
    if _config is None or refresh:
        load_dotenv(override=False)
        _config = AppConfig()
    return _config


def set_config(config: AppConfig) -> None:
    """Override the process-wide configuration (used by tests and the CLI)."""
    global _config
    _config = config
