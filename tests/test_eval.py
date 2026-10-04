import pytest
from build_eval_corpus import build, targets
from docgen import CJK, LATIN_THAI
from eval_retrieval import first_hit_rank, hit_rates, load_questions, run_eval, sweep_thresholds

from tamra.ingest.parsers import parse_file
from tamra.store import ChunkRecord


def test_questions_are_well_formed():
    questions = load_questions()
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids))
    assert {q["lang"] for q in questions} == {"th", "en", "zh"}
    assert all(set(q) == {"id", "lang", "question", "file", "page"} for q in questions)
    names = set(targets())
    answerable = [q for q in questions if q["file"] is not None]
    assert len(answerable) >= 20
    assert len(questions) - len(answerable) >= 4
    assert all(q["file"] in names for q in answerable)
    assert all(q["page"] is None or q["file"].endswith(".pdf") for q in questions)


@pytest.mark.skipif(
    not (LATIN_THAI.exists() and CJK.exists()), reason="needs Tahoma and Microsoft YaHei"
)
def test_the_corpus_renders_into_parseable_documents(tmp_path):
    paths = build(tmp_path)
    assert sorted(p.name for p in paths) == targets()
    for path in paths:
        assert parse_file(path).units, path.name
    canteen = parse_file(tmp_path / "canteen-rules.txt")
    assert "โรงอาหาร" in canteen.units[0].text


def chunk(rel_path, location):
    return ChunkRecord(1, 1, rel_path, 0, "text", location, None)


def test_first_hit_rank_checks_the_file_and_page():
    page_one = {"kind": "pdf", "page_start": 1, "page_end": 1, "char_start": 0, "char_end": 9}
    both_pages = {**page_one, "page_end": 2}
    chunks = [
        chunk("other.md", {"kind": "text", "line_start": 1, "line_end": 2}),
        chunk("lease.pdf", page_one),
        chunk("lease.pdf", both_pages),
    ]
    assert first_hit_rank({"file": "lease.pdf", "page": 2}, chunks) == 3
    assert first_hit_rank({"file": "other.md", "page": None}, chunks) == 1
    assert first_hit_rank({"file": "missing.md", "page": None}, chunks) is None


def test_hit_rates_count_only_answerable_questions():
    results = [
        {"answerable": True, "rank": 1},
        {"answerable": True, "rank": 3},
        {"answerable": True, "rank": None},
        {"answerable": False, "rank": None},
    ]
    assert hit_rates(results, ks=(1, 4)) == {"hit@1": 1 / 3, "hit@4": 2 / 3}


def test_the_threshold_sweep_separates_answerable_from_off_topic_questions():
    results = [
        {"answerable": True, "best_similarity": 0.62},
        {"answerable": True, "best_similarity": 0.55},
        {"answerable": False, "best_similarity": 0.41},
        {"answerable": False, "best_similarity": 0.35},
    ]
    sweep = sweep_thresholds(results)
    assert sweep["best_accuracy"] == 1.0
    assert sweep["range"] == [0.42, 0.55]
    assert 0.42 <= sweep["recommended"] <= 0.55


@pytest.mark.assets
def test_retrieval_meets_the_m1_floor(bge_dir, tmp_path):
    corpus = tmp_path / "corpus"
    build(corpus)
    report = run_eval(corpus, load_questions(), bge_dir)
    assert set(report["files"].values()) == {"indexed"}
    assert report["hit_rates"]["hit@4"] >= 0.85
