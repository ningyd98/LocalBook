"""M1 Vault core: safe root-relative bytes access and lifecycle primitives."""

from .errors import VaultError, VaultErrorCode
from .service import VaultService, sha256_bytes

__all__ = ["VaultError", "VaultErrorCode", "VaultService", "sha256_bytes"]
