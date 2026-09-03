import json
from pathlib import Path
import shlex
import sys

import pytest

from yawl_run.campaign import campaign_status, create_campaign, start_local
from yawl_run.model import load_spec


def test_create_then_start_local_campaign(tmp_path, capsys):
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(root / "examples" / "hello" / "Yawlfile")
    campaign_dir = create_campaign(spec, tmp_path, backend="local")

    assert not list(campaign_dir.glob("*_attempt_*"))
    assert not (campaign_dir / "provenance.json").exists()
    assert not (campaign_dir / "tasks").exists()
    assert campaign_status(campaign_dir)["counts"] == {"pending": 3}
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    assert manifest["schema"] == 7
    assert manifest["execution"] == {"local": {"jobs": 1}}
    assert manifest["task_order"] == ["left", "right", "finish"]
    assert manifest["tasks"]["finish"]["parents"] == ["left", "right"]
    assert manifest["creation"]["cwd"]
    assert manifest["creation"]["hostname"] is not None
    assert json.loads((campaign_dir / "state" / "left.json").read_text()) == {
        "attempts": 0,
        "state": "pending",
    }

    start_local(campaign_dir)
    output = capsys.readouterr().out
    assert "[local] host=" in output
    assert "jobs=1" in output
    assert "logical_cpus=" in output
    assert "[start] left" in output
    assert "[done ] finish" in output
    assert "real=" in output
    assert "user=" in output
    assert "sys=" in output
    assert "[local] finished completed=3 failed=0 blocked=0" in output

    status = campaign_status(campaign_dir)
    assert status["counts"] == {"completed": 3}
    assert all(t["attempts"] == 1 for t in status["tasks"])
    start_record = json.loads((campaign_dir / "start.json").read_text())
    assert start_record["execution"] == {"local": {"jobs": 1}}


def test_local_j_is_frozen_at_create_and_runs_tasks_concurrently(
    tmp_path, monkeypatch, capsys
):
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
    campaign_dir = create_campaign(spec, tmp_path / "campaigns", local_jobs=2)
    start_local(campaign_dir)
    output = capsys.readouterr().out

    assert output.index("[done ] left") < output.index("[start] finish")
    assert output.index("[done ] right") < output.index("[start] finish")
    assert campaign_status(campaign_dir)["counts"] == {"completed": 3}
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    assert manifest["execution"] == {"local": {"jobs": 2}}


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
    assert attempt["timing"]["real_seconds"] >= 0
    if attempt["timing"]["user_seconds"] is not None:
        assert attempt["timing"]["user_seconds"] >= 0
    if attempt["timing"]["sys_seconds"] is not None:
        assert attempt["timing"]["sys_seconds"] >= 0
    assert provenance["campaign"]["id"] == campaign_dir.name
    assert provenance["task"]["name"] == "transform"
    assert provenance["task"]["attempt"] == 1
    assert provenance["task"]["outputs"][0]["path"] == str(tmp_path / "output.txt")
    assert stdout[0] == "transform"
    assert stdout[1] == str(attempt_dir / "provenance.json")
    assert (tmp_path / "output.txt").read_text() == "HELLO\n"


def test_missing_declared_input_fails_without_running_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "should_not_run.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[2]).write_text('ran\\n')\n"
        "Path(sys.argv[3]).write_text('output\\n')\n"
    )
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign missing-input\n"
        "backend local\n\n"
        "work:\n"
        f"    %cwd {shlex.quote(str(tmp_path))}\n"
        "    @input source missing.txt\n"
        "    @output result output.txt\n"
        f"    {shlex.quote(sys.executable)} {shlex.quote(str(script))} "
        "@input.source marker.txt @output.result\n"
    )

    campaign_dir = create_campaign(
        load_spec(spec_file), tmp_path / "campaigns", backend="local"
    )
    with pytest.raises(RuntimeError, match="local campaign failed"):
        start_local(campaign_dir)

    attempt_dir = campaign_dir / "work_attempt_001"
    attempt = json.loads((attempt_dir / "attempt.json").read_text())
    assert attempt["state"] == "failed"
    assert attempt["returncode"] == 2
    assert attempt["command_returncode"] is None
    assert attempt["failure"] == {
        "kind": "missing_inputs",
        "paths": [str(tmp_path / "missing.txt")],
    }
    assert attempt["inputs"][0]["exists"] is False
    assert attempt["outputs"][0]["exists"] is False
    assert "declared input missing" in (attempt_dir / "stderr.log").read_text()
    assert not (tmp_path / "marker.txt").exists()
    assert not (tmp_path / "output.txt").exists()
    assert campaign_status(campaign_dir)["counts"] == {"failed": 1}


def test_missing_declared_output_fails_after_successful_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "runs_without_output.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('ran\\n')\n"
    )
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign missing-output\n"
        "backend local\n\n"
        "work:\n"
        f"    %cwd {shlex.quote(str(tmp_path))}\n"
        "    @output result output.txt\n"
        f"    {shlex.quote(sys.executable)} {shlex.quote(str(script))} marker.txt\n"
    )

    campaign_dir = create_campaign(
        load_spec(spec_file), tmp_path / "campaigns", backend="local"
    )
    with pytest.raises(RuntimeError, match="local campaign failed"):
        start_local(campaign_dir)

    attempt_dir = campaign_dir / "work_attempt_001"
    attempt = json.loads((attempt_dir / "attempt.json").read_text())
    assert attempt["state"] == "failed"
    assert attempt["returncode"] == 1
    assert attempt["command_returncode"] == 0
    assert attempt["failure"] == {
        "kind": "missing_outputs",
        "paths": [str(tmp_path / "output.txt")],
    }
    assert attempt["outputs"][0]["exists"] is False
    assert "declared output missing" in (attempt_dir / "stderr.log").read_text()
    assert (tmp_path / "marker.txt").read_text() == "ran\n"
    assert not (tmp_path / "output.txt").exists()
    assert campaign_status(campaign_dir)["counts"] == {"failed": 1}
