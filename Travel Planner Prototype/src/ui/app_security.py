"""Route policy for published Cursor Apps."""

from __future__ import annotations

_STATIC_FILE_SUFFIXES = (
    ".js",
    ".css",
    ".svg",
    ".ico",
    ".woff2",
    ".woff",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".txt",
    ".json",
)


def path_requires_auth(path: str, method: str) -> bool:
    if method == "OPTIONS":
        return False
    if path == "/health":
        return False
    if path == "/":
        return False
    if method == "GET" and path.startswith("/assets/"):
        return False
    if method == "GET" and any(path.endswith(sfx) for sfx in _STATIC_FILE_SUFFIXES):
        return False
    return True
