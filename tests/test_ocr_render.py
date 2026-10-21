import pypdfium2 as pdfium
import pytest
from docgen import make_pdf

from tamra.ocr import render_page


def test_render_page_refuses_page_zero(tmp_path):
    pdf = make_pdf(tmp_path / "p.pdf", [["one"], ["two"]])
    with pytest.raises(IndexError):
        render_page(pdf, 0)  # pdf[-1] would quietly draw the last page


def test_render_page_caps_a_huge_page(tmp_path):
    path = tmp_path / "big.pdf"
    doc = pdfium.PdfDocument.new()
    doc.new_page(2000, 2000)
    doc.save(path)
    doc.close()
    assert max(render_page(path, 1).shape[:2]) <= 4000  # 300 dpi would be 8334 px
