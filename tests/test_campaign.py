import json
from pathlib import Path
import sys

from yawl_run.campaign import campaign_status, start_campaign
from yawl_run.model import load_spec


def test_local_campaign(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(root / "examples" / "hello.toml")
    campaign_dir = start_campaign(spec, tmp_path)
    status = campaign_status(campaign_dir)
    assert status["counts"] == {"completed": 2}
    assert all(t["attempts"] == 1 for t in status["tasks"])


def test_argv_command_and_file_provenance(tmp_path):
    input_path = tmp_path / "input.txt"
    input_path.write_text("hello\n")
    spec_file = tmp_path / "provenance.toml"
    spec_file.write_text(
        "[campaign]\n"
        "name = \"provenance-test\"\n\n"
        "[[task]]\n"
        "name = \"transform\"\n"
        f"cwd = {json.dumps(str(tmp_path))}\n"
        f"command = [{json.dumps(sys.executable)}, \"-c\", "
        "\"from pathlib import Path; Path('output.txt').write_text(Path('input.txt').read_text().upper())\"]\n"
        "inputs = [{role = \"source\", path = \"input.txt\"}]\n"
        "outputs = [{role = \"result\", path = \"output.txt\"}]\n"
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
