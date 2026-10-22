import numpy as np
import pytest
from docgen import make_pdf

from tamra.ocr import OcrEngine, render_page

pytestmark = pytest.mark.assets


def test_reads_lines_with_a_box_per_code_point_where_the_text_is(ocr_dir, tmp_path):
    pdf = make_pdf(tmp_path / "p.pdf", [["Tamra reads scanned pages", "สวัสดีครับ ทดสอบ"]])
    result = OcrEngine.load(ocr_dir).read(render_page(pdf, 1))
    lines = result.text.split("\n")
    assert lines[0] == "Tamra reads scanned pages"
    assert lines[1].replace(" ", "") == "สวัสดีครับทดสอบ"
    assert result.boxes.shape == (len(result.text), 4) and result.boxes.dtype == np.float32
    newline = result.text.index("\n")
    assert result.boxes[newline].tolist() == [0.0, 0.0, 0.0, 0.0]
    # make_pdf draws the first line at x = 50 pt and baseline 800 pt on a 595 x 842 pt page
    x0, y0, x1, y1 = result.boxes[0]
    assert 0.07 < x0 < 0.10 and 0.02 < y0 < 0.05 and x1 > x0 and 0.04 < y1 < 0.07
    assert (result.boxes >= 0).all() and (result.boxes <= 1).all()


def test_render_page_draws_the_page_at_300_dpi(tmp_path):
    image = render_page(make_pdf(tmp_path / "p.pdf", [["x"]]), 1)
    assert image.shape[:2] == (3509, 2480)  # 842 x 595 pt at 300/72, rounded up by pdfium


def test_an_empty_page_reads_as_nothing(ocr_dir, tmp_path):
    pdf = make_pdf(tmp_path / "blank.pdf", [[]])
    result = OcrEngine.load(ocr_dir).read(render_page(pdf, 1))
    assert result.text == "" and result.boxes.shape == (0, 4)
