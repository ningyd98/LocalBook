"""
ReadFlow Reader Module

LocalBook server-side infrastructure for reading collection and sync.
"""

from server import __version__

from .router import router

__all__ = ["router", "__version__"]
