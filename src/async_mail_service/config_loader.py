# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Configuration loader for attachment settings.

This module provides utilities for loading attachment settings from
INI-style configuration files.

Example:
    Configuration file format (config.ini)::

        [attachments]
        # Filesystem base directory for relative paths
        base_dir = /var/mail-service/files

        # HTTP endpoint for @params paths
        default_endpoint = https://api.example.com/attachments
        auth_method = bearer
        auth_token = my-secret-token

        # Cache settings
        cache_memory_max_mb = 50
        cache_memory_ttl_seconds = 300
        cache_disk_dir = /var/mail-service/cache
        cache_disk_max_mb = 500
        cache_disk_ttl_seconds = 3600
        cache_disk_threshold_kb = 100

    Loading attachment configuration::

        config = load_attachment_config("/etc/mail-proxy/config.ini")
        # Returns AttachmentConfig dataclass
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from async_mail_service.logger import get_logger


@dataclass
class AttachmentConfig:
    """Configuration for attachment handling.

    Attributes:
        base_dir: Base directory for relative filesystem paths.
        http_endpoint: Default HTTP endpoint for @params paths.
        http_auth_method: Authentication method (none, bearer, basic).
        http_auth_token: Bearer token for HTTP auth.
        http_auth_user: Username for HTTP basic auth.
        http_auth_password: Password for HTTP basic auth.
        cache_memory_max_mb: Max memory cache size in MB.
        cache_memory_ttl_seconds: Memory cache TTL in seconds.
        cache_disk_dir: Directory for disk cache.
        cache_disk_max_mb: Max disk cache size in MB.
        cache_disk_ttl_seconds: Disk cache TTL in seconds.
        cache_disk_threshold_kb: Size threshold for disk vs memory.
    """

    # Filesystem
    base_dir: str | None = None

    # HTTP
    http_endpoint: str | None = None
    http_auth_method: str = "none"
    http_auth_token: str | None = None
    http_auth_user: str | None = None
    http_auth_password: str | None = None

    # Cache - Memory
    cache_memory_max_mb: float = 50.0
    cache_memory_ttl_seconds: int = 300

    # Cache - Disk
    cache_disk_dir: str | None = None
    cache_disk_max_mb: float = 500.0
    cache_disk_ttl_seconds: int = 3600
    cache_disk_threshold_kb: float = 100.0

    @property
    def http_auth_config(self) -> dict[str, str] | None:
        """Build HTTP auth config dict for HttpFetcher."""
        if self.http_auth_method == "none":
            return None
        config = {"method": self.http_auth_method}
        if self.http_auth_method == "bearer" and self.http_auth_token:
            config["token"] = self.http_auth_token
        elif self.http_auth_method == "basic":
            if self.http_auth_user:
                config["user"] = self.http_auth_user
            if self.http_auth_password:
                config["password"] = self.http_auth_password
        return config

    @property
    def cache_enabled(self) -> bool:
        """Check if caching is enabled (disk dir configured)."""
        return self.cache_disk_dir is not None


logger = get_logger("config_loader")


def load_attachment_config(config_path: str) -> AttachmentConfig:
    """Load attachment configuration from config file.

    Reads the [attachments] section from an INI configuration file
    and returns an AttachmentConfig dataclass.

    Args:
        config_path: Path to config.ini file

    Returns:
        AttachmentConfig with parsed settings, using defaults for
        any missing values.

    Raises:
        FileNotFoundError: If config file doesn't exist.
    """
    if not Path(config_path).exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    config = configparser.ConfigParser()
    config.read(config_path)

    if not config.has_section("attachments"):
        logger.info("No [attachments] section in config, using defaults")
        return AttachmentConfig()

    def get_str(key: str, default: str | None = None) -> str | None:
        value = config.get("attachments", key, fallback=default)
        return value.strip() if value else default

    def get_float(key: str, default: float) -> float:
        try:
            return config.getfloat("attachments", key, fallback=default)
        except ValueError:
            logger.warning(f"Invalid float for {key}, using default {default}")
            return default

    def get_int(key: str, default: int) -> int:
        try:
            return config.getint("attachments", key, fallback=default)
        except ValueError:
            logger.warning(f"Invalid int for {key}, using default {default}")
            return default

    return AttachmentConfig(
        # Filesystem
        base_dir=get_str("base_dir"),

        # HTTP
        http_endpoint=get_str("default_endpoint"),
        http_auth_method=get_str("auth_method", "none") or "none",
        http_auth_token=get_str("auth_token"),
        http_auth_user=get_str("auth_user"),
        http_auth_password=get_str("auth_password"),

        # Cache - Memory
        cache_memory_max_mb=get_float("cache_memory_max_mb", 50.0),
        cache_memory_ttl_seconds=get_int("cache_memory_ttl_seconds", 300),

        # Cache - Disk
        cache_disk_dir=get_str("cache_disk_dir"),
        cache_disk_max_mb=get_float("cache_disk_max_mb", 500.0),
        cache_disk_ttl_seconds=get_int("cache_disk_ttl_seconds", 3600),
        cache_disk_threshold_kb=get_float("cache_disk_threshold_kb", 100.0),
    )
