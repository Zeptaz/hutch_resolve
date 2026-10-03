from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request

from backend.resolve.app.auth_rate_limit import client_address


def _request(peer: str, forwarded: str | None, trusted: frozenset[str]) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode("ascii"))]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": (peer, 12345),
        "server": ("localhost", 80),
        "app": SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(trusted_proxy_ips=trusted))),
    }
    return Request(scope)


def test_trusted_proxy_chain_walks_right_to_left_and_skips_spoofed_leftmost_value() -> None:
    request = _request(
        "10.0.0.2",
        "198.51.100.77, 203.0.113.9, 10.0.0.1",
        frozenset({"10.0.0.1", "10.0.0.2"}),
    )

    # 10.0.0.1 is trusted, but 203.0.113.9 is the first untrusted hop from
    # the right. The caller-supplied leftmost value must not win.
    assert client_address(request) == "203.0.113.9"


def test_untrusted_direct_peer_cannot_spoof_forwarded_address() -> None:
    request = _request("198.51.100.20", "203.0.113.99", frozenset({"10.0.0.2"}))

    assert client_address(request) == "198.51.100.20"


def test_malformed_forwarded_chain_falls_back_to_trusted_peer() -> None:
    request = _request("10.0.0.2", "198.51.100.20, not-an-ip", frozenset({"10.0.0.2"}))

    assert client_address(request) == "10.0.0.2"
