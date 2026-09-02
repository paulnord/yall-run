from pathlib import Path

import pytest

from yawl_run.model import load_spec


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_named_refs_and_resources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign demo
backend condor
%memory 3GB

convert:
    @input raw raw_137.h2g
    @output root converted/raw_137.root
    %retry 1
    %cpus 4
    %memory 8GB
    ./Convert -i @input.raw -o @output.root
""",
    )
    spec = load_spec(spec_file)
    task = spec.tasks[0]
    assert spec.condor.request_memory == "3GB"
    assert task.command == (
        "./Convert",
        "-i",
        "raw_137.h2g",
        "-o",
        "converted/raw_137.root",
    )
    assert task.resources.cpus == 4
    assert task.resources.memory == "8GB"
    assert task.retries == 1


def test_each_maps_one_input_to_one_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "converted").mkdir()
    for run in ("137", "138", "142"):
        (tmp_path / "converted" / f"2026_PST10_raw_{run}.root").touch()
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign pedestal-map
backend condor
@set dataset 2026_PST10

pedestal-{run}:
    @each raw converted/{dataset}_raw_{run}.root
    @output pedestal pedestal/{dataset}_pedestal_{run}.root
    %memory 4GB
    ./make-pedestal @input.raw -o @output.pedestal
""",
    )
    spec = load_spec(spec_file)
    assert [task.name for task in spec.tasks] == [
        "pedestal-137",
        "pedestal-138",
        "pedestal-142",
    ]
    assert spec.tasks[1].inputs[0].path == "converted/2026_PST10_raw_138.root"
    assert spec.tasks[1].outputs[0].path == "pedestal/2026_PST10_pedestal_138.root"
    assert spec.tasks[1].command == (
        "./make-pedestal",
        "converted/2026_PST10_raw_138.root",
        "-o",
        "pedestal/2026_PST10_pedestal_138.root",
    )


def test_patterned_child_inherits_family(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "converted").mkdir()
    for run in ("137", "138"):
        (tmp_path / "converted" / f"raw_{run}.root").touch()
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign chain

pedestal-{run}:
    @each raw converted/raw_{run}.root
    @output pedestal pedestal/pedestal_{run}.root
    make-ped @input.raw @output.pedestal

check-{run}: pedestal-{run}
    @input pedestal pedestal/pedestal_{run}.root
    check @input.pedestal
""",
    )
    spec = load_spec(spec_file)
    assert [task.name for task in spec.tasks] == [
        "pedestal-137",
        "pedestal-138",
        "check-137",
        "check-138",
    ]
    assert spec.tasks[2].parents == ("pedestal-137",)
    assert spec.tasks[3].parents == ("pedestal-138",)


def test_static_task_fans_in_pattern_family(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "converted").mkdir()
    for run in ("137", "138"):
        (tmp_path / "converted" / f"raw_{run}.root").touch()
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign fanin

pedestal-{run}:
    @each raw converted/raw_{run}.root
    @output pedestal pedestal/pedestal_{run}.root
    make-ped @input.raw @output.pedestal

summary: pedestal-{run}
    @input pedestal pedestal/pedestal_{run}.root
    summarize @input.pedestal
""",
    )
    spec = load_spec(spec_file)
    summary = spec.tasks[-1]
    assert summary.parents == ("pedestal-137", "pedestal-138")
    assert [item.path for item in summary.inputs] == [
        "pedestal/pedestal_137.root",
        "pedestal/pedestal_138.root",
    ]
    assert summary.command == (
        "summarize",
        "pedestal/pedestal_137.root",
        "pedestal/pedestal_138.root",
    )


def test_shell_escape_hatch_quotes_named_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign shell

report:
    @output text 'report file.txt'
    ! echo hello > @output.text
""",
    )
    spec = load_spec(spec_file)
    assert spec.tasks[0].command == "echo hello > 'report file.txt'"


def test_bad_named_reference_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign bad-ref

thing:
    echo @input.missing
""",
    )
    with pytest.raises(ValueError, match="no such input"):
        load_spec(spec_file)
