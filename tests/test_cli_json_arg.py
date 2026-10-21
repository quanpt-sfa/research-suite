"""`_json_arg` takes inline JSON or a path to a JSON file, on every platform.

Regression: on Linux, `Path(<inline JSON longer than 255 bytes>).is_file()` raises `OSError: [Errno 36] File name too long`;
Windows returns False, so the bug was invisible locally and broke CI from M3 on.
"""
from __future__ import annotations

import json

import pytest

from research_harness.cli_knowledge import _json_arg


def test_json_arg_inline_value_longer_than_255_bytes():
    steps = [{"id": f"s_step_{i:02d}", "element": f"E{i:02d}", "fidelity": "omitted", "omit_reason": "not_applicable"} for i in range(6)]
    raw = json.dumps(steps)
    assert len(raw.encode("utf-8")) > 255
    assert _json_arg(raw, "--steps") == steps


def test_json_arg_inline_value_with_leading_whitespace():
    assert _json_arg('  {"a": 1}', "--x") == {"a": 1}


def test_json_arg_file_path_still_read(tmp_path):
    f = tmp_path / "steps.json"
    f.write_text(json.dumps([{"id": "s1"}]), encoding="utf-8")
    assert _json_arg(str(f), "--steps") == [{"id": "s1"}]


def test_json_arg_bracket_file_name(tmp_path):
    # a file whose name starts with a bracket is still read when given as a path that does not start with one
    f = tmp_path / "[steps].json"
    f.write_text('{"ok": true}', encoding="utf-8")
    assert _json_arg(str(f), "--steps") == {"ok": True}


def test_json_arg_not_json_and_not_a_file():
    with pytest.raises(ValueError, match="not valid JSON"):
        _json_arg("no-such-file.json", "--steps")


def test_json_arg_none():
    assert _json_arg(None, "--steps") is None
