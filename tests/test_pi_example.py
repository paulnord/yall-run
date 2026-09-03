import math
from pathlib import Path
import shutil

from yawl_run.campaign import campaign_status, create_campaign, start_local
from yawl_run.model import load_spec


def test_pi_map_reduce_example(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    example = tmp_path / "pi"
    shutil.copytree(
        root / "examples" / "pi",
        example,
        ignore=shutil.ignore_patterns("*-work"),
    )
    monkeypatch.chdir(example)

    spec = load_spec("Yawlfile")
    assert len(spec.tasks) == 10
    assert [task.name for task in spec.tasks[:2]] == ["prepare", "partial-000"]
    assert spec.tasks[-1].name == "sum"
    assert len(spec.tasks[-1].parents) == 8
    assert len(spec.tasks[-1].inputs) == 8

    campaign_dir = create_campaign(
        spec,
        tmp_path / "campaigns",
        backend="local",
        local_jobs=4,
    )
    start_local(campaign_dir)
    status = campaign_status(campaign_dir)
    assert status["counts"] == {"completed": 10}

    values = dict(
        line.split("=", 1)
        for line in (example / "pi-work" / "pi.txt").read_text().splitlines()
    )
    assert int(values["terms"]) == 2_000_000
    assert abs(float(values["pi"]) - math.pi) < 1.0e-6
