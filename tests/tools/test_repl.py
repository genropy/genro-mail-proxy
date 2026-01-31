# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for REPL protection utilities."""

import pytest

from tools.repl import RESERVED_ATTR, is_reserved, repl_wrap, reserved


class TestReservedDecorator:
    """Tests for @reserved decorator."""

    def test_marks_method_as_reserved(self):
        """@reserved sets _reserved attribute on method."""

        class MyClass:
            @reserved
            def secret_method(self):
                return "secret"

        assert hasattr(MyClass.secret_method, RESERVED_ATTR)
        assert getattr(MyClass.secret_method, RESERVED_ATTR) is True

    def test_method_still_callable(self):
        """@reserved doesn't prevent direct method calls."""

        class MyClass:
            @reserved
            def secret_method(self):
                return "secret"

        obj = MyClass()
        assert obj.secret_method() == "secret"

    def test_is_reserved_true_for_decorated(self):
        """is_reserved() returns True for @reserved methods."""

        class MyClass:
            @reserved
            def secret_method(self):
                return "secret"

        assert is_reserved(MyClass.secret_method) is True

    def test_is_reserved_false_for_normal(self):
        """is_reserved() returns False for normal methods."""

        class MyClass:
            def normal_method(self):
                return "normal"

        assert is_reserved(MyClass.normal_method) is False


class TestREPLWrapper:
    """Tests for repl_wrap() and REPLWrapper."""

    def test_allows_normal_method_access(self):
        """Wrapped object allows access to normal methods."""

        class MyClass:
            def normal_method(self):
                return "normal"

        obj = MyClass()
        wrapped = repl_wrap(obj)
        assert wrapped.normal_method() == "normal"

    def test_blocks_reserved_method_access(self):
        """Wrapped object blocks access to @reserved methods."""

        class MyClass:
            @reserved
            def secret_method(self):
                return "secret"

        obj = MyClass()
        wrapped = repl_wrap(obj)

        with pytest.raises(AttributeError, match="reserved"):
            wrapped.secret_method()

    def test_allows_normal_attribute_access(self):
        """Wrapped object allows access to normal attributes."""

        class MyClass:
            def __init__(self):
                self.name = "test"

        obj = MyClass()
        wrapped = repl_wrap(obj)
        assert wrapped.name == "test"

    def test_allows_attribute_setting(self):
        """Wrapped object allows setting attributes."""

        class MyClass:
            def __init__(self):
                self.name = "test"

        obj = MyClass()
        wrapped = repl_wrap(obj)
        wrapped.name = "new_name"
        assert obj.name == "new_name"

    def test_dir_excludes_reserved(self):
        """dir() on wrapped object excludes @reserved methods."""

        class MyClass:
            @reserved
            def secret_method(self):
                return "secret"

            def normal_method(self):
                return "normal"

        obj = MyClass()
        wrapped = repl_wrap(obj)
        dir_result = dir(wrapped)

        assert "normal_method" in dir_result
        assert "secret_method" not in dir_result

    def test_repr_passes_through(self):
        """repr() on wrapped object uses original __repr__."""

        class MyClass:
            def __repr__(self):
                return "<MyClass instance>"

        obj = MyClass()
        wrapped = repl_wrap(obj)
        assert repr(wrapped) == "<MyClass instance>"

    def test_str_passes_through(self):
        """str() on wrapped object uses original __str__."""

        class MyClass:
            def __str__(self):
                return "MyClass string"

        obj = MyClass()
        wrapped = repl_wrap(obj)
        assert str(wrapped) == "MyClass string"

    def test_nested_object_also_wrapped(self):
        """Nested objects returned by wrapper are also wrapped."""

        class Inner:
            @reserved
            def inner_secret(self):
                return "inner secret"

            def inner_normal(self):
                return "inner normal"

        class Outer:
            def __init__(self):
                self.inner = Inner()

        obj = Outer()
        wrapped = repl_wrap(obj)

        # Normal method works
        assert wrapped.inner.inner_normal() == "inner normal"

        # Reserved method blocked
        with pytest.raises(AttributeError, match="reserved"):
            wrapped.inner.inner_secret()

    def test_mixed_reserved_and_normal(self):
        """Class with both reserved and normal methods works correctly."""

        class MyClass:
            @reserved
            def get_password(self):
                return "secret123"

            @reserved
            def get_api_key(self):
                return "key-xxx"

            def get_name(self):
                return "MyService"

            def get_status(self):
                return "running"

        obj = MyClass()
        wrapped = repl_wrap(obj)

        # Normal methods work
        assert wrapped.get_name() == "MyService"
        assert wrapped.get_status() == "running"

        # Reserved methods blocked
        with pytest.raises(AttributeError):
            wrapped.get_password()
        with pytest.raises(AttributeError):
            wrapped.get_api_key()

        # dir() shows only normal methods
        dir_result = dir(wrapped)
        assert "get_name" in dir_result
        assert "get_status" in dir_result
        assert "get_password" not in dir_result
        assert "get_api_key" not in dir_result
