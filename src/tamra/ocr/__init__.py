"""OCR of scanned PDF pages (spec §10): PP-OCRv5 mobile on onnxruntime and numpy."""

from tamra.ocr.engine import (
    MODEL_DIR,
    MODEL_FILES,
    OCR_DPI,
    OcrEngine,
    OcrPageResult,
    render_page,
)

__all__ = ["MODEL_DIR", "MODEL_FILES", "OCR_DPI", "OcrEngine", "OcrPageResult", "render_page"]
