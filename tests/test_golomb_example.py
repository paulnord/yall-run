import json
from pathlib import Path
import shutil
import subprocess
import sys

from yawl_run.model import load_spec


def test_golomb_example_graph(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    example = root / "examples" / "golomb"
    monkeypatch.chdir(example)
    spec = load_spec("Yawlfile")

    assert spec.name == "golomb-11-branch-and-bound"
    assert len(spec.tasks) == 11
    tasks = {task.name: task for task in spec.tasks}
    search_names = [f"search-{index:02d}" for index in range(8)]
    assert all(name in tasks for name in search_names)
    assert tasks["reduce"].parents == tuple(search_names)
    assert tasks["verify"].parents == ("reduce",)


def test_golomb_solver_protocol_on_six_marks(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = root / "examples" / "golomb"
    example = tmp_path / "golomb"
    shutil.copytree(source, example)

    work = example / "quick-work"
    work.mkdir()
    incumbent = work / "incumbent.json"
    shard0 = work / "shard-00.txt"
    shard1 = work / "shard-01.txt"
    result0 = work / "search-00.json"
    result1 = work / "search-01.json"
    best = work / "best.json"
    report = work / "report.txt"
    shard0.write_text("0 2\n")
    shard1.write_text("1 2\n")

    def run(*arguments: object) -> None:
        subprocess.run(
            [sys.executable, *map(str, arguments)],
            cwd=example,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    run("init_incumbent.py", "--order", 6, "--limit", 22, incumbent)
    run("search.py", shard0, incumbent, result0)
    run("search.py", shard1, incumbent, result1)
    run("reduce.py", incumbent, result0, result1, "-o", best)
    run("verify.py", best, report)

    reduced = json.loads(best.read_text())
    assert reduced["order"] == 6
    assert reduced["length"] == 17
    assert reduced["optimality_established"] is True
    assert len(reduced["ruler"]) == 6
    assert "optimality_established=yes" in report.read_text()
