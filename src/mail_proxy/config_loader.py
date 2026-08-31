# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Configuration loader for cache settings.

This module provides utilities for loading cache settings from
INI-style configuration files or environment variables.

Example:
    Configuration file format (config.ini)::

        [cache]
        memory_max_mb = 50
        memory_ttl_seconds = 300
        disk_dir = /var/mail-service/cache
        disk_max_mb = 500
        disk_ttl_seconds = 3600
        disk_threshold_kb = 100

    Loading cache configuration::

        config = load_cache_config("/etc/mail-proxy/config.ini")
        # Returns CacheConfig dataclass
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from mail_proxy.logger import get_logger


@dataclass
class CacheConfig:
    """Configuration for attachment cache.

    Attributes:
        memory_max_mb: Max memory cache size in MB.
        memory_ttl_seconds: Memory cache TTL in seconds.
        disk_dir: Directory for disk cache.
        disk_max_mb: Max disk cache size in MB.
        disk_ttl_seconds: Disk cache TTL in seconds.
        disk_threshold_kb: Size threshold for disk vs memory.
    """

    # Memory cache
    memory_max_mb: float = 50.0
    memory_ttl_seconds: int = 300

    # Disk cache
    disk_dir: str | None = None
    disk_max_mb: float = 500.0
    disk_ttl_seconds: int = 3600
    disk_threshold_kb: float = 100.0

    @property
    def enabled(self) -> bool:
        """Check if caching is enabled (disk dir configured)."""
        return self.disk_dir is not None


logger = get_logger("config_loader")


def load_cache_config(config_path: str | None = None) -> CacheConfig:
    """Load cache configuration from config file or environment.

    Priority: config file > environment variables > defaults.

    Environment variables:
        GMP_CACHE_MEMORY_MAX_MB: Max memory cache size in MB
        GMP_CACHE_MEMORY_TTL_SECONDS: Memory cache TTL in seconds
        GMP_CACHE_DISK_DIR: Directory for disk cache
        GMP_CACHE_DISK_MAX_MB: Max disk cache size in MB
        GMP_CACHE_DISK_TTL_SECONDS: Disk cache TTL in seconds
        GMP_CACHE_DISK_THRESHOLD_KB: Size threshold for disk vs memory

    Args:
        config_path: Optional path to config.ini file

    Returns:
        CacheConfig with parsed settings, using defaults for missing values.
    """
    # Start with defaults
    config_values: dict = {}

    # Load from environment variables
    env_mapping = {
        "memory_max_mb": ("GMP_CACHE_MEMORY_MAX_MB", float, 50.0),
        "memory_ttl_seconds": ("GMP_CACHE_MEMORY_TTL_SECONDS", int, 300),
        "disk_dir": ("GMP_CACHE_DISK_DIR", str, None),
        "disk_max_mb": ("GMP_CACHE_DISK_MAX_MB", float, 500.0),
        "disk_ttl_seconds": ("GMP_CACHE_DISK_TTL_SECONDS", int, 3600),
        "disk_threshold_kb": ("GMP_CACHE_DISK_THRESHOLD_KB", float, 100.0),
    }

    for key, (env_var, type_fn, default) in env_mapping.items():
        env_value = os.environ.get(env_var)
        if env_value is not None:
            try:
                config_values[key] = type_fn(env_value)
            except (ValueError, TypeError):
                logger.warning(f"Invalid value for {env_var}, using default")
                config_values[key] = default
        else:
            config_values[key] = default

    # Override with config file if provided
    if config_path and Path(config_path).exists():
        config = configparser.ConfigParser()
        config.read(config_path)

        if config.has_section("cache"):
            def get_float(key: str, default: float) -> float:
                try:
                    return config.getfloat("cache", key, fallback=default)
                except ValueError:
                    return default

            def get_int(key: str, default: int) -> int:
                try:
                    return config.getint("cache", key, fallback=default)
                except ValueError:
                    return default

            def get_str(key: str, default: str | None = None) -> str | None:
                value = config.get("cache", key, fallback=default)
                return value.strip() if value else default

            config_values["memory_max_mb"] = get_float("memory_max_mb", config_values["memory_max_mb"])
            config_values["memory_ttl_seconds"] = get_int("memory_ttl_seconds", config_values["memory_ttl_seconds"])
            config_values["disk_dir"] = get_str("disk_dir", config_values["disk_dir"])
            config_values["disk_max_mb"] = get_float("disk_max_mb", config_values["disk_max_mb"])
            config_values["disk_ttl_seconds"] = get_int("disk_ttl_seconds", config_values["disk_ttl_seconds"])
            config_values["disk_threshold_kb"] = get_float("disk_threshold_kb", config_values["disk_threshold_kb"])

    return CacheConfig(**config_values)
