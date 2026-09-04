import math
from pathlib import Path
import shutil

from yall_run.campaign import campaign_status, create_campaign, start_local
from yall_run.model import load_spec


def _copy_example(tmp_path: Path, name: str) -> Path:
    root = Path(__file__).resolve().parents[1]
    example = tmp_path / name
    shutil.copytree(
        root / "examples" / name,
        example,
        ignore=shutil.ignore_patterns("*-work"),
    )
    return example


def test_sqrt2_dependency_chain_example(tmp_path, monkeypatch, capsys):
    example = _copy_example(tmp_path, "sqrt2")
    monkeypatch.chdir(example)

    spec = load_spec("Yallfile")
    assert len(spec.tasks) == 15
    tasks = {task.name: task for task in spec.tasks}
    assert tasks["step-01"].parents == ("seed",)
    assert tasks["step-12"].parents == ("step-11",)
    assert tasks["check"].parents == ("step-12",)

    campaign_dir = create_campaign(
        spec,
        tmp_path / "campaigns",
        backend="local",
        local_jobs=4,
    )
    start_local(campaign_dir)
    output = capsys.readouterr().out

    assert output.index("[done ] step-11") < output.index("[start] step-12")
    assert output.index("[done ] step-12") < output.index("[start] check")
    assert campaign_status(campaign_dir)["counts"] == {"completed": 15}

    values = dict(
        line.split("=", 1)
        for line in (example / "sqrt2-work" / "sqrt2.txt").read_text().splitlines()
    )
    assert values["convergent"] == "47321/33461"
    assert abs(float(values["sqrt2"]) - math.sqrt(2.0)) < 1.0e-9


def test_sqrt2_binomial_map_reduce_example(tmp_path, monkeypatch, capsys):
    example = _copy_example(tmp_path, "sqrt2-binomial")
    monkeypatch.chdir(example)

    spec = load_spec("Yallfile")
    assert len(spec.tasks) == 11
    assert [task.name for task in spec.tasks[:2]] == ["prepare", "partial-000"]
    tasks = {task.name: task for task in spec.tasks}
    assert tasks["sum"].parents == tuple(f"partial-{i:03d}" for i in range(8))
    assert len(tasks["sum"].inputs) == 8
    assert tasks["check"].parents == ("sum",)

    campaign_dir = create_campaign(
        spec,
        tmp_path / "campaigns",
        backend="local",
        local_jobs=4,
    )
    start_local(campaign_dir)
    output = capsys.readouterr().out

    for i in range(8):
        assert output.index(f"[done ] partial-{i:03d}") < output.index("[start] sum")
    assert output.index("[done ] sum") < output.index("[start] check")
    assert campaign_status(campaign_dir)["counts"] == {"completed": 11}

    values = dict(
        line.split("=", 1)
        for line in (
            example / "sqrt2-binomial-work" / "sqrt2.txt"
        ).read_text().splitlines()
    )
    assert int(values["terms"]) == 40_000
    assert abs(float(values["sqrt2"]) - math.sqrt(2.0)) < 5.0e-8
    assert (example / "sqrt2-binomial-work" / "check.txt").is_file()


def test_e_hierarchical_reduction_example(tmp_path, monkeypatch, capsys):
    example = _copy_example(tmp_path, "e")
    monkeypatch.chdir(example)

    spec = load_spec("Yallfile")
    assert len(spec.tasks) == 17
    tasks = {task.name: task for task in spec.tasks}
    assert tasks["pair-0"].parents == ("terms-00", "terms-01")
    assert tasks["group-0"].parents == ("pair-0", "pair-1")
    assert tasks["sum"].parents == ("group-0", "group-1")
    assert tasks["check"].parents == ("sum",)

    campaign_dir = create_campaign(
        spec,
        tmp_path / "campaigns",
        backend="local",
        local_jobs=4,
    )
    start_local(campaign_dir)
    output = capsys.readouterr().out

    assert output.index("[done ] group-0") < output.index("[start] sum")
    assert output.index("[done ] group-1") < output.index("[start] sum")
    assert campaign_status(campaign_dir)["counts"] == {"completed": 17}

    values = dict(
        line.split("=", 1)
        for line in (example / "e-work" / "e.txt").read_text().splitlines()
    )
    assert int(values["terms"]) == 32
    assert abs(float(values["e"]) - math.e) < 1.0e-15
