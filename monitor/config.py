"""Загрузка и проверка config.yaml + секретов из .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    name: str
    start_url: str
    item: str
    title: str
    price: str
    link: str
    currency: str = "RUB"
    next_page: str | None = None
    max_pages: int = 1


@dataclass(frozen=True)
class Config:
    sources: list[Source]
    alert_drop_percent: float = 5.0
    suspicious_drop_percent: float = 70.0
    request_delay_sec: float = 1.0
    timeout_sec: float = 20.0
    user_agent: str = "PriceMonitorBot/1.0"
    # из .env
    db_path: str = "output/prices.db"
    output_dir: str = "output"
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    google_credentials_file: str | None = None
    spreadsheet_id: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.google_credentials_file and self.spreadsheet_id)


REQUIRED = ("name", "start_url", "item", "title", "price", "link")


def load(path: str | Path = "config.yaml") -> Config:
    load_dotenv()
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(f"Не найден {path}. Скопируйте пример из репозитория.") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: ошибка YAML — {e}") from None

    sources = []
    for i, s in enumerate(raw.get("sources") or [], start=1):
        missing = [k for k in REQUIRED if not s.get(k)]
        if missing:
            raise ConfigError(f"Источник #{i} ({s.get('name', 'без имени')}): не заполнено {', '.join(missing)}")
        sources.append(Source(**{k: v for k, v in s.items() if k in Source.__dataclass_fields__}))
    if not sources:
        raise ConfigError(f"{path}: список sources пуст — нечего парсить")

    names = [s.name for s in sources]
    if len(names) != len(set(names)):
        raise ConfigError(f"{path}: имена источников должны быть уникальными")

    return Config(
        sources=sources,
        alert_drop_percent=float(raw.get("alert_drop_percent", 5)),
        suspicious_drop_percent=float(raw.get("suspicious_drop_percent", 70)),
        request_delay_sec=float(raw.get("request_delay_sec", 1.0)),
        timeout_sec=float(raw.get("timeout_sec", 20)),
        user_agent=str(raw.get("user_agent", "PriceMonitorBot/1.0")),
        db_path=os.getenv("DB_PATH", "output/prices.db"),
        output_dir=os.getenv("OUTPUT_DIR", "output"),
        telegram_token=os.getenv("TELEGRAM_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        google_credentials_file=os.getenv("GOOGLE_CREDENTIALS_FILE") or None,
        spreadsheet_id=os.getenv("SPREADSHEET_ID") or None,
    )
