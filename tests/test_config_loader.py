"""Tests for config_loader module."""

import os
import pytest
from pathlib import Path

from async_mail_service.config_loader import CacheConfig, load_cache_config


class TestCacheConfig:
    """Tests for CacheConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = CacheConfig()
        assert config.memory_max_mb == 50.0
        assert config.memory_ttl_seconds == 300
        assert config.disk_dir is None
        assert config.disk_max_mb == 500.0
        assert config.disk_ttl_seconds == 3600
        assert config.disk_threshold_kb == 100.0

    def test_enabled_property_false_when_no_disk_dir(self):
        """Test that cache is disabled when no disk_dir is set."""
        config = CacheConfig()
        assert config.enabled is False

    def test_enabled_property_true_when_disk_dir_set(self):
        """Test that cache is enabled when disk_dir is set."""
        config = CacheConfig(disk_dir="/tmp/cache")
        assert config.enabled is True

    def test_custom_values(self):
        """Test configuration with custom values."""
        config = CacheConfig(
            memory_max_mb=100.0,
            memory_ttl_seconds=600,
            disk_dir="/var/cache",
            disk_max_mb=1000.0,
            disk_ttl_seconds=7200,
            disk_threshold_kb=200.0,
        )
        assert config.memory_max_mb == 100.0
        assert config.memory_ttl_seconds == 600
        assert config.disk_dir == "/var/cache"
        assert config.disk_max_mb == 1000.0
        assert config.disk_ttl_seconds == 7200
        assert config.disk_threshold_kb == 200.0


class TestLoadCacheConfig:
    """Tests for load_cache_config function."""

    def test_defaults_when_no_config(self):
        """Test that defaults are returned when no config file or env vars."""
        config = load_cache_config()
        assert config.memory_max_mb == 50.0
        assert config.disk_dir is None

    def test_load_from_env_vars(self, monkeypatch):
        """Test loading configuration from environment variables."""
        monkeypatch.setenv("GMP_CACHE_MEMORY_MAX_MB", "100")
        monkeypatch.setenv("GMP_CACHE_MEMORY_TTL_SECONDS", "600")
        monkeypatch.setenv("GMP_CACHE_DISK_DIR", "/tmp/test-cache")
        monkeypatch.setenv("GMP_CACHE_DISK_MAX_MB", "1000")
        monkeypatch.setenv("GMP_CACHE_DISK_TTL_SECONDS", "7200")
        monkeypatch.setenv("GMP_CACHE_DISK_THRESHOLD_KB", "200")

        config = load_cache_config()

        assert config.memory_max_mb == 100.0
        assert config.memory_ttl_seconds == 600
        assert config.disk_dir == "/tmp/test-cache"
        assert config.disk_max_mb == 1000.0
        assert config.disk_ttl_seconds == 7200
        assert config.disk_threshold_kb == 200.0

    def test_invalid_env_var_uses_default(self, monkeypatch):
        """Test that invalid env var values fall back to defaults."""
        monkeypatch.setenv("GMP_CACHE_MEMORY_MAX_MB", "invalid")
        monkeypatch.setenv("GMP_CACHE_MEMORY_TTL_SECONDS", "not-a-number")

        config = load_cache_config()

        assert config.memory_max_mb == 50.0  # default
        assert config.memory_ttl_seconds == 300  # default

    def test_load_from_config_file(self, tmp_path):
        """Test loading configuration from INI file."""
        config_file = tmp_path / "config.ini"
        config_file.write_text("""
[cache]
memory_max_mb = 75
memory_ttl_seconds = 450
disk_dir = /var/cache/test
disk_max_mb = 750
disk_ttl_seconds = 5400
disk_threshold_kb = 150
""")

        config = load_cache_config(str(config_file))

        assert config.memory_max_mb == 75.0
        assert config.memory_ttl_seconds == 450
        assert config.disk_dir == "/var/cache/test"
        assert config.disk_max_mb == 750.0
        assert config.disk_ttl_seconds == 5400
        assert config.disk_threshold_kb == 150.0

    def test_config_file_overrides_env_vars(self, tmp_path, monkeypatch):
        """Test that config file values override environment variables."""
        # Set env vars
        monkeypatch.setenv("GMP_CACHE_MEMORY_MAX_MB", "100")
        monkeypatch.setenv("GMP_CACHE_DISK_DIR", "/env/cache")

        # Create config file with different values
        config_file = tmp_path / "config.ini"
        config_file.write_text("""
[cache]
memory_max_mb = 200
disk_dir = /file/cache
""")

        config = load_cache_config(str(config_file))

        # File values should win
        assert config.memory_max_mb == 200.0
        assert config.disk_dir == "/file/cache"

    def test_nonexistent_config_file_uses_env_and_defaults(self, monkeypatch):
        """Test that nonexistent config file falls back to env vars and defaults."""
        monkeypatch.setenv("GMP_CACHE_MEMORY_MAX_MB", "80")

        config = load_cache_config("/nonexistent/config.ini")

        assert config.memory_max_mb == 80.0  # from env
        assert config.disk_dir is None  # default

    def test_config_file_without_cache_section(self, tmp_path):
        """Test config file without [cache] section uses defaults."""
        config_file = tmp_path / "config.ini"
        config_file.write_text("""
[other]
some_key = some_value
""")

        config = load_cache_config(str(config_file))

        assert config.memory_max_mb == 50.0
        assert config.disk_dir is None

    def test_config_file_with_invalid_values(self, tmp_path):
        """Test config file with invalid values uses defaults for those keys."""
        config_file = tmp_path / "config.ini"
        config_file.write_text("""
[cache]
memory_max_mb = invalid
memory_ttl_seconds = not-int
disk_dir = /valid/path
disk_max_mb = 750
""")

        config = load_cache_config(str(config_file))

        # Invalid values get defaults, valid ones are used
        assert config.memory_max_mb == 50.0  # default due to invalid
        assert config.memory_ttl_seconds == 300  # default due to invalid
        assert config.disk_dir == "/valid/path"
        assert config.disk_max_mb == 750.0

    def test_partial_config_file(self, tmp_path):
        """Test config file with only some values set."""
        config_file = tmp_path / "config.ini"
        config_file.write_text("""
[cache]
disk_dir = /partial/cache
""")

        config = load_cache_config(str(config_file))

        assert config.memory_max_mb == 50.0  # default
        assert config.disk_dir == "/partial/cache"  # from file
        assert config.disk_max_mb == 500.0  # default

    def test_disk_dir_with_whitespace_stripped(self, tmp_path):
        """Test that disk_dir value has whitespace stripped."""
        config_file = tmp_path / "config.ini"
        config_file.write_text("""
[cache]
disk_dir =   /path/with/spaces
""")

        config = load_cache_config(str(config_file))

        assert config.disk_dir == "/path/with/spaces"
