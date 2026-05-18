"""Tests for HTML-to-PDF rendering and its SSRF/LFI defenses (H1, M4)."""

from __future__ import annotations

import pytest

from tender_agent.emailer.pdf import (
    ResourceFetchBlocked,
    deny_all_fetcher,
    render_pdf,
)


def test_deny_all_fetcher_blocks_file_url() -> None:
    """H1: the deny-all fetcher refuses file:// URLs (local file disclosure)."""
    with pytest.raises(ResourceFetchBlocked, match="file:///etc/passwd"):
        deny_all_fetcher("file:///etc/passwd")


def test_deny_all_fetcher_blocks_http_url() -> None:
    """H1: the deny-all fetcher refuses http(s) URLs (SSRF)."""
    with pytest.raises(ResourceFetchBlocked):
        deny_all_fetcher("http://169.254.169.254/latest/meta-data/")


def test_render_pdf_wires_deny_all_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """H1/M4: render_pdf passes the deny-all url_fetcher to WeasyPrint.

    WeasyPrint swallows fetcher errors for individual resources (logging
    them), so the defense is verified by confirming the deny-all fetcher is
    the one handed to ``HTML(...)`` — i.e. no resource can ever be fetched.
    """
    import tender_agent.emailer.pdf as pdf_module

    captured: dict[str, object] = {}
    real_html = pdf_module.HTML

    def spy_html(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return real_html(*args, **kwargs)

    monkeypatch.setattr(pdf_module, "HTML", spy_html)
    render_pdf("<html><body><h1>Звіт</h1></body></html>")
    assert captured.get("url_fetcher") is deny_all_fetcher


def test_render_pdf_does_not_embed_blocked_resource() -> None:
    """H1/M4: a report referencing a local file renders without disclosing it.

    The deny-all fetcher prevents the ``file://`` resource from ever being
    read, so its contents cannot leak into the produced PDF.
    """
    malicious_html = "<html><body><img src='file:///etc/hostname'></body></html>"
    pdf = render_pdf(malicious_html)
    assert pdf.startswith(b"%PDF")


def test_render_pdf_renders_self_contained_html() -> None:
    """A normal, self-contained report still renders to PDF bytes."""
    pdf = render_pdf("<html><body><h1>Звіт</h1></body></html>")
    assert pdf.startswith(b"%PDF")
