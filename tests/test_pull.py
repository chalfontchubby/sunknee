import socket

from sunknee.pull import resolve_ipv4


def test_resolve_ipv4_filters_out_ipv6(monkeypatch):
    def fake_getaddrinfo(host, port, family):
        assert family == socket.AF_INET
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.96", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    assert resolve_ipv4("homeassistant.local") == "192.168.1.96"
