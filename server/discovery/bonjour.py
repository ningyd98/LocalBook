"""
Bonjour/mDNS service discovery for LocalBook.

Advertises LocalBook server on the local network so ReadFlow clients
can discover it automatically without manual IP configuration.
"""

import logging
import socket

from zeroconf import ServiceInfo, Zeroconf

from server import __version__

logger = logging.getLogger(__name__)


class BonjourService:
    """
    Broadcast LocalBook service via Bonjour/mDNS.

    Service type: _localbook._tcp.local.

    Advertised properties:
    - version: LocalBook version
    - reader_api: Reader API version (v1)
    - port: HTTP server port

    Usage:
        service = BonjourService()
        service.start(port=3780)
        # ... server running ...
        service.stop()
    """

    def __init__(self):
        self.zeroconf: Zeroconf | None = None
        self.service_info: ServiceInfo | None = None
        self._started = False

    def start(self, port: int, version: str = __version__) -> None:
        """
        Start advertising the LocalBook service.

        Args:
            port: HTTP server port
            version: LocalBook version string

        Raises:
            RuntimeError: If service is already started
        """
        if self._started:
            logger.warning("BonjourService already started, ignoring start() call")
            return

        try:
            hostname = socket.gethostname()
            service_name = f"LocalBook on {hostname}._localbook._tcp.local."

            # Get all local IP addresses
            addresses = self._get_local_addresses()

            if not addresses:
                logger.warning("No local IP addresses found, using 0.0.0.0")
                addresses = [socket.inet_aton("0.0.0.0")]

            self.service_info = ServiceInfo(
                type_="_localbook._tcp.local.",
                name=service_name,
                addresses=addresses,
                port=port,
                properties={
                    b"version": version.encode("utf-8"),
                    b"reader_api": b"v1",
                    b"port": str(port).encode("utf-8"),
                },
                server=f"{hostname}.local.",
            )

            self.zeroconf = Zeroconf()
            self.zeroconf.register_service(self.service_info)

            self._started = True
            logger.info(
                f"Bonjour service started: {service_name} on port {port} "
                f"with {len(addresses)} address(es)"
            )

        except Exception as e:
            logger.error(f"Failed to start Bonjour service: {e}")
            # Clean up on failure
            if self.zeroconf:
                try:
                    self.zeroconf.close()
                except Exception:
                    pass
                self.zeroconf = None
            self.service_info = None
            raise

    def stop(self) -> None:
        """
        Stop advertising the LocalBook service.

        Safe to call multiple times.
        """
        if not self._started:
            return

        try:
            if self.zeroconf and self.service_info:
                logger.info("Stopping Bonjour service...")
                self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.close()
                logger.info("Bonjour service stopped")
        except Exception as e:
            logger.error(f"Error stopping Bonjour service: {e}")
        finally:
            self.zeroconf = None
            self.service_info = None
            self._started = False

    def _get_local_addresses(self) -> list[bytes]:
        """
        Get all local IP addresses (excluding loopback).

        Returns:
            List of IP addresses in binary format
        """
        addresses = []

        try:
            # Get hostname and resolve to addresses
            hostname = socket.gethostname()

            # Try to get all addresses associated with this host
            try:
                addr_info = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
                for info in addr_info:
                    addr = info[4][0]
                    if not addr.startswith("127."):
                        addresses.append(socket.inet_aton(addr))
            except socket.gaierror:
                pass

            # Fallback: try to connect to external address to find local IP
            if not addresses:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    local_ip = s.getsockname()[0]
                    s.close()
                    if not local_ip.startswith("127."):
                        addresses.append(socket.inet_aton(local_ip))
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"Could not determine local addresses: {e}")

        return addresses

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup."""
        self.stop()
        return False
