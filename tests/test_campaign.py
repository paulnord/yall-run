import json
from pathlib import Path
import shlex
import sys

from yawl_run.campaign import campaign_status, start_campaign
from yawl_run.model import load_spec


def test_local_campaign(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(root / "examples" / "hello.yawl")
    campaign_dir = start_campaign(spec, tmp_path)
    status = campaign_status(campaign_dir)
    assert status["counts"] == {"completed": 3}
    assert all(t["attempts"] == 1 for t in status["tasks"])


def test_argv_command_and_file_provenance(tmp_path):
    input_path = tmp_path / "input.txt"
    input_path.write_text("hello\n")
    script = tmp_path / "transform.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[2]).write_text(Path(sys.argv[1]).read_text().upper())\n"
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
    campaign_dir = start_campaign(spec, tmp_path / "campaigns")

    attempt = json.loads(
        (campaign_dir / "tasks" / "transform" / "attempts" / "001" / "attempt.json").read_text()
    )
    assert attempt["state"] == "completed"
    assert isinstance(attempt["command"], list)
    assert attempt["inputs"][0]["role"] == "source"
    assert attempt["inputs"][0]["exists"] is True
    assert attempt["outputs"][0]["role"] == "result"
    assert attempt["outputs"][0]["exists"] is True
    assert (tmp_path / "output.txt").read_text() == "HELLO\n"
