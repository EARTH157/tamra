"""The M5 OCR quality gate (controller decision 1) on the spike's Thai and Chinese pages and an
English page. Print the numbers with -s; they go into the results doc."""

import statistics
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from docgen import make_pdf
from ocr_eval import cer, photocopy, word_hit

from tamra.ocr import OcrEngine

pytestmark = pytest.mark.assets

FIXTURES = Path(__file__).parent / "fixtures" / "ocr"

ENGLISH = [
    [
        "QDrone 2 Autonomous Air Vehicle Data Sheet",
        "The QDrone 2 is a quadrotor research platform for indoor flight.",
        "It carries an onboard computer, a stereo camera and a depth camera.",
        "Flight time is about 12 minutes with the standard 3S battery pack.",
        "Specifications",
        "Frame size 260 mm diagonal, carbon fibre arms",
        "Weight 1.2 kg including the battery",
        "Onboard computer NVIDIA Jetson Xavier NX",
        "Wireless link 802.11ac dual band",
        "Motion capture markers six passive reflective spheres",
    ],
    [
        "Getting started",
        "Charge the battery fully before the first flight.",
        "Place the vehicle in the centre of the flight area, facing forward.",
        "Start the ground station software and wait for the link light.",
        "Arm the motors only when every person is outside the safety net.",
        "Land at once if the battery warning sounds twice.",
    ],
]


@pytest.fixture(scope="module")
def engine(ocr_dir):
    return OcrEngine.load(ocr_dir)


def _render(path: Path, gray: bool) -> np.ndarray:
    pdf = pdfium.PdfDocument(path)
    try:
        return pdf[0].render(scale=300 / 72, grayscale=gray).to_numpy().copy()
    finally:
        pdf.close()


def _pages(lang: str) -> list[tuple[Path, str]]:
    return [
        (p, p.with_suffix(".txt").read_text(encoding="utf-8"))
        for p in sorted(FIXTURES.glob(f"{lang}_*.pdf"))
    ]


def test_thai_at_300_dpi(engine):
    scores = [cer(truth, engine.read(_render(p, False)).text, "th") for p, truth in _pages("th")]
    print("thai 300 dpi CER", [round(s, 3) for s in scores], round(statistics.mean(scores), 3))
    assert len(scores) == 6
    assert statistics.mean(scores) <= 0.06


def test_chinese_photocopy(engine):
    scores = [
        cer(truth, engine.read(photocopy(_render(p, True), seed)).text, "zh")
        for seed, (p, truth) in enumerate(_pages("zh"))
    ]
    print("chinese photocopy CER", [round(s, 3) for s in scores], round(statistics.mean(scores), 3))
    assert len(scores) == 6
    assert statistics.mean(scores) <= 0.03


def test_english_word_hit(engine, tmp_path):
    doc = pdfium.PdfDocument(make_pdf(tmp_path / "en.pdf", ENGLISH))
    try:
        hits = [
            word_hit(
                doc[i].get_textpage().get_text_range(),
                engine.read(doc[i].render(scale=300 / 72).to_numpy().copy()).text,
            )
            for i in range(len(doc))
        ]
    finally:
        doc.close()
    print("english word hit", [round(h, 3) for h in hits])
    assert statistics.mean(hits) >= 0.9
