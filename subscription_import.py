"""Fetch public ICS feeds without giving contributors access to server networks."""

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit, urljoin, urlunsplit

MAX_BYTES = 5 * 1024 * 1024


def resolve_public_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("Enter a valid public calendar subscription URL.")
    value = value.strip()
    if value.lower().startswith("webcal://"):
        value = "https://" + value[9:]
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError("Invalid calendar URL.")
    try:
        u = urlsplit(value)
        if (
            u.scheme not in ("http", "https")
            or not u.hostname
            or u.username is not None
            or u.password is not None
        ):
            raise ValueError()
        host = u.hostname.encode("idna").decode("ascii")
        port = u.port or (443 if u.scheme == "https" else 80)
        if port not in (80, 443):
            raise ValueError()
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(a[4][0] for a in addresses))
        if not ips or any(
            (
                not ipaddress.ip_address(ip).is_global
                or ipaddress.ip_address(ip).is_multicast
                or ipaddress.ip_address(ip).is_reserved
            )
            for ip in ips
        ):
            raise ValueError()
        # Connect to this checked IP; do not resolve the hostname again at connect time.
        return u, host, port, ips[0]
    except (ValueError, UnicodeError, OSError):
        raise ValueError(
            "Use a public HTTP, HTTPS, or webcal calendar link. Private network addresses are not supported."
        ) from None


class PublicConnection(http.client.HTTPConnection):
    def __init__(self, host, port, ip, secure, timeout):
        super().__init__(host, port, timeout=timeout)
        self.ip = ip
        self.secure = secure

    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), self.timeout)
        if self.secure:
            try:
                self.sock = ssl.create_default_context().wrap_socket(
                    self.sock, server_hostname=self.host
                )
            except Exception:
                self.sock.close()
                raise


def fetch_calendar(url):
    deadline = time.monotonic() + 25
    for hop in range(4):
        u, host, port, ip = resolve_public_url(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("The calendar provider took too long to respond.")
        conn = PublicConnection(host, port, ip, u.scheme == "https", min(10, remaining))
        try:
            path = urlunsplit(("", "", u.path or "/", u.query, ""))
            conn.request(
                "GET",
                path,
                headers={
                    "Accept": "text/calendar, application/ics;q=0.9, */*;q=0.1",
                    "Accept-Encoding": "identity",
                    "User-Agent": "TimeGrid-Calendar-Import/1.0",
                },
            )
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                destination = response.getheader("Location")
                if not destination:
                    raise ValueError("The provider returned an invalid redirect.")
                url = urljoin(u.geturl(), destination)
                continue
            if response.status != 200:
                raise ValueError(
                    f"The calendar provider returned HTTP {response.status}. Use a direct public ICS subscription link."
                )
            size = response.getheader("Content-Length")
            if size and (not size.isdigit() or int(size) > MAX_BYTES):
                raise ValueError("This calendar exceeds the 5 MB import limit.")
            if response.getheader("Content-Encoding", "identity").lower() not in (
                "",
                "identity",
            ):
                raise ValueError(
                    "This provider returned an unsupported compressed response."
                )
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("The calendar provider took too long to respond.")
                if conn.sock:
                    conn.sock.settimeout(min(10, remaining))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("This calendar exceeds the 5 MB import limit.")
            if (
                not bytes(data)
                .lstrip(b"\xef\xbb\xbf \r\n\t")
                .startswith(b"BEGIN:VCALENDAR")
            ):
                raise ValueError(
                    "This link does not return an ICS calendar. Copy the Subscribe or iCal link, rather than the provider’s webpage."
                )
            return bytes(data)
        except (OSError, http.client.HTTPException):
            raise ValueError(
                "Could not download this calendar. Check that its subscription link is public and try again."
            ) from None
        finally:
            conn.close()
    raise ValueError("The calendar link redirects too many times.")
