from pathlib import Path

import pytest

from yall_run.model import load_spec


def write_spec(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "Yallfile"
    path.write_text(text)
    return path


def test_set_can_reference_imported_environment_value(tmp_path, monkeypatch):
    monkeypatch.setenv("LFHCAL_WORK", "/scratch/paul/lfhcal")
    source = write_spec(tmp_path, """campaign nested-static
@env LFHCAL_WORK
@set WORK {LFHCAL_WORK}/lfhcal-simple
@set RESULT {WORK}/result

prepare:
    @output result {RESULT}/done.txt
    echo {WORK}
""")
    task = load_spec(source).tasks[0]
    assert task.command == ("echo", "/scratch/paul/lfhcal/lfhcal-simple")
    assert task.outputs[0].path == "/scratch/paul/lfhcal/lfhcal-simple/result/done.txt"


def test_static_resolution_preserves_dynamic_task_placeholders(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT", "/data")
    source = write_spec(tmp_path, """campaign static-then-dynamic
@env ROOT
@set PREFIX {ROOT}/run{run}

convert-{run}:
    @each run: 101 102
    echo {PREFIX}
""")
    tasks = load_spec(source).tasks
    assert [task.command for task in tasks] == [
        ("echo", "/data/run101"),
        ("echo", "/data/run102"),
    ]


def test_static_values_can_reference_later_static_values(tmp_path):
    source = write_spec(tmp_path, """campaign forward-static
@set RESULT {ROOT}/result
@set ROOT /work

prepare:
    echo {RESULT}
""")
    assert load_spec(source).tasks[0].command == ("echo", "/work/result")


def test_static_variable_cycles_are_rejected(tmp_path):
    source = write_spec(tmp_path, """campaign static-cycle
@set A {B}
@set B {A}

prepare:
    echo {A}
""")
    with pytest.raises(ValueError, match=r"static variable cycle: A -> B -> A"):
        load_spec(source)
