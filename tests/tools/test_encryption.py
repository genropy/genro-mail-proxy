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
