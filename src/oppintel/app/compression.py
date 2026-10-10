"""Response compression for the web layer.

The app is served by gunicorn with no reverse proxy in the portable deployment, so without this
module every HTML and JSON response leaves the process uncompressed — ``/companies`` is over
100 KB of markup on a real dataset. A hosting platform's edge (e.g. Cloudflare in front of
Render) may add compression, but the app must not depend on an edge it does not control: a host
that serves the app directly would ship every page uncompressed.

This is deliberately opt-in and controlled by :data:`AppConfig.gzip_enabled`, which is off in a
debug process (so a developer sees readable bytes and tests are not surprised) and on in every
other process. The negotiation is conservative:

* only ``text/*`` and the JSON/JS/XML types are compressed — an already-compressed asset
  (an image, a font, a pre-compressed download) is skipped, because compressing it wastes CPU
  for no saving and can even enlarge it;
* a body below :data:`COMPRESS_MIN_BYTES` is left alone — framing overhead outweighs the saving;
* an empty body, a redirect, a 204/304, or a response that already carries a
  ``Content-Encoding`` is never re-encoded;
* ``Vary: Accept-Encoding`` is always added when the response is *eligible*, so a shared cache
  does not hand a gzip body to a client that did not ask for one.

Brotli is preferred when the ``brotli`` package is importable, falling back to gzip. Neither is
added to ``requirements.txt``: gzip ships with the standard library, so the feature works with
no new dependency, and brotli is used only if a deployment chooses to install it.
"""

from __future__ import annotations

import gzip
from typing import Optional

from flask import Response

#: A body smaller than this is not worth compressing (framing overhead can exceed the saving).
COMPRESS_MIN_BYTES = 500

#: Content-type prefixes and exact types that compress well. Everything else (images, fonts,
#: archives, ``application/octet-stream``) is skipped, already compressed or incompressible.
_COMPRESSIBLE_TYPES: tuple[str, ...] = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/xhtml+xml",
    "image/svg+xml",
)

#: Encodings this module can produce, most preferred first.
_SUPPORTED = ("br", "gzip")


def _brotli_available() -> bool:
    try:
        import brotli  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "not available"
        return False
    return True


_BROTLI = _brotli_available()


def supported_encodings() -> tuple[str, ...]:
    """The encodings the process can actually emit, most preferred first."""
    if _BROTLI:
        return ("br", "gzip")
    return ("gzip",)


def _accepts(accept_encoding: str, token: str) -> bool:
    """Whether an ``Accept-Encoding`` header allows one encoding token.

    ``*`` is accepted (it means "anything"), and a token whose quality value is ``0`` is
    refused. Parsing is deliberately small: the header is a comma-separated list of
    ``token[;q=value]``, and only the connection between a token and its own ``q`` matters.
    """
    lowered = (accept_encoding or "").lower()
    if not lowered:
        return False
    # With no matching token and no `*`, the encoding is not offered. `*` alone means "any".
    star_allowed = False
    for part in lowered.split(","):
        fields = part.split(";")
        name = fields[0].strip()
        quality = 1.0
        for field in fields[1:]:
            field = field.strip()
            if field.startswith("q="):
                try:
                    quality = float(field[2:])
                except ValueError:
                    quality = 1.0
        if name == token:
            return quality > 0
        if name == "*":
            star_allowed = quality > 0
    return star_allowed


def negotiate_encoding(accept_encoding: str) -> Optional[str]:
    """The best supported encoding the client accepts, or ``None`` to send the body as-is."""
    for token in supported_encodings():
        if _accepts(accept_encoding, token):
            return token
    return None


def _is_compressible_type(content_type: str) -> bool:
    content_type = (content_type or "").lower()
    return any(content_type.startswith(prefix) for prefix in _COMPRESSIBLE_TYPES)


def should_compress(response: Response, accept_encoding: str) -> bool:
    """Whether this response, for this request, should be compressed.

    Kept as a pure predicate so it can be unit-tested without a running request.
    """
    if response.status_code not in (200, 201, 202, 203, 204, 205, 206):
        return False
    if response.status_code in (204, 304):
        return False
    if response.headers.get("Content-Encoding"):
        return False
    if response.direct_passthrough:
        # A static file is streamed straight from disk; reading it into memory to compress it
        # would defeat the point. Flask's static handler can be fronted by the platform's edge.
        return False
    if not _is_compressible_type(response.headers.get("Content-Type", "")):
        return False
    if negotiate_encoding(accept_encoding) is None:
        return False
    body = response.get_data()
    return len(body) >= COMPRESS_MIN_BYTES


def compress_response(response: Response, accept_encoding: str) -> Response:
    """Compress a response body in place when it is eligible.

    Never changes a response that :func:`should_compress` rejects, so a caller can invoke this
    unconditionally. Adds ``Content-Encoding`` and ``Vary: Accept-Encoding`` on success and
    recomputes ``Content-Length`` (via ``Response.set_data``).
    """
    if not should_compress(response, accept_encoding):
        return response
    body = response.get_data()
    encoding = negotiate_encoding(accept_encoding)
    if encoding == "br":
        import brotli  # type: ignore[import-not-found]

        compressed = brotli.compress(body)
    else:
        # mtime=0 keeps the gzip header deterministic, so an identical body produces an
        # identical byte sequence (a stable ETag/Length and reproducible tests).
        compressed = gzip.compress(body, compresslevel=6, mtime=0)

    response.set_data(compressed)
    response.headers["Content-Encoding"] = encoding
    # A shared cache must key on the request's Accept-Encoding; without Vary it may serve a
    # gzip body to a client that cannot decode it.
    response.headers.add("Vary", "Accept-Encoding")
    return response
