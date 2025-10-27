import asyncio
import os
import json
import logging
import configparser
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from async_mail_service.core import AsyncMailCore
from async_mail_service.api import create_app

# Configure logging level from environment
log_level = os.getenv("GMP_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True  # Force reconfiguration to avoid duplicate handlers
)


def load_settings() -> dict[str, object]:
    """
    Load configuration from an INI file (default: config.ini) with environment variables as fallbacks.

    Environment variables (all prefixed with GMP_):
      GMP_CONFIG - Path to config.ini file (default: config.ini)
      GMP_LOG_LEVEL - Logging level (default: INFO)
      GMP_DB_PATH - Database path (default: /data/mail_service.db)
      GMP_HOST - Server host (default: 0.0.0.0)
      GMP_PORT - Server port (default: 8000)
      GMP_SCHEDULER_ACTIVE - Enable scheduler (default: False)
      GMP_API_TOKEN - API authentication token
      GMP_CLIENT_SYNC_URL - Client sync URL
      GMP_CLIENT_SYNC_USER - Client sync username
      GMP_CLIENT_SYNC_PASSWORD - Client sync password
      GMP_CLIENT_SYNC_TOKEN - Client sync token
      GMP_SEND_LOOP_INTERVAL - Send loop interval in seconds
      GMP_TEST_MODE - Enable test mode (default: False)
      GMP_DEFAULT_PRIORITY - Default message priority (default: 2)
      GMP_DELIVERY_REPORT_RETENTION_SECONDS - Retention time for delivery reports (default: 7 days)
      GMP_BATCH_SIZE_PER_ACCOUNT - Batch size per account (default: 50)
      GMP_LOG_DELIVERY_ACTIVITY - Log delivery activity (default: False)

    Config file sections/keys:
      [storage] db_path
      [server] host, port, api_token
      [client] client_sync_url, client_sync_user, client_sync_password, client_sync_token
      [delivery] send_interval_seconds, test_mode, default_priority, delivery_report_retention_seconds
      [logging] delivery_activity
    """
    config_path = Path(os.getenv("GMP_CONFIG", "config.ini"))
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
        "db_path": get("storage", "db_path", os.getenv("GMP_DB_PATH", "/data/mail_service.db")),
        "http_host": get("server", "host", os.getenv("GMP_HOST", "0.0.0.0")),
        "http_port": get_int("server", "port", os.getenv("GMP_PORT", "8000")),
        "scheduler_active": get_bool("scheduler", "active", os.getenv("GMP_SCHEDULER_ACTIVE"), False),
        "api_token": get("server", "api_token", os.getenv("GMP_API_TOKEN")),
        "client_sync_url": get("client", "client_sync_url", os.getenv("GMP_CLIENT_SYNC_URL")),
        "client_sync_user": get("client", "client_sync_user", os.getenv("GMP_CLIENT_SYNC_USER")),
        "client_sync_password": get("client", "client_sync_password", os.getenv("GMP_CLIENT_SYNC_PASSWORD")),
        "client_sync_token": get("client", "client_sync_token", os.getenv("GMP_CLIENT_SYNC_TOKEN")),
        "send_loop_interval": get_float("delivery", "send_interval_seconds", os.getenv("GMP_SEND_LOOP_INTERVAL")),
        "test_mode": get_bool("delivery", "test_mode", os.getenv("GMP_TEST_MODE"), False),
        "default_priority": get_int("delivery", "default_priority", os.getenv("GMP_DEFAULT_PRIORITY"), default=2),
        "report_retention_seconds": get_int(
            "delivery",
            "delivery_report_retention_seconds",
            os.getenv("GMP_DELIVERY_REPORT_RETENTION_SECONDS"),
            default=7 * 24 * 3600,
        ),
        "batch_size_per_account": get_int(
            "delivery",
            "batch_size_per_account",
            os.getenv("GMP_BATCH_SIZE_PER_ACCOUNT"),
            default=50,
        ),
        "log_delivery_activity": get_bool(
            "logging",
            "delivery_activity",
            os.getenv("GMP_LOG_DELIVERY_ACTIVITY"),
            default=False,
        ),
    }

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
        client_sync_url=settings.get("client_sync_url"),
        client_sync_user=settings.get("client_sync_user"),
        client_sync_password=settings.get("client_sync_password"),
        client_sync_token=settings.get("client_sync_token"),
        default_priority=settings.get("default_priority"),
        report_retention_seconds=settings.get("report_retention_seconds"),
        batch_size_per_account=settings.get("batch_size_per_account"),
        test_mode=bool(settings.get("test_mode")),
        log_delivery_activity=bool(settings.get("log_delivery_activity")),
    )
    send_loop_interval = settings.get("send_loop_interval")
    if send_loop_interval is not None:
        service_kwargs["send_loop_interval"] = float(send_loop_interval)

    service = AsyncMailCore(**service_kwargs)
    await service.start()
    return service


if __name__ == "__main__":
    settings = load_settings()
    # Create service instance but don't start it yet - let uvicorn handle the event loop
    service_kwargs = dict(
        db_path=settings["db_path"],
        start_active=bool(settings.get("scheduler_active")),
        client_sync_url=settings.get("client_sync_url"),
        client_sync_user=settings.get("client_sync_user"),
        client_sync_password=settings.get("client_sync_password"),
        client_sync_token=settings.get("client_sync_token"),
        default_priority=settings.get("default_priority"),
        report_retention_seconds=settings.get("report_retention_seconds"),
        batch_size_per_account=settings.get("batch_size_per_account"),
        test_mode=bool(settings.get("test_mode")),
        log_delivery_activity=bool(settings.get("log_delivery_activity")),
    )
    send_loop_interval = settings.get("send_loop_interval")
    if send_loop_interval is not None:
        service_kwargs["send_loop_interval"] = float(send_loop_interval)

    service = AsyncMailCore(**service_kwargs)

    # Define lifespan context manager for startup/shutdown events
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: start the mail service
        await service.start()
        yield
        # Shutdown: cleanup if needed (currently no shutdown logic required)

    app = create_app(service, api_token=settings.get("api_token"), lifespan=lifespan)

    uvicorn.run(app, host=str(settings["http_host"]), port=int(settings["http_port"]))
