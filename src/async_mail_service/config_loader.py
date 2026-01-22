# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Configuration loader for storage volumes and attachment settings.

This module provides utilities for loading storage volume configurations and
attachment settings from INI-style configuration files. Volumes define the
backend storage systems (S3, GCS, Azure, local filesystem, etc.) used for
email attachments.

The configuration format supports both account-specific volumes (accessible
only by a particular SMTP account) and global volumes (shared across all
accounts).

Example:
    Configuration file format (config.ini)::

        [volumes]
        volume.documents.backend = s3
        volume.documents.config = {"bucket": "docs", "region": "us-east-1"}
        volume.documents.account_id = tenant1

        # Global volume (no account_id)
        volume.shared.backend = local
        volume.shared.config = {"path": "/data/shared"}

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

    Loading volumes into the database::

        loader = VolumeConfigLoader("/etc/mail-proxy/config.ini")
        loader.load_config()
        volumes = loader.parse_volumes()

        # Or use the convenience function
        count = await load_volumes_from_config(
            "/etc/mail-proxy/config.ini",
            persistence,
            overwrite=False
        )

    Loading attachment configuration::

        config = load_attachment_config("/etc/mail-proxy/config.ini")
        # Returns AttachmentConfig dataclass
"""

from __future__ import annotations

import configparser
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

logger = get_logger("VolumeConfigLoader")


class VolumeConfigLoader:
    """Parser for INI-based storage volume configuration files.

    Reads volume definitions from an INI configuration file and converts
    them into a format suitable for database persistence. Supports validation
    of required fields and JSON parsing of configuration objects.

    Attributes:
        config_path: Filesystem path to the configuration file.
        config: ConfigParser instance holding the parsed configuration.
    """

    def __init__(self, config_path: str):
        """Initialize the loader with a configuration file path.

        Args:
            config_path: Absolute or relative path to the INI configuration file.
        """
        self.config_path = config_path
        self.config = configparser.ConfigParser()

    def load_config(self) -> None:
        """Read and parse the configuration file.

        Raises:
            FileNotFoundError: If the configuration file does not exist.
        """
        if not Path(self.config_path).exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        self.config.read(self.config_path)

    def parse_volumes(self) -> list[dict[str, Any]]:
        """Parse volumes from [volumes] section.

        Expected format in config.ini:
        ```ini
        [volumes]
        volume.name.backend = s3
        volume.name.config = {"bucket": "my-bucket", "region": "us-east-1"}
        volume.name.account_id = tenant1

        # Shared volumes (account_id omitted or empty)
        volume.shared.backend = s3
        volume.shared.config = {"bucket": "shared-files"}
        ```

        Returns:
            List of volume dictionaries with keys: name, backend, config, account_id
        """
        if not self.config.has_section("volumes"):
            logger.info("No [volumes] section found in config file")
            return []

        volumes_dict: dict[str, dict[str, Any]] = {}

        # Parse all volume.* entries
        for key, value in self.config.items("volumes"):
            if not key.startswith("volume."):
                logger.warning(f"Ignoring invalid key in [volumes] section: {key}")
                continue

            # Parse key: volume.name.field
            parts = key.split(".", 2)
            if len(parts) != 3:
                logger.warning(f"Invalid volume key format: {key}")
                continue

            _, vol_name, field = parts

            if vol_name not in volumes_dict:
                volumes_dict[vol_name] = {"name": vol_name}

            if field == "backend":
                volumes_dict[vol_name]["backend"] = value.strip()
            elif field == "config":
                try:
                    volumes_dict[vol_name]["config"] = json.loads(value)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON in volume.{vol_name}.config: {e}")
                    raise ValueError(f"Invalid JSON in volume.{vol_name}.config: {e}") from e
            elif field == "account_id":
                # Empty string or whitespace becomes None (global volume)
                account_id = value.strip()
                volumes_dict[vol_name]["account_id"] = account_id if account_id else None
            else:
                logger.warning(f"Unknown volume field: {field} (in {key})")

        # Validate volumes
        volumes: list[dict[str, Any]] = []
        for vol_name, vol_data in volumes_dict.items():
            if "backend" not in vol_data:
                logger.error(f"Volume '{vol_name}' missing required field 'backend'")
                raise ValueError(f"Volume '{vol_name}' missing required field 'backend'")
            if "config" not in vol_data:
                logger.error(f"Volume '{vol_name}' missing required field 'config'")
                raise ValueError(f"Volume '{vol_name}' missing required field 'config'")

            # Set account_id to None if not specified (global volume)
            if "account_id" not in vol_data:
                vol_data["account_id"] = None

            volumes.append(vol_data)

        logger.info(f"Parsed {len(volumes)} volumes from config")
        return volumes

    async def load_into_db(self, persistence, overwrite: bool = False) -> int:
        """Load parsed volumes into the database.

        Args:
            persistence: Persistence instance with add_volumes() method
            overwrite: If False (default), only loads volumes that don't exist.
                       If True, replaces existing volumes with same name.

        Returns:
            Number of volumes loaded
        """
        volumes = self.parse_volumes()

        if not volumes:
            logger.info("No volumes to load")
            return 0

        if not overwrite:
            # Check which volumes already exist
            existing_names = set()
            all_volumes = await persistence.list_volumes(account_id=None)  # Get all volumes
            existing_names = {v["name"] for v in all_volumes}

            # Filter out existing volumes
            volumes = [v for v in volumes if v["name"] not in existing_names]

            if not volumes:
                logger.info("All config volumes already exist in database")
                return 0

        # Add volumes to database
        await persistence.add_volumes(volumes)
        logger.info(f"Loaded {len(volumes)} volumes into database")
        return len(volumes)


async def load_volumes_from_config(
    config_path: str,
    persistence,
    overwrite: bool = False
) -> int:
    """Convenience function to load volumes from config file.

    Args:
        config_path: Path to config.ini file
        persistence: Persistence instance
        overwrite: Whether to overwrite existing volumes

    Returns:
        Number of volumes loaded
    """
    loader = VolumeConfigLoader(config_path)
    loader.load_config()
    return await loader.load_into_db(persistence, overwrite=overwrite)


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
