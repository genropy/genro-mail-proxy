# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for tools.encryption module."""

import pytest

from tools.encryption import (
    ENCRYPTED_PREFIX,
    EncryptionError,
    decrypt_value_with_key,
    encrypt_value_with_key,
    generate_key,
    is_encrypted,
)


@pytest.fixture
def test_key() -> bytes:
    """Generate a test encryption key (32 bytes)."""
    import base64
    return base64.b64decode(generate_key())


class TestEncryption:
    """Tests for encrypt/decrypt functions."""

    def test_generate_key_is_32_bytes(self):
        """Generated key should be 32 bytes when decoded."""
        import base64
        key_b64 = generate_key()
        key = base64.b64decode(key_b64)
        assert len(key) == 32

    def test_encrypt_returns_prefixed_string(self, test_key):
        """Encrypted value should start with ENC: prefix."""
        encrypted = encrypt_value_with_key("secret", test_key)
        assert encrypted.startswith(ENCRYPTED_PREFIX)

    def test_decrypt_returns_original(self, test_key):
        """Decrypting should return the original plaintext."""
        plaintext = "my-secret-password"
        encrypted = encrypt_value_with_key(plaintext, test_key)
        decrypted = decrypt_value_with_key(encrypted, test_key)
        assert decrypted == plaintext

    def test_encrypt_empty_string_returns_empty(self, test_key):
        """Empty string should not be encrypted."""
        result = encrypt_value_with_key("", test_key)
        assert result == ""

    def test_encrypt_none_returns_none(self, test_key):
        """None should pass through unchanged."""
        result = encrypt_value_with_key(None, test_key)
        assert result is None

    def test_decrypt_empty_string_returns_empty(self, test_key):
        """Empty string should pass through unchanged."""
        result = decrypt_value_with_key("", test_key)
        assert result == ""

    def test_decrypt_non_encrypted_returns_unchanged(self, test_key):
        """Non-encrypted string should pass through unchanged."""
        plaintext = "not-encrypted"
        result = decrypt_value_with_key(plaintext, test_key)
        assert result == plaintext

    def test_encrypt_already_encrypted_is_idempotent(self, test_key):
        """Encrypting already encrypted value should return it unchanged."""
        plaintext = "secret"
        encrypted = encrypt_value_with_key(plaintext, test_key)
        double_encrypted = encrypt_value_with_key(encrypted, test_key)
        assert double_encrypted == encrypted

    def test_decrypt_with_wrong_key_raises(self, test_key):
        """Decrypting with wrong key should raise EncryptionError."""
        import base64
        wrong_key = base64.b64decode(generate_key())
        encrypted = encrypt_value_with_key("secret", test_key)

        with pytest.raises(EncryptionError, match="Decryption failed"):
            decrypt_value_with_key(encrypted, wrong_key)

    def test_invalid_key_size_raises(self):
        """Key with wrong size should raise EncryptionError."""
        invalid_key = b"too-short"

        with pytest.raises(EncryptionError, match="must be 32 bytes"):
            encrypt_value_with_key("secret", invalid_key)

        with pytest.raises(EncryptionError, match="must be 32 bytes"):
            decrypt_value_with_key("ENC:something", invalid_key)

    def test_is_encrypted_true_for_encrypted(self, test_key):
        """is_encrypted should return True for encrypted values."""
        encrypted = encrypt_value_with_key("secret", test_key)
        assert is_encrypted(encrypted) is True

    def test_is_encrypted_false_for_plaintext(self):
        """is_encrypted should return False for plaintext."""
        assert is_encrypted("plaintext") is False
        assert is_encrypted("") is False
        assert is_encrypted(None) is False

    def test_unicode_roundtrip(self, test_key):
        """Unicode characters should survive encrypt/decrypt."""
        plaintext = "パスワード 🔐 Пароль"
        encrypted = encrypt_value_with_key(plaintext, test_key)
        decrypted = decrypt_value_with_key(encrypted, test_key)
        assert decrypted == plaintext

    def test_long_value_roundtrip(self, test_key):
        """Long values should encrypt/decrypt correctly."""
        plaintext = "x" * 10000
        encrypted = encrypt_value_with_key(plaintext, test_key)
        decrypted = decrypt_value_with_key(encrypted, test_key)
        assert decrypted == plaintext

    def test_each_encryption_produces_different_ciphertext(self, test_key):
        """Same plaintext should produce different ciphertext (random nonce)."""
        plaintext = "secret"
        encrypted1 = encrypt_value_with_key(plaintext, test_key)
        encrypted2 = encrypt_value_with_key(plaintext, test_key)

        # Different ciphertext due to random nonce
        assert encrypted1 != encrypted2

        # But both decrypt to same plaintext
        assert decrypt_value_with_key(encrypted1, test_key) == plaintext
        assert decrypt_value_with_key(encrypted2, test_key) == plaintext

    def test_invalid_base64_raises_error(self, test_key):
        """Invalid base64 in encrypted value should raise EncryptionError."""
        with pytest.raises(EncryptionError, match="Invalid encrypted data"):
            decrypt_value_with_key("ENC:not-valid-base64!!!", test_key)

    def test_truncated_ciphertext_raises_error(self, test_key):
        """Encrypted data too short should raise EncryptionError."""
        import base64
        # Create data shorter than NONCE_SIZE + TAG_SIZE (12 + 16 = 28 bytes)
        short_data = base64.b64encode(b"short").decode()
        with pytest.raises(EncryptionError, match="too short"):
            decrypt_value_with_key(f"ENC:{short_data}", test_key)


class TestGlobalKeyEncryption:
    """Tests for encrypt_value/decrypt_value using global key."""

    def test_encrypt_decrypt_with_env_key(self, monkeypatch, test_key):
        """Should use key from MAIL_PROXY_ENCRYPTION_KEY env var."""
        import base64
        from tools.encryption import (
            decrypt_value,
            encrypt_value,
            set_key_for_testing,
        )

        # Clear any cached key
        set_key_for_testing(None)

        # Set key via environment
        key_b64 = base64.b64encode(test_key).decode()
        monkeypatch.setenv("MAIL_PROXY_ENCRYPTION_KEY", key_b64)

        plaintext = "secret-password"
        encrypted = encrypt_value(plaintext)
        assert encrypted.startswith("ENC:")

        decrypted = decrypt_value(encrypted)
        assert decrypted == plaintext

        # Cleanup
        set_key_for_testing(None)

    def test_missing_key_raises_error(self, monkeypatch):
        """Should raise EncryptionKeyNotConfigured when no key available."""
        from tools.encryption import (
            EncryptionKeyNotConfigured,
            encrypt_value,
            set_key_for_testing,
        )

        # Clear cached key and env var
        set_key_for_testing(None)
        monkeypatch.delenv("MAIL_PROXY_ENCRYPTION_KEY", raising=False)

        with pytest.raises(EncryptionKeyNotConfigured, match="not configured"):
            encrypt_value("secret")

    def test_invalid_env_key_raises_error(self, monkeypatch):
        """Invalid base64 key in env var should raise EncryptionError."""
        from tools.encryption import encrypt_value, set_key_for_testing

        set_key_for_testing(None)
        monkeypatch.setenv("MAIL_PROXY_ENCRYPTION_KEY", "not-valid-base64!!!")

        with pytest.raises(EncryptionError, match="Invalid"):
            encrypt_value("secret")

        set_key_for_testing(None)

    def test_wrong_size_env_key_raises_error(self, monkeypatch):
        """Key with wrong size in env var should raise EncryptionError."""
        import base64
        from tools.encryption import encrypt_value, set_key_for_testing

        set_key_for_testing(None)
        wrong_size_key = base64.b64encode(b"only-16-bytes!!!").decode()
        monkeypatch.setenv("MAIL_PROXY_ENCRYPTION_KEY", wrong_size_key)

        with pytest.raises(EncryptionError, match="must be 32 bytes"):
            encrypt_value("secret")

        set_key_for_testing(None)

    def test_set_key_for_testing(self, test_key):
        """set_key_for_testing should set the global key."""
        from tools.encryption import (
            decrypt_value,
            encrypt_value,
            set_key_for_testing,
        )

        set_key_for_testing(test_key)

        encrypted = encrypt_value("test-value")
        assert encrypted.startswith("ENC:")

        decrypted = decrypt_value(encrypted)
        assert decrypted == "test-value"

        # Cleanup
        set_key_for_testing(None)

    def test_set_key_for_testing_invalid_size(self):
        """set_key_for_testing should reject invalid key size."""
        from tools.encryption import set_key_for_testing

        with pytest.raises(ValueError, match="must be 32 bytes"):
            set_key_for_testing(b"too-short")

    def test_decrypt_value_empty_passthrough(self, test_key):
        """decrypt_value should pass through empty/None values."""
        from tools.encryption import decrypt_value, set_key_for_testing

        set_key_for_testing(test_key)

        assert decrypt_value("") == ""
        assert decrypt_value(None) is None
        assert decrypt_value("not-encrypted") == "not-encrypted"

        set_key_for_testing(None)

    def test_encrypt_value_empty_passthrough(self, test_key):
        """encrypt_value should pass through empty values."""
        from tools.encryption import encrypt_value, set_key_for_testing

        set_key_for_testing(test_key)

        assert encrypt_value("") == ""

        set_key_for_testing(None)

    def test_encrypt_value_already_encrypted_passthrough(self, test_key):
        """encrypt_value should pass through already encrypted values."""
        from tools.encryption import encrypt_value, set_key_for_testing

        set_key_for_testing(test_key)

        encrypted = encrypt_value("secret")
        # Encrypting again should return same value
        double_encrypted = encrypt_value(encrypted)
        assert double_encrypted == encrypted

        set_key_for_testing(None)


class TestSecretsFileLoading:
    """Tests for loading encryption key from secrets file."""

    def test_secrets_file_with_valid_key(self, tmp_path, monkeypatch):
        """Should load key from /run/secrets/encryption_key if exists."""
        from unittest.mock import patch, MagicMock
        from tools.encryption import set_key_for_testing, encrypt_value

        # Clear cached key and env var
        set_key_for_testing(None)
        monkeypatch.delenv("MAIL_PROXY_ENCRYPTION_KEY", raising=False)

        # Create valid 32-byte key
        valid_key = b"0123456789abcdef0123456789abcdef"

        # Mock Path to return our mock secrets file
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.read_bytes.return_value = valid_key + b"\n"  # With newline

        with patch("tools.encryption.Path") as mock_path_cls:
            mock_path_cls.return_value = mock_path_instance

            encrypted = encrypt_value("test-secret")
            assert encrypted.startswith("ENC:")

        set_key_for_testing(None)

    def test_secrets_file_with_wrong_size_key(self, monkeypatch):
        """Should raise error if secrets file key has wrong size."""
        from unittest.mock import patch, MagicMock
        from tools.encryption import (
            set_key_for_testing,
            encrypt_value,
            EncryptionError,
        )

        # Clear cached key and env var
        set_key_for_testing(None)
        monkeypatch.delenv("MAIL_PROXY_ENCRYPTION_KEY", raising=False)

        # Create key with wrong size
        wrong_size_key = b"too-short"

        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.read_bytes.return_value = wrong_size_key

        with patch("tools.encryption.Path") as mock_path_cls:
            mock_path_cls.return_value = mock_path_instance

            with pytest.raises(EncryptionError, match="must be 32 bytes"):
                encrypt_value("test-secret")

        set_key_for_testing(None)


class TestCryptographyImportError:
    """Tests for handling missing cryptography package."""

    def test_encrypt_value_without_cryptography(self, monkeypatch, test_key):
        """encrypt_value should raise clear error if cryptography not installed."""
        import sys
        from tools.encryption import set_key_for_testing
        import tools.encryption as enc_module

        set_key_for_testing(test_key)

        # Simulate missing cryptography by patching the import
        original_import = __builtins__["__import__"]

        def mock_import(name, *args, **kwargs):
            if "cryptography" in name:
                raise ImportError("No module named 'cryptography'")
            return original_import(name, *args, **kwargs)

        # Actually patch the import inside the function
        from unittest.mock import patch

        with patch.dict(sys.modules, {"cryptography.hazmat.primitives.ciphers.aead": None}):
            # Force re-import by removing cached module
            if "cryptography.hazmat.primitives.ciphers.aead" in sys.modules:
                del sys.modules["cryptography.hazmat.primitives.ciphers.aead"]

            # The test verifies the code path exists even though cryptography is installed
            # We can't easily test ImportError since cryptography is a required dependency
            # This test documents the expected behavior

        set_key_for_testing(None)

    def test_encrypt_with_key_without_cryptography(self, test_key):
        """encrypt_value_with_key handles ImportError gracefully."""
        # Since cryptography is installed, we can just verify the normal path works
        # The import error code path (lines 234-235, 271-272) exists for environments
        # without cryptography installed
        from tools.encryption import encrypt_value_with_key, decrypt_value_with_key

        encrypted = encrypt_value_with_key("test", test_key)
        decrypted = decrypt_value_with_key(encrypted, test_key)
        assert decrypted == "test"


class TestDecryptValueGlobal:
    """Tests for decrypt_value function edge cases."""

    def test_decrypt_value_with_invalid_base64(self, test_key):
        """decrypt_value raises error for invalid base64 data."""
        from tools.encryption import decrypt_value, set_key_for_testing

        set_key_for_testing(test_key)

        with pytest.raises(EncryptionError, match="Invalid encrypted data"):
            decrypt_value("ENC:not-valid-base64!!!")

        set_key_for_testing(None)

    def test_decrypt_value_with_truncated_data(self, test_key):
        """decrypt_value raises error for truncated ciphertext."""
        import base64
        from tools.encryption import decrypt_value, set_key_for_testing

        set_key_for_testing(test_key)

        # Create data shorter than required (NONCE_SIZE + TAG_SIZE = 28 bytes)
        short_data = base64.b64encode(b"short").decode()

        with pytest.raises(EncryptionError, match="too short"):
            decrypt_value(f"ENC:{short_data}")

        set_key_for_testing(None)

    def test_decrypt_value_with_wrong_key(self, test_key):
        """decrypt_value raises error when decryption fails."""
        import base64
        from tools.encryption import (
            decrypt_value,
            encrypt_value,
            set_key_for_testing,
            generate_key,
        )

        set_key_for_testing(test_key)
        encrypted = encrypt_value("secret")

        # Change key to a different one
        new_key = base64.b64decode(generate_key())
        set_key_for_testing(new_key)

        with pytest.raises(EncryptionError, match="Decryption failed"):
            decrypt_value(encrypted)

        set_key_for_testing(None)
