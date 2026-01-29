# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Bounce detection module."""

from .parser import BounceInfo, BounceParser
from .receiver import BounceConfig, BounceReceiver

__all__ = ["BounceConfig", "BounceInfo", "BounceParser", "BounceReceiver"]
