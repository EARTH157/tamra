"""Scoring and scan simulation for the OCR quality gate (the M5 spike's method, numpy only)."""

import re
import unicodedata
from collections import Counter

import numpy as np


def normalise(text: str, lang: str) -> str:
    """NFKC; English keeps single spaces, Thai and Chinese drop all whitespace."""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip() if lang == "en" else re.sub(r"\s+", "", text)


def _last_row(a: str, b: str, anywhere: bool = False) -> list[int]:
    """The last row of the edit-distance table from a to b; with anywhere=True, entry j is the
    distance from a to the closest substring of b that ends at j."""
    previous = [0] * (len(b) + 1) if anywhere else list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous


def levenshtein(a: str, b: str) -> int:
    return _last_row(a, b)[-1]


def cer(truth: str, ocr: str, lang: str) -> float:
    t, o = normalise(truth, lang), normalise(ocr, lang)
    return levenshtein(t, o) / max(len(t), 1)


def line_recall(truth: str, ocr: str, lang: str, head: int = 10, edits: int = 2) -> float:
    """The share of truth paragraph heads (the first `head` normalised characters of each truth
    line) found in the OCR text in order, each after the previous match, with at most `edits`
    edits: a dropped line fails it, a lost tone mark does not (CER counts those)."""
    o, start, found = normalise(ocr, lang), 0, 0
    heads = [normalise(t, lang)[:head] for t in truth.splitlines() if t.strip()]
    for h in heads:
        row = _last_row(h, o[start:], anywhere=True)
        if min(row) <= edits:
            found += 1
            start += row.index(min(row))
    return found / max(len(heads), 1)


def word_hit(truth: str, ocr: str) -> float:
    """The share of truth words (lowercased, punctuation stripped, as a multiset) in the OCR."""

    def words(s: str) -> Counter:
        found = (re.sub(r"[^\w]", "", w.lower()) for w in unicodedata.normalize("NFKC", s).split())
        return Counter(w for w in found if w)

    t, o = words(truth), words(ocr)
    return sum((t & o).values()) / max(sum(t.values()), 1)


def photocopy(gray: np.ndarray, seed: int, degrees: float = 1.0, noise: float = 18.0) -> np.ndarray:
    """A grayscale page with Gaussian noise, turned `degrees` counter-clockwise on white
    (canvas expanded, bilinear), as the spike made its photocopy variant with PIL."""
    rng = np.random.default_rng(seed)
    noisy = np.clip(gray.astype(np.float32) + rng.normal(0, noise, gray.shape), 0, 255)
    h, w = noisy.shape
    t = np.radians(degrees)
    c, s = np.cos(t), np.sin(t)
    out_w = int(np.ceil(abs(w * c) + abs(h * s)))
    out_h = int(np.ceil(abs(w * s) + abs(h * c)))
    ys, xs = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
    xs, ys = xs - out_w / 2, ys - out_h / 2
    sx = c * xs - s * ys + w / 2  # inverse rotation of each output pixel into the source
    sy = s * xs + c * ys + h / 2
    x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
    fx, fy = sx - x0, sy - y0
    padded = np.pad(noisy, 1, constant_values=255)

    def at(y, x):
        return padded[np.clip(y + 1, 0, h + 1), np.clip(x + 1, 0, w + 1)]

    out = (at(y0, x0) * (1 - fx) + at(y0, x0 + 1) * fx) * (1 - fy) + (
        at(y0 + 1, x0) * (1 - fx) + at(y0 + 1, x0 + 1) * fx
    ) * fy
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)
