# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""PEC (Posta Elettronica Certificata) receipt handling."""

from .parser import PecReceiptInfo, PecReceiptParser
from .receiver import PecReceiver

__all__ = ["PecReceiptInfo", "PecReceiptParser", "PecReceiver"]
