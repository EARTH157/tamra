from ocr_eval import levenshtein, line_recall

TRUTH = "\n".join(
    [
        "ระเบียบการใช้ห้องปฏิบัติการ",
        "ผู้ใช้ห้องปฏิบัติการทุกคนต้องผ่านการอบรม",
        "ห้ามนำอาหารและเครื่องดื่มเข้ามาในห้อง",
        "เมื่อเกิดอุบัติเหตุ ให้หยุดการทดลองทันที",
    ]
)


def test_levenshtein():
    assert levenshtein("kitten", "sitting") == 3


def test_line_recall_tolerates_a_lost_mark_but_not_a_dropped_line():
    assert line_recall(TRUTH, TRUTH, "th") == 1.0
    assert line_recall(TRUTH, TRUTH.replace("ผู้ใช้", "ผูใช้"), "th") == 1.0
    lines = TRUTH.splitlines()
    assert line_recall(TRUTH, "\n".join(lines[:1] + lines[2:]), "th") == 0.75


def test_line_recall_finds_the_heads_in_order():
    # the dropped middle line's head appears earlier in the text, inside the first line
    truth = "abcdefghijklmnop\nhijklmnopqrs\nzzzzzzzzzzzzzzz"
    assert line_recall(truth, "abcdefghijklmnop\nzzzzzzzzzzzzzzz", "th") < 1.0
