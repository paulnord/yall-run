from pathlib import Path

from yawl_run.campaign import campaign_status, start_campaign
from yawl_run.model import load_spec


def test_local_campaign(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(root / "examples" / "hello.toml")
    campaign_dir = start_campaign(spec, tmp_path)
    status = campaign_status(campaign_dir)
    assert status["counts"] == {"completed": 2}
    assert all(t["attempts"] == 1 for t in status["tasks"])
