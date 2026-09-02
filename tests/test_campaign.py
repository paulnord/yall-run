import json
from pathlib import Path
import shlex
import sys

from yawl_run.campaign import campaign_status, create_campaign, start_local
from yawl_run.model import load_spec


def test_create_then_start_local_campaign(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(root / "examples" / "hello.yawl")
    campaign_dir = create_campaign(spec, tmp_path, backend="local")

    assert not list(campaign_dir.glob("*_attempt_*"))
    assert campaign_status(campaign_dir)["counts"] == {"pending": 3}

    start_local(campaign_dir)
    status = campaign_status(campaign_dir)
    assert status["counts"] == {"completed": 3}
    assert all(t["attempts"] == 1 for t in status["tasks"])
    assert json.loads((campaign_dir / "start.json").read_text())["max_jobs"] == 1


def test_local_j_runs_independent_tasks_concurrently(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    barrier = tmp_path / "barrier.py"
    barrier.write_text(
        "from pathlib import Path\n"
        "import sys, time\n"
        "mine, other = map(Path, sys.argv[1:3])\n"
        "mine.touch()\n"
        "for _ in range(100):\n"
        "    if other.exists():\n"
        "        raise SystemExit(0)\n"
        "    time.sleep(0.02)\n"
        "raise SystemExit(1)\n"
    )
    py = shlex.quote(sys.executable)
    script = shlex.quote(str(barrier))
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign parallel\n"
        "backend local\n\n"
        "left:\n"
        f"    {py} {script} left.ready right.ready\n\n"
        "right:\n"
        f"    {py} {script} right.ready left.ready\n\n"
        "finish: left right\n"
        "    echo done\n"
    )
    spec = load_spec(spec_file)
    campaign_dir = create_campaign(spec, tmp_path / "campaigns")
    start_local(campaign_dir, jobs=2)

    assert campaign_status(campaign_dir)["counts"] == {"completed": 3}
    assert json.loads((campaign_dir / "start.json").read_text())["max_jobs"] == 2


def test_argv_command_file_and_launch_provenance(tmp_path):
    input_path = tmp_path / "input.txt"
    input_path.write_text("hello\n")
    script = tmp_path / "transform.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os, sys\n"
        "Path(sys.argv[2]).write_text(Path(sys.argv[1]).read_text().upper())\n"
        "print(os.environ['YAWL_TASK'])\n"
        "print(os.environ['YAWL_PROVENANCE'])\n"
    )
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign provenance-test\n\n"
        "transform:\n"
        f"    %cwd {shlex.quote(str(tmp_path))}\n"
        "    @input source input.txt\n"
        "    @output result output.txt\n"
        f"    {shlex.quote(sys.executable)} {shlex.quote(str(script))} "
        "@input.source @output.result\n"
    )
    spec = load_spec(spec_file)
    campaign_dir = create_campaign(spec, tmp_path / "campaigns", backend="local")
    start_local(campaign_dir)

    attempt_dir = campaign_dir / "transform_attempt_001"
    attempt = json.loads((attempt_dir / "attempt.json").read_text())
    provenance = json.loads((attempt_dir / "provenance.json").read_text())
    stdout = (attempt_dir / "stdout.log").read_text().splitlines()

    assert attempt["state"] == "completed"
    assert isinstance(attempt["command"], list)
    assert attempt["inputs"][0]["role"] == "source"
    assert attempt["inputs"][0]["exists"] is True
    assert attempt["outputs"][0]["role"] == "result"
    assert attempt["outputs"][0]["exists"] is True
    assert provenance["campaign"]["id"] == campaign_dir.name
    assert provenance["task"]["name"] == "transform"
    assert provenance["task"]["attempt"] == 1
    assert provenance["task"]["outputs"][0]["path"] == str(tmp_path / "output.txt")
    assert stdout[0] == "transform"
    assert stdout[1] == str(attempt_dir / "provenance.json")
    assert (tmp_path / "output.txt").read_text() == "HELLO\n"
