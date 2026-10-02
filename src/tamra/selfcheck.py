"""Exercises every risky dependency; runs from source and from the packaged exe."""

import time
from collections.abc import Callable
from pathlib import Path

import sqlite_vec

from tamra import store

PASSAGE = "Tamra ตอบคำถามจากเอกสารพร้อมอ้างอิงแหล่งที่มา 它会引用来源。 " * 12


def _guard(check: Callable[[], dict]) -> dict:
    try:
        return check()
    except Exception as e:  # report, never crash: this runs on unknown machines
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _check_sqlite() -> dict:
    conn = store.connect(":memory:")
    caps = store.capabilities(conn)
    conn.execute("CREATE VIRTUAL TABLE v USING vec0(embedding float[2])")
    conn.execute(
        "INSERT INTO v(rowid, embedding) VALUES (1, ?)", (sqlite_vec.serialize_float32([1, 0]),)
    )
    hit = conn.execute(
        "SELECT rowid FROM v WHERE embedding MATCH ? AND k = 1",
        (sqlite_vec.serialize_float32([1, 0]),),
    ).fetchone()
    return {"ok": bool(caps["fts5_trigram"]) and hit == (1,), **caps}


def _check_embedding(model_dir: Path) -> dict:
    from tamra.embedder import Embedder

    t0 = time.perf_counter()
    embedder = Embedder(model_dir)
    load_s = time.perf_counter() - t0

    docs = ["แมวกำลังนอนหลับอยู่บนโซฟา", "汽车停在路边"]
    conn = store.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE v USING vec0(embedding float[1024])")
    for rowid, vec in enumerate(embedder.embed(docs), start=1):
        conn.execute("INSERT INTO v(rowid, embedding) VALUES (?, ?)", (rowid, vec.tobytes()))
    query = embedder.embed(["A cat sleeping on a couch"])[0]
    nearest = conn.execute(
        "SELECT rowid FROM v WHERE embedding MATCH ? AND k = 1", (query.tobytes(),)
    ).fetchone()[0]

    t0 = time.perf_counter()
    embedder.embed([PASSAGE] * 32)
    rate = 32 / (time.perf_counter() - t0)
    return {"ok": nearest == 1, "load_s": round(load_s, 2), "passages_per_sec": round(rate, 2)}


def _check_llm(llama_exe: Path, model: Path, log_dir: Path) -> dict:
    from tamra.llm.llama_server import LlamaServer
    from tamra.llm.openai_compat import OpenAICompatibleLLM

    t0 = time.perf_counter()
    with LlamaServer(llama_exe, model, log_dir / "llama-server.log") as srv:
        start_s = time.perf_counter() - t0
        llm = OpenAICompatibleLLM(srv.base_url, "local")
        t1 = time.perf_counter()
        first_token_s, tokens = None, 0
        for _ in llm.generate([{"role": "user", "content": "Count from 1 to 20."}], max_tokens=64):
            first_token_s = first_token_s or time.perf_counter() - t1
            tokens += 1
        gen_s = time.perf_counter() - t1
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
