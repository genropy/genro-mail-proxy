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
      [smtp] host, port, user, password
      [fetch] url
      [storage] db_path
      [server] host, port
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

    settings = {
        "smtp_host": get("smtp", "host", os.getenv("SMTP_HOST", "smtp.gmail.com")),
        "smtp_port": get_int("smtp", "port", os.getenv("SMTP_PORT", "587")),
        "smtp_user": get("smtp", "user", os.getenv("SMTP_USER")),
        "smtp_password": get("smtp", "password", os.getenv("SMTP_PASSWORD")),
        "smtp_use_tls": get_bool("smtp", "use_tls", os.getenv("SMTP_USE_TLS"), None),
        "fetch_url": get("fetch", "url", os.getenv("FETCH_URL", "http://localhost:8080/mail-service-endpoint")),
        "db_path": get("storage", "db_path", os.getenv("DB_PATH", "/data/mail_service.db")),
        "http_host": get("server", "host", os.getenv("HOST", "0.0.0.0")),
        "http_port": get_int("server", "port", os.getenv("PORT", "8000")),
        "scheduler_active": get_bool("scheduler", "active", os.getenv("SCHEDULER_ACTIVE"), False),
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
    return settings


async def run_service(settings: dict[str, object]):
    service = AsyncMailCore(
        host=str(settings["smtp_host"]),
        port=int(settings["smtp_port"]),
        user=settings["smtp_user"],
        password=settings["smtp_password"],
        use_tls=settings["smtp_use_tls"],
        fetch_url=settings["fetch_url"],
        db_path=settings["db_path"],
        start_active=bool(settings.get("scheduler_active")),
    )
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
    app = create_app(service)
    uvicorn.run(app, host=str(settings["http_host"]), port=int(settings["http_port"]))
