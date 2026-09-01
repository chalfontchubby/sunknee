import socket

import pytest

from sunknee.pull import resolve_ipv4


def test_resolve_ipv4_filters_out_ipv6_from_unrestricted_query(monkeypatch):
    def fake_getaddrinfo(host, port):
        # No family filter -- passing AF_INET into getaddrinfo() itself
        # is exactly the macOS/.local mDNS quirk this function works
        # around, so the mock must not require it either.
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1234", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.96", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    assert resolve_ipv4("homeassistant.local") == "192.168.1.96"


def test_resolve_ipv4_raises_when_no_ipv4_present(monkeypatch):
    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1234", 0, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(OSError, match="No IPv4 address"):
        resolve_ipv4("homeassistant.local")
