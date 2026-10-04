"""Build small PDF and DOCX fixtures. Uses Windows system fonts (Tahoma, Microsoft YaHei)."""

import ctypes
from pathlib import Path

import docx
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

FONTS = Path("C:/Windows/Fonts")
LATIN_THAI = FONTS / "tahoma.ttf"
CJK = FONTS / "msyh.ttc"


def make_pdf(path: Path, pages: list[list[str]], font: Path = LATIN_THAI) -> Path:
    """Write a PDF: one inner list per page, one text line per item (empty list = no text)."""
    pdf = pdfium.PdfDocument.new()
    data = font.read_bytes()
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    handle = pdfium_c.FPDFText_LoadFont(pdf, buffer, len(data), pdfium_c.FPDF_FONT_TRUETYPE, True)
    if not handle:
        pdf.close()
        raise RuntimeError(f"pdfium could not load {font}")
    try:
        for lines in pages:
            page = pdf.new_page(595, 842)
            y = 800
            for line in lines:
                obj = pdfium_c.FPDFPageObj_CreateTextObj(pdf, handle, 12.0)
                text = ctypes.create_unicode_buffer(line)
                pdfium_c.FPDFText_SetText(
                    obj, ctypes.cast(text, ctypes.POINTER(pdfium_c.FPDF_WCHAR))
                )
                pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 50, y)
                pdfium_c.FPDFPage_InsertObject(page, obj)
                y -= 20
            pdfium_c.FPDFPage_GenerateContent(page)
            page.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(path)
    finally:
        pdfium_c.FPDFFont_Close(handle)
        pdf.close()
    return path


def make_docx(path: Path, blocks: list[tuple[int, str]]) -> Path:
    """Write a DOCX from (heading level, text) pairs; level 0 is a body paragraph."""
    document = docx.Document()
    for level, text in blocks:
        if level:
            document.add_heading(text, level=level)
        else:
            document.add_paragraph(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return path
