"""The MCP discovery file (docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §4)."""

import pytest

from tamra.discovery import FILE_NAME, read_discovery, remove_discovery, write_discovery


def test_written_values_read_back_and_no_temporary_file_is_left(tmp_path):
    write_discovery(tmp_path, 5123, "tok")
    assert read_discovery(tmp_path) == (5123, "tok")
    assert [p.name for p in tmp_path.iterdir()] == [FILE_NAME]


def test_a_new_launch_replaces_the_old_values(tmp_path):
    write_discovery(tmp_path, 1111, "old")
    write_discovery(tmp_path, 2222, "new")
    assert read_discovery(tmp_path) == (2222, "new")


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{",
        "[]",
        '{"port": 5123}',
        '{"port": "5123", "token": "t"}',
        '{"port": true, "token": "t"}',
        '{"port": 0, "token": "t"}',
        '{"port": 70000, "token": "t"}',
        '{"port": 5123, "token": ""}',
        '{"port": 5123, "token": 7}',
    ],
)
def test_a_missing_or_damaged_file_reads_as_none(tmp_path, content):
    assert read_discovery(tmp_path) is None
    (tmp_path / FILE_NAME).write_text(content, encoding="utf-8")
    assert read_discovery(tmp_path) is None


def test_remove_is_quiet_when_there_is_nothing_to_remove(tmp_path):
    remove_discovery(tmp_path)
    write_discovery(tmp_path, 5123, "tok")
    remove_discovery(tmp_path)
    assert read_discovery(tmp_path) is None
