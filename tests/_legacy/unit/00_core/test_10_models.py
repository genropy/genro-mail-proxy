# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for Pydantic models, enums, and dynamic schema generation.

With the new architecture, Pydantic request models are generated dynamically
from endpoint method signatures via BaseEndpoint.create_request_model().

This test file validates:
1. Enum types (AuthMethod, MessageStatus, FetchMode) - defined in endpoint.py
2. Helper models (AttachmentPayload) - defined in endpoint.py
3. Dynamic model generation from endpoint signatures via BaseEndpoint
"""

import pytest
from pydantic import ValidationError

# Enums and helper models are now in endpoint.py files
from core.mail_proxy.entities.message.endpoint import (
    AttachmentPayload,
    FetchMode,
    MessageStatus,
)
from core.mail_proxy.entities.tenant.endpoint import AuthMethod
from core.mail_proxy.endpoint_base import BaseEndpoint


# --- MessageStatus Enum Tests ---

class TestMessageStatus:
    """Tests for MessageStatus enum."""

    def test_all_statuses(self):
        """Test all status values."""
        assert MessageStatus.PENDING.value == "pending"
        assert MessageStatus.DEFERRED.value == "deferred"
        assert MessageStatus.SENT.value == "sent"
        assert MessageStatus.ERROR.value == "error"

    def test_string_enum(self):
        """Test that MessageStatus is a string enum."""
        assert isinstance(MessageStatus.PENDING, str)
        assert MessageStatus.PENDING == "pending"


# --- FetchMode Enum Tests ---

class TestFetchMode:
    """Tests for FetchMode enum."""

    def test_all_modes(self):
        """Test all fetch mode values."""
        assert FetchMode.ENDPOINT.value == "endpoint"
        assert FetchMode.HTTP_URL.value == "http_url"
        assert FetchMode.BASE64.value == "base64"
        assert FetchMode.FILESYSTEM.value == "filesystem"

    def test_string_enum(self):
        """Test that FetchMode is a string enum."""
        assert isinstance(FetchMode.ENDPOINT, str)
        assert FetchMode.ENDPOINT == "endpoint"


# --- AuthMethod Enum Tests ---

class TestAuthMethod:
    """Tests for AuthMethod enum."""

    def test_all_methods(self):
        """Test all auth method values."""
        assert AuthMethod.NONE.value == "none"
        assert AuthMethod.BEARER.value == "bearer"
        assert AuthMethod.BASIC.value == "basic"

    def test_string_enum(self):
        """Test that AuthMethod is a string enum."""
        assert isinstance(AuthMethod.NONE, str)
        assert AuthMethod.NONE == "none"


# --- AttachmentPayload Tests ---

class TestAttachmentPayload:
    """Tests for AttachmentPayload model."""

    def test_minimal_attachment(self):
        """Test minimal attachment."""
        att = AttachmentPayload(filename="doc.pdf", storage_path="/path/doc.pdf")
        assert att.filename == "doc.pdf"
        assert att.storage_path == "/path/doc.pdf"
        assert att.mime_type is None
        assert att.fetch_mode is None
        assert att.content_md5 is None
        assert att.auth is None

    def test_with_mime_type(self):
        """Test attachment with explicit MIME type."""
        att = AttachmentPayload(
            filename="data.bin",
            storage_path="/path/data.bin",
            mime_type="application/octet-stream"
        )
        assert att.mime_type == "application/octet-stream"

    def test_with_fetch_mode(self):
        """Test attachment with explicit fetch mode."""
        att = AttachmentPayload(
            filename="doc.pdf",
            storage_path="doc_id=123",
            fetch_mode=FetchMode.ENDPOINT
        )
        assert att.fetch_mode == FetchMode.ENDPOINT

    def test_with_md5(self):
        """Test attachment with MD5 hash."""
        att = AttachmentPayload(
            filename="doc.pdf",
            storage_path="/path/doc.pdf",
            content_md5="d41d8cd98f00b204e9800998ecf8427e"
        )
        assert att.content_md5 == "d41d8cd98f00b204e9800998ecf8427e"

    def test_md5_pattern_validation(self):
        """Test that MD5 must be valid 32-char hex."""
        with pytest.raises(ValidationError):
            AttachmentPayload(
                filename="doc.pdf",
                storage_path="/path",
                content_md5="invalid"
            )

        with pytest.raises(ValidationError):
            AttachmentPayload(
                filename="doc.pdf",
                storage_path="/path",
                content_md5="d41d8cd98f00b204e9800998ecf8427"  # 31 chars
            )

    def test_filename_required(self):
        """Test that filename is required."""
        with pytest.raises(ValidationError):
            AttachmentPayload(storage_path="/path")

    def test_storage_path_required(self):
        """Test that storage_path is required."""
        with pytest.raises(ValidationError):
            AttachmentPayload(filename="file.txt")

    def test_extra_fields_forbidden(self):
        """Test that extra fields are not allowed."""
        with pytest.raises(ValidationError):
            AttachmentPayload(
                filename="doc.pdf",
                storage_path="/path",
                extra_field="value"
            )


# --- BaseEndpoint Dynamic Schema Generation Tests ---

class TestBaseEndpointSchemaGeneration:
    """Tests for dynamic Pydantic model generation from endpoint signatures."""

    def test_create_request_model_simple(self):
        """Test creating a model from a simple method signature."""
        class SimpleEndpoint(BaseEndpoint):
            name = "simple"

            async def get(self, item_id: str) -> dict:
                return {}

        endpoint = SimpleEndpoint(table=None)
        model = endpoint.create_request_model("get")

        # Model should have item_id field
        assert "item_id" in model.model_fields
        assert model.model_fields["item_id"].annotation == str

    def test_create_request_model_with_defaults(self):
        """Test creating a model with default values."""
        class DefaultsEndpoint(BaseEndpoint):
            name = "defaults"

            async def list(self, active_only: bool = False, limit: int = 100) -> list:
                return []

        endpoint = DefaultsEndpoint(table=None)
        model = endpoint.create_request_model("list")

        # Model should have fields with defaults
        assert "active_only" in model.model_fields
        assert "limit" in model.model_fields
        assert model.model_fields["active_only"].default is False
        assert model.model_fields["limit"].default == 100

    def test_create_request_model_optional_types(self):
        """Test creating a model with optional types."""
        class OptionalEndpoint(BaseEndpoint):
            name = "optional"

            async def add(self, id: str, name: str | None = None) -> dict:
                return {}

        endpoint = OptionalEndpoint(table=None)
        model = endpoint.create_request_model("add")

        # Model should have both fields
        assert "id" in model.model_fields
        assert "name" in model.model_fields

    def test_get_http_method_from_name(self):
        """Test HTTP method inference from method names."""
        class TestEndpoint(BaseEndpoint):
            name = "test"

            async def add(self): pass
            async def create(self): pass
            async def get(self): pass
            async def list(self): pass
            async def delete(self): pass
            async def update(self): pass
            async def run_now(self): pass

        endpoint = TestEndpoint(table=None)

        assert endpoint.get_http_method("add") == "POST"
        assert endpoint.get_http_method("create") == "POST"
        assert endpoint.get_http_method("get") == "GET"
        assert endpoint.get_http_method("list") == "GET"
        assert endpoint.get_http_method("delete") == "DELETE"
        assert endpoint.get_http_method("update") == "PATCH"
        assert endpoint.get_http_method("run_now") == "POST"

    def test_get_methods_returns_public_async_only(self):
        """Test that get_methods returns only public async methods."""
        class MixedEndpoint(BaseEndpoint):
            name = "mixed"

            async def public_async(self): pass
            def public_sync(self): pass
            async def _private_async(self): pass
            def _private_sync(self): pass

        endpoint = MixedEndpoint(table=None)
        methods = endpoint.get_methods()
        method_names = [name for name, _ in methods]

        assert "public_async" in method_names
        assert "public_sync" not in method_names
        assert "_private_async" not in method_names
        assert "_private_sync" not in method_names

    def test_count_params(self):
        """Test parameter counting."""
        class ParamsEndpoint(BaseEndpoint):
            name = "params"

            async def no_params(self) -> dict:
                return {}

            async def one_param(self, id: str) -> dict:
                return {}

            async def many_params(self, id: str, name: str, active: bool = True) -> dict:
                return {}

        endpoint = ParamsEndpoint(table=None)

        assert endpoint.count_params("no_params") == 0
        assert endpoint.count_params("one_param") == 1
        assert endpoint.count_params("many_params") == 3

    def test_is_simple_params(self):
        """Test simple params detection (suitable for query string)."""
        class SimpleParamsEndpoint(BaseEndpoint):
            name = "simple_params"

            async def simple(self, id: str, count: int = 10) -> dict:
                return {}

            async def complex(self, data: dict, items: list) -> dict:
                return {}

        endpoint = SimpleParamsEndpoint(table=None)

        assert endpoint.is_simple_params("simple") is True
        assert endpoint.is_simple_params("complex") is False
