"""M3 derived-index domain errors (PLAN-M3 §6.3).

The index is a pure in-memory derived layer; its failure must never affect
Vault reads/writes, health or the editor.  Consumers of metadata/links/search
translate ``IndexUnavailable`` into HTTP 503 ``index_unavailable``.
"""

from __future__ import annotations

from ..vault.errors import VaultError, VaultErrorCode


class IndexUnavailable(VaultError):
    code = VaultErrorCode.INDEX_UNAVAILABLE
    default_message = "Derived index is unavailable"


__all__ = ["IndexUnavailable"]
