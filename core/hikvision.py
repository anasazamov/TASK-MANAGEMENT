"""Hikvision access-control event reader (pull mode, DS-KV6124-WBE1 door station).

The door station sits on the local network and we connect out to it, holding a
long-lived ISAPI alertStream — an HTTP server-push response framed as
multipart/mixed. Each boundary part is either an event (XML or JSON) or a JPEG
the camera captured at the moment a card was read or the door opened.

Phase one does not interpret the event yet: we first need the real shape of what
this firmware sends, so `events()` only yields raw parts and the listener command
dumps them. The attendance parser is built around what the device actually emits.

Credentials stay in the environment. Only protocol-level sizes and content types
are logged here — never card numbers, employee names or image bytes.
"""
import logging
import re

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

BOUNDARY_RE = re.compile(rb'boundary=([^;]+)', re.IGNORECASE)


class HikvisionNotConfigured(Exception):
    """Raised when HIKVISION_HOST (and friends) are not set in the environment."""


def base_url():
    host = settings.HIKVISION_HOST
    if not host:
        raise HikvisionNotConfigured('HIKVISION_HOST sozlanmagan. .env faylida domofon manzilini kiriting.')
    if '://' in host:
        return host.rstrip('/')
    scheme = 'https' if settings.HIKVISION_HTTPS else 'http'
    return f'{scheme}://{host}:{settings.HIKVISION_PORT}'


def auth():
    # Hikvision ISAPI uses HTTP Digest; httpx answers the 401 challenge itself.
    return httpx.DigestAuth(settings.HIKVISION_USER, settings.HIKVISION_PASSWORD)


def extract_boundary(content_type):
    match = BOUNDARY_RE.search((content_type or '').encode('latin-1'))
    if not match:
        raise ValueError('alertStream javobida multipart boundary topilmadi: ' + (content_type or '')[:120])
    return match.group(1).strip().strip(b'"')


def parse_headers(raw):
    headers = {}
    for line in raw.split(b'\r\n'):
        if b':' in line:
            name, _, value = line.partition(b':')
            headers[name.strip().decode('latin-1').lower()] = value.strip().decode('latin-1')
    return headers


def is_image(headers):
    return headers.get('content-type', '').split(';')[0].strip() in ('image/jpeg', 'image/jpg', 'image/png')


class MultipartStream:
    """Incremental multipart/mixed parser for an ISAPI alertStream response.

    Every event is `--boundary` + header lines + a blank line + the body. A body
    that carries a Content-Length is read exactly — JPEG snapshots must not be cut
    on a byte that happens to resemble the boundary — otherwise it runs to the
    next boundary. Parts are yielded as soon as they are complete; incomplete
    tails stay in the buffer for the next `feed()`.
    """

    def __init__(self, boundary):
        self.delim = b'--' + boundary
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        yield from self._drain()

    def _drain(self):
        while True:
            start = self.buffer.find(self.delim)
            if start == -1:
                return
            after = start + len(self.delim)
            # `--boundary--` closes the stream; drop whatever is left.
            if self.buffer[after:after + 2] == b'--':
                self.buffer.clear()
                return
            header_end = self.buffer.find(b'\r\n\r\n', after)
            if header_end == -1:
                # Headers not fully arrived: keep from this boundary onward.
                del self.buffer[:start]
                return
            headers = parse_headers(bytes(self.buffer[after:header_end]))
            body_start = header_end + 4
            length = headers.get('content-length')
            if length is not None and length.isdigit():
                body_end = body_start + int(length)
                if len(self.buffer) < body_end:
                    del self.buffer[:start]
                    return
                body = bytes(self.buffer[body_start:body_end])
                del self.buffer[:body_end]
            else:
                nxt = self.buffer.find(self.delim, body_start)
                if nxt == -1:
                    del self.buffer[:start]
                    return
                body = bytes(self.buffer[body_start:nxt]).rstrip(b'\r\n')
                del self.buffer[:nxt]
            yield headers, body


def events(path=None, connect_timeout=10.0, read_timeout=65.0):
    """Open one alertStream connection and yield (headers, body) parts.

    One connection, no reconnection: the caller decides how to back off and
    whether to stop, so a long-running listener and a short capture run share the
    same generator. The read timeout sits just above the device's keep-alive so a
    silent link is noticed instead of hanging forever.
    """
    url = base_url() + (path or settings.HIKVISION_ALERT_PATH)
    timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
    with httpx.Client(timeout=timeout, auth=auth(), verify=settings.HIKVISION_VERIFY_TLS,
                      follow_redirects=False) as client:
        with client.stream('GET', url) as response:
            response.raise_for_status()
            parser = MultipartStream(extract_boundary(response.headers.get('content-type', '')))
            for chunk in response.iter_bytes():
                yield from parser.feed(chunk)
