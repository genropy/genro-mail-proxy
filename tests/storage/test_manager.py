# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for StorageManager."""

import json

import pytest

from storage import StorageManager


class TestStorageManagerConfiguration:
    """Tests for StorageManager configuration."""

    def test_configure_from_list(self):
        """Configure from list of dicts."""
        storage = StorageManager()
        storage.configure([
            {"name": "data", "protocol": "local", "base_path": "/tmp/data"},
            {"name": "cache", "protocol": "local", "base_path": "/tmp/cache"},
        ])

        assert storage.has_mount("data")
        assert storage.has_mount("cache")
        assert not storage.has_mount("unknown")

    def test_configure_missing_name_raises(self):
        """Config without name raises ValueError."""
        storage = StorageManager()
        with pytest.raises(ValueError, match="must have 'name'"):
            storage.configure([{"protocol": "local", "base_path": "/tmp"}])

    def test_configure_from_json_file(self, tmp_path):
        """Configure from JSON file."""
        config_file = tmp_path / "storage.json"
        config_file.write_text(json.dumps([
            {"name": "uploads", "protocol": "local", "base_path": "/data/uploads"}
        ]))

        storage = StorageManager()
        storage.configure(str(config_file))

        assert storage.has_mount("uploads")

    def test_get_mount_names(self):
        """get_mount_names returns list of mount names."""
        storage = StorageManager()
        storage.configure([
            {"name": "alpha", "protocol": "local", "base_path": "/a"},
            {"name": "beta", "protocol": "local", "base_path": "/b"},
        ])

        names = storage.get_mount_names()

        assert "alpha" in names
        assert "beta" in names

    def test_get_mount_config(self):
        """get_mount_config returns mount configuration."""
        storage = StorageManager()
        storage.configure([
            {"name": "data", "protocol": "local", "base_path": "/data"}
        ])

        config = storage.get_mount_config("data")

        assert config is not None
        assert config["base_path"] == "/data"

    def test_get_mount_config_not_found(self):
        """get_mount_config returns None for unknown mount."""
        storage = StorageManager()
        assert storage.get_mount_config("unknown") is None


class TestStorageManagerRegister:
    """Tests for StorageManager.register()."""

    def test_register_with_config_dict(self):
        """Register mount with config dict."""
        storage = StorageManager()
        storage.register("data", {"protocol": "local", "base_path": "/data"})

        assert storage.has_mount("data")
        assert storage.get_mount_config("data")["base_path"] == "/data"

    def test_register_with_local_path(self):
        """Register mount with local path string."""
        storage = StorageManager()
        storage.register("data", "/data/files")

        config = storage.get_mount_config("data")
        assert config["protocol"] == "local"
        assert config["base_path"] == "/data/files"

    def test_register_with_file_url(self):
        """Register mount with file:// URL."""
        storage = StorageManager()
        storage.register("data", "file:///var/data")

        config = storage.get_mount_config("data")
        assert config["protocol"] == "local"
        assert config["base_path"] == "/var/data"

    def test_register_with_s3_url(self):
        """Register mount with s3:// URL."""
        storage = StorageManager()
        storage.register("s3data", "s3://my-bucket/path/to/files")

        config = storage.get_mount_config("s3data")
        assert config["protocol"] == "s3"
        assert config["bucket"] == "my-bucket"
        assert config["prefix"] == "path/to/files"

    def test_register_with_s3_url_no_prefix(self):
        """Register mount with s3:// URL without prefix."""
        storage = StorageManager()
        storage.register("s3data", "s3://my-bucket")

        config = storage.get_mount_config("s3data")
        assert config["protocol"] == "s3"
        assert config["bucket"] == "my-bucket"
        assert config["prefix"] == ""

    def test_register_with_gcs_url(self):
        """Register mount with gs:// URL."""
        storage = StorageManager()
        storage.register("gcs", "gs://gcs-bucket/prefix")

        config = storage.get_mount_config("gcs")
        assert config["protocol"] == "gcs"
        assert config["bucket"] == "gcs-bucket"
        assert config["prefix"] == "prefix"

    def test_register_with_azure_url(self):
        """Register mount with az:// URL."""
        storage = StorageManager()
        storage.register("azure", "az://container/path")

        config = storage.get_mount_config("azure")
        assert config["protocol"] == "azure"
        assert config["container"] == "container"
        assert config["prefix"] == "path"


class TestStorageManagerNode:
    """Tests for StorageManager.node()."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create storage with local mount."""
        s = StorageManager()
        s.configure([
            {"name": "data", "protocol": "local", "base_path": str(tmp_path)}
        ])
        return s

    def test_node_with_colon_path(self, storage):
        """Create node with mount:path format."""
        node = storage.node("data:files/report.pdf")

        assert node.mount_name == "data"
        assert node.path == "files/report.pdf"
        assert node.basename == "report.pdf"

    def test_node_with_parts(self, storage):
        """Create node with separate path parts."""
        node = storage.node("data", "files", "report.pdf")

        assert node.mount_name == "data"
        assert node.path == "files/report.pdf"

    def test_node_with_colon_path_and_parts(self, storage):
        """Create node with mount:path and additional parts."""
        node = storage.node("data:files", "subdir", "report.pdf")

        assert node.path == "files/subdir/report.pdf"

    def test_node_unknown_mount_raises(self, storage):
        """Unknown mount raises ValueError."""
        with pytest.raises(ValueError, match="not configured"):
            storage.node("unknown:file.txt")

    def test_node_fullpath(self, storage):
        """node.fullpath returns mount:path format."""
        node = storage.node("data:files/doc.txt")
        assert node.fullpath == "data:files/doc.txt"
