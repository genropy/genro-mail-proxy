# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""StorageManager: mount point management compatible with genro-storage API.

The StorageManager handles configuration of storage backends and creation
of StorageNode instances for file operations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .node import StorageNode


class StorageManager:
    """Manages storage mount points and creates StorageNode instances.

    API is compatible with genro-storage for future migration.

    Usage:
        storage = StorageManager()
        storage.configure([
            {'name': 'data', 'protocol': 'local', 'base_path': '/data'},
            {'name': 'cache', 'protocol': 'local', 'base_path': '/tmp/cache'},
        ])

        node = storage.node('data:files/report.pdf')
        await node.write_bytes(content)
    """

    def __init__(self):
        """Initialize the storage manager."""
        self._mounts: dict[str, dict[str, Any]] = {}

    def configure(self, source: str | list[dict[str, Any]]) -> None:
        """Configure mount points from file or list.

        Args:
            source: Either a path to YAML/JSON file, or a list of mount configs.
                Each config must have 'name' and 'protocol' keys.

        Example configs:
            {'name': 'data', 'protocol': 'local', 'base_path': '/data'}
            {'name': 's3', 'protocol': 's3', 'bucket': 'my-bucket'}
        """
        if isinstance(source, str):
            configs = self._load_config_file(source)
        else:
            configs = source

        for config in configs:
            name = config.get("name")
            if not name:
                raise ValueError("Mount config must have 'name' field")
            self._mounts[name] = config

    def _load_config_file(self, path: str) -> list[dict[str, Any]]:
        """Load configuration from YAML or JSON file."""
        file_path = Path(path)
        content = file_path.read_text()

        if file_path.suffix in (".yaml", ".yml"):
            try:
                import yaml

                return yaml.safe_load(content)
            except ImportError:
                raise ImportError("PyYAML required for YAML config: pip install pyyaml")

        return json.loads(content)

    def register(self, name: str, config: dict[str, Any] | str) -> None:
        """Register a single mount point.

        Args:
            name: Mount point name.
            config: Either a config dict or a storage URL string.

        Examples:
            storage.register('data', {'protocol': 'local', 'base_path': '/data'})
            storage.register('data', '/data')  # Shorthand for local
            storage.register('s3', 's3://bucket/path')
        """
        if isinstance(config, str):
            config = self._parse_url(name, config)
        config["name"] = name
        self._mounts[name] = config

    def _parse_url(self, name: str, url: str) -> dict[str, Any]:
        """Parse a storage URL into a config dict."""
        if url.startswith("s3://"):
            parts = url[5:].split("/", 1)
            return {
                "name": name,
                "protocol": "s3",
                "bucket": parts[0],
                "prefix": parts[1] if len(parts) > 1 else "",
            }
        elif url.startswith("gs://"):
            parts = url[5:].split("/", 1)
            return {
                "name": name,
                "protocol": "gcs",
                "bucket": parts[0],
                "prefix": parts[1] if len(parts) > 1 else "",
            }
        elif url.startswith("az://"):
            parts = url[5:].split("/", 1)
            return {
                "name": name,
                "protocol": "azure",
                "container": parts[0],
                "prefix": parts[1] if len(parts) > 1 else "",
            }
        elif url.startswith("file://"):
            return {
                "name": name,
                "protocol": "local",
                "base_path": url[7:],
            }
        else:
            # Assume local path
            return {
                "name": name,
                "protocol": "local",
                "base_path": url,
            }

    def get_mount_names(self) -> list[str]:
        """Get list of configured mount point names."""
        return list(self._mounts.keys())

    def has_mount(self, name: str) -> bool:
        """Check if a mount point is configured."""
        return name in self._mounts

    def get_mount_config(self, name: str) -> dict[str, Any] | None:
        """Get configuration for a mount point."""
        return self._mounts.get(name)

    def node(self, mount_or_path: str, *parts: str) -> StorageNode:
        """Create a StorageNode for a file or directory.

        Args:
            mount_or_path: Either 'mount:path' or just mount name.
            *parts: Additional path components.

        Returns:
            StorageNode for the specified path (composed with EE mixin if available).

        Examples:
            storage.node('data:files/report.pdf')
            storage.node('data', 'files', 'report.pdf')
        """
        # Import from package to get composed class (CE + EE mixin if available)
        from . import StorageNode as StorageNodeClass

        if ":" in mount_or_path:
            mount_name, path = mount_or_path.split(":", 1)
        else:
            mount_name = mount_or_path
            path = ""

        if parts:
            path = "/".join([path] + list(parts)) if path else "/".join(parts)

        config = self._mounts.get(mount_name)
        if not config:
            raise ValueError(f"Mount point '{mount_name}' not configured")

        return StorageNodeClass(self, mount_name, path, config)


__all__ = ["StorageManager"]
