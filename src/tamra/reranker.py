"""bge-reranker-v2-m3 cross-encoder scores with ONNX Runtime on the CPU."""

from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_DIR = "bge-reranker-v2-m3"  # under the models directory, as in the catalog
MODEL_FILES = ("tokenizer.json", "model.onnx")


class Reranker:
    """Scores (query, passage) pairs: sigmoid of the model's single logit, 0 to 1."""

    def __init__(self, tokenizer: Tokenizer, session, max_length: int = 512):
        """session: an onnxruntime.InferenceSession (or anything with get_inputs and run)."""
        self._tok = tokenizer
        # longest_first truncation: the long passage is cut, not the short query
        self._tok.enable_truncation(max_length)
        self._tok.enable_padding(pad_id=tokenizer.token_to_id("<pad>"), pad_token="<pad>")
        self._session = session
        self._input_names = {i.name for i in session.get_inputs()}

    @classmethod
    def load(cls, model_dir: Path, max_length: int = 512) -> "Reranker":
        """Load tokenizer.json and model.onnx from model_dir."""
        for name in MODEL_FILES:
            if not (model_dir / name).is_file():
                raise FileNotFoundError(f"reranker model file not found: {model_dir / name}")
        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        session = ort.InferenceSession(
            str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        return cls(tokenizer, session, max_length)

    def score(self, query: str, passages: list[str], batch_size: int = 8) -> np.ndarray:
        """One relevance score per passage, in the passages' order."""
        if batch_size < 1:
            raise ValueError(f"batch_size must be at least 1, got {batch_size}")
        out = []
        for start in range(0, len(passages), batch_size):
            pairs = [(query, p) for p in passages[start : start + batch_size]]
            encodings = self._tok.encode_batch(pairs)
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            feeds = {
                "input_ids": ids,
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
            }
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            logits = self._session.run(None, feeds)[0]
            out.append(1.0 / (1.0 + np.exp(-logits[:, 0])))
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out).astype(np.float32)
