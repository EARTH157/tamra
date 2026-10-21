"""PP-OCRv5 mobile on onnxruntime: detection, a Thai-first recogniser cascade, character boxes."""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pypdfium2 as pdfium

from tamra.ingest.parsers import PDFIUM_LOCK
from tamra.ocr.geometry import (
    box_score,
    components,
    crop_quad,
    min_area_rect,
    order_clockwise,
    resize_bilinear,
    run_points,
    slice_box,
    sort_boxes,
    unclip,
)

MODEL_DIR = "ppocr-v5"  # under the models folder
DET_FILE = "ch_PP-OCRv5_det_mobile.onnx"
REC_TH_FILE = "th_PP-OCRv5_rec_mobile.onnx"  # Thai + Latin: run on every line
REC_ZH_FILE = "ch_PP-OCRv5_rec_mobile.onnx"  # Chinese + Latin: only where the Thai one is weak
MODEL_FILES = (DET_FILE, REC_TH_FILE, REC_ZH_FILE)

OCR_DPI = 300  # page render resolution for OCR
OCR_THREADS = 4  # onnxruntime intra-op threads: leaves cores for the UI and the embedder
MAX_SIDE = 2000  # the page is scaled to at most this many pixels on its long side
DET_MIN_SIDE = 736
DET_THRESH, BOX_THRESH, UNCLIP_RATIO, MAX_CANDIDATES = 0.3, 0.5, 1.6, 1000
CASCADE = 0.9  # a line the Thai recogniser scores below this is also read as Chinese
MIN_SCORE = 0.5  # lines scoring lower are dropped
REC_HEIGHT, REC_MIN_WIDTH, REC_BATCH = 48, 320, 6


@dataclass(frozen=True)
class OcrPageResult:
    text: str  # the lines in reading order, joined with "\n"
    boxes: np.ndarray  # float32 (len(text), 4): x0, y0, x1, y1 as page fractions; "\n" is zeros


def render_page(path: Path, page: int, dpi: int = OCR_DPI) -> np.ndarray:
    """The 1-based page drawn at `dpi` as an (h, w, 3 or 4) BGR(x) array. PDFium is held only
    while drawing: recognition runs without the lock."""
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            pdf_page = pdf[page - 1]
            try:
                bitmap = pdf_page.render(scale=dpi / 72)
                try:
                    return bitmap.to_numpy().copy()  # a copy: the bitmap is freed below
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        finally:
            pdf.close()


def _session(path: Path, threads: int) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def _normalise(img: np.ndarray) -> np.ndarray:
    return ((img / 255.0 - 0.5) / 0.5).astype(np.float32)


class _Recogniser:
    def __init__(self, path: Path, threads: int):
        self.session = _session(path, threads)
        chars = self.session.get_modelmeta().custom_metadata_map["character"].splitlines()
        self.chars = ["", *chars, " "]  # CTC blank first; PP-OCR adds the space last

    def __call__(
        self, crops: list[np.ndarray]
    ) -> list[tuple[str, float, list[tuple[str, float, float]]]]:
        """(text, mean score, [(char, x0, x1) as fractions of the crop width]) per crop."""
        order = np.argsort([c.shape[1] / c.shape[0] for c in crops], kind="stable")
        out: list = [None] * len(crops)
        for start in range(0, len(crops), REC_BATCH):
            batch = order[start : start + REC_BATCH]
            ratio = max(
                REC_MIN_WIDTH / REC_HEIGHT, *(crops[i].shape[1] / crops[i].shape[0] for i in batch)
            )
            width = int(REC_HEIGHT * ratio)
            x = np.zeros((len(batch), 3, REC_HEIGHT, width), np.float32)
            widths = []
            for n, i in enumerate(batch):
                crop = crops[i]
                w = min(width, math.ceil(REC_HEIGHT * crop.shape[1] / crop.shape[0]))
                x[n, :, :, :w] = _normalise(resize_bilinear(crop, REC_HEIGHT, w)).transpose(2, 0, 1)
                widths.append(w)
            probs = self.session.run(None, {self.session.get_inputs()[0].name: x})[0]
            steps = probs.shape[1]
            for n, i in enumerate(batch):
                ids, best = probs[n].argmax(1), probs[n].max(1)
                text, scores, spans, last = [], [], [], 0
                for t in range(steps):
                    k = int(ids[t])
                    if k and k != last:
                        text.append(self.chars[k])
                        scores.append(float(best[t]))
                        x0 = t * width / steps / widths[n]
                        spans.append(
                            (
                                self.chars[k],
                                min(x0, 1.0),
                                min((t + 1) * width / steps / widths[n], 1.0),
                            )
                        )
                    last = k
                out[i] = ("".join(text), float(np.mean(scores)) if scores else 0.0, spans)
        return out


class OcrEngine:
    def __init__(self, det: ort.InferenceSession, thai: _Recogniser, chinese: _Recogniser):
        self._det, self._thai, self._chinese = det, thai, chinese

    @classmethod
    def load(cls, model_dir: Path, threads: int = OCR_THREADS) -> "OcrEngine":
        return cls(
            _session(model_dir / DET_FILE, threads),
            _Recogniser(model_dir / REC_TH_FILE, threads),
            _Recogniser(model_dir / REC_ZH_FILE, threads),
        )

    def read(self, image: np.ndarray) -> OcrPageResult:
        img = image[:, :, :3] if image.ndim == 3 else np.repeat(image[:, :, None], 3, axis=2)
        h, w = img.shape[:2]
        if max(h, w) > MAX_SIDE:  # RapidOCR's reduce_max_side: sides rounded to multiples of 32
            r = MAX_SIDE / max(h, w)
            rh, rw = round(int(h * r) / 32) * 32, round(int(w * r) / 32) * 32
            img = np.clip(np.rint(resize_bilinear(img, rh, rw)), 0, 255).astype(np.uint8)
            h, w = img.shape[:2]
        boxes = self._detect(img)
        crops = [crop_quad(img, b) for b in boxes]
        lines = self._recognise([c for c, _ in crops])
        text: list[str] = []
        cells: list[tuple[float, float, float, float]] = []
        for box, (_, turned), (line, score, spans) in zip(boxes, crops, lines, strict=True):
            if score < MIN_SCORE or not line.strip():
                continue
            if text:
                text.append("\n")
                cells.append((0.0, 0.0, 0.0, 0.0))
            for char, a, b in spans:
                x0, y0, x1, y1 = slice_box(box, 0.0, 1.0) if turned else slice_box(box, a, b)
                cell = (x0 / w, y0 / h, x1 / w, y1 / h)
                text.append(char)
                cells.extend([cell] * len(char))  # a dictionary entry may hold several code points
        joined = "".join(text)
        grid = np.clip(np.asarray(cells, np.float32).reshape(-1, 4), 0.0, 1.0)
        return OcrPageResult(joined, grid)

    def _detect(self, img: np.ndarray) -> list[np.ndarray]:
        h, w = img.shape[:2]
        r = DET_MIN_SIDE / min(h, w) if min(h, w) < DET_MIN_SIDE else 1.0
        dh, dw = max(32, round(int(h * r) / 32) * 32), max(32, round(int(w * r) / 32) * 32)
        x = _normalise(resize_bilinear(img, dh, dw)).transpose(2, 0, 1)[None]
        prob = self._det.run(None, {self._det.get_inputs()[0].name: x})[0][0, 0]
        mask = prob > DET_THRESH
        mask[:, 1:] |= mask[:, :-1]  # cv2.dilate with a 2x2 kernel
        mask[1:, :] |= mask[:-1, :]
        out = []
        for runs in components(mask)[:MAX_CANDIDATES]:
            box, short = min_area_rect(run_points(runs))
            if short < 3 or box_score(prob, box) < BOX_THRESH:
                continue
            box, short = min_area_rect(unclip(box, UNCLIP_RATIO))
            if short < 5:
                continue
            box[:, 0] = np.clip(np.round(box[:, 0] / dw * w), 0, w)
            box[:, 1] = np.clip(np.round(box[:, 1] / dh * h), 0, h)
            box = order_clockwise(box)
            box[:, 0] = np.clip(box[:, 0], 0, w - 1)
            box[:, 1] = np.clip(box[:, 1], 0, h - 1)
            if int(np.hypot(*(box[0] - box[1]))) <= 3 or int(np.hypot(*(box[0] - box[3]))) <= 3:
                continue
            out.append(box)
        return sort_boxes(out)

    def _recognise(self, crops: list[np.ndarray]) -> list[tuple[str, float, list]]:
        if not crops:
            return []
        lines = self._thai(crops)
        weak = [i for i, line in enumerate(lines) if line[1] < CASCADE]
        if weak:
            for i, line in zip(weak, self._chinese([crops[i] for i in weak]), strict=True):
                if line[1] > lines[i][1]:
                    lines[i] = line
        return lines
