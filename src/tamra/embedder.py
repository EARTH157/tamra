"""bge-m3 dense embeddings with ONNX Runtime on the CPU."""

from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_ID = "bge-m3-int8@Xenova/bge-m3:4de13258"  # the export pinned in scripts/assets.json
MODEL_FILES = ("tokenizer.json", "model.onnx")


class Embedder:
    """bge-m3 dense embeddings: CLS token of last_hidden_state, L2-normalised."""

    dim = 1024

    def __init__(self, tokenizer: Tokenizer, session, max_length: int = 512):
        """session: an onnxruntime.InferenceSession (or anything with get_inputs and run)."""
        self._tok = tokenizer
        self._tok.enable_truncation(max_length)
        self._tok.enable_padding(pad_id=tokenizer.token_to_id("<pad>"), pad_token="<pad>")
        self._session = session
        self._input_names = {i.name for i in session.get_inputs()}

    @classmethod
    def load(cls, model_dir: Path, max_length: int = 512) -> "Embedder":
        """Load tokenizer.json and model.onnx from model_dir."""
        for name in MODEL_FILES:
            if not (model_dir / name).is_file():
                raise FileNotFoundError(f"embedding model file not found: {model_dir / name}")
        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        session = ort.InferenceSession(
            str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        return cls(tokenizer, session, max_length)

    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        if batch_size < 1:
            raise ValueError(f"batch_size must be at least 1, got {batch_size}")
        out = []
        for start in range(0, len(texts), batch_size):
            encodings = self._tok.encode_batch(texts[start : start + batch_size])
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            feeds = {
                "input_ids": ids,
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
            }
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feeds)[0]
            cls = hidden[:, 0, :]
            out.append(cls / np.linalg.norm(cls, axis=1, keepdims=True))
        if not out:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack(out).astype(np.float32)
