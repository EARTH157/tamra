import re

import pytest

from tamra.ingest.chunker import CHUNK_TOKENS, bge_token_spans, chunk_document
from tamra.ingest.parsers import ParsedDoc, Unit


def words(text):
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def chars(text):
    return [(m.start(), m.end()) for m in re.finditer(r"\S", text)]


def test_a_short_document_is_one_chunk():
    doc = ParsedDoc("text", [Unit("alpha beta", line=1), Unit("gamma", line=3)])
    [chunk] = chunk_document(doc, words)
    assert chunk.text == "alpha beta\n\ngamma"
    assert chunk.location == {"kind": "text", "line_start": 1, "line_end": 3}


def test_chunks_respect_the_limit_and_repeat_whole_paragraphs_as_overlap():
    units = [Unit(" ".join(f"p{i}w{j}" for j in range(30)), line=2 * i + 1) for i in range(40)]
    chunks = chunk_document(ParsedDoc("text", units), words, max_tokens=450, overlap=68)
    assert len(chunks) > 2
    assert all(len(words(c.text)) <= 450 for c in chunks)
    for previous, following in zip(chunks, chunks[1:], strict=False):
        tail = "\n\n".join(previous.text.split("\n\n")[-2:])  # 60 tokens fit in 68
        assert following.text.startswith(tail)
    joined = "\n\n".join(c.text for c in chunks)
    assert all(u.text in joined for u in units)


def test_a_long_paragraph_is_split_at_sentence_ends():
    sentence = " ".join(["word"] * 20) + ". "
    unit = Unit(sentence * 40, line=1)  # 800 tokens, one paragraph
    chunks = chunk_document(ParsedDoc("text", [unit]), words, max_tokens=450, overlap=0)
    assert len(chunks) == 2
    assert all(c.text.endswith(".") for c in chunks)
    assert all(len(words(c.text)) <= 450 for c in chunks)


def test_text_without_any_separator_is_cut_at_token_boundaries():
    unit = Unit("ก" * 1000, line=1)  # one run, every character a token
    chunks = chunk_document(ParsedDoc("text", [unit]), chars, max_tokens=450, overlap=0)
    assert [len(c.text) for c in chunks] == [450, 450, 100]
    assert "".join(c.text for c in chunks) == unit.text


def test_pdf_chunks_span_pages_with_character_offsets():
    units = [Unit("Page one text", page=1), Unit("Page two text", page=2)]
    [chunk] = chunk_document(ParsedDoc("pdf", units), words)
    assert chunk.location == {
        "kind": "pdf",
        "page_start": 1,
        "char_start": 0,
        "page_end": 2,
        "char_end": 13,
    }
    assert chunk.text == "Page one text\n\nPage two text"


def test_docx_chunks_record_heading_and_paragraph_range():
    units = [
        Unit("Intro", paragraph=0, heading_path=("H",)),
        Unit("Body", paragraph=1, heading_path=("H",)),
    ]
    [chunk] = chunk_document(ParsedDoc("docx", units), words)
    assert chunk.location == {
        "kind": "docx",
        "heading_path": ["H"],
        "paragraph_start": 0,
        "paragraph_end": 1,
    }


def test_line_range_follows_line_breaks_inside_a_unit():
    [chunk] = chunk_document(ParsedDoc("text", [Unit("one\ntwo\nthree", line=10)]), words)
    assert chunk.location == {"kind": "text", "line_start": 10, "line_end": 12}


def test_windows_line_breaks_are_normalised():
    [chunk] = chunk_document(ParsedDoc("pdf", [Unit("a\r\nb", page=1)]), words)
    assert chunk.text == "a\nb"


def test_whitespace_only_units_produce_no_chunks():
    assert chunk_document(ParsedDoc("text", [Unit("   \n  ", line=1)]), words) == []


@pytest.mark.assets
def test_real_tokenizer_keeps_thai_chunks_near_the_limit(bge_dir):
    spans = bge_token_spans(bge_dir / "tokenizer.json")
    text = "สัญญาเช่าบ้านมีอายุสามปีนับจากวันที่ลงนาม " * 120
    chunks = chunk_document(ParsedDoc("text", [Unit(text, line=1)]), spans)
    assert len(chunks) > 1
    assert all(len(spans(c.text)) <= CHUNK_TOKENS + 8 for c in chunks)
