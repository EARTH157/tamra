from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


class Embedder:
    """bge-m3 dense embeddings: CLS token of last_hidden_state, L2-normalised."""

    dim = 1024

    def __init__(self, model_dir: Path, max_length: int = 512):
        self._tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tok.enable_truncation(max_length)
        self._tok.enable_padding(pad_id=self._tok.token_to_id("<pad>"), pad_token="<pad>")
        self._session = ort.InferenceSession(
            str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._session.get_inputs()}

    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
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
