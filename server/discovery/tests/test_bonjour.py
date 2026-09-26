"""
Tests for Bonjour/mDNS service discovery.
"""

import socket
import time
from unittest.mock import MagicMock, patch

import pytest

from server.discovery.bonjour import BonjourService


class TestBonjourService:
    """Test BonjourService functionality."""

    def test_init(self):
        """Test service initialization."""
        service = BonjourService()
        assert service.zeroconf is None
        assert service.service_info is None
        assert service._started is False

    @patch("server.discovery.bonjour.Zeroconf")
    @patch("server.discovery.bonjour.ServiceInfo")
    def test_start_basic(self, mock_service_info, mock_zeroconf):
        """Test starting the service."""
        service = BonjourService()

        mock_zc_instance = MagicMock()
        mock_zeroconf.return_value = mock_zc_instance

        mock_si_instance = MagicMock()
        mock_service_info.return_value = mock_si_instance

        service.start(port=3780, version="1.3.0")

        assert service._started is True
        assert service.zeroconf is mock_zc_instance
        assert service.service_info is mock_si_instance

        # Verify ServiceInfo was created with correct parameters
        mock_service_info.assert_called_once()
        call_kwargs = mock_service_info.call_args[1]
        assert call_kwargs["type_"] == "_localbook._tcp.local."
        assert call_kwargs["port"] == 3780
        assert b"version" in call_kwargs["properties"]
        assert call_kwargs["properties"][b"version"] == b"1.3.0"
        assert call_kwargs["properties"][b"reader_api"] == b"v1"
        assert call_kwargs["properties"][b"port"] == b"3780"

        # Verify service was registered
        mock_zc_instance.register_service.assert_called_once_with(mock_si_instance)

    @patch("server.discovery.bonjour.Zeroconf")
    @patch("server.discovery.bonjour.ServiceInfo")
    def test_start_already_started(self, mock_service_info, mock_zeroconf):
        """Test starting service when already started."""
        service = BonjourService()
        service._started = True

        # Should not raise, just log warning
        service.start(port=3780)

        # Should not create new instances
        mock_zeroconf.assert_not_called()
        mock_service_info.assert_not_called()

    @patch("server.discovery.bonjour.Zeroconf")
    @patch("server.discovery.bonjour.ServiceInfo")
    def test_stop(self, mock_service_info, mock_zeroconf):
        """Test stopping the service."""
        service = BonjourService()

        mock_zc_instance = MagicMock()
        mock_zeroconf.return_value = mock_zc_instance

        mock_si_instance = MagicMock()
        mock_service_info.return_value = mock_si_instance

        # Start then stop
        service.start(port=3780)
        service.stop()

        assert service._started is False
        assert service.zeroconf is None
        assert service.service_info is None

        # Verify unregister was called
        mock_zc_instance.unregister_service.assert_called_once_with(mock_si_instance)
        mock_zc_instance.close.assert_called_once()

    def test_stop_not_started(self):
        """Test stopping service that was never started."""
        service = BonjourService()

        # Should not raise
        service.stop()

        assert service._started is False

    @patch("server.discovery.bonjour.Zeroconf")
    @patch("server.discovery.bonjour.ServiceInfo")
    def test_context_manager(self, mock_service_info, mock_zeroconf):
        """Test using service as context manager."""
        mock_zc_instance = MagicMock()
        mock_zeroconf.return_value = mock_zc_instance

        mock_si_instance = MagicMock()
        mock_service_info.return_value = mock_si_instance

        with BonjourService() as service:
            service.start(port=3780)
            assert service._started is True

        # Should auto-cleanup on exit
        mock_zc_instance.unregister_service.assert_called_once()
        mock_zc_instance.close.assert_called_once()

    def test_get_local_addresses(self):
        """Test getting local IP addresses."""
        service = BonjourService()
        addresses = service._get_local_addresses()

        # Should return at least one address (or empty list)
        assert isinstance(addresses, list)

        # Each address should be 4 bytes (IPv4)
        for addr in addresses:
            assert isinstance(addr, bytes)
            assert len(addr) == 4

            # Should not be loopback
            ip_str = socket.inet_ntoa(addr)
            assert not ip_str.startswith("127.")

    @patch("server.discovery.bonjour.Zeroconf")
    @patch("server.discovery.bonjour.ServiceInfo")
    def test_start_with_custom_version(self, mock_service_info, mock_zeroconf):
        """Test starting with custom version."""
        service = BonjourService()

        mock_zc_instance = MagicMock()
        mock_zeroconf.return_value = mock_zc_instance

        mock_si_instance = MagicMock()
        mock_service_info.return_value = mock_si_instance

        service.start(port=8080, version="2.0.0")

        call_kwargs = mock_service_info.call_args[1]
        assert call_kwargs["properties"][b"version"] == b"2.0.0"
        assert call_kwargs["properties"][b"port"] == b"8080"
        assert call_kwargs["port"] == 8080

    @patch("server.discovery.bonjour.Zeroconf")
    @patch("server.discovery.bonjour.ServiceInfo")
    def test_start_failure_cleanup(self, mock_service_info, mock_zeroconf):
        """Test cleanup on start failure."""
        service = BonjourService()

        mock_zc_instance = MagicMock()
        mock_zeroconf.return_value = mock_zc_instance

        # Make register_service raise an exception
        mock_zc_instance.register_service.side_effect = Exception("Network error")

        with pytest.raises(Exception, match="Network error"):
            service.start(port=3780)

        # Should cleanup on failure
        assert service._started is False
        assert service.zeroconf is None
        mock_zc_instance.close.assert_called_once()


class TestBonjourServiceIntegration:
    """Integration tests requiring actual network."""

    @pytest.mark.skip(reason="Requires zeroconf library and network access")
    def test_real_service_lifecycle(self):
        """
        Test real service start/stop cycle.

        This test is skipped by default as it requires:
        1. zeroconf library installed
        2. Network interface available
        3. Bonjour/Avahi running

        To run manually:
        1. Install zeroconf: uv pip install zeroconf
        2. Remove @pytest.mark.skip
        3. Run: pytest server/discovery/tests/test_bonjour.py -v -k real_service
        4. In another terminal: dns-sd -B _localbook._tcp
        """
        service = BonjourService()

        try:
            service.start(port=3780, version="1.3.0")

            # Let it advertise
            time.sleep(2)

            assert service._started is True
            assert service.zeroconf is not None
            assert service.service_info is not None

        finally:
            service.stop()

            assert service._started is False
            assert service.zeroconf is None
