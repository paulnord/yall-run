import json
from pathlib import Path

import pytest

from yawl_run.campaign import create_campaign
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


def test_each_explicit_values_select_exact_family(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign selected-runs

convert-{run}:
    @each run 296 298 299 300
    @input raw /shared/LFHCAL/raw/Run{run}.h2g
    @output root work/converted/rawHGCROC_{run}.root
    ./Convert -i @input.raw -o @output.root
""",
    )
    spec = load_spec(spec_file)
    assert [task.name for task in spec.tasks] == [
        "convert-296",
        "convert-298",
        "convert-299",
        "convert-300",
    ]
    assert [task.inputs[0].path for task in spec.tasks] == [
        "/shared/LFHCAL/raw/Run296.h2g",
        "/shared/LFHCAL/raw/Run298.h2g",
        "/shared/LFHCAL/raw/Run299.h2g",
        "/shared/LFHCAL/raw/Run300.h2g",
    ]
    assert spec.tasks[1].outputs[0].path == "work/converted/rawHGCROC_298.root"
    assert spec.tasks[1].command == (
        "./Convert",
        "-i",
        "/shared/LFHCAL/raw/Run298.h2g",
        "-o",
        "work/converted/rawHGCROC_298.root",
    )


def test_each_explicit_values_are_frozen_in_campaign_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign selected-runs

convert-{run}:
    @each run 296 308
    @input raw /shared/LFHCAL/raw/Run{run}.h2g
    @output root converted/rawHGCROC_{run}.root
    ./Convert @input.raw @output.root
""",
    )
    campaign_dir = create_campaign(load_spec(spec_file), tmp_path / "campaigns")
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    assert manifest["task_order"] == ["convert-296", "convert-308"]
    assert manifest["tasks"]["convert-308"]["inputs"][0]["path"] == (
        "/shared/LFHCAL/raw/Run308.h2g"
    )
    output_path = str(tmp_path / "converted" / "rawHGCROC_308.root")
    assert manifest["tasks"]["convert-308"]["outputs"][0]["path"] == output_path
    assert manifest["tasks"]["convert-308"]["command"] == [
        "./Convert",
        "/shared/LFHCAL/raw/Run308.h2g",
        "converted/rawHGCROC_308.root",
    ]


def test_each_explicit_name_must_match_task_placeholder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign bad-each

convert-{run}:
    @each sample 296 298
    echo {run}
""",
    )
    with pytest.raises(ValueError, match="explicit @each name must match"):
        load_spec(spec_file)


def test_env_value_substitutes_into_paths_and_is_recorded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw_root = tmp_path / "shared" / "raw"
    monkeypatch.setenv("LFHCAL_RAW", str(raw_root))
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign env-path
@env LFHCAL_RAW

convert-{run}:
    @each run 296 308
    @input raw {LFHCAL_RAW}/Run{run}.h2g
    @output root converted/rawHGCROC_{run}.root
    ./Convert -c @input.raw -o @output.root
""",
    )
    spec = load_spec(spec_file)
    assert spec.set_values == (("LFHCAL_RAW", str(raw_root)),)
    assert spec.tasks[1].inputs[0].path == str(raw_root / "Run308.h2g")
    assert spec.tasks[1].command == (
        "./Convert",
        "-c",
        str(raw_root / "Run308.h2g"),
        "-o",
        "converted/rawHGCROC_308.root",
    )

    campaign_dir = create_campaign(spec, tmp_path / "campaigns")
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    assert manifest["set_values"] == {"LFHCAL_RAW": str(raw_root)}
    assert manifest["tasks"]["convert-308"]["inputs"][0]["path"] == str(
        raw_root / "Run308.h2g"
    )
    assert "@env LFHCAL_RAW" in (campaign_dir / "Yawlfile").read_text()


def test_env_requires_variable_to_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LFHCAL_RAW", raising=False)
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign missing-env
@env LFHCAL_RAW

thing:
    echo {LFHCAL_RAW}
""",
    )
    with pytest.raises(ValueError, match="required environment variable 'LFHCAL_RAW' is not set"):
        load_spec(spec_file)


def test_set_behavior_is_unchanged_next_to_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_ROOT", "/shared/data")
    spec_file = _write(
        tmp_path / "Yawlfile",
        """campaign set-and-env
@set dataset beam2026
@env DATA_ROOT

thing:
    echo {dataset} {DATA_ROOT}
""",
    )
    spec = load_spec(spec_file)
    assert spec.tasks[0].command == ("echo", "beam2026", "/shared/data")
    assert spec.set_values == (
        ("dataset", "beam2026"),
        ("DATA_ROOT", "/shared/data"),
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
