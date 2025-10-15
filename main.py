import asyncio
import os
import json
import configparser
from pathlib import Path

import uvicorn

from async_mail_service.core import AsyncMailCore
from async_mail_service.api import create_app


def load_settings() -> dict[str, object]:
    """
    Load configuration from an INI file (default: config.ini) with environment variables as fallbacks.
    Supported sections/keys:
      [storage] db_path
      [server] host, port, api_token
      [client] client_sync_url, client_sync_user, client_sync_password, client_sync_token
      [delivery] send_interval_seconds, default_priority
    """
    config_path = Path(os.getenv("ASYNC_MAIL_CONFIG", "config.ini"))
    parser = configparser.ConfigParser()
    parser.read(config_path)

    def get(section: str, option: str, fallback: str | None = None) -> str | None:
        if parser.has_option(section, option):
            return parser.get(section, option)
        return fallback

    def get_int(section: str, option: str, fallback: str | None = None, default: int | None = None) -> int | None:
        value = get(section, option, fallback)
        if value is None:
            return default
        return int(value)

    def get_bool(section: str, option: str, fallback: str | None = None, default: bool | None = None) -> bool | None:
        value = get(section, option, fallback)
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    def get_float(section: str, option: str, fallback: str | None = None, default: float | None = None) -> float | None:
        value = get(section, option, fallback)
        if value is None:
            return default
        return float(value)

    settings = {
        "db_path": get("storage", "db_path", os.getenv("DB_PATH", "/data/mail_service.db")),
        "http_host": get("server", "host", os.getenv("HOST", "0.0.0.0")),
        "http_port": get_int("server", "port", os.getenv("PORT", "8000")),
        "scheduler_active": get_bool("scheduler", "active", os.getenv("SCHEDULER_ACTIVE"), False),
        "api_token": get("server", "api_token", os.getenv("API_TOKEN")),
        "timezone": get("scheduler", "timezone", os.getenv("TIMEZONE", "Europe/Rome")),
        "client_sync_url": get("client", "client_sync_url", os.getenv("CLIENT_SYNC_URL")),
        "client_sync_user": get("client", "client_sync_user", os.getenv("CLIENT_SYNC_USER")),
        "client_sync_password": get("client", "client_sync_password", os.getenv("CLIENT_SYNC_PASSWORD")),
        "client_sync_token": get("client", "client_sync_token", os.getenv("CLIENT_SYNC_TOKEN")),
        "send_loop_interval": get_float("delivery", "send_interval_seconds", os.getenv("SEND_LOOP_INTERVAL")),
        "default_priority": get_int("delivery", "default_priority", os.getenv("DEFAULT_PRIORITY"), default=1),
    }

    rules_raw = get("scheduler", "rules", os.getenv("SCHEDULER_RULES"))
    scheduler_rules = []
    if rules_raw:
        try:
            scheduler_rules = json.loads(rules_raw)
        except json.JSONDecodeError:
            scheduler_rules = []
    settings["scheduler_rules"] = scheduler_rules

    db_path = settings["db_path"]
    if isinstance(db_path, str):
        settings["db_path"] = os.path.expanduser(db_path)
    token = settings.get("api_token")
    if isinstance(token, str):
        token = token.strip() or None
    settings["api_token"] = token
    return settings


async def run_service(settings: dict[str, object]):
    service_kwargs = dict(
        db_path=settings["db_path"],
        start_active=bool(settings.get("scheduler_active")),
        timezone=str(settings.get("timezone") or "Europe/Rome"),
        client_sync_url=settings.get("client_sync_url"),
        client_sync_user=settings.get("client_sync_user"),
        client_sync_password=settings.get("client_sync_password"),
        client_sync_token=settings.get("client_sync_token"),
        default_priority=settings.get("default_priority"),
    )
    send_loop_interval = settings.get("send_loop_interval")
    if send_loop_interval is not None:
        service_kwargs["send_loop_interval"] = float(send_loop_interval)

    service = AsyncMailCore(**service_kwargs)
    await service.start()
    rules = settings.get("scheduler_rules") or []
    if rules:
        await service.handle_command("schedule", {"rules": rules, "active": settings.get("scheduler_active", False)})
    elif not settings.get("scheduler_active", False):
        service._active = False
    return service


if __name__ == "__main__":
    settings = load_settings()
    loop = asyncio.get_event_loop()
    service = loop.run_until_complete(run_service(settings))
    app = create_app(service, api_token=settings.get("api_token"))
    uvicorn.run(app, host=str(settings["http_host"]), port=int(settings["http_port"]))
