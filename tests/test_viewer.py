import struct
import threading
import zlib

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from docgen import CJK, LATIN_THAI, make_docx, make_pdf
from fakes import fake_spans

import tamra.viewer as viewer
from tamra.ingest.chunker import chunk_document
from tamra.ingest.parsers import PDFIUM_LOCK, ParsedDoc, Unit, parse_file, parse_pdf
from tamra.viewer import (
    ViewerError,
    _encode_png,
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


def _decode_png(png: bytes) -> np.ndarray:
    width, height = _ihdr(png)[:2]
    at = png.index(b"IDAT")
    (length,) = struct.unpack(">I", png[at - 4 : at])
    raw = np.frombuffer(zlib.decompress(png[at + 4 : at + 4 + length]), dtype=np.uint8)
    return raw.reshape(height, 1 + width * 3)[:, 1:].reshape(height, width, 3)


def test_png_encoder_round_trips_a_known_array():
    rgb = np.zeros((2, 3, 3), dtype=np.uint8)
    rgb[0, 0] = (255, 0, 0)
    rgb[0, 2] = (0, 0, 255)
    rgb[1, 1] = (10, 200, 30)
    png = _encode_png(rgb)
    assert _ihdr(png)[:4] == (3, 2, 8, 2)
    assert (_decode_png(png) == rgb).all()


def test_rendered_colours_keep_their_channel_order(tmp_path, fonts):
    path = make_pdf(tmp_path / "c.pdf", [["x"]])
    pdf = pdfium.PdfDocument(path)
    page = pdf[0]
    for x, colour in ((100, (255, 0, 0)), (200, (0, 255, 0)), (300, (0, 0, 255))):
        rect = pdfium_c.FPDFPageObj_CreateNewRect(x, 300, 50, 50)
        pdfium_c.FPDFPageObj_SetFillColor(rect, *colour, 255)
        pdfium_c.FPDFPath_SetDrawMode(rect, 1, False)
        pdfium_c.FPDFPage_InsertObject(page, rect)
    pdfium_c.FPDFPage_GenerateContent(page)
    coloured = tmp_path / "coloured.pdf"
    pdf.save(coloured)
    page.close()
    pdf.close()
    pixels = _decode_png(render_pdf_page(coloured, 1, 1.0))
    y = 842 - 325  # page y grows upward, the image downward
    assert pixels[y, 125].tolist() == [255, 0, 0]
    assert pixels[y, 225].tolist() == [0, 255, 0]
    assert pixels[y, 325].tolist() == [0, 0, 255]


def test_render_ignores_a_nan_scale_and_caps_the_pixel_count(tmp_path, fonts, monkeypatch):
    path = make_pdf(tmp_path / "m.pdf", [["x"]])
    assert _ihdr(render_pdf_page(path, 1, float("nan")))[:2] == (595, 842)
    assert _ihdr(render_pdf_page(path, 1, float("inf")))[:2] == (595, 842)
    monkeypatch.setattr(viewer, "MAX_PIXELS", 1_000_000)
    width, height = _ihdr(render_pdf_page(path, 1, 3.0))[:2]
    assert width * height <= 1_000_000
    assert abs(width / height - 595 / 842) < 0.01


def test_resolve_file_refuses_drive_relative_unc_and_nul_paths(tmp_path):
    (tmp_path / "x.txt").write_text("x")
    for rel in ("D:x.txt", "\\\\host\\share\\x.txt"):
        with pytest.raises(ViewerError, match="outside the collection folder"):
            resolve_file(str(tmp_path), rel)
    with pytest.raises(ViewerError):
        resolve_file(str(tmp_path), "a\x00b.txt")


def test_opening_a_directory_or_missing_pdf_raises_viewer_error(tmp_path):
    with pytest.raises(ViewerError, match="cannot open PDF"):
        pdf_page_count(tmp_path)
    with pytest.raises(ViewerError, match="cannot open PDF"):
        pdf_page_count(tmp_path / "missing.pdf")


# --- rotated pages and crop boxes -------------------------------------------------------------


def _ink_box(pixels: np.ndarray) -> tuple[float, float, float, float]:
    height, width = pixels.shape[:2]
    ys, xs = np.nonzero(pixels.min(axis=2) < 128)
    return xs.min() / width, ys.min() / height, (xs.max() + 1) / width, (ys.max() + 1) / height


def _union(rects):
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def _rotated(tmp_path, source, rotation=0, cropbox=None):
    pdf = pdfium.PdfDocument(source)
    page = pdf[0]
    if rotation:
        page.set_rotation(rotation)
    if cropbox:
        page.set_cropbox(*cropbox)
    out = tmp_path / f"r{rotation}-{bool(cropbox)}.pdf"
    pdf.save(out)
    page.close()
    pdf.close()
    return out


LINE = "Only line on this page"


@pytest.mark.parametrize(
    ("rotation", "cropbox"),
    [
        (0, None),
        (90, None),
        (180, None),
        (270, None),
        (0, (0, 400, 595, 842)),
        (90, (100, 300, 500, 842)),
    ],
)
def test_pdf_rects_land_where_the_rendered_page_shows_the_text(tmp_path, fonts, rotation, cropbox):
    source = make_pdf(tmp_path / "s.pdf", [[LINE]])
    path = _rotated(tmp_path, source, rotation, cropbox)
    rects = pdf_rects(path, 1, 0, len(LINE))
    assert rects
    expected = _ink_box(_decode_png(render_pdf_page(path, 1, 1.0)))
    got = _union(rects)
    assert all(abs(a - b) < 0.02 for a, b in zip(got, expected, strict=True)), (got, expected)
    assert all(0 <= v <= 1 for r in rects for v in r)
    assert all(r[0] < r[2] and r[1] < r[3] for r in rects)


def test_pdf_rects_follow_the_rotation_maths(tmp_path, fonts):
    source = make_pdf(tmp_path / "s.pdf", [[LINE]])
    x0, y0, x1, y1 = _union(pdf_rects(source, 1, 0, len(LINE)))
    clockwise = {
        90: (1 - y1, x0, 1 - y0, x1),
        180: (1 - x1, 1 - y1, 1 - x0, 1 - y0),
        270: (y0, 1 - x1, y1, 1 - x0),
    }
    for rotation, expected in clockwise.items():
        path = _rotated(tmp_path, source, rotation)
        got = _union(pdf_rects(path, 1, 0, len(LINE)))
        assert all(abs(a - b) < 0.002 for a, b in zip(got, expected, strict=True)), rotation


# --- offsets beyond the BMP -------------------------------------------------------------------


def _emoji_pdf(path):
    """Helvetica text whose codes 1 and 2 map to U+1F600 and U+1F601 through a ToUnicode CMap."""
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        b"/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
        b"1 begincodespacerange <00> <FF> endcodespacerange\n"
        b"2 beginbfchar\n<01> <D83DDE00>\n<02> <D83DDE01>\nendbfchar\n"
        b"endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    content = b"BT /F1 12 Tf 50 750 Td (A\\001\\002B hello) Tj 0 -20 Td (second line here) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(cmap) + cmap + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    for number, body in enumerate(objects, start=1):
        data += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    path.write_bytes(data + b"trailer\n<< /Root 1 0 R /Size 7 >>\n%%EOF\n")
    return path


def test_pdf_rects_map_python_offsets_past_non_bmp_characters(tmp_path):
    path = _emoji_pdf(tmp_path / "emoji.pdf")
    text = _page_text(path, 1)
    assert text.startswith("A\U0001f600\U0001f601B hello")
    start = text.index("B hello")
    # pdfium counts UTF-16 units, so "B" is char 5 for pdfium but offset 3 for Python.
    pdf = pdfium.PdfDocument(path)
    page = pdf[0]
    textpage = page.get_textpage()
    assert textpage.count_rects(5, 1) == 1
    left, bottom, right, top = textpage.get_rect(0)
    textpage.close()
    page.close()
    pdf.close()
    [(x0, y0, x1, y1)] = pdf_rects(path, 1, start, start + 1)
    assert x0 == pytest.approx(left / 595, abs=0.002)
    assert x1 == pytest.approx(right / 595, abs=0.002)
    assert y0 == pytest.approx(1 - top / 842, abs=0.002)
    # the second line, after the line break, is also placed correctly
    second = text.index("second")
    rects = pdf_rects(path, 1, second, second + len("second line here"))
    assert rects and rects[0][1] > y1


# --- short parts, laziness, locking -----------------------------------------------------------


def _text_doc(*paragraphs: str) -> ParsedDoc:
    units, line = [], 1
    for text in paragraphs:
        units.append(Unit(text=text, line=line))
        line += text.count("\n") + 2
    return ParsedDoc("text", units)


RENT = "The tenant shall pay the rent on the first day of every month."


def test_short_parts_beside_the_anchor_are_part_of_the_span():
    doc = _text_doc(
        "Intro paragraph that is unrelated to anything.",
        "Terms",
        RENT,
        "Total: 5",
        "Closing paragraph that is also unrelated to it.",
    )
    found = locate(doc, f"Terms\n\n{RENT}\n\nTotal: 5")
    assert (found.start, found.end) == (3, 8)  # lines 3 (Terms) to 7 (Total: 5)


def test_a_short_part_far_from_the_anchor_is_ignored():
    doc = _text_doc("Notes", "x" * 200, RENT, "Filler text that sits after the anchor paragraph.")
    found = locate(doc, f"Notes\n\n{RENT}")
    assert (found.start, found.end) == (5, 6)  # "Notes" is two units away, so not adjacent


def test_widening_stops_when_a_part_is_more_than_three_units_away():
    opening = "Opening clause about the premises and their permitted use."
    fillers = [f"Filler paragraph number {i} of the lease." for i in range(5)]
    doc = _text_doc(opening, *fillers, RENT)
    found = locate(doc, f"{opening}\n\n{RENT}")
    assert (found.start, found.end) == (13, 14)  # the opening is 6 units back: not joined


def test_locate_normalises_only_the_units_it_needs(monkeypatch):
    paragraphs = [f"Paragraph {i} says something different about topic {i}." for i in range(300)]
    doc = _text_doc(*paragraphs)
    built = []
    original = viewer._Normalized

    class Counting(original):
        def __init__(self, text):
            built.append(text)
            super().__init__(text)

    monkeypatch.setattr(viewer, "_Normalized", Counting)
    hint = {"kind": "text", "line_start": 2 * 150 + 1, "line_end": 2 * 150 + 1}
    found = locate(doc, paragraphs[150], hint)
    assert found.start == 2 * 150 + 1
    assert built == [paragraphs[150]]  # one index map, for the hit; no other unit was mapped


def _blocked_by_the_lock(call) -> None:
    """Hold PDFIUM_LOCK in this thread; `call` run on another thread must wait for it."""
    done = threading.Event()
    errors = []

    def run():
        try:
            call()
        except Exception as e:  # reported below
            errors.append(e)
        done.set()

    with PDFIUM_LOCK:
        thread = threading.Thread(target=run)
        thread.start()
        assert not done.wait(0.3), "ran while another thread held the pdfium lock"
    thread.join(10)
    assert done.is_set()
    assert not errors


def test_pdfium_calls_wait_for_the_shared_lock(tmp_path, fonts):
    path = make_pdf(tmp_path / "l.pdf", [["lock test line"]])
    _blocked_by_the_lock(lambda: pdf_page_count(path))
    _blocked_by_the_lock(lambda: render_pdf_page(path, 1, 1.0))
    _blocked_by_the_lock(lambda: pdf_rects(path, 1, 0, 4))
    _blocked_by_the_lock(lambda: parse_pdf(path))


# --- chunks produced by the real chunker are found where the chunker says ---------------------


def _chunks(doc):
    chunks = chunk_document(doc, fake_spans, max_tokens=60, overlap=10)
    assert len(chunks) >= 4
    return chunks


def test_every_text_chunk_is_found_at_its_lines(tmp_path):
    paragraphs = []
    for i in range(30):
        if i % 4 == 1:
            paragraphs.append(f"Note {i}.")
        elif i % 4 == 2:
            first = f"Clause {i} says the tenant pays {i * 100} baht each month, in advance,"
            paragraphs.append(first + f"\nand the landlord issues receipt number {i} for it.")
        else:
            paragraphs.append(
                f"Section {i}: the parties agree that item {i} applies on day {i} of the term, "
                f"subject to notice of {i + 7} days and the conditions stated above in full."
            )
    body = "\r\n\r\n".join(p.replace("\n", "\r\n") for p in paragraphs)
    path = tmp_path / "t.txt"
    path.write_bytes(body.encode("utf-8"))
    doc = parse_file(path)
    for c in _chunks(doc):
        found = locate(doc, c.text, c.location)
        assert found is not None, c.text
        assert (found.kind, found.page) == ("text", None)
        assert found.start == c.location["line_start"], c.text
        assert found.end - 1 == c.location["line_end"], c.text


def test_every_docx_chunk_is_found_at_its_paragraphs(tmp_path):
    blocks = []
    for i in range(12):
        blocks.append((1 if i % 4 == 0 else 2, f"Part {i}"))
        blocks.append(
            (0, f"Rule {i} says the tenant must pay {i * 50} baht on day {i} each month.")
        )
        blocks.append((0, f"Ok {i}."))
        blocks.append(
            (0, f"Details of rule {i}: the landlord may inspect with {i + 2} days of notice.")
        )
    doc = parse_file(make_docx(tmp_path / "d.docx", blocks))
    for c in _chunks(doc):
        found = locate(doc, c.text, c.location)
        assert found is not None, c.text
        assert found.kind == "docx"
        assert found.start == c.location["paragraph_start"], c.text
        assert found.end - 1 == c.location["paragraph_end"], c.text


def test_every_pdf_chunk_is_found_on_its_page_with_matching_text(tmp_path, fonts):
    english = [
        [
            f"Clause {p}.{n} the tenant pays {p * 100 + n} baht on day {n} of the month"
            for n in range(1, 6)
        ]
        for p in range(1, 4)
    ]
    thai = [
        [f"ข้อ {p}.{n} ผู้เช่า ต้อง ชำระ ค่าเช่า เดือนละ {p * 100 + n} บาท ทุก วันที่ {n}" for n in range(1, 6)]
        for p in range(4, 7)
    ]
    path = make_pdf(tmp_path / "m.pdf", english + thai)
    doc = parse_file(path)
    chunks = _chunks(doc)
    assert any(c.location["page_start"] != c.location["page_end"] for c in chunks)
    for c in chunks:
        found = locate(doc, c.text, c.location)
        assert found is not None, c.text
        assert found.kind == "pdf"
        assert c.location["page_start"] <= found.page <= c.location["page_end"]
        if c.location["page_start"] == c.location["page_end"]:
            assert found.page == c.location["page_start"]
            assert (found.start, found.end) == (c.location["char_start"], c.location["char_end"])
        covered = " ".join(_page_text(path, found.page)[found.start : found.end].split())
        assert covered
        assert covered in " ".join(c.text.split())
