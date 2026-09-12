import io
import socket
import pytest
from subscription_import import resolve_public_url, fetch_calendar, MAX_BYTES

ICS = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


def address(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:pass@example.org/calendar.ics",
        "http://example.org:9200/feed",
        "http://127.0.0.1/feed",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/feed",
        "http://224.0.0.1/feed",
    ],
)
def test_non_public_destinations_are_blocked(monkeypatch, url):
    monkeypatch.setattr(
        "subscription_import.socket.getaddrinfo",
        lambda host, *a, **kw: address(
            host if host[0].isdigit() or ":" in host else "93.184.215.14"
        ),
    )
    with pytest.raises(ValueError):
        resolve_public_url(url)


def test_webcal_and_public_address(monkeypatch):
    monkeypatch.setattr(
        "subscription_import.socket.getaddrinfo",
        lambda *a, **kw: address("93.184.215.14"),
    )
    u, host, port, ip = resolve_public_url(
        "webcal://calendar.example.org/sports.ics?team=1"
    )
    assert (
        u.scheme == "https"
        and port == 443
        and host == "calendar.example.org"
        and ip == "93.184.215.14"
    )


class Response:
    def __init__(self, data=ICS, status=200, headers=None):
        self.data = io.BytesIO(data)
        self.status = status
        self.headers = headers or {}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, n):
        return self.data.read(n)


def transport(monkeypatch, responses):
    connections = []

    class Fake:
        def __init__(self, host, port, ip, secure, timeout):
            self.sock = None
            connections.append((host, ip, secure))
            self.response = responses.pop(0)

        def request(self, *a, **kw):
            pass

        def getresponse(self):
            return self.response

        def close(self):
            pass

    monkeypatch.setattr("subscription_import.PublicConnection", Fake)
    monkeypatch.setattr(
        "subscription_import.socket.getaddrinfo",
        lambda host, *a, **kw: address(
            "127.0.0.1" if host == "internal.example.org" else "93.184.215.14"
        ),
    )
    return connections


def test_redirects_revalidate_before_connect(monkeypatch):
    connections = transport(
        monkeypatch,
        [
            Response(
                status=302, headers={"Location": "http://internal.example.org/secret"}
            )
        ],
    )
    with pytest.raises(ValueError):
        fetch_calendar("https://calendar.example.org/feed")
    assert len(connections) == 1


def test_download_pins_verified_address_and_accepts_redirect(monkeypatch):
    connections = transport(
        monkeypatch,
        [Response(status=302, headers={"Location": "/real.ics"}), Response()],
    )
    assert fetch_calendar("https://calendar.example.org/feed") == ICS
    assert connections == [("calendar.example.org", "93.184.215.14", True)] * 2


@pytest.mark.parametrize(
    "response",
    [
        Response(data=b"<html>Subscribe here</html>"),
        Response(headers={"Content-Length": str(MAX_BYTES + 1)}),
        Response(data=b"x" * (MAX_BYTES + 1)),
        Response(status=403),
    ],
)
def test_rejects_webpages_oversize_and_errors(monkeypatch, response):
    transport(monkeypatch, [response])
    with pytest.raises(ValueError):
        fetch_calendar("https://calendar.example.org/feed")
