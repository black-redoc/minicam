"""Tests for the WS-Discovery camera discovery service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.discovery import discover_onvif_cameras


def _make_scope(value: str) -> MagicMock:
    scope = MagicMock()
    scope.getValue.return_value = value
    return scope


def _make_service(
    xaddrs: list[str],
    scopes: list[object],
) -> MagicMock:
    svc = MagicMock()
    svc.getXAddrs.return_value = xaddrs
    svc.getScopes.return_value = scopes
    return svc


class TestDiscoverOnvifCamerasEmpty:
    def test_returns_empty_list_when_no_services(self) -> None:
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = []
            result = discover_onvif_cameras(timeout=1)
        assert result == []

    def test_returns_empty_list_on_exception(self) -> None:
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.side_effect = RuntimeError("network error")
            result = discover_onvif_cameras(timeout=1)
        assert result == []

    def test_skips_services_with_no_xaddrs(self) -> None:
        svc = _make_service(xaddrs=[], scopes=[])
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = [svc]
            result = discover_onvif_cameras(timeout=1)
        assert result == []


class TestDiscoverOnvifCamerasHostExtraction:
    def test_extracts_host_from_xaddr_url(self) -> None:
        svc = _make_service(
            xaddrs=["http://192.168.1.42/onvif/device_service"],
            scopes=[],
        )
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = [svc]
            result = discover_onvif_cameras(timeout=1)
        assert len(result) == 1
        assert result[0].host == "192.168.1.42"

    def test_uses_first_xaddr_when_multiple_present(self) -> None:
        svc = _make_service(
            xaddrs=[
                "http://10.0.0.1/onvif/device_service",
                "http://10.0.0.2/onvif/device_service",
            ],
            scopes=[],
        )
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = [svc]
            result = discover_onvif_cameras(timeout=1)
        assert result[0].host == "10.0.0.1"


class TestDiscoverOnvifCamerasScopeExtraction:
    def test_extracts_name_from_scope(self) -> None:
        svc = _make_service(
            xaddrs=["http://192.168.1.10/onvif/device_service"],
            scopes=[_make_scope("onvif://www.onvif.org/name/IPCamera")],
        )
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = [svc]
            result = discover_onvif_cameras(timeout=1)
        assert result[0].name == "IPCamera"

    def test_extracts_hardware_from_scope(self) -> None:
        svc = _make_service(
            xaddrs=["http://192.168.1.10/onvif/device_service"],
            scopes=[_make_scope("onvif://www.onvif.org/hardware/DS-2CD2T47")],
        )
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = [svc]
            result = discover_onvif_cameras(timeout=1)
        assert result[0].hardware == "DS-2CD2T47"

    def test_extracts_location_from_scope(self) -> None:
        svc = _make_service(
            xaddrs=["http://192.168.1.10/onvif/device_service"],
            scopes=[_make_scope("onvif://www.onvif.org/location/Garage")],
        )
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = [svc]
            result = discover_onvif_cameras(timeout=1)
        assert result[0].location == "Garage"

    def test_uses_empty_strings_for_missing_scopes(self) -> None:
        svc = _make_service(
            xaddrs=["http://192.168.1.10/onvif/device_service"],
            scopes=[],
        )
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = [svc]
            result = discover_onvif_cameras(timeout=1)
        assert result[0].name == ""
        assert result[0].hardware == ""
        assert result[0].location == ""

    def test_handles_plain_string_scopes(self) -> None:
        """Scope values that are plain strings (not objects) are handled."""
        svc = _make_service(
            xaddrs=["http://192.168.1.10/onvif/device_service"],
            scopes=["onvif://www.onvif.org/name/StringScope"],
        )
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = [svc]
            result = discover_onvif_cameras(timeout=1)
        assert result[0].name == "StringScope"


class TestDiscoverOnvifCamerasLifecycle:
    def test_calls_wsd_start(self) -> None:
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = []
            discover_onvif_cameras(timeout=1)
        mock_wsd.start.assert_called_once()

    def test_calls_wsd_stop(self) -> None:
        with patch("app.services.discovery.WSDiscovery") as mock_wsd_cls:
            mock_wsd = MagicMock()
            mock_wsd_cls.return_value = mock_wsd
            mock_wsd.searchServices.return_value = []
            discover_onvif_cameras(timeout=1)
        mock_wsd.stop.assert_called_once()
