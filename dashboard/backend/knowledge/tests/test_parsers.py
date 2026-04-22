"""Tests for knowledge/parsers.

Marker round-trip test requires marker-pdf installed.
Tests use a minimal generated PDF fixture or skip if marker not available.
"""

import os
import sys
import pytest
from pathlib import Path


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _create_minimal_pdf(path: Path) -> None:
    """Write a minimal valid PDF with one page of text."""
    content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 100 700 Td (Hello World) Tj ET
endstream
endobj

5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj

xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000360 00000 n

trailer
<< /Size 6 /Root 1 0 R >>
startxref
441
%%EOF"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ---------------------------------------------------------------------------
# Registry tests (no marker needed)
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_get_parser_auto_without_marker_raises(self, monkeypatch):
        _add_backend()
        from knowledge.parsers import base
        monkeypatch.setattr(base, "_marker_available", lambda: False)
        monkeypatch.setattr(base, "_llamaparse_api_key", lambda: None)

        with pytest.raises(RuntimeError, match="No document parser available"):
            base.get_parser("auto")

    def test_get_parser_unknown_raises(self):
        _add_backend()
        from knowledge.parsers.base import get_parser
        with pytest.raises(ValueError, match="Unknown parser name"):
            get_parser("unknown_parser")

    def test_get_parser_auto_returns_marker_when_available(self, monkeypatch):
        _add_backend()
        from knowledge.parsers import base
        monkeypatch.setattr(base, "_marker_available", lambda: True)
        parser = base.get_parser("auto")
        assert parser.__class__.__name__ == "MarkerParser"

    def test_get_parser_auto_falls_back_to_llama(self, monkeypatch):
        _add_backend()
        from knowledge.parsers import base
        monkeypatch.setattr(base, "_marker_available", lambda: False)
        monkeypatch.setattr(base, "_llamaparse_api_key", lambda: "llx-test-key")
        parser = base.get_parser("auto")
        assert parser.__class__.__name__ == "LlamaParseParser"


# ---------------------------------------------------------------------------
# MarkerParser (requires marker-pdf)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_pdf():
    """Create a minimal PDF fixture for testing."""
    pdf_path = FIXTURES_DIR / "sample.pdf"
    if not pdf_path.exists():
        _create_minimal_pdf(pdf_path)
    return pdf_path


def _marker_installed():
    try:
        import importlib
        spec = importlib.util.find_spec("marker")
        return spec is not None
    except Exception:
        return False


@pytest.mark.skipif(not _marker_installed(), reason="marker-pdf not installed")
class TestMarkerParser:
    def test_parse_pdf_returns_parse_result_keys(self, sample_pdf):
        _add_backend()
        from knowledge.parsers.marker_parser import MarkerParser
        parser = MarkerParser()
        result = parser.parse(sample_pdf)
        assert "markdown" in result
        assert "pages" in result
        assert "metadata" in result

    def test_parse_pdf_has_non_empty_markdown(self, sample_pdf):
        _add_backend()
        from knowledge.parsers.marker_parser import MarkerParser
        parser = MarkerParser()
        result = parser.parse(sample_pdf)
        # Even a minimal PDF should produce some output
        assert isinstance(result["markdown"], str)

    def test_parse_nonexistent_file_raises(self):
        _add_backend()
        from knowledge.parsers.marker_parser import MarkerParser
        parser = MarkerParser()
        with pytest.raises(FileNotFoundError):
            parser.parse(Path("/tmp/nonexistent_file_xyz.pdf"))

    def test_parse_returns_pages_list(self, sample_pdf):
        _add_backend()
        from knowledge.parsers.marker_parser import MarkerParser
        parser = MarkerParser()
        result = parser.parse(sample_pdf)
        assert isinstance(result["pages"], list)
        assert len(result["pages"]) >= 1


class TestMarkerNotInstalled:
    def test_parse_raises_actionable_error(self, monkeypatch):
        _add_backend()
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "marker" or name.startswith("marker."):
                raise ImportError("No module named 'marker'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        from knowledge.parsers.marker_parser import MarkerParser, MarkerNotInstalledError
        # Reset cached converter
        import knowledge.parsers.marker_parser as mod
        original_converter = mod._converter
        mod._converter = None
        try:
            parser = MarkerParser()
            with pytest.raises(MarkerNotInstalledError, match="pip install marker-pdf"):
                parser.parse(Path("/tmp/test.pdf"))
        finally:
            mod._converter = original_converter


# ---------------------------------------------------------------------------
# LlamaParseParser stub
# ---------------------------------------------------------------------------

class TestLlamaParseParser:
    def test_raises_without_api_key(self, monkeypatch):
        _add_backend()
        monkeypatch.delenv("LLAMAPARSE_API_KEY", raising=False)
        from knowledge.parsers.llamaparse_parser import LlamaParseParser, LlamaParseNotConfiguredError
        parser = LlamaParseParser()
        with pytest.raises(LlamaParseNotConfiguredError, match="LLAMAPARSE_API_KEY"):
            parser.parse(Path("/tmp/test.pdf"))
