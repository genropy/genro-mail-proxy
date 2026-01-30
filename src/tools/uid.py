# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unique identifier generation utilities.

Provides sortable, URL-safe unique identifiers suitable for database
primary keys, session IDs, and other uses across distributed systems.

Copied from genro-toolbox for standalone use in mail-proxy.
"""

import os
import time

# Base62 alphabet (sortable: 0-9, A-Z, a-z)
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

# Epoch: 2025-01-01 00:00:00 UTC in microseconds
_EPOCH_US = 1735689600_000_000


def get_uuid() -> str:
    """Generate a 22-character sortable unique identifier.

    Format: Z + 9 chars timestamp + 12 chars random = 22 chars

    The ID consists of:
    - 'Z': Version marker (distinguishes from legacy UUIDs, sorts after them)
    - 9 characters: microseconds since 2025-01-01 UTC (base62 encoded)
    - 12 characters: cryptographically secure random (base62 encoded)

    Properties:
    - Lexicographically sortable by creation time (UTC)
    - URL-safe (alphanumeric only)
    - 22 characters (compatible with legacy Genro ID columns)
    - IDs starting with 'Z' are new format, others are legacy
    - Timestamp valid for ~440 years from 2025
    - Collision probability ~10^-19 for same microsecond

    Returns:
        22-character string suitable for primary keys, session IDs, etc.

    Example:
        >>> uid = get_uuid()
        >>> len(uid)
        22
        >>> uid[0]
        'Z'
        >>> uid.isalnum()
        True
    """
    # Timestamp part: 9 chars in base62 (µs since 2025-01-01 UTC)
    ts = int(time.time() * 1_000_000) - _EPOCH_US
    ts_part = ""
    for _ in range(9):
        ts_part = _ALPHABET[ts % 62] + ts_part
        ts //= 62

    # Random part: 12 chars in base62 (~71 bits entropy)
    rand_bytes = os.urandom(9)  # 72 bits
    val = int.from_bytes(rand_bytes, "big")
    rand_part = ""
    for _ in range(12):
        rand_part += _ALPHABET[val % 62]
        val //= 62

    return "Z" + ts_part + rand_part


__all__ = ["get_uuid"]
