"""Tests for volume configuration loading from config.ini."""

import pytest
from pathlib import Path
from async_mail_service.config_loader import VolumeConfigLoader, load_volumes_from_config
from async_mail_service.persistence import Persistence


@pytest.mark.asyncio
async def test_parse_volumes_from_config(tmp_path):
    """Test parsing volumes from config.ini [volumes] section."""
    config_file = tmp_path / "test_config.ini"
    config_file.write_text("""
[volumes]
volume.s3-uploads.backend = s3
volume.s3-uploads.config = {"bucket": "my-uploads", "region": "us-east-1"}

volume.cdn.backend = http
volume.cdn.config = {"base_url": "https://cdn.example.com"}

volume.tenant1-storage.backend = s3
volume.tenant1-storage.config = {"bucket": "tenant1-files"}
volume.tenant1-storage.account_id = tenant1
""")

    loader = VolumeConfigLoader(str(config_file))
    loader.load_config()
    volumes = loader.parse_volumes()

    assert len(volumes) == 3

    # Check s3-uploads volume (global)
    s3_vol = next(v for v in volumes if v["name"] == "s3-uploads")
    assert s3_vol["backend"] == "s3"
    assert s3_vol["config"]["bucket"] == "my-uploads"
    assert s3_vol["config"]["region"] == "us-east-1"
    assert s3_vol["account_id"] is None

    # Check cdn volume (global)
    cdn_vol = next(v for v in volumes if v["name"] == "cdn")
    assert cdn_vol["backend"] == "http"
    assert cdn_vol["config"]["base_url"] == "https://cdn.example.com"
    assert cdn_vol["account_id"] is None

    # Check tenant1-storage volume (tenant-specific)
    tenant_vol = next(v for v in volumes if v["name"] == "tenant1-storage")
    assert tenant_vol["backend"] == "s3"
    assert tenant_vol["config"]["bucket"] == "tenant1-files"
    assert tenant_vol["account_id"] == "tenant1"


@pytest.mark.asyncio
async def test_load_volumes_into_db(tmp_path):
    """Test loading parsed volumes into database."""
    config_file = tmp_path / "load_test.ini"
    config_file.write_text("""
[volumes]
volume.test-vol.backend = local
volume.test-vol.config = {"path": "/tmp/test"}
""")

    db_file = tmp_path / "test.db"
    persistence = Persistence(str(db_file))
    await persistence.init_db()

    loader = VolumeConfigLoader(str(config_file))
    loader.load_config()
    count = await loader.load_into_db(persistence, overwrite=False)

    assert count == 1

    # Verify volume was loaded
    volumes = await persistence.list_volumes()
    assert len(volumes) == 1
    assert volumes[0]["name"] == "test-vol"
    assert volumes[0]["backend"] == "local"


@pytest.mark.asyncio
async def test_invalid_config_handling(tmp_path):
    """Test handling of malformed JSON and missing fields."""
    # Test invalid JSON
    config_invalid_json = tmp_path / "invalid_json.ini"
    config_invalid_json.write_text("""
[volumes]
volume.broken.backend = s3
volume.broken.config = {invalid json}
""")

    loader = VolumeConfigLoader(str(config_invalid_json))
    loader.load_config()
    with pytest.raises(ValueError, match="Invalid JSON"):
        loader.parse_volumes()

    # Test missing backend
    config_missing_backend = tmp_path / "missing_backend.ini"
    config_missing_backend.write_text("""
[volumes]
volume.incomplete.config = {"bucket": "test"}
""")

    loader2 = VolumeConfigLoader(str(config_missing_backend))
    loader2.load_config()
    with pytest.raises(ValueError, match="missing required field 'backend'"):
        loader2.parse_volumes()

    # Test missing config
    config_missing_config = tmp_path / "missing_config.ini"
    config_missing_config.write_text("""
[volumes]
volume.incomplete.backend = s3
""")

    loader3 = VolumeConfigLoader(str(config_missing_config))
    loader3.load_config()
    with pytest.raises(ValueError, match="missing required field 'config'"):
        loader3.parse_volumes()


@pytest.mark.asyncio
async def test_overwrite_behavior(tmp_path):
    """Test overwrite=True/False behavior."""
    config_file = tmp_path / "overwrite.ini"
    config_file.write_text("""
[volumes]
volume.test.backend = s3
volume.test.config = {"bucket": "original"}
""")

    db_file = tmp_path / "overwrite.db"
    persistence = Persistence(str(db_file))
    await persistence.init_db()

    # First load
    loader = VolumeConfigLoader(str(config_file))
    loader.load_config()
    count1 = await loader.load_into_db(persistence, overwrite=False)
    assert count1 == 1

    vol = await persistence.get_volume("test")
    assert vol["config"]["bucket"] == "original"

    # Update config file
    config_file.write_text("""
[volumes]
volume.test.backend = s3
volume.test.config = {"bucket": "updated"}
""")

    # Load again with overwrite=False (should skip)
    loader2 = VolumeConfigLoader(str(config_file))
    loader2.load_config()
    count2 = await loader2.load_into_db(persistence, overwrite=False)
    assert count2 == 0  # No new volumes loaded

    vol_after_no_overwrite = await persistence.get_volume("test")
    assert vol_after_no_overwrite["config"]["bucket"] == "original"  # Still original

    # Load with overwrite=True (should replace)
    loader3 = VolumeConfigLoader(str(config_file))
    loader3.load_config()
    count3 = await loader3.load_into_db(persistence, overwrite=True)
    assert count3 == 1

    vol_after_overwrite = await persistence.get_volume("test")
    assert vol_after_overwrite["config"]["bucket"] == "updated"  # Now updated


@pytest.mark.asyncio
async def test_empty_account_id_becomes_none(tmp_path):
    """Test that empty account_id becomes None (global volume)."""
    config_file = tmp_path / "empty_account.ini"
    config_file.write_text("""
[volumes]
volume.global1.backend = s3
volume.global1.config = {"bucket": "shared"}
volume.global1.account_id =

volume.global2.backend = s3
volume.global2.config = {"bucket": "shared2"}
volume.global2.account_id =

volume.with_account.backend = s3
volume.with_account.config = {"bucket": "tenant"}
volume.with_account.account_id = tenant1
""")

    loader = VolumeConfigLoader(str(config_file))
    loader.load_config()
    volumes = loader.parse_volumes()

    # Empty/whitespace account_id should be None
    global1 = next(v for v in volumes if v["name"] == "global1")
    assert global1["account_id"] is None

    global2 = next(v for v in volumes if v["name"] == "global2")
    assert global2["account_id"] is None

    # Non-empty account_id should be preserved
    tenant_vol = next(v for v in volumes if v["name"] == "with_account")
    assert tenant_vol["account_id"] == "tenant1"


@pytest.mark.asyncio
async def test_no_volumes_section(tmp_path):
    """Test handling of config file without [volumes] section."""
    config_file = tmp_path / "no_volumes.ini"
    config_file.write_text("""
[server]
host = 0.0.0.0
port = 8000
""")

    loader = VolumeConfigLoader(str(config_file))
    loader.load_config()
    volumes = loader.parse_volumes()

    assert volumes == []


@pytest.mark.asyncio
async def test_load_volumes_from_config_convenience(tmp_path):
    """Test convenience function load_volumes_from_config."""
    config_file = tmp_path / "convenience.ini"
    config_file.write_text("""
[volumes]
volume.conv-test.backend = local
volume.conv-test.config = {"path": "/tmp"}
""")

    db_file = tmp_path / "convenience.db"
    persistence = Persistence(str(db_file))
    await persistence.init_db()

    # Use convenience function
    count = await load_volumes_from_config(str(config_file), persistence)
    assert count == 1

    # Verify
    volumes = await persistence.list_volumes()
    assert len(volumes) == 1
    assert volumes[0]["name"] == "conv-test"


@pytest.mark.asyncio
async def test_config_file_not_found():
    """Test handling of missing config file."""
    loader = VolumeConfigLoader("/nonexistent/config.ini")
    with pytest.raises(FileNotFoundError):
        loader.load_config()


@pytest.mark.asyncio
async def test_complex_json_config(tmp_path):
    """Test parsing complex JSON configurations."""
    config_file = tmp_path / "complex.ini"
    config_file.write_text("""
[volumes]
volume.webdav.backend = webdav
volume.webdav.config = {"base_url": "https://cloud.example.com/remote.php/dav", "username": "user", "password": "secret", "timeout": 30}
""")

    loader = VolumeConfigLoader(str(config_file))
    loader.load_config()
    volumes = loader.parse_volumes()

    assert len(volumes) == 1
    webdav = volumes[0]
    assert webdav["backend"] == "webdav"
    assert webdav["config"]["base_url"] == "https://cloud.example.com/remote.php/dav"
    assert webdav["config"]["username"] == "user"
    assert webdav["config"]["password"] == "secret"
    assert webdav["config"]["timeout"] == 30
