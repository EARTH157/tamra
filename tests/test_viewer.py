import struct
import zlib

import numpy as np
import pypdfium2 as pdfium
import pytest
from docgen import CJK, LATIN_THAI, make_docx, make_pdf

from tamra.ingest.parsers import parse_file
from tamra.viewer import (
    ViewerError,
    locate,
    pdf_page_count,
    pdf_rects,
    render_pdf_page,
    resolve_file,
    text_view,
)


@pytest.fixture
def fonts():
    if not (LATIN_THAI.exists() and CJK.exists()):
        pytest.skip("Windows fonts Tahoma and Microsoft YaHei are needed to build PDFs")


def _page_text(path, page):
    pdf = pdfium.PdfDocument(path)
    try:
        textpage = pdf[page - 1].get_textpage()
        try:
            return textpage.get_text_range()
        finally:
            textpage.close()
    finally:
        pdf.close()


def test_resolve_file_accepts_a_nested_file(tmp_path):
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    target = tmp_path / "docs" / "sub" / "a.txt"
    target.write_text("x")
    assert resolve_file(str(tmp_path / "docs"), "sub/a.txt") == target.resolve()


def test_resolve_file_refuses_escapes(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (tmp_path / "docs-other").mkdir()
    (tmp_path / "docs-other" / "x.txt").write_text("secret")
    (tmp_path / "x.txt").write_text("secret")
    for rel in ("..\\..\\x", "../x.txt", "../docs-other/x.txt", str(tmp_path / "x.txt")):
        with pytest.raises(ViewerError, match="outside the collection folder"):
            resolve_file(str(folder), rel)


def test_resolve_file_reports_a_missing_file(tmp_path):
    with pytest.raises(ViewerError, match="file not found"):
        resolve_file(str(tmp_path), "nope.txt")


def test_locate_exact_text_gives_one_based_lines(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text(
        "Title line\n\nFirst paragraph here.\nIt has two lines.\n\n"
        "The rent is 12,000 baht monthly.\n",
        encoding="utf-8",
    )
    found = locate(parse_file(path), "The rent is 12,000 baht monthly.")
    assert (found.kind, found.page, found.start, found.end) == ("text", None, 6, 7)
    multi = locate(parse_file(path), "First paragraph here.\nIt has two lines.")
    assert (multi.start, multi.end) == (3, 5)


def test_locate_ignores_line_endings_spacing_and_case(tmp_path):
    path = tmp_path / "w.txt"
    path.write_bytes(b"Intro text.\r\n\r\nThe  tenant   shall pay\r\nrent on the FIRST day.\r\n")
    found = locate(parse_file(path), "the tenant shall pay rent\non the first day.")
    assert (found.start, found.end) == (3, 5)


def test_locate_joined_parts_inside_one_docx_range(tmp_path):
    path = make_docx(
        tmp_path / "d.docx",
        [
            (1, "Lease"),
            (0, "The tenant pays rent monthly in advance."),
            (0, "Unrelated filler paragraph about parking."),
            (0, "The deposit is two months of rent."),
            (0, "Closing paragraph of the lease."),
        ],
    )
    doc = parse_file(path)
    passage = (
        "The tenant pays rent monthly in advance.\n\nUnrelated filler paragraph about parking."
    )
    found = locate(doc, passage)
    assert (found.kind, found.page, found.start, found.end) == ("docx", None, 1, 3)
    # Two parts with a gap between them still span first start to last end.
    gap = "The tenant pays rent monthly in advance.\n\nThe deposit is two months of rent."
    spanning = locate(doc, gap)
    assert (spanning.start, spanning.end) == (1, 4)


def test_locate_pdf_page_offsets_match_the_page_text(tmp_path, fonts):
    path = make_pdf(
        tmp_path / "p.pdf",
        [["Page one intro line."], ["Second page heading", "The deposit equals two months rent."]],
    )
    found = locate(parse_file(path), "The deposit equals two months rent.")
    assert (found.kind, found.page) == ("pdf", 2)
    assert _page_text(path, 2)[found.start : found.end] == "The deposit equals two months rent."


def test_locate_pdf_part_across_pages_returns_the_page_of_the_longest_part(tmp_path, fonts):
    path = make_pdf(
        tmp_path / "x.pdf",
        [
            ["Short tail of page one here."],
            ["A much longer opening on the second page of the file."],
        ],
    )
    passage = (
        "Short tail of page one here.\n\nA much longer opening on the second page of the file."
    )
    found = locate(parse_file(path), passage)
    assert found.page == 2
    assert _page_text(path, 2)[found.start : found.end].startswith("A much longer opening")


def test_locate_returns_none_when_missing_or_too_short(tmp_path):
    path = tmp_path / "n.txt"
    path.write_text("Some document content that is here.\n", encoding="utf-8")
    doc = parse_file(path)
    assert locate(doc, "text that is not in the document at all") is None
    assert locate(doc, "content") is None  # shorter than 12 normalised characters
    assert locate(doc, "   ") is None


def test_locate_hint_picks_the_occurrence_it_names(tmp_path, fonts):
    same = "The notice period is thirty days."
    path = make_pdf(tmp_path / "h.pdf", [[same], ["Middle page."], [same]])
    doc = parse_file(path)
    assert locate(doc, same).page == 1
    hint = {"kind": "pdf", "page_start": 3, "page_end": 3, "char_start": 0, "char_end": 33}
    assert locate(doc, same, hint).page == 3
    txt = tmp_path / "h.txt"
    txt.write_text(f"{same}\n\nOther.\n\n{same}\n", encoding="utf-8")
    text_hint = {"kind": "text", "line_start": 5, "line_end": 5}
    assert locate(parse_file(txt), same, text_hint).start == 5


def _ihdr(png: bytes):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    length, kind = struct.unpack(">I4s", png[8:16])
    assert (length, kind) == (13, b"IHDR")
    return struct.unpack(">IIBBBBB", png[16:29])


def test_render_pdf_page_makes_a_valid_rgb_png(tmp_path, fonts):
    path = make_pdf(tmp_path / "r.pdf", [["Hello render"]])
    png = render_pdf_page(path, 1, 1.0)
    width, height, depth, colour, *_ = _ihdr(png)
    assert (width, height, depth, colour) == (595, 842, 8, 2)
    # The IDAT stream decodes to height rows of (filter byte + RGB pixels).
    idat_at = png.index(b"IDAT")
    (length,) = struct.unpack(">I", png[idat_at - 4 : idat_at])
    raw = zlib.decompress(png[idat_at + 4 : idat_at + 4 + length])
    assert len(raw) == height * (1 + width * 3)
    assert png.endswith(b"IEND" + struct.pack(">I", zlib.crc32(b"IEND")))
    # Dark text on a white page: the corner is white and some pixel is dark.
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(height, 1 + width * 3)
    assert rows[:, 0].max() == 0  # every row uses filter 0
    assert rows[0, 1:4].tolist() == [255, 255, 255]
    assert rows[:, 1:].min() < 80


def test_render_pdf_page_scales_and_clamps(tmp_path, fonts):
    path = make_pdf(tmp_path / "s.pdf", [["x"]])
    assert _ihdr(render_pdf_page(path, 1, 2.0))[:2] == (1190, 1684)
    assert _ihdr(render_pdf_page(path, 1, 99))[0] == round(595 * 3.0)
    assert _ihdr(render_pdf_page(path, 1, 0.01))[0] == round(595 * 0.5)


def test_pdf_page_count_and_page_range_errors(tmp_path, fonts):
    path = make_pdf(tmp_path / "c.pdf", [["a"], ["b"], ["c"]])
    assert pdf_page_count(path) == 3
    with pytest.raises(ViewerError, match="page out of range"):
        render_pdf_page(path, 4, 1.0)


def test_pdf_rects_cover_a_known_line_as_top_left_fractions(tmp_path, fonts):
    path = make_pdf(tmp_path / "b.pdf", [["First line of text", "Second line of text"]])
    text = _page_text(path, 1)
    start = text.index("Second line")
    rects = pdf_rects(path, 1, start, start + len("Second line of text"))
    assert rects
    for x0, y0, x1, y1 in rects:
        assert all(0 <= v <= 1 for v in (x0, y0, x1, y1))
        assert y0 < y1 and x0 < x1
    # make_pdf puts line 2 at y=780 of 842 points, so it sits near the top of the page.
    assert all(0.02 < r[1] < 0.1 for r in rects)
    first = pdf_rects(path, 1, 0, len("First line of text"))
    assert first[0][1] < rects[0][1]  # the first line is above the second


def test_pdf_rects_of_an_empty_range_is_empty(tmp_path, fonts):
    path = make_pdf(tmp_path / "e.pdf", [["abc def"]])
    assert pdf_rects(path, 1, 3, 3) == []


def test_text_view_keeps_blank_lines_so_numbers_match_the_file(tmp_path):
    path = tmp_path / "v.txt"
    path.write_text("one\n\n\nsecond line\nthird line\n\nfour\n", encoding="utf-8")
    view = text_view(parse_file(path))
    assert view["kind"] == "text"
    assert view["lines"] == ["one", "", "", "second line", "third line", "", "four"]
    found = locate(parse_file(path), "second line\nthird line")
    assert view["lines"][found.start - 1 : found.end - 1] == ["second line", "third line"]


def test_text_view_marks_docx_headings(tmp_path):
    path = make_docx(
        tmp_path / "t.docx", [(1, "Lease"), (0, "Body text."), (2, "Rent"), (0, "Pay on time.")]
    )
    view = text_view(parse_file(path))
    assert view["kind"] == "docx"
    assert [(p["index"], p["text"], p["heading"]) for p in view["paragraphs"]] == [
        (0, "Lease", True),
        (1, "Body text.", False),
        (2, "Rent", True),
        (3, "Pay on time.", False),
    ]
