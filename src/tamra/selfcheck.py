"""Exercises every risky dependency; runs from source and from the packaged exe."""

import time
from collections.abc import Callable
from pathlib import Path

PASSAGE = "Tamra ตอบคำถามจากเอกสารพร้อมอ้างอิงแหล่งที่มา 它会引用来源。 " * 12


def _guard(check: Callable[[], dict]) -> dict:
    try:
        return check()
    except Exception as e:  # report, never crash: this runs on unknown machines
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _check_sqlite() -> dict:
    import numpy as np

    from tamra.store import ChunkInput, Store

    store = Store.open(":memory:")
    try:
        caps = store.capabilities()
        collection = store.replace_collection("selfcheck", ".", "selfcheck")
        file_id = store.add_file(collection.id, "probe.txt", 0, 0.0)
        vector = np.zeros(1024, dtype=np.float32)
        vector[0] = 1.0
        location = {"kind": "text", "line_start": 1, "line_end": 1}
        store.replace_file_chunks(
            file_id,
            [ChunkInput("selfcheck probe text", location)],
            vector[None, :],
            content_hash="-",
            note=None,
        )
        dense = store.search_dense(collection.id, vector, 1)
        keyword = store.search_keyword(collection.id, '"pro"', 1)
    finally:
        store.close()
    ok = bool(caps["fts5_trigram"]) and len(dense) == 1 and keyword == [dense[0][0]]
    return {"ok": ok, **caps}


def _check_embedding(model_dir: Path) -> dict:
    from tamra.embedder import Embedder
    from tamra.store import ChunkInput, Store

    t0 = time.perf_counter()
    embedder = Embedder(model_dir)
    load_s = time.perf_counter() - t0

    docs = ["แมวกำลังนอนหลับอยู่บนโซฟา", "汽车停在路边"]
    store = Store.open(":memory:")
    try:
        collection = store.replace_collection("selfcheck", ".", "selfcheck")
        file_id = store.add_file(collection.id, "probe.txt", 0, 0.0)
        chunks = [
            ChunkInput(text, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
            for i, text in enumerate(docs)
        ]
        store.replace_file_chunks(
            file_id, chunks, embedder.embed(docs), content_hash="-", note=None
        )
        query = embedder.embed(["A cat sleeping on a couch"])[0]
        best = store.get_chunks([store.search_dense(collection.id, query, 1)[0][0]])[0]
    finally:
        store.close()

    t0 = time.perf_counter()
    embedder.embed([PASSAGE] * 32)
    rate = 32 / (time.perf_counter() - t0)
    return {
        "ok": best.text == docs[0],
        "load_s": round(load_s, 2),
        "passages_per_sec": round(rate, 2),
    }


def _check_llm(llama_exe: Path, model: Path, log_dir: Path) -> dict:
    from tamra.llm.llama_server import LlamaServer
    from tamra.llm.openai_compat import OpenAICompatibleLLM

    t0 = time.perf_counter()
    with LlamaServer(llama_exe, model, log_dir / "llama-server.log") as srv:
        start_s = time.perf_counter() - t0
        llm = OpenAICompatibleLLM(srv.base_url, "local")
        try:
            t1 = time.perf_counter()
            first_token_s, tokens = None, 0
            for _ in llm.generate(
                [{"role": "user", "content": "Count from 1 to 20."}], max_tokens=64
            ):
                first_token_s = first_token_s or time.perf_counter() - t1
                tokens += 1
            gen_s = time.perf_counter() - t1
        finally:
            llm.close()
        gpu_used = srv.gpu_used
    return {
        "ok": tokens > 0,
        "gpu_used": gpu_used,
        "server_start_s": round(start_s, 2),
        "first_token_s": round(first_token_s or 0, 2),
        "tokens": tokens,
        "tokens_per_sec": round(tokens / gen_s, 2) if gen_s else 0,
    }


def run_selfcheck(
    embed_model_dir: Path | None, llm_model: Path | None, llama_exe: Path, log_dir: Path
) -> dict:
    checks = {"sqlite": _guard(_check_sqlite)}
    if embed_model_dir is not None:
        checks["embedding"] = _guard(lambda: _check_embedding(embed_model_dir))
    if llm_model is not None:
        checks["llm"] = _guard(lambda: _check_llm(llama_exe, llm_model, log_dir))
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}
