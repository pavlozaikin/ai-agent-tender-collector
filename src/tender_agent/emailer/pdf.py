"""HTML-to-PDF conversion using WeasyPrint."""

from __future__ import annotations

import os
import platform

# On macOS with Homebrew, Pango/GObject libs live in /opt/homebrew/lib but are
# not on the default dynamic-linker path. Setting DYLD_LIBRARY_PATH here —
# before the WeasyPrint import — ensures cffi's dlopen can find them.
if platform.system() == "Darwin":
    _hb = "/opt/homebrew/lib"
    _existing = os.environ.get("DYLD_LIBRARY_PATH", "")
    if _hb not in _existing:
        os.environ["DYLD_LIBRARY_PATH"] = f"{_hb}:{_existing}".rstrip(":")

from typing import NoReturn

from weasyprint import HTML  # type: ignore[import-untyped]  # noqa: E402


class ResourceFetchBlocked(Exception):
    """Raised when the report HTML tries to fetch an external/local resource."""


def deny_all_fetcher(url: str) -> NoReturn:
    """A WeasyPrint URL fetcher that refuses every request (SSRF/LFI defense).

    The tender report is fully self-contained — it never legitimately needs
    external or local resources — so any URL request (``file://``, ``http://``,
    internal addresses, …) is treated as an attack and blocked unconditionally.
    """
    raise ResourceFetchBlocked(f"Blocked resource fetch from report HTML: {url!r}")


def render_pdf(html: str) -> bytes:
    """Convert a rendered HTML report string to PDF bytes.

    A deny-all ``url_fetcher`` is passed to WeasyPrint so the report cannot
    fetch any external or local resource (defense-in-depth against SSRF / local
    file disclosure).
    """
    return HTML(  # type: ignore[no-any-return]
        string=html, url_fetcher=deny_all_fetcher
    ).write_pdf()
