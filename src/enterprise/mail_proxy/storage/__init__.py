# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition storage extensions.

Adds cloud storage backends (S3, Azure, GCS) via fsspec.
"""

from .node_ee import StorageNode_EE

__all__ = ["StorageNode_EE"]
