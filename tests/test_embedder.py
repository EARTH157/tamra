import numpy as np
import pytest

from tamra.embedder import Embedder

pytestmark = pytest.mark.assets


@pytest.fixture(scope="module")
def embedder(bge_dir):
    return Embedder(bge_dir)


def test_shape_dtype_and_normalisation(embedder):
    vecs = embedder.embed(["hello", "สวัสดี", "你好"])
    assert vecs.shape == (3, 1024)
    assert vecs.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-4)


def test_empty_input(embedder):
    assert embedder.embed([]).shape == (0, 1024)


def test_cross_lingual_similarity(embedder):
    th, en_same, zh_same, en_other = embedder.embed(
        [
            "แมวกำลังนอนหลับอยู่บนโซฟา",
            "A cat is sleeping on the sofa",
            "猫正在沙发上睡觉",
            "The stock market fell sharply today",
        ]
    )
    assert th @ en_same > th @ en_other + 0.1
    assert th @ zh_same > th @ en_other + 0.1


def test_padding_does_not_change_embedding(embedder):
    short = "Tamra answers questions about documents."
    alone = embedder.embed([short])[0]
    batched = embedder.embed([short, short + " " + "padding forces longer batch. " * 20])[0]
    np.testing.assert_allclose(alone, batched, atol=1e-3)
