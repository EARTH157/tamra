import re
import zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fakes import fake_spans

from tamra.attribution import (
    NUMBER_PENALTY,
    STRONG,
    WINDOW_STRIDE,
    WINDOW_TOKENS,
    Attributor,
    cited_in,
    numbers,
    windows,
)
from tamra.retriever import trigrams
from tamra.store import MessageRecord, SourceRecord, Store

DIM = 512


def trigram_embed(texts: list[str]) -> np.ndarray:
    """Deterministic unit vectors from character trigrams, so cosine tracks shared text."""
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    for row, text in enumerate(texts):
        for gram in trigrams(text):
            out[row, zlib.crc32(gram.encode("utf-8")) % DIM] += 1.0
        norm = np.linalg.norm(out[row])
        if norm:
            out[row] /= norm
        else:
            out[row, 0] = 1.0
    return out


class CountingEmbed:
    def __init__(self):
        self.batches: list[list[str]] = []

    def __call__(self, texts):
        self.batches.append(list(texts))
        return trigram_embed(texts)

    def embedded(self, text: str) -> int:
        return sum(1 for batch in self.batches for t in batch if t == text)


def same_embed(texts: list[str]) -> np.ndarray:
    """Every text gets the same vector (cosine 1), so only the number rules decide."""
    return np.ones((len(texts), 4), dtype=np.float32) / 2


def source(n: int, text: str) -> SourceRecord:
    return SourceRecord(
        n=n,
        chunk_id=n,
        file_id=n,
        rel_path=f"doc{n}.txt",
        text=text,
        location={"kind": "text", "line_start": 1, "line_end": 1},
        file_hash="h",
    )


def message(sources: list[SourceRecord], content: str = "", message_id: int = 1) -> MessageRecord:
    return MessageRecord(
        id=message_id,
        role="assistant",
        content=content,
        provider="local",
        model="m",
        created_at="2026-10-05T00:00:00",
        sources=tuple(sources),
    )


LEASE = "The lease term is three years, starting on 1 March 2026. Rent is due on the 5th."
PETS = "Cats are allowed. Dogs and other animals are not allowed in the apartment."
QUOTE = "Dogs and other animals are not allowed"  # part of PETS, so never equal to a window
PARKING = "One parking space on level B2 is included in the rent. Guests park on level B1."


def sources3() -> list[SourceRecord]:
    return [source(1, LEASE), source(2, PETS), source(3, PARKING)]


def attributor(embed=None, **kwargs) -> Attributor:
    return Attributor(embed or trigram_embed, fake_spans, **kwargs)


def words(count: int) -> str:
    return " ".join(f"word{i}" for i in range(count))


# --- windows ---


def test_a_short_text_is_one_window():
    text = words(WINDOW_TOKENS)
    assert windows(text, fake_spans) == [(0, len(text))]
    assert windows("", fake_spans) == [(0, 0)]


def test_windows_cover_the_text_and_overlap_by_half():
    text = words(150)
    ranges = windows(text, fake_spans)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(text)
    for (a_start, a_end), (b_start, b_end) in zip(ranges, ranges[1:], strict=False):
        assert b_start > a_start and b_end > a_end
        assert b_start < a_end  # consecutive windows overlap
    for start, end in ranges:
        assert len(fake_spans(text[start:end])) <= WINDOW_TOKENS
    covered = set()
    for start, end in ranges:
        covered.update(range(start, end))
    assert covered >= {i for i, c in enumerate(text) if not c.isspace()}
    first, second = ranges[0], ranges[1]
    overlap_words = len(fake_spans(text[second[0] : first[1]]))
    assert overlap_words == WINDOW_TOKENS - WINDOW_STRIDE


def test_the_last_window_ends_the_text_without_a_tiny_tail():
    text = words(WINDOW_TOKENS + 5)
    ranges = windows(text, fake_spans)
    assert ranges[-1][1] == len(text)
    assert len(fake_spans(text[ranges[-1][0] : ranges[-1][1]])) >= WINDOW_STRIDE


# --- cited_in ---


def test_cited_in_finds_markers_in_the_selection():
    assert cited_in("Rent is due on the 5th [2].", "due on the 5th [2]") == {2}


def test_cited_in_uses_the_sentence_containing_the_selection():
    content = "The lease is three years [1]. Cats are allowed [2][3]. Parking is free."
    assert cited_in(content, "Cats are allowed") == {2, 3}
    assert cited_in(content, "three years") == {1}
    assert cited_in(content, "Parking is free") == set()


def test_a_marker_after_the_full_stop_belongs_to_the_sentence():
    content = "Cats are allowed.[2] Dogs are not. [1]"
    assert cited_in(content, "Cats") == {2}
    assert cited_in(content, "Dogs are not") == {1}


def test_cited_in_handles_cjk_ends_newlines_and_decimals():
    content = "沙发保修五年。[1]床垫保修十年。[2]\nThe fee is 1.5 baht [3]"
    assert cited_in(content, "沙发保修五年") == {1}
    assert cited_in(content, "床垫") == {2}
    assert cited_in(content, "1.5 baht") == {3}


def test_a_thai_marker_followed_by_a_space_ends_the_sentence():
    content = "ลาพักร้อนได้ 12 วัน [1] ส่วนลาป่วยได้ไม่เกิน 30 วัน [2] และที่พักคืนละ 1,500 บาท [3]"
    assert cited_in(content, "ลาป่วยได้ไม่เกิน 30 วัน") == {2}
    assert cited_in(content, "ลาพักร้อนได้ 12 วัน") == {1}
    assert cited_in(content, "ที่พักคืนละ 1,500 บาท") == {3}


def test_cited_in_with_no_marker_or_unknown_text_is_empty():
    assert cited_in("No markers here.", "No markers") == set()
    assert cited_in("Rent is due [1].", "something the answer never said") == set()
    assert cited_in("", "") == set()


# --- numbers ---


def test_numbers_normalise_commas_and_thai_digits():
    assert numbers("rent 18,500 baht, 1.5% and ๑๒ days; room B2") == {"18500", "1.5", "12", "2"}
    assert numbers("no digits here") == frozenset()


def test_numbers_keep_lists_apart_and_drop_thousands_commas_only():
    assert numbers("1,2,3") == {"1", "2", "3"}
    assert numbers("1,500 and 12,000,000") == {"1500", "12000000"}
    assert numbers("1,50") == {"1", "50"}


def test_equal_values_in_different_formats_agree():
    assert numbers("7:00") == numbers("07.00") == numbers("7") == {"7"}
    assert numbers("15:00") == numbers("15.00 น.")
    assert numbers("1.50") == numbers("1.5") == {"1.5"}
    assert numbers("7:05") == numbers("07.05") == {"7.05"}
    assert numbers("0") == {"0"} and numbers("007") == {"7"}
    assert numbers("0.05") == {"0.05"}


# --- attribute ---


def test_an_exact_quote_ranks_its_source_first_as_strong():
    matches = attributor().attribute(message(sources3()), PETS)
    assert matches[0].n == 2
    assert matches[0].label == "strong"
    assert 0 <= matches[0].start < matches[0].end <= len(PETS)


def test_only_restricts_the_candidates():
    matches = attributor().attribute(message(sources3()), PETS, only=1)
    assert all(m.n == 1 for m in matches)
    only_pets = attributor().attribute(message(sources3()), PETS, only=2)
    assert [m.n for m in only_pets] == [2]


def test_unrelated_text_matches_nothing():
    assert attributor().attribute(message(sources3()), "Quarterly stock buyback programme") == []


def test_an_empty_selection_or_no_sources_matches_nothing():
    assert attributor().attribute(message(sources3()), "   ") == []
    assert attributor().attribute(message([]), PETS) == []


def test_a_citation_marker_breaks_a_near_tie():
    twins = [source(1, "Fees are charged monthly."), source(2, "Fees are charged weekly.")]
    selection = "Fees are charged"
    plain = attributor().attribute(message(twins), selection)
    cited = attributor().attribute(message(twins, content="Fees are charged [2]."), selection)
    assert {m.n for m in plain} == {1, 2}
    assert cited[0].n == 2


def test_a_number_the_source_lacks_loses_to_the_source_that_has_it():
    twins = [
        source(1, "Unused leave carries over for up to 6 days."),
        source(2, "Unused leave carries over for up to 10 days."),
    ]
    matches = attributor().attribute(message(twins), "Unused leave carries over for up to 10 days.")
    assert [m.n for m in matches[:2]] == [2, 1]
    assert matches[0].score - matches[1].score > NUMBER_PENALTY / 2


def test_cited_sources_rank_first_even_with_a_lower_score():
    twins = [source(1, "Fees are charged monthly."), source(2, "Fees are charged weekly.")]
    content = "Fees are charged monthly [2]."
    matches = attributor().attribute(message(twins, content=content), "Fees are charged monthly")
    assert [m.n for m in matches] == [2, 1]
    assert matches[0].score < matches[1].score


def test_a_cited_source_below_the_partial_floor_still_matches_nothing():
    twins = [source(1, "Fees are charged monthly."), source(2, "Parking is on level B2.")]
    content = "Fees are charged monthly [2]."
    matches = attributor().attribute(message(twins, content=content), "Fees are charged monthly")
    assert [m.n for m in matches] == [1]


def test_citation_markers_in_the_selection_are_not_part_of_the_claim():
    text = "The rent is due on the fifth day of each month."
    msg = message([source(1, text)])
    plain = attributor().attribute(msg, text)
    marked = attributor().attribute(msg, text + " [7]")
    assert [(m.n, m.label) for m in marked] == [(1, "strong")]
    assert marked[0].score == pytest.approx(plain[0].score)
    assert attributor().attribute(msg, "[1]") == []


def test_a_marker_before_punctuation_leaves_no_stray_space():
    text = "The rent is due on the fifth day of each month."
    msg = message([source(1, text)])
    plain = attributor().attribute(msg, text)
    marked = attributor().attribute(msg, text[:-1] + " [1].")
    assert marked[0].score == pytest.approx(plain[0].score)


def test_a_number_in_one_window_but_not_another_is_penalised_there():
    twins = [
        source(1, "The monthly rent is 18,500 baht, due on the 5th day of each month."),
        source(2, "The monthly rent is 21,000 baht, due on the 5th day of each month."),
    ]
    matches = attributor().attribute(message(twins), twins[1].text)
    assert [(m.n, m.label) for m in matches[:1]] == [(2, "strong")]
    assert matches[0].score - matches[1].score > NUMBER_PENALTY / 2


def test_a_number_no_source_has_caps_the_label_at_partial():
    lease = source(1, "The monthly rent is 18,500 baht, due on the 5th day of each month.")
    exact = attributor().attribute(message([lease]), lease.text)
    wrong = attributor().attribute(message([lease]), lease.text.replace("18,500", "25,000"))
    assert exact[0].label == "strong"
    assert wrong[0].n == 1 and wrong[0].label == "partial"
    assert wrong[0].score > STRONG  # the score is not lowered, only the label is capped


def test_a_computed_total_in_the_same_language_caps_the_label():
    claim = "ค่าที่พักในกรุงเทพฯ เบิกได้ไม่เกินคืนละ 1,500 บาท ต่างจังหวัดไม่เกินคืนละ 1,200 บาท"
    hotel = source(1, claim)
    assert attributor().attribute(message([hotel]), claim)[0].label == "strong"
    total = attributor().attribute(message([hotel]), claim + " รวมสองคืน 2,700 บาท")
    assert total[0].n == 1 and total[0].label == "partial"


def test_numbers_are_compared_across_languages():
    thai = source(1, "ค่าที่พักในกรุงเทพฯ เบิกได้ไม่เกินคืนละ 1,500 บาท")
    a = Attributor(same_embed, fake_spans)
    ok = a.attribute(message([thai]), "The hotel allowance in Bangkok is 1,500 baht a night.")
    assert ok[0].label == "strong"  # nothing contradicts the source
    total = a.attribute(
        message([thai], message_id=2),
        "The hotel allowance is 1,500 baht a night, 4,500 baht for three nights.",
    )
    assert total[0].label == "partial"  # 4,500 is in no source
    two = message([thai, source(2, "ค่าเบี้ยเลี้ยงวันละ 270 บาท")], message_id=3)
    assert a.attribute(two, "The allowance is 1,500 baht")[0].n == 1  # the number breaks the tie


def test_times_written_differently_are_not_penalised():
    canteen = source(1, "โรงอาหารเปิดให้บริการเวลา 07.00 ถึง 15.00 น. ทุกวันจันทร์ถึงวันศุกร์")
    other = source(2, "ห้องประชุมเปิดเวลา 09.00 ถึง 18.00 น.")
    a = Attributor(same_embed, fake_spans)
    matches = a.attribute(message([canteen, other]), "The canteen is open from 7:00 to 15:00.")
    assert matches[0].n == 1
    assert matches[0].score - matches[1].score >= NUMBER_PENALTY * 2 - 1e-6


def test_window_embeddings_are_cached_per_message():
    embed = CountingEmbed()
    a = attributor(embed)
    msg = message(sources3())
    a.attribute(msg, QUOTE)
    a.attribute(msg, "The lease term is three years")
    for text in (LEASE, PETS, PARKING):  # short sources are a single window each
        assert embed.embedded(text) == 1
    a.attribute(message(sources3(), message_id=2), QUOTE)
    assert embed.embedded(PETS) == 2


def test_the_cache_keeps_only_the_most_recent_messages():
    embed = CountingEmbed()
    a = attributor(embed, cache_size=2)
    for message_id in (1, 2, 3):
        a.attribute(message(sources3(), message_id=message_id), QUOTE)
    assert embed.embedded(PETS) == 3
    a.attribute(message(sources3(), message_id=3), QUOTE)  # still cached
    assert embed.embedded(PETS) == 3
    a.attribute(message(sources3(), message_id=1), QUOTE)  # was evicted
    assert embed.embedded(PETS) == 4


def test_only_embeds_just_that_source_and_later_calls_add_the_rest():
    embed = CountingEmbed()
    a = attributor(embed)
    msg = message(sources3())
    a.attribute(msg, QUOTE, only=2)
    assert embed.embedded(PETS) == 1 and embed.embedded(LEASE) == 0
    a.attribute(msg, QUOTE)
    assert embed.embedded(PETS) == 1 and embed.embedded(LEASE) == 1


def test_a_reused_message_id_never_serves_the_old_windows(tmp_path):
    old_text = " ".join(f"old{i}" for i in range(200))
    new_text = "Cats are allowed. Dogs are not."
    store = Store.open(tmp_path / "tamra.db")
    try:
        a = attributor()
        chat = store.create_chat()
        first_id = store.add_assistant_message(
            chat.id, "Old answer [1].", provider=None, model=None, sources=[source(1, old_text)]
        )
        assert a.attribute(store.get_message(first_id), words(60).replace("word", "old"))[0].n == 1
        assert store.delete_chat(chat.id)

        chat = store.create_chat()
        second_id = store.add_assistant_message(
            chat.id, "New answer [1].", provider=None, model=None, sources=[source(1, new_text)]
        )
        assert second_id == first_id  # SQLite reuses the id: the cache must not trust it
        matches = a.attribute(store.get_message(second_id), new_text)
        assert [(m.n, m.label) for m in matches] == [(1, "strong")]
        assert all(0 <= m.start < m.end <= len(new_text) for m in matches)
    finally:
        store.close()


def test_a_long_single_source_gives_up_to_three_separate_windows():
    block = " ".join(f"blk{i}" for i in range(WINDOW_TOKENS))
    filler = " ".join(f"fill{i}" for i in range(WINDOW_TOKENS * 2))
    text = f"{block} {filler} {block}"  # the block sits in the first and the last window
    ranges = windows(text, fake_spans)
    matches = attributor().attribute(message([source(1, text)]), block)
    assert 2 <= len(matches) <= 3
    assert {(m.start, m.end) for m in matches[:2]} == {ranges[0], ranges[-1]}
    spans = [(m.start, m.end) for m in matches]
    for i, (a_start, a_end) in enumerate(spans):
        for b_start, b_end in spans[i + 1 :]:
            assert a_end <= b_start or b_end <= a_start


def test_at_most_one_window_per_source_when_there_are_several():
    text = " ".join(["Cats are allowed in the apartment."] * 40)
    sources = [source(1, text), source(2, PETS), source(3, text), source(4, text)]
    matches = attributor().attribute(message(sources), "Cats are allowed in the apartment.")
    assert len(matches) <= 3
    assert len({m.n for m in matches}) == len(matches)
    assert [m.score for m in matches] == sorted((m.score for m in matches), reverse=True)


def test_the_selection_is_matched_to_the_window_that_holds_it():
    filler = " ".join(f"filler{i}" for i in range(100))
    text = f"{filler} {PETS} {filler}"
    matches = attributor().attribute(message([source(1, text)]), PETS)
    assert matches
    best = matches[0]
    assert "Dogs and other animals" in text[best.start : best.end]


def test_attribution_is_thread_safe():
    embed = CountingEmbed()
    a = attributor(embed)
    msg = message(sources3())
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: a.attribute(msg, QUOTE)[0].n, range(16)))
    assert results == [2] * 16
    assert embed.embedded(PETS) == 1


# --- calibration cases ---


def test_the_calibration_cases_are_well_formed():
    from eval_attribution import load_cases

    cases = load_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    main = [c for c in cases if c.get("group", "main") == "main"]
    assert len(main) >= 24
    assert {c.get("group", "main") for c in cases} == {"main", "heldout", "hard_negative"}
    assert all(c["expect"] == "none" for c in cases if c.get("group") == "hard_negative")
    assert {c["expect"] for c in main} == {"strong", "partial", "none"}
    for case in cases:
        numbers = {s["n"] for s in case["sources"]}
        assert case["expect"] == "none" or case["expect_n"] in numbers
        assert case["expect"] != "none" or case["expect_n"] is None
    selections = " ".join(c["selection"] for c in cases)
    assert re.search(r"[฀-๿]", selections)
    assert re.search(r"[一-鿿]", selections)


@pytest.mark.assets
def test_the_calibration_set_meets_the_accuracy_bar(bge_dir):
    from eval_attribution import load_cases, run_cases

    from tamra.embedder import Embedder
    from tamra.ingest.chunker import bge_token_spans

    embedder = Embedder.load(bge_dir)
    real = Attributor(embedder.embed, bge_token_spans(bge_dir / "tokenizer.json"))
    report = run_cases(real, load_cases())
    assert report["n_accuracy"] >= 0.9
    assert report["false_matches"] == []
    assert report["strong_on_wrong_source"] == []
    rows = {r["id"]: r for r in report["rows"]}
    assert rows["en-wrong-rent"]["got"] == "partial"  # the misquoted number caps the label
    assert rows["en-rent-21000"]["got_n"] == 2


def test_prepare_caches_every_window_without_embedding_a_selection():
    embed = CountingEmbed()
    a = attributor(embed)
    msg = message(sources3())
    a.prepare(msg)
    assert sorted(t for batch in embed.batches for t in batch) == sorted([LEASE, PETS, PARKING])
    calls = len(embed.batches)
    a.attribute(msg, QUOTE)
    assert len(embed.batches) == calls + 1  # only the selection
    assert embed.embedded(LEASE) == 1
