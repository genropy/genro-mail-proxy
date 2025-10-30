"""Configuration loader for storage volumes."""

from __future__ import annotations

import configparser
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from async_mail_service.logger import logger


class VolumeConfigLoader:
    """Load storage volume configuration from config.ini."""

    def __init__(self, config_path: str):
        """Initialize with path to config.ini file."""
        self.config_path = config_path
        self.config = configparser.ConfigParser()

    def load_config(self) -> None:
        """Load the configuration file."""
        if not Path(self.config_path).exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        self.config.read(self.config_path)

    def parse_volumes(self) -> List[Dict[str, Any]]:
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

        volumes_dict: Dict[str, Dict[str, Any]] = {}

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
                    raise ValueError(f"Invalid JSON in volume.{vol_name}.config: {e}")
            elif field == "account_id":
                # Empty string or whitespace becomes None (global volume)
                account_id = value.strip()
                volumes_dict[vol_name]["account_id"] = account_id if account_id else None
            else:
                logger.warning(f"Unknown volume field: {field} (in {key})")

        # Validate volumes
        volumes: List[Dict[str, Any]] = []
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
