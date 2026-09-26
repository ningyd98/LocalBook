"""
Discovery service for LocalBook.

Provides Bonjour/mDNS service advertisement for local network discovery.
"""

from server.discovery.bonjour import BonjourService

__all__ = ["BonjourService"]
