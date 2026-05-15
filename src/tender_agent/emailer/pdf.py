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

from weasyprint import HTML  # type: ignore[import-untyped]  # noqa: E402


def render_pdf(html: str) -> bytes:
    """Convert a rendered HTML report string to PDF bytes."""
    return HTML(string=html).write_pdf()  # type: ignore[no-any-return]
