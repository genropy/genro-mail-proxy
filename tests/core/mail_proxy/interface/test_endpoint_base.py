# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for interface.endpoint_base module."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.mail_proxy.interface.endpoint_base import (
    POST,
    BaseEndpoint,
    EndpointDispatcher,
)


class MockTable:
    """Mock table for testing."""

    async def list_all(self, **kwargs):
        return [{"id": "1"}]

    async def get(self, pk: str):
        return {"id": pk, "name": "test"}


class TestPOSTDecorator:
    """Tests for POST decorator."""

    def test_marks_method_as_post(self):
        """POST decorator should set _http_post attribute."""

        @POST
        async def my_method():
            pass

        assert hasattr(my_method, "_http_post")
        assert my_method._http_post is True

    def test_preserves_function(self):
        """POST decorator should not change function behavior."""

        @POST
        async def my_method(x: int) -> int:
            return x * 2

        assert my_method.__name__ == "my_method"
        assert inspect.iscoroutinefunction(my_method)


class SampleEndpoint(BaseEndpoint):
    """Sample endpoint for testing."""

    name = "samples"

    async def list(self, active_only: bool = False) -> list[dict]:
        """List all samples."""
        return await self.table.list_all(active_only=active_only)

    async def get(self, sample_id: str) -> dict:
        """Get a sample by ID."""
        return await self.table.get(sample_id)

    @POST
    async def add(self, id: str, name: str, data: dict | None = None) -> dict:
        """Add a new sample."""
        return {"id": id, "name": name}

    async def complex_params(
        self, items: list[str], config: dict[str, Any] | None = None
    ) -> dict:
        """Method with complex parameters."""
        return {"items": items}

    def _private_method(self):
        """Private method should not be discovered."""
        pass

    def sync_method(self):
        """Sync method should not be discovered."""
        pass


class TestBaseEndpoint:
    """Tests for BaseEndpoint class."""

    @pytest.fixture
    def endpoint(self):
        """Create a sample endpoint."""
        table = MockTable()
        return SampleEndpoint(table)

    def test_init_stores_table(self, endpoint):
        """Should store table reference."""
        assert endpoint.table is not None

    def test_name_attribute(self, endpoint):
        """Should have name attribute."""
        assert endpoint.name == "samples"

    def test_get_methods_returns_async_public_methods(self, endpoint):
        """get_methods should return only public async methods."""
        methods = endpoint.get_methods()
        method_names = [name for name, _ in methods]

        assert "list" in method_names
        assert "get" in method_names
        assert "add" in method_names
        assert "complex_params" in method_names

        # Private and sync methods should not be included
        assert "_private_method" not in method_names
        assert "sync_method" not in method_names

    def test_get_http_method_returns_get_by_default(self, endpoint):
        """Methods without @POST should return GET."""
        assert endpoint.get_http_method("list") == "GET"
        assert endpoint.get_http_method("get") == "GET"

    def test_get_http_method_returns_post_for_decorated(self, endpoint):
        """Methods with @POST should return POST."""
        assert endpoint.get_http_method("add") == "POST"

    def test_create_request_model_creates_pydantic_model(self, endpoint):
        """create_request_model should create a valid Pydantic model."""
        model = endpoint.create_request_model("add")

        assert model.__name__ == "AddRequest"
        # Check that model has expected fields
        fields = model.model_fields
        assert "id" in fields
        assert "name" in fields
        assert "data" in fields

    def test_create_request_model_required_vs_optional(self, endpoint):
        """Required params should have no default, optional should have default."""
        model = endpoint.create_request_model("add")
        fields = model.model_fields

        # id and name are required (no default)
        assert fields["id"].is_required()
        assert fields["name"].is_required()

        # data has default (Optional[dict])
        assert not fields["data"].is_required()

    def test_is_simple_params_true_for_primitives(self, endpoint):
        """Methods with only primitive params should be simple."""
        assert endpoint.is_simple_params("list") is True
        assert endpoint.is_simple_params("get") is True

    def test_is_simple_params_false_for_complex_types(self, endpoint):
        """Methods with list/dict params should not be simple."""
        assert endpoint.is_simple_params("add") is False  # has dict param
        assert endpoint.is_simple_params("complex_params") is False

    def test_count_params(self, endpoint):
        """count_params should return correct count excluding self."""
        assert endpoint.count_params("list") == 1  # active_only
        assert endpoint.count_params("get") == 1  # sample_id
        assert endpoint.count_params("add") == 3  # id, name, data

    def test_discover_finds_endpoints(self):
        """discover should find endpoint classes from packages."""
        # This test verifies the discovery mechanism works
        endpoints = BaseEndpoint.discover()

        # Should find at least some endpoints
        assert len(endpoints) > 0

        # All should be subclasses of BaseEndpoint (or composed)
        for endpoint_class in endpoints:
            assert hasattr(endpoint_class, "name")
            assert hasattr(endpoint_class, "get_methods")


class TestEndpointDispatcher:
    """Tests for EndpointDispatcher class."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        # Configure mock tables
        mock_table = MagicMock()
        db.table.return_value = mock_table
        return db

    @pytest.fixture
    def dispatcher(self, mock_db):
        """Create a dispatcher with mock db."""
        return EndpointDispatcher(mock_db)

    def test_command_map_has_expected_commands(self):
        """COMMAND_MAP should have expected legacy commands."""
        assert "addMessages" in EndpointDispatcher.COMMAND_MAP
        assert "listTenants" in EndpointDispatcher.COMMAND_MAP
        assert "addAccount" in EndpointDispatcher.COMMAND_MAP

    @pytest.mark.asyncio
    async def test_dispatch_unknown_command_returns_error(self, dispatcher):
        """Unknown command should return error."""
        result = await dispatcher.dispatch("unknownCommand", {})

        assert result["ok"] is False
        assert "unknown command" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatch_routes_to_endpoint(self, mock_db):
        """dispatch should route to correct endpoint method."""
        # Create dispatcher with real endpoints
        from core.mail_proxy.entities.tenant import TenantEndpoint

        dispatcher = EndpointDispatcher(mock_db)

        # Mock the tenant table
        mock_table = MagicMock()
        mock_table.list_all = AsyncMock(return_value=[{"id": "t1", "name": "Test"}])
        mock_db.table.return_value = mock_table

        result = await dispatcher.dispatch("listTenants", {})

        assert result["ok"] is True
        assert "tenants" in result

    @pytest.mark.asyncio
    async def test_dispatch_validates_payload(self, dispatcher):
        """dispatch should validate payload before routing."""
        # updateTenant requires id
        result = await dispatcher.dispatch("updateTenant", {})

        assert result["ok"] is False
        assert "tenant id required" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatch_maps_legacy_keys(self, mock_db):
        """dispatch should map legacy payload keys."""
        from core.mail_proxy.entities.tenant import TenantEndpoint

        dispatcher = EndpointDispatcher(mock_db)

        # Mock tenant get
        mock_table = MagicMock()
        mock_table.get = AsyncMock(return_value={"id": "t1", "name": "Test"})
        mock_db.table.return_value = mock_table

        result = await dispatcher.dispatch("getTenant", {"id": "t1"})

        # Should have mapped "id" to "tenant_id"
        mock_table.get.assert_called()

    def test_get_endpoint_returns_endpoint(self, mock_db):
        """get_endpoint should return endpoint instance."""
        dispatcher = EndpointDispatcher(mock_db)

        endpoint = dispatcher.get_endpoint("tenants")
        assert endpoint is not None
        assert hasattr(endpoint, "list")

    def test_get_endpoint_caches_instances(self, mock_db):
        """get_endpoint should cache endpoint instances."""
        dispatcher = EndpointDispatcher(mock_db)

        endpoint1 = dispatcher.get_endpoint("tenants")
        endpoint2 = dispatcher.get_endpoint("tenants")

        assert endpoint1 is endpoint2

    def test_get_endpoint_unknown_raises(self, mock_db):
        """get_endpoint with unknown name should raise."""
        dispatcher = EndpointDispatcher(mock_db)

        with pytest.raises(ValueError, match="Unknown endpoint"):
            dispatcher.get_endpoint("nonexistent")

    @pytest.mark.asyncio
    async def test_wrap_result_list(self, dispatcher):
        """_wrap_result should wrap lists with key."""
        result = dispatcher._wrap_result("listTenants", [{"id": "t1"}])

        assert result["ok"] is True
        assert "tenants" in result
        assert result["tenants"] == [{"id": "t1"}]

    @pytest.mark.asyncio
    async def test_wrap_result_bool_true(self, dispatcher):
        """_wrap_result should wrap True as ok."""
        result = dispatcher._wrap_result("deleteTenant", True)

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_wrap_result_bool_false(self, dispatcher):
        """_wrap_result should wrap False as error."""
        result = dispatcher._wrap_result("deleteTenant", False)

        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_wrap_result_none(self, dispatcher):
        """_wrap_result should wrap None as not found."""
        result = dispatcher._wrap_result("getTenant", None)

        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_wrap_result_dict_preserves_ok(self, dispatcher):
        """_wrap_result should preserve ok in dict."""
        result = dispatcher._wrap_result("addTenant", {"id": "t1", "ok": True})

        assert result["ok"] is True
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_wrap_result_dict_adds_ok(self, dispatcher):
        """_wrap_result should add ok to dict if missing."""
        result = dispatcher._wrap_result("addTenant", {"id": "t1"})

        assert result["ok"] is True
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_dispatch_handles_value_error(self, mock_db):
        """dispatch should catch ValueError and return error."""
        dispatcher = EndpointDispatcher(mock_db)

        # Mock to raise ValueError
        mock_table = MagicMock()
        mock_table.list_all = AsyncMock(side_effect=ValueError("test error"))
        mock_db.table.return_value = mock_table

        result = await dispatcher.dispatch("listTenants", {})

        assert result["ok"] is False
        assert "test error" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatch_handles_generic_exception(self, mock_db):
        """dispatch should catch generic exceptions."""
        dispatcher = EndpointDispatcher(mock_db)

        # Mock to raise generic exception
        mock_table = MagicMock()
        mock_table.list_all = AsyncMock(side_effect=RuntimeError("unexpected"))
        mock_db.table.return_value = mock_table

        result = await dispatcher.dispatch("listTenants", {})

        assert result["ok"] is False
        assert "Internal error" in result["error"]


class TestComplexTypeDetection:
    """Tests for _is_complex_type method."""

    @pytest.fixture
    def endpoint(self):
        """Create endpoint for testing."""
        return SampleEndpoint(MockTable())

    def test_list_is_complex(self, endpoint):
        """list type should be detected as complex."""
        assert endpoint._is_complex_type(list) is True
        assert endpoint._is_complex_type(list[str]) is True

    def test_dict_is_complex(self, endpoint):
        """dict type should be detected as complex."""
        assert endpoint._is_complex_type(dict) is True
        assert endpoint._is_complex_type(dict[str, Any]) is True

    def test_primitives_are_simple(self, endpoint):
        """Primitive types should not be complex."""
        assert endpoint._is_complex_type(str) is False
        assert endpoint._is_complex_type(int) is False
        assert endpoint._is_complex_type(bool) is False

    def test_optional_list_is_complex(self, endpoint):
        """Optional[list] should be detected as complex."""
        from typing import Optional

        assert endpoint._is_complex_type(Optional[list[str]]) is True
        assert endpoint._is_complex_type(list[str] | None) is True

    def test_optional_primitive_is_simple(self, endpoint):
        """Optional[str] should not be complex."""
        from typing import Optional

        assert endpoint._is_complex_type(Optional[str]) is False
        assert endpoint._is_complex_type(str | None) is False


class TestCreateRequestModelEdgeCases:
    """Tests for create_request_model edge cases."""

    def test_create_request_model_without_type_hints(self):
        """create_request_model should handle methods without type hints."""

        class NoHintsEndpoint(BaseEndpoint):
            name = "nohints"

            async def simple_method(self, param1, param2=None):
                return {"ok": True}

        endpoint = NoHintsEndpoint(MockTable())
        model = endpoint.create_request_model("simple_method")

        # Should create model even without type hints
        assert model is not None
        assert "param1" in model.model_fields
        assert "param2" in model.model_fields


class TestIsSimpleParamsEdgeCases:
    """Tests for is_simple_params edge cases."""

    def test_is_simple_params_without_type_hints(self):
        """is_simple_params should handle methods without type hints."""

        class NoHintsEndpoint(BaseEndpoint):
            name = "nohints"

            async def method_no_hints(self, param1, param2):
                return {"ok": True}

        endpoint = NoHintsEndpoint(MockTable())
        # Methods without hints are considered simple
        assert endpoint.is_simple_params("method_no_hints") is True


class TestEndpointDiscoveryFilters:
    """Tests for endpoint discovery filtering logic."""

    def test_get_class_from_module_filters_base_classes(self):
        """_get_class_from_module should filter out BaseEndpoint."""
        # Create a mock module with BaseEndpoint
        mock_module = type('MockModule', (), {})()
        mock_module.BaseEndpoint = BaseEndpoint

        result = BaseEndpoint._get_class_from_module(mock_module, "Endpoint")
        assert result is None

    def test_get_class_from_module_filters_private_classes(self):
        """_get_class_from_module should filter out private classes."""
        mock_module = type('MockModule', (), {})()

        class _PrivateEndpoint(BaseEndpoint):
            name = "private"

        mock_module._PrivateEndpoint = _PrivateEndpoint

        result = BaseEndpoint._get_class_from_module(mock_module, "Endpoint")
        assert result is None

    def test_get_class_from_module_filters_ee_classes(self):
        """_get_class_from_module should filter out _EE classes."""
        mock_module = type('MockModule', (), {})()

        class TestEndpoint_EE:
            name = "test"

        mock_module.TestEndpoint_EE = TestEndpoint_EE

        result = BaseEndpoint._get_class_from_module(mock_module, "Endpoint")
        assert result is None

    def test_get_class_from_module_filters_classes_without_name(self):
        """_get_class_from_module should filter out classes without name attr."""
        mock_module = type('MockModule', (), {})()

        class NoNameEndpoint:
            pass  # No name attribute

        mock_module.NoNameEndpoint = NoNameEndpoint

        result = BaseEndpoint._get_class_from_module(mock_module, "Endpoint")
        assert result is None

    def test_get_ee_mixin_from_module_returns_mixin(self):
        """_get_ee_mixin_from_module should find _EE mixin."""
        mock_module = type('MockModule', (), {})()

        class MyEndpoint_EE:
            pass

        mock_module.MyEndpoint_EE = MyEndpoint_EE

        result = BaseEndpoint._get_ee_mixin_from_module(mock_module, "_EE")
        assert result is MyEndpoint_EE

    def test_get_ee_mixin_from_module_filters_private(self):
        """_get_ee_mixin_from_module should filter private classes."""
        mock_module = type('MockModule', (), {})()

        class _PrivateEndpoint_EE:
            pass

        mock_module._PrivateEndpoint_EE = _PrivateEndpoint_EE

        result = BaseEndpoint._get_ee_mixin_from_module(mock_module, "_EE")
        assert result is None

    def test_get_ee_mixin_from_module_returns_none_when_not_found(self):
        """_get_ee_mixin_from_module should return None if no mixin."""
        mock_module = type('MockModule', (), {})()

        class SomeClass:
            pass

        mock_module.SomeClass = SomeClass

        result = BaseEndpoint._get_ee_mixin_from_module(mock_module, "_EE")
        assert result is None


class TestDispatcherMapPayload:
    """Tests for _map_payload edge cases."""

    @pytest.fixture
    def dispatcher(self):
        mock_db = MagicMock()
        return EndpointDispatcher(mock_db)

    def test_map_payload_delete_account_id(self, dispatcher):
        """_map_payload should map id to account_id for deleteAccount."""
        payload = {"id": "acc1", "tenant_id": "t1"}
        result = dispatcher._map_payload("deleteAccount", payload)

        assert "account_id" in result
        assert result["account_id"] == "acc1"
        assert "id" not in result

    def test_map_payload_list_messages_defaults(self, dispatcher):
        """_map_payload should add defaults for listMessages."""
        payload = {"tenant_id": "t1"}
        result = dispatcher._map_payload("listMessages", payload)

        assert result["active_only"] is False
        assert result["include_history"] is False

    def test_map_payload_preserves_existing_keys(self, dispatcher):
        """_map_payload should preserve existing keys."""
        payload = {"tenant_id": "t1", "extra": "value"}
        result = dispatcher._map_payload("listTenants", payload)

        assert result["tenant_id"] == "t1"
        assert result["extra"] == "value"


class TestDispatcherWrapResultEdgeCases:
    """Tests for _wrap_result edge cases."""

    @pytest.fixture
    def dispatcher(self):
        mock_db = MagicMock()
        return EndpointDispatcher(mock_db)

    def test_wrap_result_scalar_value(self, dispatcher):
        """_wrap_result should wrap scalar values."""
        result = dispatcher._wrap_result("someCommand", "scalar_value")

        assert result["ok"] is True
        assert result["value"] == "scalar_value"

    def test_wrap_result_list_default_key(self, dispatcher):
        """_wrap_result should use 'items' as default key for unlisted commands."""
        result = dispatcher._wrap_result("unknownListCommand", [{"id": "1"}])

        assert result["ok"] is True
        assert "items" in result
        assert result["items"] == [{"id": "1"}]


class TestDispatcherCreateEndpoint:
    """Tests for _create_endpoint."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.table.return_value = MagicMock()
        return db

    def test_create_endpoint_messages(self, mock_db):
        """_create_endpoint should create MessageEndpoint."""
        from core.mail_proxy.entities.message import MessageEndpoint

        dispatcher = EndpointDispatcher(mock_db)
        endpoint = dispatcher._create_endpoint("messages")

        assert isinstance(endpoint, MessageEndpoint)

    def test_create_endpoint_accounts(self, mock_db):
        """_create_endpoint should create AccountEndpoint."""
        from core.mail_proxy.entities.account import AccountEndpoint

        dispatcher = EndpointDispatcher(mock_db)
        endpoint = dispatcher._create_endpoint("accounts")

        assert isinstance(endpoint, AccountEndpoint)

    def test_create_endpoint_tenants(self, mock_db):
        """_create_endpoint should create TenantEndpoint."""
        from core.mail_proxy.entities.tenant import TenantEndpoint

        dispatcher = EndpointDispatcher(mock_db)
        endpoint = dispatcher._create_endpoint("tenants")

        assert isinstance(endpoint, TenantEndpoint)

    def test_create_endpoint_instance(self, mock_db):
        """_create_endpoint should create InstanceEndpoint with proxy."""
        from core.mail_proxy.entities.instance import InstanceEndpoint

        mock_proxy = MagicMock()
        dispatcher = EndpointDispatcher(mock_db, proxy=mock_proxy)
        endpoint = dispatcher._create_endpoint("instance")

        assert isinstance(endpoint, InstanceEndpoint)
